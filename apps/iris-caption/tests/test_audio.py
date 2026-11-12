"""Pure and simulated audio tests: no hardware recording or model download."""
import multiprocessing
import queue
from array import array

import pytest

from iris_caption.audio import (
    AudioWindow, CaptureBuffer, ContextualOnnxAdapter, FsmnSpeechVerifier,
    Segmenter, StreamingResampler, _capture_loop, _emit, _recognize, run_audio_worker, validate_model,
)


def samples(seconds, level=.1):
    return array("f", [level]) * round(seconds * 16000)


class FixtureVad:
    """Deterministic learned-endpoint protocol fixture, not production gating."""
    quiet = False
    def __init__(self):
        self.reset()
    def reset(self):
        self.position = 0
        self.active = False
    def feed(self, audio, final=False):
        start = self.position
        self.position += len(audio)
        self.quiet = not any(audio)
        if not self.active and not self.quiet:
            self.active = True
            return [(start, -1)]
        return []


def segmenter(**kwargs):
    return Segmenter(FixtureVad(), **kwargs)


def test_pause_flushes_subframe_and_resume_excludes_paused_samples():
    seg = segmenter()
    seg.feed(samples(.013))
    final, = seg.pause()
    assert final.end_sample == 208
    assert final.reason == "pause"
    assert seg.feed(samples(10)) == []
    assert seg.position == 208
    assert seg.pause() == []
    seg.resume()
    seg.feed(samples(.1))
    final, = seg.flush()
    assert (final.start_sample, final.end_sample, final.segment_id) == (208, 1808, 2)


def test_long_segments_are_bounded_and_overlap():
    seg = segmenter(max_segment_s=1, hard_segment_s=2)
    out = seg.feed(samples(8)) + seg.flush()
    finals = [w for w in out if w.kind == "final"]
    assert all(len(w.samples) <= 32000 for w in out)
    assert all(a.end_sample - b.start_sample == 6400 for a,b in zip(finals, finals[1:]))
    assert sum(len(w.samples) - w.overlap_prefix_samples for w in finals) == 128000
    assert seg.buffered_samples == 0


def test_partials_share_id_and_revision_increases():
    seg = segmenter(partial_s=.2)
    out = seg.feed(samples(.6)) + seg.flush()
    assert [e.kind for e in out] == ["partial", "partial", "partial", "final"]
    assert [e.revision for e in out] == [1, 2, 3, 4]
    assert {e.segment_id for e in out} == {1}


@pytest.mark.parametrize("kwargs", [{"pause_s": 0}, {"max_segment_s": 31},
                                     {"partial_s": float("nan")}])
def test_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        segmenter(**kwargs)


def test_capture_backpressure_detected():
    class FakeAudio:
        def __getitem__(self, key):
            return self

        def copy(self):
            return samples(.04)

    capture = CaptureBuffer(1)
    capture.callback(FakeAudio(), 640, None, False)
    capture.callback(FakeAudio(), 640, None, False)
    assert capture.failed.is_set()
    assert capture.reason == "audio_backpressure"
    assert capture.queue.qsize() == 1


def test_output_backpressure_detected():
    events = queue.Queue(1)
    events.put({})
    with pytest.raises(RuntimeError, match="event_backpressure"):
        _emit(events, {})


@pytest.mark.parametrize("rate", [16000, 44100, 48000])
def test_resampler_block_boundaries_and_clock(rate):
    data = array("f", (i / rate for i in range(rate)))
    resampler = StreamingResampler(rate)
    out = array("f")
    for i in range(0, rate, 137):
        out.extend(resampler.feed(data[i:i + 137]))
    out.extend(resampler.flush())
    assert len(out) == 16000
    delay = 63 / rate if rate > 16000 else 0
    assert out[8000] == pytest.approx(.5 - delay, abs=1e-6)
    whole = StreamingResampler(rate)
    assert out == whole.feed(data) + whole.flush()
    segmenter = Segmenter(FixtureVad())
    segmenter.feed(out)
    final = segmenter.flush()[-1]
    assert final.end_sample == 16000


def test_resampler_pause_does_not_interpolate_across_gap():
    resampler = StreamingResampler(44100)
    before = resampler.feed([1.] * 17) + resampler.flush()
    after = resampler.feed([-1.] * 17) + resampler.flush()
    assert all(x == 1 for x in before)
    assert all(x == -1 for x in after)
    assert len(before) + len(after) == 13


def test_antialias_rejects_high_frequency_noise():
    import numpy as np
    rate = 48000
    time = np.arange(rate) / rate
    def rms(frequency):
        resampler = StreamingResampler(rate)
        out = resampler.feed(np.sin(2 * np.pi * frequency * time))
        return np.sqrt(np.mean(np.asarray(out)[1000:] ** 2))
    assert rms(1000) > .6
    assert rms(12000) < .01


def test_fsmn_rejection_skips_asr():
    from iris_caption.audio import ContextualOnnxAdapter
    class Reject:
        def has_speech(self, samples):
            return False
    adapter = ContextualOnnxAdapter.__new__(ContextualOnnxAdapter)
    adapter.vad = Reject()
    # No frontend or acoustic session exists, proving rejected windows skip ASR.
    assert adapter.transcribe(samples(.2)) == ""


