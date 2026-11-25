"""Incremental ledger and coalesced writer; all disk writes stay in tmp_path."""
import random
import threading
from unittest.mock import Mock

from iris_caption.session import Session, Segment
from iris_caption.statistics import COUNT_FIELDS, SessionCounts, StatisticsWriter, StatsStore, session_snapshot

START = '2026-01-01T00:00:00+00:00'


def row(key, n):
    return dict(session_id=key, started_at=START, updated_at=START,
                raw_chars=n, corrected_chars=n, finalized_segments=n)


def test_incremental_counts_match_reference_after_every_transition():
    rng = random.Random(42)
    session = Session()
    counts = SessionCounts(session.id, START)
    for _ in range(450):
        sid = str(rng.randint(1, 20))
        seg = session.segments.setdefault(sid, Segment(sid, 0, 1000))
        seg.status = rng.choice(['partial', 'transcribing', 'interrupted', 'pending',
                                 'corrected', 'manual', 'retry', 'error', 'local'])
        seg.raw = rng.choice(['保密原文abc123。', 'é𝟙あ', '简短', ''])
        seg.corrected = rng.choice(['保密校正', '换成较长的一句', ''])
        counts.update(seg)
        assert not counts.update(seg)
        oracle, snapshot = session_snapshot(session), counts.snapshot()
        assert all(oracle[key] == snapshot[key] for key in COUNT_FIELDS)
        assert not {'raw', 'corrected', 'speaker_name'}.intersection(snapshot)
    assert '保密' not in repr(vars(counts))


def test_latest_per_session_flush_and_shutdown(tmp_path):
    store, errors = StatsStore(tmp_path), []
    recorded = Mock(wraps=store)
    writer = StatisticsWriter(recorded, lambda: errors.append(True), interval=60)
    for i in range(1, 100):
        writer.submit(dict(row('first', i), raw='secret'))
    writer.submit(row('first', 100), flush=True)
    writer.submit(row('second', 101))
    writer.close()
    assert writer.wait(2) and not errors
    assert not writer.submit(row('late', 99))
    assert [(c.args[0]['session_id'], c.args[0]['raw_chars']) for c in recorded.record.call_args_list] == [
        ('first', 100), ('second', 101)]
    assert 'secret' not in store.path.read_text()
    assert sum(r['raw_chars'] for r in store.rows()) == 201


def test_write_inflight_does_not_discard_newer_or_next_session():
    entered, release = threading.Event(), threading.Event()
    stored = []
    class Store:
        def record(self, snapshot):
            if not stored:
                entered.set()
                assert release.wait(3)
            stored.append((snapshot['session_id'], snapshot['raw_chars']))
    writer = StatisticsWriter(Store(), lambda: None, interval=30)
    writer.submit(row('first', 1), flush=True)
    assert entered.wait(1)
    writer.submit(row('first', 2), flush=True)
    writer.submit(row('second', 1))
    writer.close()  # Nonblocking even though record() is in progress.
    release.set()
    assert writer.wait(2)
    assert stored == [('first', 1), ('first', 2), ('second', 1)]


def test_periodic_write_without_close_and_failure_does_not_kill_worker():
    failed, stored = threading.Event(), threading.Event()
    store = Mock()
    store.record.side_effect = [OSError('private path'), None]
    writer = StatisticsWriter(store, failed.set, interval=.02)
    writer.submit(row('one', 1))
    assert failed.wait(1)
    def record(snapshot):
        stored.set()
    store.record.side_effect = record
    writer.submit(row('one', 2))
    assert stored.wait(1)
    writer.close()
    assert writer.wait(2)
