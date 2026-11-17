"""Synthetic PCM only: never open audio devices, models, network or Keychain."""
import base64
import json
import threading
import time
import wave
from array import array

import pytest

from iris_interview.exceptions import InterviewError
from iris_interview.transcription import CaptureConfig, TranscriptionRuntime, parse_helper_line


def pcm(source="local", sequence=0, timestamp=0, rate=16000, channels=1, value=.2, frames=640,
        samples=None):
    values = array("f", [value] * frames * channels if samples is None else samples)
    return json.dumps(dict(protocol=1, event="pcm", source=source, sequence=sequence,
                           timestamp_ns=timestamp, sample_rate=rate, channels=channels,
                           frames=frames, encoding="f32le", layout="interleaved",
                           data=base64.b64encode(values.tobytes()).decode()))


class Vad:
    quiet = False

    def __init__(self):
        self.reset()

    def reset(self):
        self.position = 0
        self.started = False

    def feed(self, samples, final=False):
        previous = self.position
        self.position += len(samples)
        if final:
            return [(-1, self.position)] if self.started else []
        if not self.started and samples:
            self.started = True
            return [(previous, -1)]
        return []


class Recognizer:
    def transcribe(self, samples):
        return "synthetic speech" if len(samples) else ""


class Pipe:
    def __init__(self):
        self.closed = False

    def write(self, value):
        pass

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.stdin = Pipe()
        self.stdout = self
        self.done = threading.Event()
        self.sent_started = False

    def readline(self, limit):
        if not self.sent_started:
            self.sent_started = True
            return b'{"protocol":1,"event":"started"}\n'
        self.done.wait(5)
        return b""

    def poll(self):
        return 0 if self.done.is_set() else None

    def wait(self, timeout):
        self.done.set()
        return 0

    def kill(self):
        self.done.set()

    def close(self):
        pass


def wait_for(predicate):
    until = time.monotonic() + 3
    while not predicate():
        if time.monotonic() > until:
            raise AssertionError("timeout")
        threading.Event().wait(.005)


def runtime(events, **kwargs):
    config = CaptureConfig("fake-helper", 10, "mic-uid", "fake-model", "fake-vad", **kwargs)
    return TranscriptionRuntime(config, events.append, recognizer_factory=Recognizer,
                                vad_factory=Vad, process_factory=FakeProcess)


def test_parser_native_stereo_and_rejects_malformed():
    p = parse_helper_line(pcm(channels=2, rate=48000))
    assert p.channels == 2 and len(p.samples) == 1280
    for change in ({"data": "bad!"}, {"frames": 100}, {"sample_rate": 0},
                   {"source": "unknown"}, {"timestamp_ns": -1}, {"protocol": 2},
                   {"layout": "planar"}, {"channels": True}):
        event = json.loads(pcm())
        event.update(change)
        with pytest.raises(InterviewError):
            parse_helper_line(json.dumps(event))
    with pytest.raises(InterviewError):
        parse_helper_line(pcm(value=float("nan")))


