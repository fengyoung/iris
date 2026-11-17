import Foundation
import ScreenCaptureKit
import AVFoundation
import CoreAudio
import CoreGraphics
import AppKit

// NDJSON exclusively on stdout. No file recording, ASR, network or permission prompts by default.
final class Events {
    private let lock = NSLock()
    func send(_ event: [String: Any]) {
        lock.lock(); defer { lock.unlock() }
        var object = event; object["protocol"] = 1
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
        FileHandle.standardOutput.write(data + Data([10]))
    }
    func error(_ code: String, _ message: String, source: String = "helper") {
        send(["event": "error", "code": code, "message": message, "source": source])
    }
}
let events = Events()

func microphones() -> [[String: Any]] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size) == noErr else { return [] }
    var devices = [AudioDeviceID](repeating: 0, count: Int(size)/MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &devices) == noErr else { return [] }
    return devices.compactMap { device in
        var input = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreamConfiguration, mScope: kAudioDevicePropertyScopeInput, mElement: kAudioObjectPropertyElementMain)
        var bytes: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &input, 0, nil, &bytes) == noErr, bytes > 0 else { return nil }
        let memory = UnsafeMutableRawPointer.allocate(byteCount: Int(bytes), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { memory.deallocate() }
        guard AudioObjectGetPropertyData(device, &input, 0, nil, &bytes, memory) == noErr else { return nil }
        let list = UnsafeMutableAudioBufferListPointer(memory.assumingMemoryBound(to: AudioBufferList.self))
        guard list.contains(where: { $0.mNumberChannels > 0 }) else { return nil }
        func string(_ selector: AudioObjectPropertySelector) -> String {
            var a = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
            var value: Unmanaged<CFString>?
            var n = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            guard AudioObjectGetPropertyData(device, &a, 0, nil, &n, &value) == noErr,
                  let value else { return "" }
            return value.takeUnretainedValue() as String
        }
        return ["id": string(kAudioDevicePropertyDeviceUID), "name": string(kAudioObjectPropertyName), "device_id": device]
    }
}

func permissionStatus() -> [String: Any] {
    let status: String
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: status = "authorized"
    case .denied: status = "denied"
    case .restricted: status = "restricted"
    case .notDetermined: status = "not_determined"
    @unknown default: status = "unknown"
    }
    return ["screen_audio_authorized": CGPreflightScreenCaptureAccess(), "microphone": status]
}

@available(macOS 15.0, *)
final class Capture: NSObject, SCStreamOutput, SCStreamDelegate {
    var stream: SCStream?
    let queue = DispatchQueue(label: "iris.audio.samples")
    let origin = CMClockGetTime(CMClockGetHostTimeClock())
    var active = false
    var finishing = false
    var sequence: [String: Int] = [:]
    var pending: [[String: Any]] = []
    var monitor: DispatchSourceTimer?
    let pid: pid_t
    let microphoneID: String
    init(pid: pid_t, microphoneID: String) { self.pid = pid; self.microphoneID = microphoneID }

