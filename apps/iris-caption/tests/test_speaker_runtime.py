"""No microphone or real model dependencies for optional runtime scheduling."""
from array import array
from queue import Queue
from threading import Event
from types import SimpleNamespace

import numpy as np

from iris_caption.audio import AudioWindow, _recognize
from iris_caption.speaker_runtime import SpeakerRuntime


class Rolling:
    def __init__(self):
        self.calls = []

    def reset(self, session_id, start_sample=0):
        self.calls.append(("reset", start_sample))

    def acknowledge_cut(self, sample):
        self.calls.append(("cut", sample))

    def feed(self, audio, start_sample):
        self.calls.append(("feed", start_sample, len(audio)))
        return ()


class Diarizer:
    def __init__(self, gate=None, fail=False):
        self.gate, self.fail = gate, fail
        self.started = Event()
        self.calls = []

    def analyze_final(self, audio, **kwargs):
        self.started.set()
        if self.gate:
            assert self.gate.wait(3)
        if self.fail:
            raise ValueError("private model path")
        self.calls.append((len(audio), kwargs))
        return SimpleNamespace(label="A", reason="confirmed", overlap=())

    def reset(self):
        pass


def window(i=1, overlap=0):
    return AudioWindow("final", i, (i - 1) * 32000, i * 32000,
                       array("f", [0.] * 32000), 3, overlap_prefix_samples=overlap)


def test_disabled_and_invalid_models_fail_open(tmp_path):
    events = []
    assert SpeakerRuntime.create({}, events.append) is None
    assert events == []
    assert SpeakerRuntime.create({"speakers_enabled": True, "session_id": "s",
                                  "speaker_model_path": str(tmp_path)}, events.append) is None
    assert events[-1]["state"] == "unavailable"
    assert str(tmp_path) not in str(events)


def test_final_first_and_discard_not_enqueued():
    events = Queue()
    submitted = []
    def final(w):
        submitted.append((events.get_nowait()["type"], w))
    _recognize([window()], SimpleNamespace(transcribe=lambda _: "text"), events,
               {"session_id": "s"}, final)
    assert submitted[0][0] == "final"
    _recognize([window(2)], SimpleNamespace(transcribe=lambda _: ""), events,
               {"session_id": "s"}, final)
    assert len(submitted) == 1
    assert events.get_nowait()["type"] == "discard"


def test_owned_pcm_and_absolute_overlap():
    events = []
    diarizer = Diarizer()
    runtime = SpeakerRuntime(diarizer, Rolling(), "s", events.append)
    runtime.submit_final(window(overlap=6400))
    runtime.suspend()
    runtime.close()
    runtime._thread.join(1)
    assert diarizer.calls[0][0] == 25600
    assert diarizer.calls[0][1]["start_sample"] == 6400
    assert events[0]["audio_revision"] == 3
    assert events[0]["speaker_state"] == "confirmed"
    assert events[0]["start_ms"] == 400


def test_bounded_overload_timeout_and_no_late_assignment():
    gate = Event()
    events = []
    diarizer = Diarizer(gate)
    runtime = SpeakerRuntime(diarizer, Rolling(), "s", events.append, capacity=2)
    runtime.submit_final(window())
    assert diarizer.started.wait(1)
    runtime.submit_final(window(2))
    assert not runtime.submit_final(window(3))
    runtime.feed(np.zeros(32000), 0)
    assert len(runtime._pcm[0]) <= 16000
    runtime.suspend(timeout=0)
    assert [e["reason"] for e in events if e["type"] == "speaker"] == ["overloaded", "timeout", "timeout"]
    gate.set()
    runtime.close(0)
    runtime._thread.join(1)
    assert not any(e.get("speaker_state") == "confirmed" for e in events)


def test_failure_disables_only_optional_worker():
    events = []
    runtime = SpeakerRuntime(Diarizer(fail=True), Rolling(), "s", events.append)
    runtime.submit_final(window())
    runtime.suspend()
    runtime.close()
    runtime._thread.join(1)
    assert any(e.get("code") == "speaker_inference_failed" for e in events)
    assert any(e.get("reason") == "inference-failed" for e in events)
    assert "private model path" not in str(events)


def test_boundaries_ignore_old_cuts_and_pause():
    runtime = SpeakerRuntime(Diarizer(), Rolling(), "s", lambda _: None)
    event = SimpleNamespace(start_sample=100, detected_at_sample=200)
    runtime._boundaries.append((0, event))
    assert runtime.poll_boundary(200)
    runtime._boundaries.append((0, event))
    runtime.acknowledge_cut(300)
    assert not runtime.poll_boundary(400)
    runtime._boundaries.append((0, event))
    runtime.suspend(0)
    runtime.resume(400)
    assert not runtime.poll_boundary(500)
    runtime.close(0)
    runtime._thread.join(1)