def test_two_tracks_flush_and_no_default_recording(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    events = []
    r = runtime(events)
    r.start()
    wait_for(lambda: r.state == "recording")
    r.submit_ndjson(pcm("local", timestamp=100_000_000))
    r.submit_ndjson(pcm("remote", timestamp=110_000_000, rate=48000, channels=2, frames=1920))
    r.stop()
    finals = [e for e in events if e["type"] == "final"]
    assert {e["track"] for e in finals} == {"local", "remote"}
    assert len({e["segment_id"] for e in finals}) == 2
    assert all(e["end_ns"] > e["start_ns"] for e in finals)
    assert all(e["speaker_state"] == "source" for e in finals)
    assert list(tmp_path.iterdir()) == []
    assert [e.get("state") for e in events if e["type"] == "status"][-2:] == ["processing", "stopped"]


def test_pause_resume_keeps_wall_clock_and_reports_gap():
    events = []
    r = runtime(events)
    r.start()
    wait_for(lambda: r.state == "recording")
    r.submit_ndjson(pcm())
    r.pause()
    assert r.state == "paused"
    previous = max(e["end_ns"] for e in events if e["type"] == "final")
    r.resume()
    r.submit_ndjson(pcm(timestamp=100_000_000))
    r.stop()
    finals = [e for e in events if e["type"] == "final"]
    assert finals[-1]["start_ns"] > previous
    assert any(e["type"] == "gap" and e["reason"] == "pause" for e in events)


def test_sequence_gap_is_not_silent_and_speaker_mode_uncertain():
    events = []
    r = runtime(events, speaker_mode="speaker")
    r.start()
    wait_for(lambda: r.state == "recording")
    for source in ("local", "remote"):
        r.submit_ndjson(pcm(source))
        r.submit_ndjson(pcm(source, sequence=2, timestamp=80_000_000))
    r.stop()
    assert sum(e["type"] == "gap" for e in events) == 2
    assert {e["track"] for e in events if e["type"] == "final"} == {"local", "remote"}
    assert all(e["speaker_state"] == "uncertain" for e in events if e["type"] == "final")


def test_opt_in_wav_and_manifest_timeline(tmp_path):
    directory = tmp_path / "recording"
    events = []
    r = runtime(events, recording_dir=str(directory))
    r.start()
    wait_for(lambda: r.state == "recording")
    r.submit_ndjson(pcm("local"))
    r.submit_ndjson(pcm("remote", timestamp=10_000_000))
    r.stop()
    manifest = json.loads((directory / "manifest.json").read_text())
    assert len(manifest["chunks"]) == 2
    for chunk in manifest["chunks"]:
        with wave.open(str(directory / chunk["file"]), "rb") as stream:
            assert stream.getnframes() == 640
            assert stream.getframerate() == 16000
    assert manifest["chunks"][1]["timestamp_ns"] - manifest["chunks"][0]["timestamp_ns"] == 10_000_000


def test_meter_runs_before_asr_queue_and_is_per_track_throttled(monkeypatch):
    import iris_interview.transcription as module
    clock = [1_000_000_000]
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: clock[0])
    events = []
    r = runtime(events)
    r.epoch_offset = 50_000_000
    # Worker is deliberately not started: PCM remains queued and never inferred.
    r.submit_ndjson(pcm("local", timestamp=10_000_000, value=.5))
    r.submit_ndjson(pcm("local", sequence=1))
    r.submit_ndjson(pcm("remote", value=2.))
    levels = [e for e in events if e["type"] == "level"]
    assert len(levels) == 2
    assert levels[0]["rms"] == .5 and levels[0]["peak"] == .5
    assert levels[0]["timestamp_ns"] == 60_000_000
    assert levels[1]["rms"] == 1. and levels[1]["peak"] == 1.
    clock[0] += 150_000_000
    r.submit_ndjson(pcm("local", sequence=2, value=0.))
    levels = [e for e in events if e["type"] == "level"]
    assert len(levels) == 3 and levels[-1]["rms"] == 0.


def test_bounded_queue_explicit_failure():
    events = []
    r = runtime(events, queue_capacity=1)
    r.submit_ndjson(pcm())
    with pytest.raises(InterviewError, match="audio_backpressure"):
        r.submit_ndjson(pcm())
    assert any(e.get("code") == "audio_backpressure" for e in events)
    assert any(e["type"] == "gap" for e in events)


def test_speaker_echo_is_processed_after_resampling_and_recording_stays_raw(tmp_path):
    import numpy as np
    events, recognized = [], []
    class CaptureRecognizer:
        def transcribe(self, samples):
            recognized.append(np.asarray(samples).copy())
            return "synthetic"
    remote = np.random.default_rng(731).normal(0, .1, 3200).astype(np.float32)
    local = .6 * remote[720:2320]
    r = runtime(events, speaker_mode="speaker", recording_dir=str(tmp_path / "record"))
    r.recognizer_factory = CaptureRecognizer
    r.start()
    wait_for(lambda: r.state == "recording")
    r.submit_ndjson(pcm("remote", frames=3200, samples=remote))
    r.submit_ndjson(pcm("local", timestamp=1440*62500, frames=1600, samples=local))
    r.stop()
    diagnostic = [e for e in events if e["type"] == "echo"]
    assert any(e["state"] == "attenuated_uncertain" and e["delay_samples"] == 720 for e in diagnostic)
    assert any(np.mean(s*s) < np.mean(local*local)*.001 for s in recognized if len(s))
    manifest = json.loads((tmp_path / "record" / "manifest.json").read_text())
    chunk = next(c for c in manifest["chunks"] if c["source"] == "local")
    with wave.open(str(tmp_path / "record" / chunk["file"]), "rb") as stream:
        recorded = np.frombuffer(stream.readframes(1600), dtype="<i2") / 32767
    np.testing.assert_allclose(recorded, local, atol=1/32767)
    assert all(e["speaker_state"] == "uncertain" for e in events if e["type"] == "final")


