"""Deterministic contract tests; no network, model files, or microphone."""
from threading import Event

import numpy as np
import pytest

from iris_caption.diarization import Diarizer, DiarizationWorker, StableClusters
from iris_caption.speaker import decode_powerset, validate_audio, unit_vector


def frames(seconds=3, column=1):
    logits = np.full((int(seconds / .016875), 7), -20., dtype=np.float32)
    logits[:, column] = 0
    return decode_powerset(logits, int(seconds * 16000))


class Embedding:
    def __init__(self):
        self.calls = 0

    def extract(self, audio):
        self.calls += 1
        return np.array([1., 0., 0.])


class Segments:
    def __init__(self, result):
        self.result = result

    def analyze(self, audio):
        return self.result


def test_powerset_overlap_is_pairs_not_speaker_slot():
    result = decode_powerset(np.eye(7) * 30, 16000)
    assert np.all(result.overlap[:4] < .001)
    assert np.all(result.overlap[4:] > .999)
    assert result.speech[0] < .001
    assert result.centers[1] - result.centers[0] == pytest.approx(.016875)


@pytest.mark.parametrize('audio', [np.zeros((3, 2)), np.zeros(160001), np.array([np.nan]),
                                 np.array([np.inf]), np.array([2.]), np.array([])])
def test_invalid_audio(audio):
    with pytest.raises(ValueError):
        validate_audio(audio)


def test_unit_vector_rejects_invalid():
    for vector in [np.zeros(3), np.array([np.nan]), np.zeros((2, 2))]:
        with pytest.raises(ValueError):
            unit_vector(vector)


def test_labels_stable_confirmation_capacity_and_reset():
    clusters = StableClusters(max_speakers=3)
    for i, label in enumerate('ABC'):
        vector = np.eye(4)[i]
        assert clusters.assign(vector).label is None
        assert clusters.assign(vector).label == label
    assert clusters.assign(np.eye(4)[3]).reason == 'capacity'
    assert clusters.assign(np.eye(4)[0]).label == 'A'
    clusters.reset()
    assert clusters.count == 0
    assert clusters.assign(np.eye(4)[2]).label is None
    assert clusters.assign(np.eye(4)[2]).label == 'A'


def test_ambiguous_neither_creates_nor_changes_cluster():
    clusters = StableClusters()
    clusters.assign(np.array([1., 0.]))
    clusters.assign(np.array([1., 0.]))
    assert clusters.assign(np.array([.6, .8])).reason == 'ambiguous'
    assert clusters.count == 1
    assert clusters.assign(np.array([1., 0.])).label == 'A'


@pytest.mark.parametrize('column,interrupted,seconds,reason', [
    (4, False, 3, 'overlap'), (1, True, 3, 'interrupted'),
    (1, False, .5, 'short'), (0, False, 3, 'insufficient-clean-speech'),
])
def test_bad_turns_never_embed_or_update(column, interrupted, seconds, reason):
    embedding = Embedding()
    diarizer = Diarizer(embedding, Segments(frames(seconds, column)), enabled=True)
    result = diarizer.analyze(np.zeros(int(seconds * 16000)), interrupted=interrupted)
    assert result.reason == reason
    assert embedding.calls == diarizer.clusters.count == 0
    assert bool(result.overlap) == (column == 4)


def test_default_disabled_no_inference():
    diarizer = Diarizer(None, None)
    assert diarizer.analyze(np.zeros(10)).reason == 'disabled'


def test_clean_turn_confirmed_and_no_cross_session_identity():
    diarizer = Diarizer(Embedding(), Segments(frames()), enabled=True)
    assert diarizer.analyze(np.zeros(48000)).reason == 'unconfirmed'
    assert diarizer.analyze(np.zeros(48000)).label == 'A'
    diarizer.reset()
    assert diarizer.analyze(np.zeros(48000)).label is None


def test_multispeaker_turn_unknown():
    a = frames()
    a.probabilities[80:] = np.roll(a.probabilities[80:], 1, axis=1)
    embedding = Embedding()
    diarizer = Diarizer(embedding, Segments(a), enabled=True)
    assert diarizer.analyze(np.zeros(48000)).reason == 'multiple-speakers'
    assert embedding.calls == 0


def test_bounded_worker_owns_copy_and_clears_on_close():
    started, release = Event(), Event()
    seen = []

    class Blocking(Diarizer):
        def analyze(self, audio, **kwargs):
            seen.append(audio)
            started.set()
            assert release.wait(3)
            return super().analyze(audio, **kwargs)

    original = np.ones(32000, dtype=np.float32) * .1
    worker = DiarizationWorker(Blocking(None, None))
    first = worker.submit(original)
    assert started.wait(3)
    second = worker.submit(original)
    assert worker.submit(original) is None
    release.set()
    assert first.result(3).reason == second.result(3).reason == 'disabled'
    worker.close()
    assert all(np.all(buffer == 0) for buffer in seen)
    assert np.all(original == .1)
    assert worker.submit(original) is None
