# Iris-interview audio helper protocol v1

macOS 15+; standalone Swift helper, no Iris/Caption imports. Caption inspection found sounddevice single-microphone capture at 16kHz, unsuitable for the independent meeting track. Existing Caption files are unchanged.

## Build and safe checks

Primary verified build: `sh build.sh`; output `.build/iris-audio-helper`. `python3 test_protocol.py` validates only synthetic transport. The installed Swift 6.3.3 toolchain's SwiftPM manifest library currently fails to link PackageDescription.Package; direct `swiftc` compilation succeeds. `Package.swift` is retained for compatible SwiftPM toolchains but is not the verified build on this host.

`--self-test` emits only synthetic samples, never enumerates, opens devices or asks permission. `--diagnostics` checks existing permission status without requesting it. Neither records audio. `--list` lists CoreAudio input UIDs and running applications; without existing screen/audio permission, emits microphones plus screen_permission_required, never calls the permission-triggering ScreenCaptureKit enumeration API. Application list deliberately includes all applications; UI can prefer Feishu/Lark/Tencent Meeting without fragile bundle-ID exclusions.

`--request-permission` is the ONLY explicit permission request action; invoke solely from a user UI action. It may show system dialogs and requires packaged privacy usage descriptions (NSMicrophoneUsageDescription; NSAudioCaptureUsageDescription for relevant systems). Never run this during automated tests. Parent app packaging/signing and TCC identity need real UI verification.

## Capture

`--capture --app-pid PID --microphone-id CORE_AUDIO_UID`

Preflights existing screen/audio and microphone grants; never requests implicitly. Uses SCContentFilter selecting the specified running process, excluding helper audio. ScreenCaptureKit separate `.audio` and `.microphone` outputs; no screen frame output, no files, no network. Rejects unavailable app/device without silently falling back. Explicitly requires macOS15 to use native independent microphone output.

stdin one command per line: `{"command":"stop"}`. EOF, SIGTERM, SIGINT also stop capture. Do not use `communicate()` and immediately close stdin if capture is intended to continue.

stdout exclusively UTF-8 NDJSON. Every event has `protocol:1`.

- `hello`: helper identity and minimum_macos.
- `devices`: applications [{pid,bundle_id,name}], microphones [{id,name,device_id}], screen_audio_authorized.
- `diagnostics`: permissions and OS capability.
- `started`: emitted only after startCapture succeeds. Contains host monotonic origin; PCM queued before success is emitted after started.
- `pcm`: source `local` or `remote`, per-source sequence, timestamp_ns relative to shared origin, sample_rate, channels, frames, encoding `f32le`, layout `interleaved`, base64 `data`.
- `error`: code, source, message. Sources include helper/control/capture/local/remote.
- `stopped`: reason command/stdin_eof/signal/application_ended/device_disconnected/stream_error/pcm_conversion_failed.

PCM preserves native rates (remote requests 48000Hz stereo; microphone device native). Python must independently downmix/resample each source to ASR rate, and map timestamps using timestamp_ns, not receipt order. Per-packet format is authoritative; do not assume fixed frames or same rate across tracks. Audio PTS uses the system host clock; invalid timestamps fail explicitly, not replaced by arrival time. No AEC here: speaker mode remains unverified; do not claim remote leakage is local speech with confidence. Local/remote labels identify source, not individual identity if more people join.

Protocol consumer must continuously drain stdout. Transport is synchronous and has no lossy application queue: a stalled consumer can backpressure capture. Parent should monitor packet freshness and helper process, and terminate helper if hung. Native helper observes application termination and microphone disappearance once per second; stream-level failures are also emitted. Permission revocation may appear as stream error. Silence does not itself indicate failure.

No pause command: parent stops helper and starts a fresh session on resume, retaining the previous session offset and explicit gap in Project timeline. Host origin changes on restart. CLI does not create a persistent Project ID; parent owns session identity and recording policy.

## Limits

Compile/protocol tests do not establish real mic/meeting capture, shared-clock accuracy on hardware, permission packaging, Bluetooth reconnect, AEC or extended-session performance. No real capture or permission request is run during development checks.
