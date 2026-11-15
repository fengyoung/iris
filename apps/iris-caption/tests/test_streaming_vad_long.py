"""Long streaming regressions use public audio only; never record/download."""
from pathlib import Path

import pytest

from iris_caption.streaming_vad import StreamingFsmnVad, _FrameHistory


@pytest.fixture
def local_model():
    model = Path.home() / '.cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-onnx'
    if not (model / 'model_quant.onnx').exists():
        pytest.skip('optional local FSMN model not installed; never download')
    pytest.importorskip('funasr_onnx')
    return model


def test_frame_history_uses_absolute_indices():
    history = _FrameHistory(3)
    for value in range(10):
        history.append(value)
    assert history[7] == 7 and history[9] == 9
    with pytest.raises(IndexError, match='expired'):
        history[6]


@pytest.mark.parametrize('chunk_size', [320, 640, 1600])
def test_real_six_minute_voice_noise_silence(local_model, chunk_size):
    np = pytest.importorskip('numpy')
    sf = pytest.importorskip('soundfile')
    wav = Path(__file__).resolve().parents[3] / 'output/iris-caption-benchmark/asr_example_zh.wav'
    if not wav.exists():
        pytest.skip('optional public WAV not installed; never download')
    voice, rate = sf.read(wav, dtype='float32')
    assert rate == 16000
    def repeated(seconds):
        return np.tile(voice, int(np.ceil(seconds * rate / len(voice))))[:seconds * rate]
    audio = np.concatenate((repeated(75), np.zeros(3 * rate), repeated(80),
        np.random.default_rng(91).normal(0, .002, 65 * rate), np.zeros(65 * rate), repeated(75)))
    vad = StreamingFsmnVad(local_model)
    events = []
    for offset in range(0, len(audio), chunk_size):
        events.extend(vad.feed(audio[offset:offset + chunk_size]))
        # Each decibel window must describe the same absolute frame as its score.
        assert vad.scorer.decibel.total == vad.scorer.frm_cnt
        assert len(vad.scorer.decibel.values) <= 4096
        assert len(vad._pending_waveform) < 1600
        assert len(vad.scorer.output_data_buf) <= 1
    events.extend(vad.feed([], final=True))
    assert any(start > 300 * rate for start, _ in events)
    assert all(0 <= x <= len(audio) for pair in events for x in pair if x != -1)
    assert vad.position == 363 * rate


def test_continuous_speech_maximum_and_long_silence(local_model, monkeypatch):
    np = pytest.importorskip('numpy')
    vad = StreamingFsmnVad(local_model)
    speech = True
    def infer(inputs):
        frames = inputs[0].shape[1]
        scores = np.empty((1, frames, 2), dtype=np.float32)
        scores[:] = [.001, .999] if speech else [.999, .001]
        return scores, inputs[1:]
    monkeypatch.setattr(vad.model, 'infer', infer)
    events = []
    # Real frontend/scorer, controlled posteriors guarantee uninterrupted speech
    # and force the vendor's 60-second maximum rather than natural endpoints.
    for seconds, state in [(130, True), (70, False), (130, True)]:
        speech = state
        for _ in range(seconds * 10):
            events.extend(vad.feed(np.full(1600, .1, dtype=np.float32)))
            assert vad.scorer.decibel.total == vad.scorer.frm_cnt
            assert len(vad.scorer.output_data_buf) <= 1
    events.extend(vad.feed([], final=True))
    ends = [end for _, end in events if end >= 0]
    assert len(ends) >= 6
    assert ends == sorted(ends)
    assert any(start >= 200 * 16000 for start, _ in events)


def test_inference_exception_keeps_cause(local_model, monkeypatch):
    vad = StreamingFsmnVad(local_model)
    failure = ValueError('model failure')
    def fail(_):
        raise failure
    monkeypatch.setattr(vad.model, 'infer', fail)
    with pytest.raises(RuntimeError, match='fsmn_inference_failed') as caught:
        vad.feed([.1] * 1600)
    assert caught.value.__cause__ is failure
