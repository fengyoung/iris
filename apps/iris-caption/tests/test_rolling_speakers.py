"""Rolling local-slot changes and disjoint final evidence contracts."""
from threading import Event

import numpy as np

from iris_caption.diarization import Diarizer, DiarizationWorker, StableClusters
from iris_caption.rolling_speakers import RollingSpeakerAnalyzer
from iris_caption.speaker import decode_powerset


def make_frames(seconds=4, transition=2, slots=(1, 2)):
    count = int((seconds * 16000 - 991) // 270 + 1)
    logits = np.full((count, 7), -20., dtype=np.float32)
    for i in range(count):
        logits[i, slots[int((i * 270 + 495.5) / 16000 >= transition)]] = 0
    return decode_powerset(logits, int(seconds * 16000))


class Segmenter:
    def __init__(self):
        self.calls = 0

    def analyze(self, audio):
        self.calls += 1
        return make_frames(len(audio) / 16000)


def test_boundary_absolute_dedup_and_late_ack():
    detector = RollingSpeakerAnalyzer(None, 'session', enabled=True)
    events = detector._events(make_frames(), 10000, 74000)
    boundary = [e for e in events if e.kind == 'boundary']
    assert len(boundary) == 1
    assert abs(boundary[0].start_sample - 42000) < 270
    assert boundary[0].session_id == 'session'
    assert detector._events(make_frames(), 10000, 74000) == ()
    detector.acknowledge_cut(90000)
    assert detector._events(make_frames(), 50000, 114000) == ()


def test_no_cross_window_slot_identity_and_no_overlap_transition():
    detector = RollingSpeakerAnalyzer(None, 's', enabled=True)
    for slots in [(1, 1), (2, 2), (3, 3), (1, 4), (0, 2)]:
        events = detector._events(make_frames(slots=slots), 0, 64000)
        assert not any(e.kind == 'boundary' for e in events)


def test_bounded_uncertain_gap_requires_clean_distinct_endpoints():
    f = make_frames()
    gap = (f.centers > 1.8) & (f.centers < 2.2)
    f.probabilities[gap] = np.array([.1, .45, .45, 0, 0, 0, 0])
    detector = RollingSpeakerAnalyzer(None, 's', enabled=True)
    assert any(e.kind == 'boundary' for e in detector._events(f, 0, 64000))
    f.probabilities[gap] = np.array([0, .4, .4, 0, .2, 0, 0])
    detector.reset('s')
    assert not any(e.kind == 'boundary' for e in detector._events(f, 0, 64000))


def test_bounded_ring_ticks_replay_and_gap_reset():
    model = Segmenter()
    detector = RollingSpeakerAnalyzer(model, 's', enabled=True)
    for i in range(20):
        detector.feed(np.zeros(8000), i * 8000)
        assert detector.retained_samples <= 64000
    assert model.calls == 9
    assert detector.feed(np.zeros(8000), 0) == ()
    assert model.calls == 9
    detector.feed(np.zeros(8000), 200000)
    assert detector.retained_samples == 8000
    detector.close()
    assert detector.retained_samples == 0


def test_default_capacity_supports_four_or_more_participants():
    clusters = StableClusters()
    for i in range(8):
        assert clusters.assign(np.eye(9)[i]).label is None
        assert clusters.assign(np.eye(9)[i]).label == chr(65 + i)
    assert clusters.assign(np.eye(9)[8]).reason == 'capacity'


class CleanSegments:
    def analyze(self, audio):
        return make_frames(len(audio) / 16000, slots=(1, 1))


class Embeddings:
    def extract(self, audio):
        return np.array([1., 0.]) if np.mean(audio) < .3 else np.array([0., 1.])


def test_disjoint_final_and_stale_session_guard():
    d = Diarizer(Embeddings(), CleanSegments(), enabled=True)
    audio = np.zeros(64000)
    assert d.analyze_final(audio, start_sample=0, session_id='s').reason == 'unconfirmed'
    assert d.analyze_final(audio, start_sample=1000, session_id='s').reason == 'duplicate-range'
    assert d.clusters.count == 0
    assert d.analyze_final(audio, start_sample=64000, session_id='old').reason == 'stale-session'
    assert d.analyze_final(audio, start_sample=64000, session_id='s').label == 'A'


def test_long_mixed_final_never_mutates_clusters():
    d = Diarizer(Embeddings(), CleanSegments(), enabled=True)
    audio = np.r_[np.zeros(64000), np.ones(64000) * .5]
    assert d.analyze_final(audio, start_sample=0, session_id='s').reason == 'multiple-speakers'
    assert d.clusters.count == 0
    assert d.analyze_final(np.zeros(320000), start_sample=128000, session_id='s').label == 'A'
    assert d.clusters.count == 1  # Five verified disjoint chunks provide independent evidence.


def test_shutdown_is_bounded_and_erasure_completes_after_inflight():
    started, release = Event(), Event()

    class Blocking(Diarizer):
        def analyze(self, audio, **kwargs):
            started.set()
            assert release.wait(3)
            return super().analyze(audio, **kwargs)

    d = Blocking(None, None)
    worker = DiarizationWorker(d)
    first = worker.submit(np.zeros(32000))
    assert started.wait(3)
    queued = worker.submit(np.zeros(32000))
    assert worker.close(timeout=0) is False
    assert worker.submit(np.zeros(32000)) is None
    release.set()
    assert first.result(3).reason == 'disabled'
    assert queued.result(3).reason == 'closed'
    assert worker.close(timeout=3) is True
    assert worker.completion.done()
