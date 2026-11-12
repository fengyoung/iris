"""Optional real-model smoke test; explicitly point at downloaded artifacts.

IRIS_CAPTION_SPEAKER_MODELS=output/model-validation .venv/bin/python -m pytest \
    tests/test_speaker_models.py -q
No network or microphone use, even when enabled. Fixture WAVs are public speech
from https://huggingface.co/funasr/fsmn-vad (example/vad_example.wav) and
https://huggingface.co/datasets/Narsil/asr_dummy (1.flac), not redistributed.
"""
import os
from pathlib import Path

import numpy as np
import pytest

from iris_caption.speaker import CAMPPLUS, SEGMENTATION, CampPlusEmbedding, PyannoteOverlap


@pytest.mark.skipif(not os.environ.get('IRIS_CAPTION_SPEAKER_MODELS'), reason='Explicit local models required')
def test_actual_cpu_models_and_public_speech():
    import soundfile as sf

    root = Path(os.environ['IRIS_CAPTION_SPEAKER_MODELS'])
    CAMPPLUS.verify(root / 'campplus.onnx')
    SEGMENTATION.verify(root / 'segmentation.onnx')
    embedder = CampPlusEmbedding(root / 'campplus.onnx')
    segmenter = PyannoteOverlap(root / 'segmentation.onnx')
    a, sr_a = sf.read(root / 'voice1.wav', dtype='float32')
    b, sr_b = sf.read(root / 'voice2.wav', dtype='float32')
    assert sr_a == sr_b == 16000
    ea, eb = embedder.extract(a), embedder.extract(b)
    assert ea.shape == eb.shape == (192,)
    assert np.linalg.norm(ea) == pytest.approx(1., abs=1e-5)
    assert float(ea @ eb) < .5
    clean_a, clean_b = segmenter.analyze(a), segmenter.analyze(b)
    mixed = segmenter.analyze(a + b)
    assert mixed.overlap.max() > .5
    assert np.mean(mixed.overlap >= .5) > np.mean(clean_a.overlap >= .5)
    assert np.mean(mixed.overlap >= .5) > np.mean(clean_b.overlap >= .5)
