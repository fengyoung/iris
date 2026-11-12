"""Capture speaker hooks use actual PCM clocks without touching a microphone."""
from array import array
from types import SimpleNamespace

from iris_caption.audio import AudioWindow, _feed_capture


def test_capture_polls_before_feed_and_acknowledges_actual_final():
    calls = []
    final = AudioWindow("final", 1, 0, 1920, array("f", [0.] * 1920), 2)
    segmenter = SimpleNamespace(position=1280, pending=array("f", [0.] * 10),
                                start=0, requested_boundary=None)
    segmenter.request_boundary = lambda pos: calls.append(("request", pos))
    segmenter.feed = lambda pcm: [final]
    speakers = SimpleNamespace(
        poll_boundary=lambda pos: True,
        feed=lambda pcm, start: calls.append(("pcm", start)),
        acknowledge_cut=lambda end: calls.append(("ack", end)))
    scheduler = SimpleNamespace(submit=lambda windows: calls.append(("submit", windows)))
    _feed_capture(segmenter, scheduler, array("f", [0.] * 640), speakers)
    assert calls[:3] == [("request", 1280), ("pcm", 1290), ("ack", 1920)]
    assert calls[3] == ("submit", [final])
    assert segmenter.requested_boundary is None


def test_default_capture_does_not_require_speaker_hooks():
    submitted = []
    segmenter = SimpleNamespace(feed=lambda samples: [samples])
    _feed_capture(segmenter, SimpleNamespace(submit=submitted.extend), [0.])
    assert submitted == [[0.]]


def test_missing_optional_model_still_terminalizes_each_final(monkeypatch):
    from queue import Queue
    from iris_caption import audio, speaker_runtime
    monkeypatch.setattr(speaker_runtime, "create_speaker_runtime", lambda config, emit: None)
    final = AudioWindow("final", 1, 0, 32000, array("f", [0.] * 32000), 2)
    def capture(config, events, commands, segmenter, sd, scheduler, speakers):
        assert speakers is None
        scheduler.submit([final])
        scheduler.drain()
    monkeypatch.setattr(audio, "_capture_scheduled", capture)
    events = Queue()
    audio._capture_loop({"session_id": "s", "speakers_enabled": True}, events, None,
                        SimpleNamespace(transcribe=lambda samples: "字幕"), None, None)
    assert events.get_nowait()["type"] == "final"
    event = events.get_nowait()
    assert event["type"] == "speaker"
    assert event["speaker_state"] == "unknown"
    assert event["audio_revision"] == 2
