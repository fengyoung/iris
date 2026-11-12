"""Bounded single-owner ASR execution, independent of capture and Qt."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .audio import AudioWindow


class RecognitionScheduler:
    """Serialize recognition, preferring FIFO finals over replaceable partials.

    Input segment ids/revisions follow the capture stream's increasing clock.
    Storage is at most ``max_finals`` queued finals and one queued partial,
    plus the currently executing window. Callbacks run only on the worker;
    their return value is ignored (the callback should emit results itself).
    A failed callback stops processing and is re-raised on the capture thread.
    Native inference cannot be interrupted: shutdown is bounded and the worker
    is a daemon, but callers must not reuse its adapter after a timeout.
    """

    def __init__(self, process_window: Callable[[AudioWindow], object], *, max_finals: int = 4):
        if isinstance(max_finals, bool) or not isinstance(max_finals, int) or max_finals < 1:
            raise ValueError("max_finals must be a positive integer")
        self._process_window = process_window
        self._max_finals = max_finals
        self._condition = threading.Condition()
        self._finals: deque[AudioWindow] = deque()
        self._partial: AudioWindow | None = None
        self._latest_partial = (-1, -1)
        self._last_final = -1
        self._active = False
        self._closed = False
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="iris-recognition", daemon=True)
        self._thread.start()

    def submit(self, windows: Iterable[AudioWindow]) -> None:
        """Enqueue without waiting for ASR; overflow explicitly rejects a final.

        Iterables are consumed incrementally, not materialized. On overflow,
        preceding windows have been accepted; the caller must abort capture,
        rather than retry the whole iterable and duplicate finals.
        """
        with self._condition:
            self._check_open()
        for window in windows:
            with self._condition:
                self._check_open()
                self._enqueue(window)
                self._condition.notify_all()

    def _check_open(self) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise RuntimeError("recognition_closed")

    def _enqueue(self, window: AudioWindow) -> None:
        if window.kind == "final":
            if len(self._finals) >= self._max_finals:
                raise RuntimeError("recognition_backpressure")
            self._finals.append(window)
            self._last_final = max(self._last_final, window.segment_id)
            if self._partial is not None and self._partial.segment_id <= window.segment_id:
                self._partial = None
        elif window.kind == "partial":
            key = (window.segment_id, window.revision)
            if window.segment_id > self._last_final and key > self._latest_partial:
                self._latest_partial = key
                self._partial = window
        else:
            raise ValueError("unknown recognition window kind")

    def raise_if_failed(self) -> None:
        """Poll from capture even when it has no new windows to submit."""
        with self._condition:
            if self._failure is not None:
                raise self._failure

    @staticmethod
    def _deadline(timeout: float) -> float:
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        return time.monotonic() + timeout

    def drain(self, timeout: float = 30.0) -> None:
        """Wait for accepted work, bounded by timeout; do not close admission."""
        deadline = self._deadline(timeout)
        with self._condition:
            while self._active or self._finals or self._partial is not None:
                self.raise_if_failed()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("recognition_timeout")
                self._condition.wait(remaining)
            self.raise_if_failed()

    def close(self, cancel: bool = False, timeout: float = 30.0) -> None:
        """Reject new work, drain (or cancel queued work), and boundedly join.

        Even on timeout, shutdown remains requested. A blocked native call may
        finish later, at which point the daemon exits. Repeated close is safe.
        """
        deadline = self._deadline(timeout)
        with self._condition:
            self._closed = True
            if cancel:
                self._finals.clear()
                self._partial = None
            self._condition.notify_all()
        self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive():
            raise TimeoutError("recognition_timeout")
        self.raise_if_failed()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._finals and self._partial is None:
                    if self._closed:
                        return
                    self._condition.wait()
                if self._finals:
                    window = self._finals.popleft()
                else:
                    window, self._partial = self._partial, None
                self._active = True
            try:
                self._process_window(window)
            except BaseException as exc:
                with self._condition:
                    self._failure = exc
                    self._closed = True
                    self._finals.clear()
                    self._partial = None
                return
            finally:
                window = None  # Release audio before waiting for another item.
                with self._condition:
                    self._active = False
                    self._condition.notify_all()


__all__ = ["RecognitionScheduler"]
