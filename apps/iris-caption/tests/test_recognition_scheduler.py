"""Deterministic worker tests: no microphone, models, Qt, or timing sleeps."""
from array import array
import threading

import pytest

from iris_caption.audio import AudioWindow
from iris_caption.recognition_scheduler import RecognitionScheduler


def window(kind="partial", segment=1, revision=1):
    return AudioWindow(kind, segment, 0, 1, array("f", [0.1]), revision)


class BlockedProcessor:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.seen = []
        self.threads = []

    def __call__(self, item):
        self.threads.append(threading.get_ident())
        if not self.seen:
            self.entered.set()
            assert self.release.wait(3), "test did not release worker"
        self.seen.append((item.kind, item.segment_id, item.revision))


@pytest.fixture
def blocked():
    processor = BlockedProcessor()
    scheduler = RecognitionScheduler(processor)
    scheduler.submit([window()])
    assert processor.entered.wait(2)
    yield scheduler, processor
    processor.release.set()
    scheduler.close(timeout=2)


def test_concurrent_coalesce_prioritized_fifo_finals(blocked):
    scheduler, processor = blocked

    def produce():
        scheduler.submit(window(revision=i) for i in range(2, 100))
        scheduler.submit([window("final", revision=100)])
        scheduler.submit([window(segment=2), window("final", 2, 2)])
        scheduler.submit([window(segment=3), window(segment=3, revision=2)])

    producer = threading.Thread(target=produce)
    producer.start()
    producer.join(2)
    assert not producer.is_alive()  # Capture never waits on blocked recognition.
    processor.release.set()
    scheduler.drain(timeout=2)
    assert processor.seen == [
        ("partial", 1, 1), ("final", 1, 100), ("final", 2, 2), ("partial", 3, 2),
    ]
    assert len(set(processor.threads)) == 1
    assert processor.threads[0] != threading.get_ident()


def test_only_latest_partial_kept_and_stale_revisions_ignored(blocked):
    scheduler, processor = blocked
    scheduler.submit(window(revision=i) for i in range(2, 5000))
    scheduler.submit([window(revision=3)])
    processor.release.set()
    scheduler.drain(timeout=2)
    assert processor.seen == [("partial", 1, 1), ("partial", 1, 4999)]


def test_stale_partial_after_final_is_dropped(blocked):
    scheduler, processor = blocked
    scheduler.submit([window("final", 1, 2), window(revision=3)])
    processor.release.set()
    scheduler.drain(timeout=2)
    assert processor.seen == [("partial", 1, 1), ("final", 1, 2)]


def test_final_capacity_is_bounded_and_never_silently_drops(blocked):
    scheduler, processor = blocked
    scheduler.submit(window("final", i, 2) for i in range(1, 5))
    with pytest.raises(RuntimeError, match="^recognition_backpressure$"):
        scheduler.submit([window("final", 5, 2)])
    processor.release.set()
    scheduler.drain(timeout=2)
    assert processor.seen[1:] == [("final", i, 2) for i in range(1, 5)]


def test_drain_timeout_does_not_cancel_work(blocked):
    scheduler, processor = blocked
    with pytest.raises(TimeoutError, match="recognition_timeout"):
        scheduler.drain(timeout=0.01)
    processor.release.set()
    scheduler.drain(timeout=2)
    assert processor.seen == [("partial", 1, 1)]


def test_close_drains_and_rejects_new_work(blocked):
    scheduler, processor = blocked
    scheduler.submit([window("final", 1, 2)])
    processor.release.set()
    scheduler.close(timeout=2)
    assert processor.seen[-1] == ("final", 1, 2)
    assert not scheduler._thread.is_alive()
    for items in ([], [window(segment=2)]):
        with pytest.raises(RuntimeError, match="recognition_closed"):
            scheduler.submit(items)


def test_cancel_is_bounded_and_discards_queued_work(blocked):
    scheduler, processor = blocked
    scheduler.submit([window("final", 1, 2), window(segment=2)])
    with pytest.raises(TimeoutError, match="recognition_timeout"):
        scheduler.close(cancel=True, timeout=0.01)
    assert scheduler._thread.daemon
    with pytest.raises(RuntimeError, match="recognition_closed"):
        scheduler.submit([window(segment=3)])
    processor.release.set()
    scheduler.close(timeout=2)
    assert processor.seen == [("partial", 1, 1)]
    assert not scheduler._thread.is_alive()


def test_non_cancel_close_timeout_still_requests_shutdown(blocked):
    scheduler, processor = blocked
    scheduler.submit([window("final", 1, 2)])
    with pytest.raises(TimeoutError):
        scheduler.close(timeout=0.01)
    processor.release.set()
    scheduler.close(timeout=2)
    assert processor.seen[-1] == ("final", 1, 2)
    assert not scheduler._thread.is_alive()


def test_worker_exception_is_retained_on_all_polling_apis():
    error = ValueError("model exploded")

    def fail(item):
        raise error

    scheduler = RecognitionScheduler(fail)
    scheduler.submit([window()])
    with pytest.raises(ValueError) as caught:
        scheduler.drain(timeout=2)
    assert caught.value is error
    for operation in (scheduler.raise_if_failed, lambda: scheduler.submit([]), scheduler.close):
        with pytest.raises(ValueError, match="model exploded"):
            operation()
    assert not scheduler._thread.is_alive()


@pytest.mark.parametrize("capacity", [0, -1, 1.5, True])
def test_invalid_capacity(capacity):
    with pytest.raises(ValueError, match="max_finals"):
        RecognitionScheduler(lambda item: None, max_finals=capacity)


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_invalid_timeout(timeout):
    scheduler = RecognitionScheduler(lambda item: None)
    try:
        with pytest.raises(ValueError, match="timeout"):
            scheduler.drain(timeout)
        with pytest.raises(ValueError, match="timeout"):
            scheduler.close(timeout=timeout)
    finally:
        scheduler.close(timeout=2)


def test_idle_shutdown_and_custom_capacity():
    scheduler = RecognitionScheduler(lambda item: None, max_finals=1)
    scheduler.drain(timeout=0)
    scheduler.close(timeout=2)
    scheduler.close(timeout=2)
    assert not scheduler._thread.is_alive()


def test_invalid_window_kind():
    scheduler = RecognitionScheduler(lambda item: None)
    try:
        with pytest.raises(ValueError, match="window kind"):
            scheduler.submit([window("unexpected")])
    finally:
        scheduler.close(timeout=2)