def test_vad_failure_does_not_start_helper():
    events = []
    r = runtime(events)
    def fail():
        raise RuntimeError("bad detector")
    r.vad_factory = fail
    r.start()
    wait_for(lambda: r.failure is not None)
    assert r.process is None


def test_native_error_and_invalid_line_are_visible():
    events = []
    r = runtime(events)
    r.start()
    wait_for(lambda: r.state == "recording")
    r.submit_ndjson(json.dumps({"protocol": 1, "event": "error", "code": "device_disconnected"}))
    wait_for(lambda: r.failure is not None)
    r.worker.join(3)
    assert any(e.get("code") == "native_device_disconnected" for e in events)
    assert any(e["type"] == "gap" for e in events)
    r = runtime([])
    with pytest.raises(InterviewError, match="invalid_helper_packet"):
        r.submit_ndjson("malformed")


def test_stop_during_model_loading_never_starts_capture():
    entered, release = threading.Event(), threading.Event()
    events = []
    r = runtime(events)
    def load():
        entered.set()
        release.wait(2)
        return Recognizer()
    r.recognizer_factory = load
    r.start()
    assert entered.wait(2)
    stopper = threading.Thread(target=r.stop)
    stopper.start()
    wait_for(r._cancel_loading.is_set)
    release.set()
    stopper.join(3)
    assert r.state == "stopped"
    assert r.process is None


def test_requires_native_started_and_times_out():
    class NoStarted(FakeProcess):
        def readline(self, limit):
            self.done.wait(5)
            return b""
    events = []
    r = runtime(events, startup_timeout=.05)
    r.process_factory = NoStarted
    r.start()
    wait_for(lambda: any(e["type"] == "finalized" for e in events))
    assert not any(e.get("state") == "recording" for e in events)
    assert any(e.get("code") == "helper_start_timeout" for e in events)
    assert r.state == "failed"


def test_stop_before_native_started():
    class NoStarted(FakeProcess):
        def readline(self, limit):
            self.done.wait(5)
            return b""
    events = []
    r = runtime(events)
    r.process_factory = NoStarted
    r.start()
    wait_for(lambda: r.process is not None and r.reader is not None and r.reader.is_alive())
    r.stop()
    assert events[-1]["type"] == "finalized"
    assert events[-1]["state"] == "stopped"
    assert not any(e.get("state") == "recording" for e in events)


def test_slow_resampling_preparation_stays_loading_and_stop_cancels_capture(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    events = []
    r = runtime(events)
    errors = []

    def prepare():
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(r, '_prepare_resampling', prepare)
    r.start()
    try:
        assert entered.wait(2)
        assert r.state == 'loading'
        assert r.process is None
        assert not any(e.get('state') == 'recording' for e in events)

        def stop():
            try:
                r.stop()
            except Exception as error:
                errors.append(error)

        stopper = threading.Thread(target=stop)
        stopper.start()
        wait_for(r._cancel_loading.is_set)
        release.set()
        stopper.join(3)
        assert not stopper.is_alive()
        assert not errors
        assert r.state == 'stopped' and r.process is None
    finally:
        release.set()
        r.worker.join(3)


def test_resampling_preparation_failure_is_visible_without_capture(monkeypatch):
    events = []
    r = runtime(events)

    def fail():
        raise ImportError('private dependency path')

    monkeypatch.setattr(r, '_prepare_resampling', fail)
    r.start()
    r.worker.join(3)
    assert not r.worker.is_alive()
    assert r.state == 'failed' and r.process is None
    assert any(e.get('code') == 'transcription_failed' for e in events)
    assert not any(e.get('state') in {'starting', 'recording'} for e in events)
    assert 'private dependency path' not in repr(events)


def test_model_error_does_not_start_helper():
    events = []
    r = runtime(events)
    def fail():
        raise ValueError("secret model path must not leak")
    r.recognizer_factory = fail
    r.start()
    wait_for(lambda: r.failure is not None)
    assert r.process is None
    assert events[-1]["type"] == "finalized" and events[-1]["state"] == "failed"
    assert "secret" not in repr(events)
