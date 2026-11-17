"""Qt-free dual-source local ASR. No capture starts on import or construction.

Callbacks run on background threads: GUI clients must marshal them to their UI.
PCM source identity is not biometric speaker identification. In speaker mode all
local attribution is uncertain; no overlapping speech is removed.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import queue
import subprocess
import sys
import threading
import time
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from .exceptions import InterviewError

MAX_LINE = 4 * 1024 * 1024


@dataclass(frozen=True)
class CaptureConfig:
    helper_path: str
    app_pid: int
    microphone_id: str
    model_path: str
    vad_model_path: str
    hotwords: str = ""
    speaker_mode: str = "headphones"
    recording_dir: str | None = None
    queue_capacity: int = 128
    startup_timeout: float = 15.0


@dataclass(frozen=True)
class PcmPacket:
    source: str
    sequence: int
    timestamp_ns: int
    sample_rate: int
    channels: int
    frames: int
    samples: array


def parse_helper_line(line: str | bytes) -> dict | PcmPacket:
    """Strict protocol-1 parser; native f32le interleaved PCM, <=1 second."""
    try:
        if len(line) > MAX_LINE:
            raise ValueError("line too large")
        event = json.loads(line)
        if not isinstance(event, dict) or event.get("protocol") != 1:
            raise ValueError("protocol")
        if event.get("event") != "pcm":
            if not isinstance(event.get("event"), str):
                raise ValueError("event")
            return event
        if event.get("source") not in ("local", "remote"):
            raise ValueError("source")
        if event.get("encoding") != "f32le" or event.get("layout") != "interleaved":
            raise ValueError("encoding")
        fields = [event.get(k) for k in ("sequence", "timestamp_ns", "sample_rate", "channels", "frames")]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               or int(v) != v for v in fields):
            raise ValueError("numeric fields")
        sequence, timestamp, rate, channels, frames = map(int, fields)
        if sequence < 0 or timestamp < 0 or not 8000 <= rate <= 192000:
            raise ValueError("clock or rate")
        if not 1 <= channels <= 8 or not 1 <= frames <= rate:
            raise ValueError("dimensions")
        raw = base64.b64decode(event["data"], validate=True)
        if len(raw) != frames * channels * 4:
            raise ValueError("PCM length")
        samples = array("f")
        samples.frombytes(raw)
        if sys.byteorder != "little":
            samples.byteswap()
        if any(not math.isfinite(x) for x in samples):
            raise ValueError("non-finite PCM")
        return PcmPacket(event["source"], sequence, timestamp, rate, channels, frames, samples)
    except (ValueError, TypeError, KeyError, OverflowError, binascii.Error) as exc:
        raise InterviewError("invalid_helper_packet") from exc


class _Recording:
    """Opt-in private directory; chunk map preserves gaps without fake silence."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.files = {}
        self.manifest = {"version": 1, "clock": "session_relative_ns", "chunks": [], "gaps": []}

    def write(self, packet, timestamp):
        key = (packet.source, packet.sample_rate, packet.channels)
        if key not in self.files:
            name = f"{packet.source}-{len(self.files)}.wav"
            stream = wave.open(str(self.directory / name), "wb")
            stream.setnchannels(packet.channels)
            stream.setsampwidth(2)
            stream.setframerate(packet.sample_rate)
            self.files[key] = (stream, name)
        stream, name = self.files[key]
        offset = stream.getnframes()
        pcm = array("h", (round(max(-1., min(1., x)) * 32767) for x in packet.samples))
        if sys.byteorder != "little":
            pcm.byteswap()
        stream.writeframes(pcm.tobytes())
        self.manifest["chunks"].append({"file": name, "file_frame": offset,
                                       "frames": packet.frames, "timestamp_ns": timestamp,
                                       "source": packet.source, "sample_rate": packet.sample_rate,
                                       "channels": packet.channels})

    def close(self):
        for stream, _ in self.files.values():
            stream.close()
        target = self.directory / "manifest.json"
        temporary = self.directory / "manifest.tmp"
        from filelock import FileLock
        with FileLock(str(self.directory / ".recording.lock")):
            temporary.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)