    func start() async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        guard let app = content.applications.first(where: { $0.processID == pid }), let display = content.displays.first else {
            throw NSError(domain: "iris", code: 1, userInfo: [NSLocalizedDescriptionKey: "Selected application or display is no longer available"])
        }
        guard microphones().contains(where: { $0["id"] as? String == microphoneID }) else {
            throw NSError(domain: "iris", code: 2, userInfo: [NSLocalizedDescriptionKey: "Selected microphone is unavailable; no fallback is permitted"])
        }
        let filter = SCContentFilter(display: display, including: [app], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48000
        config.channelCount = 2
        config.captureMicrophone = true
        config.microphoneCaptureDeviceID = microphoneID
        // No screen output is attached. Tiny video geometry reduces unused capture work.
        config.width = 2; config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let s = SCStream(filter: filter, configuration: config, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        try s.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        stream = s
        try await s.startCapture()
        queue.sync {
            guard !finishing else { return }
            active = true
            events.send(["event": "started", "app_pid": pid, "microphone_id": microphoneID,
                         "clock": "host_monotonic", "origin_host_seconds": CMTimeGetSeconds(origin),
                         "encoding": "f32le", "layout": "interleaved", "sample_rate": "native_per_packet"])
            for packet in pending { events.send(packet) }
            pending.removeAll()
        }
        let timer = DispatchSource.makeTimerSource(queue: .global())
        timer.schedule(deadline: .now()+1, repeating: 1)
        timer.setEventHandler { [weak self] in
            guard let self else { return }
            if NSRunningApplication(processIdentifier: self.pid)?.isTerminated != false {
                events.error("application_ended", "Selected meeting application exited", source: "remote")
                Task { await self.stop(reason: "application_ended") }
            } else if !microphones().contains(where: { $0["id"] as? String == self.microphoneID }) {
                events.error("device_disconnected", "Selected microphone disappeared", source: "local")
                Task { await self.stop(reason: "device_disconnected") }
            }
        }
        monitor = timer; timer.resume()
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard !finishing, buffer.isValid, CMSampleBufferDataIsReady(buffer),
              type == .audio || type == .microphone else { return }
        let source = type == .audio ? "remote" : "local"
        do {
            let packet = try pcmPacket(buffer, source: source)
            if active { events.send(packet) }
            else if pending.count < 64 { pending.append(packet) }
            else { throw NSError(domain: "iris", code: 4, userInfo: [NSLocalizedDescriptionKey: "Startup audio queue overflow"]) }
        } catch {
            events.error("pcm_conversion_failed", error.localizedDescription, source: source)
            Task { await self.stop(reason: "pcm_conversion_failed") }
        }
    }

    func pcmPacket(_ buffer: CMSampleBuffer, source: String) throws -> [String: Any] {
        guard let format = CMSampleBufferGetFormatDescription(buffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee,
              asbd.mFormatID == kAudioFormatLinearPCM, asbd.mBitsPerChannel == 32,
              asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              asbd.mFormatFlags & kAudioFormatFlagIsBigEndian == 0 else {
            throw NSError(domain: "iris", code: 3, userInfo: [NSLocalizedDescriptionKey: "Expected native little-endian Float32 PCM"])
        }
        var size = 0
        CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(buffer, bufferListSizeNeededOut: &size, bufferListOut: nil, bufferListSize: 0, blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: nil)
        let memory = UnsafeMutableRawPointer.allocate(byteCount: size, alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { memory.deallocate() }
        var retained: CMBlockBuffer?
        let list = memory.assumingMemoryBound(to: AudioBufferList.self)
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(buffer, bufferListSizeNeededOut: nil, bufferListOut: list, bufferListSize: size, blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, blockBufferOut: &retained)
        guard status == noErr else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(status)) }
        let frames = CMSampleBufferGetNumSamples(buffer)
        let channels = Int(asbd.mChannelsPerFrame)
        let buffers = UnsafeMutableAudioBufferListPointer(list)
        var data = Data()
        if asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0 {
            guard buffers.count == channels, buffers.allSatisfy({ $0.mData != nil && Int($0.mDataByteSize) >= frames*4 }) else { throw NSError(domain: "iris", code: 5) }
            data.reserveCapacity(frames*channels*4)
            for frame in 0..<frames { for b in buffers { data.append(b.mData!.advanced(by: frame*4).assumingMemoryBound(to: UInt8.self), count: 4) } }
        } else {
            guard buffers.count == 1, let bytes = buffers[0].mData, Int(buffers[0].mDataByteSize) >= frames*channels*4 else { throw NSError(domain: "iris", code: 6) }
            data = Data(bytes: bytes, count: frames*channels*4)
        }
        let pts = CMSampleBufferGetPresentationTimeStamp(buffer)
        let seconds = CMTimeGetSeconds(CMTimeSubtract(pts, origin))
        guard seconds.isFinite, seconds >= 0 else { throw NSError(domain: "iris", code: 7, userInfo: [NSLocalizedDescriptionKey: "Invalid host-clock timestamp"]) }
        let number = sequence[source, default: 0]; sequence[source] = number+1
        return ["event": "pcm", "source": source, "sequence": number, "timestamp_ns": Int64(seconds*1_000_000_000), "sample_rate": asbd.mSampleRate, "channels": channels, "frames": frames, "encoding": "f32le", "layout": "interleaved", "data": data.base64EncodedString()]
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        events.error("stream_stopped", error.localizedDescription, source: "capture")
        Task { await stop(reason: "stream_error") }
    }
    func stop(reason: String) async {
        let shouldStop = queue.sync { () -> Bool in
            if finishing { return false }; finishing = true; active = false; pending.removeAll(); return true
        }
        guard shouldStop else { return }
        monitor?.cancel(); monitor = nil
        do { try await stream?.stopCapture() }
        catch { events.error("stop_failed", error.localizedDescription, source: "capture") }
        events.send(["event": "stopped", "reason": reason])
        exit(reason == "command" || reason == "stdin_eof" || reason == "signal" ? 0 : 1)
    }
}

@main
struct Main {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        events.send(["event": "hello", "name": "Iris-interview audio helper", "minimum_macos": "15.0"])
        if args == ["--self-test"] {
            let bytes = [Float(0), Float(0.5), Float(-0.5)].withUnsafeBytes { Data($0) }
            for (source, ns) in [("local", 0), ("remote", 10_000_000)] {
                events.send(["event": "pcm", "source": source, "sequence": 0, "timestamp_ns": ns, "sample_rate": 16000, "channels": 1, "frames": 3, "encoding": "f32le", "layout": "interleaved", "data": bytes.base64EncodedString(), "synthetic": true])
            }
            events.send(["event": "stopped", "reason": "self_test"]); return
        }
        if args == ["--diagnostics"] {
            events.send(["event": "diagnostics", "permissions": permissionStatus(), "os": ProcessInfo.processInfo.operatingSystemVersionString, "capture_supported": ProcessInfo.processInfo.isOperatingSystemAtLeast(OperatingSystemVersion(majorVersion: 15, minorVersion: 0, patchVersion: 0))]); return
        }
        if args == ["--request-permission"] {
            let screen = CGRequestScreenCaptureAccess()
            let mic = await AVCaptureDevice.requestAccess(for: .audio)
            events.send(["event": "permissions", "screen_audio_authorized": screen, "microphone_authorized": mic]); return
        }
        guard #available(macOS 15.0, *) else { events.error("unsupported_os", "macOS 15 or later is required"); return }
        if args == ["--list"] {
            let inputs = microphones()
            guard CGPreflightScreenCaptureAccess() else {
                events.send(["event": "devices", "applications": [], "microphones": inputs, "screen_audio_authorized": false])
                events.error("screen_permission_required", "Application audio enumeration requires existing screen/audio capture permission"); return
            }
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                let apps = content.applications.map { ["pid": $0.processID, "bundle_id": $0.bundleIdentifier, "name": $0.applicationName] as [String: Any] }
                events.send(["event": "devices", "applications": apps, "microphones": inputs, "screen_audio_authorized": true])
            } catch { events.error("enumeration_failed", error.localizedDescription) }
            return
        }
        func option(_ name: String) -> String? {
            guard let i = args.firstIndex(of: name), i+1 < args.count else { return nil }; return args[i+1]
        }
        guard args.first == "--capture", let pidString = option("--app-pid"), let pid = Int32(pidString), pid > 0, let uid = option("--microphone-id"), !uid.isEmpty else {
            events.error("invalid_arguments", "Use --diagnostics, --list, --request-permission, --self-test or --capture --app-pid PID --microphone-id UID"); exit(2)
        }
        guard CGPreflightScreenCaptureAccess(), AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else {
            events.error("permission_required", "Capture requires existing screen/audio and microphone permissions; invoke explicit permission action first"); exit(1)
        }
        let capture = Capture(pid: pid, microphoneID: uid)
        do { try await capture.start() }
        catch { events.error("start_failed", error.localizedDescription, source: "capture"); exit(1) }
        signal(SIGTERM, SIG_IGN); signal(SIGINT, SIG_IGN)
        let signals = [SIGTERM, SIGINT].map { number -> DispatchSourceSignal in
            let source = DispatchSource.makeSignalSource(signal: number)
            source.setEventHandler { Task { await capture.stop(reason: "signal") } }
            source.resume(); return source
        }
        defer { withExtendedLifetime((capture, signals)) {} }
        await withUnsafeContinuation { (_: UnsafeContinuation<Void, Never>) in
            DispatchQueue.global().async {
                while let line = readLine() {
                    guard let data = line.data(using: .utf8), let cmd = try? JSONSerialization.jsonObject(with: data) as? [String: Any], cmd["command"] as? String == "stop" else {
                        events.error("invalid_command", "Expected {\"command\":\"stop\"}", source: "control"); continue
                    }
                    Task { await capture.stop(reason: "command") }; return
                }
                Task { await capture.stop(reason: "stdin_eof") }
            }
            // The control task remains suspended; stop terminates this helper process.
        }
    }
}