def test_missing_vad_fails_before_model_or_capture():
    events = queue.Queue()
    run_audio_worker({"session_id": "s", "model_path": "/not-a-model"}, events, queue.Queue())
    assert events.get()["code"] == "vad_required"
    assert events.get()["type"] == "stopped"
    assert events.empty()
    with pytest.raises(ValueError, match="vad_required"):
        ContextualOnnxAdapter("/not-a-model")


@pytest.mark.parametrize("result, expected", [([[]], False), ([[[0, 100]]], True),
                                            ([[[20, 20]]], False)])
def test_fsmn_offline_intervals_and_fresh_cache(result, expected):
    verifier = FsmnSpeechVerifier.__new__(FsmnSpeechVerifier)
    caches = []
    def model(waveform, param_dict):
        assert param_dict["in_cache"] == []
        caches.append(param_dict["in_cache"])
        param_dict["in_cache"].append("state")
        return result
    verifier.model = model
    assert verifier.has_speech(samples(.2)) is expected
    assert verifier.has_speech(samples(.2)) is expected
    assert caches[0] is not caches[1]


@pytest.mark.parametrize("result", ["", [], [[[0, -1]]], [[[-1, 100]]],
                                    [[[0, 9999]]], [[[0, float("nan")]]],
                                    [[["0", 100]]], [[[1]]], [[], []]])
def test_fsmn_invalid_or_online_output_fails_closed(result):
    verifier = FsmnSpeechVerifier.__new__(FsmnSpeechVerifier)
    verifier.model = lambda *args, **kwargs: result
    with pytest.raises(RuntimeError, match="fsmn_inference_failed"):
        verifier.has_speech(samples(.2))


@pytest.mark.parametrize("audio", [[float("nan")], [[0.]], samples(30.01)])
def test_fsmn_input_bounded_and_finite(audio):
    verifier = FsmnSpeechVerifier.__new__(FsmnSpeechVerifier)
    with pytest.raises(ValueError, match="finite mono"):
        verifier.has_speech(audio)


@pytest.mark.parametrize("reason", ["silence", "limit", "pause", "stop"])
def test_noise_skips_both_decodes_and_closes_final(reason):
    class Reject:
        def __init__(self):
            self.calls = 0
        def has_speech(self, audio):
            self.calls += 1
            return False
    adapter = ContextualOnnxAdapter.__new__(ContextualOnnxAdapter)
    adapter.vad = Reject()
    events = queue.Queue()
    windows = [AudioWindow("partial", 7, 16000, 32000, samples(1), 1),
               AudioWindow("final", 7, 16000, 48000, samples(2), 2, reason)]
    _recognize(windows, adapter, events, {"session_id": "s"})
    assert adapter.vad.calls == 2  # No acoustic model exists: neither kind decoded.
    event = events.get_nowait()
    assert (event["type"], event["segment_id"], event["revision"]) == ("discard", "7", 2)
    assert (event["start_ms"], event["end_ms"], event["reason"]) == (1000, 3000, reason)
    assert events.empty()


def test_repetition_preserved_and_rejected_final_closes_partial():
    class Adapter:
        results = iter(["这个这这", ""])
        def transcribe(self, audio):
            return next(self.results)
    events = queue.Queue()
    windows = [AudioWindow("partial", 1, 0, 16000, samples(1), 1),
               AudioWindow("final", 1, 0, 32000, samples(2), 2, "stop")]
    _recognize(windows, Adapter(), events, {})
    partial, final = events.get(), events.get()
    assert partial["text"] == "这个这这"
    assert final["type"] == "discard"
    assert final["segment_id"] == partial["segment_id"]


def test_missing_model_has_no_download(tmp_path):
    with pytest.raises(ValueError, match="complete local"):
        validate_model(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_spawn_safe_failure_protocol():
    ctx = multiprocessing.get_context("spawn")
    events, commands = ctx.Queue(8), ctx.Queue(8)
    process = ctx.Process(target=run_audio_worker, args=({"session_id": "test"}, events, commands))
    try:
        process.start()
        error = events.get(timeout=10)
        stopped = events.get(timeout=10)
        process.join(timeout=10)
        assert not process.is_alive()
        assert error["type"] == "error"
        assert stopped["type"] == "stopped"
        assert error["session_id"] == "test"
        assert {"segment_id", "start_ms", "end_ms", "text"} <= error.keys()
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
        events.close()
        commands.close()


def test_simulated_stream_stop_flushes_and_closes():
    class FakeAudio:
        def __getitem__(self, key):
            return self

        def copy(self):
            return samples(.04)

    class Stream:
        active = True
        closed = False

        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            self.callback(FakeAudio(), 640, None, False)
            return self

        def __exit__(self, *args):
            Stream.closed = True

        def stop(self):
            self.active = False

    class SoundDevice:
        InputStream = Stream

        @staticmethod
        def query_devices(device, kind):
            return {"default_samplerate": 16000}

    class Adapter:
        def transcribe(self, audio):
            return "测试"

    events, commands = queue.Queue(), queue.Queue()
    commands.put({"type": "stop"})
    _capture_loop({"session_id": "s"}, events, commands, Adapter(), Segmenter(FixtureVad()), SoundDevice())
    assert events.get()["state"] == "recording"
    final = events.get()
    assert (final["type"], final["start_ms"], final["end_ms"], final["reason"]) == ("final", 0, 40, "stop")
    assert Stream.closed