class TranscriptionRuntime:
    """One bounded PCM queue, two independent FSMN states, serialized shared ASR.

    pause/stop drain queued PCM and finalize windows. Pause closes the helper;
    resume launches a new helper epoch and maps its clock onto the session.
    Runtime instances are single-use. No Keychain, downloads, Qt or cloud calls.
    """
    def __init__(self, config: CaptureConfig, callback, *, recognizer_factory=None,
                 vad_factory=None, process_factory=None):
        if config.speaker_mode not in ("headphones", "speaker"):
            raise InterviewError("invalid_speaker_mode")
        if not 1 <= config.queue_capacity <= 750:
            raise InterviewError("invalid_queue_capacity")
        if not math.isfinite(config.startup_timeout) or config.startup_timeout <= 0:
            raise InterviewError("invalid_startup_timeout")
        self.config, self.callback = config, callback
        self.recognizer_factory, self.vad_factory = recognizer_factory, vad_factory
        self.process_factory = process_factory or subprocess.Popen
        self.queue = queue.Queue(config.queue_capacity)
        self.state = "idle"
        self.failure = None
        self.process = self.reader = self.worker = None
        self.recording = None
        self.tracks = {}
        self.epoch = 0
        self.epoch_offset = 0
        self.origin = 0
        self._callback_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._helper_lock = threading.RLock()
        self._cancel_loading = threading.Event()
        self._level_emitted_at = {}
        from .echo import ReferenceEchoCanceller
        self.echo = ReferenceEchoCanceller() if config.speaker_mode == "speaker" else None

    def _emit(self, kind, **fields):
        with self._callback_lock:
            self.callback({"type": kind, **fields})

    def _fail(self, code):
        if self.failure is None:
            self.failure = InterviewError(code)
            self.state = "failed"
            self._emit("error", code=code, recoverable=False)
            self._gap("both", "interrupted_" + code, time.monotonic_ns() - self.origin,
                      time.monotonic_ns() - self.origin)

    def start(self):
        with self._lifecycle_lock:
            if self.state != "idle":
                raise InterviewError("runtime_already_started")
            self.origin = time.monotonic_ns()
            self.state = "loading"
            self._emit("status", state="loading")
            self.worker = threading.Thread(target=self._run, name="interview-asr", daemon=True)
            self.worker.start()

    def _launch(self):
        self.state = "starting"
        self._startup_deadline = time.monotonic() + self.config.startup_timeout
        self._emit("status", state="starting")
        cfg = self.config
        self.epoch += 1
        self.epoch_offset = time.monotonic_ns() - self.origin
        self.process = self.process_factory(
            [cfg.helper_path, "--capture", "--app-pid", str(cfg.app_pid),
             "--microphone-id", cfg.microphone_id], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.reader = threading.Thread(target=self._read, args=(self.process,), daemon=True)
        self.reader.start()

    def _read(self, process):
        try:
            while True:
                line = process.stdout.readline(MAX_LINE + 1)
                if not line:
                    break
                self.submit_ndjson(line)
            code = process.wait(timeout=5)
            if self.state not in ("pausing", "stopping", "failed"):
                self._fail("helper_unexpected_exit" if code == 0 else "helper_failed")
        except Exception:
            self._fail("helper_read_failed")

    def submit_ndjson(self, line):
        """Accept a complete helper line; exposed for synthetic protocol testing."""
        if self.failure:
            raise self.failure
        try:
            packet = parse_helper_line(line)
            if isinstance(packet, PcmPacket):
                self._meter(packet)
            self.queue.put_nowait(("packet", packet, self.epoch, self.epoch_offset))
        except queue.Full as exc:
            self._fail("audio_backpressure")
            raise self.failure from exc
        except InterviewError:
            self._fail("invalid_helper_packet")
            raise

    def _meter(self, packet):
        """Reader-path meter: no inference/ASR queue dependency, 150ms per track.

        Consumer callbacks must return promptly (GUI uses a queued signal).
        Values describe native source PCM, before echo attenuation/downmix.
        """
        now = time.monotonic_ns()
        previous = self._level_emitted_at.get(packet.source)
        if previous is not None and now - previous < 150_000_000:
            return
        self._level_emitted_at[packet.source] = now
        count = len(packet.samples)
        rms = math.sqrt(sum(float(x)*float(x) for x in packet.samples) / count)
        peak = max(abs(float(x)) for x in packet.samples)
        self._emit("level", track=packet.source, rms=min(1., rms), peak=min(1., peak),
                   timestamp_ns=self.epoch_offset + packet.timestamp_ns)

    def _halt_helper(self, timeout):
        with self._helper_lock:
            self._halt_helper_locked(timeout)

    def _halt_helper_locked(self, timeout):
        process = self.process
        if process is None:
            return
        try:
            if process.poll() is None:
                process.stdin.write(b'{"command":"stop"}\n')
                process.stdin.flush()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=timeout)
                    self._fail("helper_stop_timeout")
            if self.reader:
                self.reader.join(timeout)
                if self.reader.is_alive():
                    raise InterviewError("helper_reader_timeout")
        finally:
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
            self.process = None

    def _command(self, name, timeout):
        done = threading.Event()
        try:
            self.queue.put((name, done), timeout=timeout)
        except queue.Full as exc:
            raise InterviewError("control_backpressure") from exc
        if not done.wait(timeout):
            raise InterviewError("recognition_timeout")

    def pause(self, timeout=15):
        with self._lifecycle_lock:
            if self.state != "recording":
                raise InterviewError("not_recording")
            self.state = "pausing"
            self._halt_helper(timeout)
            self._command("pause", timeout)

    def resume(self, timeout=15):
        with self._lifecycle_lock:
            if self.state != "paused":
                raise InterviewError("not_paused")
            self._command("resume", timeout)

    def stop(self, timeout=15):
        with self._lifecycle_lock:
            if self.state in ("idle", "stopped"):
                return
            if self.worker and not self.worker.is_alive():
                self.state = "failed" if self.failure else "stopped"
                return
            if self.state == "loading":
                self._cancel_loading.set()
                self.worker.join(timeout)
                if self.worker.is_alive():
                    raise InterviewError("model_loading_timeout")
                return
            self.state = "stopping"
            self._halt_helper(timeout)
            self._emit("status", state="processing")
            if self.worker and self.worker.is_alive():
                self._command("stop", timeout)
                self.worker.join(timeout)
                if self.worker.is_alive():
                    raise InterviewError("recognition_timeout")

    @staticmethod
    def _prepare_resampling():
        """Initialize lazy FIR dependencies while loading, before capture starts.

        Native remote audio normally arrives at 48 kHz. Importing SciPy and
        initializing its filter state on that first packet can consume the stop
        deadline after the UI has already advertised recording. Exercise the
        real downsampling path on private silence; never enqueue or record it.
        """
        from iris_caption.audio import StreamingResampler
        resampler = StreamingResampler(48000)
        resampler.feed(array('f', [0.]) * 1920)
        resampler.flush()

    def _run(self):
        try:
            from iris_caption.audio import ContextualOnnxAdapter
            from iris_caption.streaming_vad import StreamingFsmnVad
            cfg = self.config
            self.adapter = (self.recognizer_factory() if self.recognizer_factory else
                            ContextualOnnxAdapter(cfg.model_path, cfg.hotwords, cfg.vad_model_path))
            self.make_vad = self.vad_factory or (lambda: StreamingFsmnVad(cfg.vad_model_path))
            # Validate both independent FSMN states before any native capture.
            self._prepared_vads = [self.make_vad(), self.make_vad()]
            if not self._cancel_loading.is_set():
                self._prepare_resampling()
            if self._cancel_loading.is_set():
                self.state = "stopped"
                self._emit("status", state="stopped")
                return
            if cfg.recording_dir:
                self.recording = _Recording(cfg.recording_dir)
            self._launch()
            if self.failure:
                raise self.failure
            while True:
                if self.state == "starting" and time.monotonic() > self._startup_deadline:
                    self._fail("helper_start_timeout")
                if self.failure:
                    break
                try:
                    item = self.queue.get(timeout=.1)
                except queue.Empty:
                    continue
                if item[0] == "packet":
                    self._consume(*item[1:])
                else:
                    name, done = item
                    try:
                        if name in ("pause", "stop"):
                            self._flush(name)
                            self.tracks.clear()
                            self.state = "paused" if name == "pause" else "stopped"
                            self._emit("status", state=self.state)
                        elif name == "resume":
                            now = time.monotonic_ns() - self.origin
                            self._gap("both", "pause_interval", self._paused_at, now)
                            self._launch()
                        if name == "stop":
                            return
                    finally:
                        done.set()
        except Exception:
            self._fail("transcription_failed")
        finally:
            try:
                self._halt_helper(2)
            except Exception:
                self._fail("helper_cleanup_failed")
            if self.recording:
                try:
                    self.recording.close()
                except Exception:
                    self._fail("recording_save_failed")
            if self.state != "stopped":
                self.state = "failed" if self.failure else "stopped"
                self._emit("status", state=self.state)
            self._emit("finalized", state=self.state,
                       recording_dir=str(self.recording.directory) if self.recording else None)

    def _gap(self, source, reason, start, end):
        if self.echo:
            self.echo.reset()
        event = dict(track=source, reason=reason, start_ns=start, end_ns=end)
        self._emit("gap", **event)
        if self.recording:
            self.recording.manifest["gaps"].append(event)

    def _consume(self, packet, epoch, offset):
        if isinstance(packet, dict):
            event = packet["event"]
            if event == "started":
                if self.state == "starting":
                    self.state = "recording"
                    self._emit("status", state="recording")
            elif event == "error":
                self._fail("native_" + str(packet.get("code", "error")))
            elif event == "gap":
                self._gap(packet.get("source", "unknown"), packet.get("reason", "native_gap"),
                          offset + packet.get("timestamp_ns", 0), offset + packet.get("timestamp_ns", 0))
            else:
                self._emit("helper", event=event)
            return
        from iris_caption.audio import StreamingResampler
        from iris_caption.segmentation import Segmenter
        timestamp = offset + packet.timestamp_ns
        track = self.tracks.get(packet.source)
        discontinuity = None
        if track:
            delta = timestamp - track["end"]
            if packet.sequence != track["sequence"] + 1:
                discontinuity = "sequence_gap"
            elif abs(delta) > 5_000_000:
                discontinuity = "timestamp_gap" if delta > 0 else "timestamp_overlap"
            elif packet.sample_rate != track["rate"]:
                discontinuity = "format_changed"
        if discontinuity:
            self._gap(packet.source, discontinuity, track["end"], timestamp)
            self._finish_track(packet.source, track, "gap")
            track = None
        if track is None:
            vad = self._prepared_vads.pop() if self._prepared_vads else self.make_vad()
            track = {"segmenter": Segmenter(vad),
                     "resampler": StreamingResampler(packet.sample_rate), "origin": timestamp,
                     "rate": packet.sample_rate, "epoch": epoch}
            self.tracks[packet.source] = track
        if self.recording:
            self.recording.write(packet, timestamp)
        mono = array("f", (sum(packet.samples[i:i+packet.channels]) / packet.channels
                           for i in range(0, len(packet.samples), packet.channels)))
        produced = track["resampler"].produced
        self._feed_resampled(packet.source, track, track["resampler"].feed(mono), produced)
        track["sequence"] = packet.sequence
        track["end"] = timestamp + round(packet.frames * 1e9 / packet.sample_rate)

    def _feed_resampled(self, source, track, samples, produced):
        if self.echo and len(samples):
            start_ns = track["origin"] + round(produced * 1e9 / 16000)
            if source == "remote":
                self.echo.feed_remote(samples, start_ns)
            else:
                result = self.echo.process_local(samples, start_ns)
                samples = result.samples
                self._emit("echo", track=source, state=result.state,
                           start_ns=start_ns, confidence=result.confidence,
                           delay_samples=result.delay_samples, speaker_state="uncertain")
        self._recognize(source, track, track["segmenter"].feed(samples))

    def _recognize(self, source, track, windows):
        for window in windows:
            text = self.adapter.transcribe(window.samples)
            # Forced cuts: decode owned audio separately, without deleting words
            # via global text matching or comparing to the other speaker.
            if window.kind == "final" and window.overlap_prefix_samples:
                text = self.adapter.transcribe(window.samples[window.overlap_prefix_samples:])
            start = track["origin"] + round((window.start_sample + window.overlap_prefix_samples) * 1e9/16000)
            end = track["origin"] + round(window.end_sample * 1e9/16000)
            uncertain = self.config.speaker_mode == "speaker"
            self._emit(window.kind if text else "discard", track=source,
                       speaker="interviewer" if source == "local" else "candidate",
                       speaker_state="uncertain" if uncertain else "source",
                       reason=window.reason, text=text, revision=window.revision,
                       segment_id=f'{source}:{track["epoch"]}:{track["origin"]}:{window.segment_id}',
                       start_ns=start, end_ns=end, start_ms=start//1_000_000, end_ms=end//1_000_000,
                       boundary_uncertain=bool(window.overlap_prefix_samples))

    def _finish_track(self, source, track, reason):
        produced = track["resampler"].produced
        self._feed_resampled(source, track, track["resampler"].flush(), produced)
        self._recognize(source, track, track["segmenter"].flush(reason))
        if reason == "gap" and self.echo:
            self.echo.reset()

    def _flush(self, reason):
        for source, track in self.tracks.items():
            self._finish_track(source, track, reason)
        if reason == "pause":
            now = time.monotonic_ns() - self.origin
            self._paused_at = now
            self._gap("both", "pause", now, now)
