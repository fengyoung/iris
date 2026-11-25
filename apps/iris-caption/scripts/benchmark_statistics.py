"""Synthetic four-hour timeline; CPU replay, not a four-hour real-device soak."""
import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from iris_caption.session import Segment, Session
from iris_caption.statistics import SessionCounts, StatisticsWriter, StatsStore, session_snapshot

STARTED = '2026-01-01T00:00:00+00:00'


def replay(count, incremental):
    session = Session(id='synthetic')
    counts = SessionCounts(session.id, STARTED)
    times = []
    result = {}
    for i in range(count):
        seg = Segment(str(i), i*20000, (i+1)*20000, raw='合成示例 123。' * 3, status='pending')
        session.segments[seg.id] = seg
        for state, text in [('pending', ''), ('corrected', '校正示例 456。' * 3), ('manual', '人工编辑789。')]:
            seg.status, seg.corrected = state, text
            before = time.perf_counter_ns()
            if incremental:
                counts.update(seg)
                result = counts.snapshot()
            else:
                result = session_snapshot(session, started_at=STARTED)
            times.append((time.perf_counter_ns() - before)/1000)
    return {'events': len(times), 'total_ms': sum(times)/1000, 'median_us': statistics.median(times),
            'p95_us': sorted(times)[int(len(times)*.95)]}, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--segments', type=int, default=720)
    args = parser.parse_args()
    if not 1 <= args.segments <= 5000:
        parser.error('segments must be 1..5000')
    old, baseline = replay(args.segments, False)
    new, actual = replay(args.segments, True)
    fields = ('raw_chars', 'corrected_chars', 'finalized_segments')
    assert all(actual[k] == baseline[k] for k in fields)
    with tempfile.TemporaryDirectory() as directory:
        store = StatsStore(Path(directory))
        writes, failures = [], []
        class CountingStore:
            def record(self, row):
                writes.append(row['session_id'])
                store.record(row)
        writer = StatisticsWriter(CountingStore(), lambda: failures.append(True))
        for n in range(1, args.segments+1):
            writer.submit(dict(actual, raw_chars=n, finalized_segments=n))
        writer.submit(actual, flush=True)
        writer.close()
        assert writer.wait(3) and not failures
        assert all(store.rows()[0][k] == actual[k] for k in fields)
    print(json.dumps(dict(timeline_hours=args.segments*20/3600, baseline=old, incremental=new,
                          totals={k: actual[k] for k in fields}, writes_in_accelerated_burst=len(writes)),
                     indent=2))


if __name__ == '__main__':
    main()
