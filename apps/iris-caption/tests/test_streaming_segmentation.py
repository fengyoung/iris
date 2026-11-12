"""Streaming endpoints, ownership, real local FSMN; never record or download."""
from array import array
from pathlib import Path
import pytest

from iris_caption.segmentation import Segmenter


class Endpoints:
    quiet = False
    def __init__(self, events):
        self.events = iter(events)
    def feed(self, samples, final=False):
        return next(self.events, [])
    def reset(self):
        pass


def test_quiet_waveform_can_be_learned_speech_and_preroll():
    detector = Endpoints([[]] * 10 + [[(6400, -1)]] + [[]] * 20 + [[(-1, 16000)]])
    seg = Segmenter(detector)
    windows = seg.feed(array('f', [0.00001]) * 20480)
    final, = [w for w in windows if w.kind == 'final']
    assert final.start_sample == 0  # 400ms pre-roll, not energy-gated
    assert final.end_sample == 16000


def test_natural_preroll_does_not_steal_previous_ownership():
    seg = Segmenter(Endpoints([[(0,-1)], [(-1,640)], [(1280,-1)]]))
    out = seg.feed(array('f', [.1]) * 1920) + seg.flush()
    finals = [w for w in out if w.kind == 'final']
    assert finals[1].start_sample >= finals[0].end_sample


def test_speaker_hook_and_safe_future_cut():
    chunks = []
    seg = Segmenter(Endpoints([[(0,-1)]]), partial_s=.2, on_speech_chunk=chunks.append)
    seg.feed(array('f',[.1])*6400)
    assert chunks and chunks[0].kind == 'partial'
    assert not seg.request_boundary(0)
    assert seg.request_boundary(7040)
    out = seg.feed(array('f',[.1])*640)
    assert out[-1].reason == 'speaker'


def test_soft_cap_waits_for_learned_short_pause():
    vad = Endpoints([[(0, -1)]])
    seg = Segmenter(vad, max_segment_s=1, hard_segment_s=2)
    assert not [w for w in seg.feed(array('f',[.1])*16000) if w.kind == 'final']
    vad.quiet = True
    out = seg.feed(array('f',[.1])*1920)
    assert out[-1].reason == 'soft_pause'
    assert out[-1].end_sample == 17920


def test_actual_local_streaming_fsmn_public_wav():
    model = Path.home()/'.cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-onnx'
    wav = Path(__file__).resolve().parents[3]/'output/iris-caption-benchmark/asr_example_zh.wav'
    if not (model/'model_quant.onnx').exists() or not wav.exists():
        pytest.skip('optional local model/public WAV not installed; never download')
    sf = pytest.importorskip('soundfile')
    pytest.importorskip('funasr_onnx')
    from iris_caption.streaming_vad import StreamingFsmnVad
    x, rate = sf.read(wav, dtype='float32')
    assert rate == 16000
    vad = StreamingFsmnVad(model)
    seg = Segmenter(vad)
    out = seg.feed(x) + seg.feed(array('f',[0.])*16000) + seg.flush()
    finals = [w for w in out if w.kind == 'final']
    assert finals and any(w.reason == 'silence' for w in finals)
    assert any(w.kind == 'partial' for w in out)
    assert all(0 <= w.start_sample < w.end_sample <= len(x)+16000 for w in finals)
    assert seg.position == len(x)+16000
    assert seg.buffered_samples == 0
    # Tiny initial/final packets must not hit vendor's unbound scorer bug.
    vad.feed([0.])
    vad.feed([], final=True)
