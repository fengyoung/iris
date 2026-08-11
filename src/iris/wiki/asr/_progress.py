"""ASR Pipeline 内部进度追踪器 — 线程安全的批处理进度输出。

供 hotwords.py 和 extractor.py 的内部批处理循环使用，
在 stderr 上逐批输出实时进度，确保并发场景下不交错。
"""

from __future__ import annotations

import sys
import threading
import time


class ProgressTracker:
    """线程安全的批处理进度追踪器。

    每个工作线程在 LLM 调用完成后立即调用 ``increment()``，
    锁保护计数器并原子输出一行进度信息到 stderr。

    用法::

        tracker = ProgressTracker(total=len(batches), label="热词提取")
        ...
        tracker.increment(detail=f"第{idx+1}批 {count}词")
        ...
        print(f"... 完成 ({tracker.elapsed():.1f}s)", file=sys.stderr)
    """

    def __init__(self, total: int, label: str = "") -> None:
        self._total = total
        self._label = label
        self._completed = 0
        self._errors = 0
        self._lock = threading.Lock()
        self._start = time.monotonic()

    # ── 公共 API ──────────────────────────────────────────

    def increment(self, *, detail: str = "") -> int:
        """成功完成一个批次。

        Args:
            detail: 批次详情，如 "第3批 72词 (候选85)"

        Returns:
            当前已完成的批次数（含本批次）
        """
        return self._add_completed(detail=detail, is_error=False)

    def increment_error(self, *, detail: str = "") -> int:
        """标记一个批次失败（因 LLM 调用异常等）。

        Args:
            detail: 错误详情，如 "第5批失败: timeout"

        Returns:
            当前已完成的批次数（含本批次）
        """
        return self._add_completed(detail=detail, is_error=True)

    def elapsed(self) -> float:
        """返回从创建到现在的耗时（秒）。"""
        return time.monotonic() - self._start

    # ── 内部实现 ──────────────────────────────────────────

    def _add_completed(self, *, detail: str, is_error: bool) -> int:
        """在锁保护下递增计数器并输出进度行。"""
        with self._lock:
            self._completed += 1
            if is_error:
                self._errors += 1
            self._print_progress(detail, is_error)
            return self._completed

    def _print_progress(self, detail: str, is_error: bool) -> None:
        """构建并输出一行进度信息。

        格式:
            成功: ``  [asr] 3/10 批完成 (14.1s): 第3批 72词 (候选85)``
            失败: ``  [asr] 3/10 批完成 (14.1s): [失败] 第5批失败: timeout``
        """
        t = time.monotonic() - self._start
        tag = "[失败]" if is_error else ""
        msg = f"  [asr] {self._completed}/{self._total} 批完成 ({t:.1f}s): {tag}{detail}"
        print(msg, file=sys.stderr)
