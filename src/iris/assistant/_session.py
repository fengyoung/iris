"""会议会话：积压丢弃状态机 + 会议状态累积（供分析 Prompt / 面板 / 文档共享）。"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Optional

from .models import MeetingState, VoiceSegment


class MeetingSession:
    """单槽 pending + Condition 的积压丢弃状态机。

    轮询线程（submit）与工作线程（take_pending）不共享其他状态：
    - submit：seq 递增；若旧 pending 尚在 → 覆盖（丢弃中间段，dropped_count += 1）
    - take_pending：阻塞等待（可中断），取走后串行处理；处理期间新 submit
      只覆盖 pending → 天然只消费最新段
    """

    def __init__(self) -> None:
        self._state = MeetingState()
        self._cond = threading.Condition()
        self._pending: Optional[VoiceSegment] = None
        self._next_seq = 1
        self._stop = threading.Event()

    # ── 轮询线程侧 ──────────────────────────────────────────

    def submit(self, raw_text: str, started_at: Optional[datetime] = None) -> VoiceSegment:
        """提交新语音段；若已有未消费段则覆盖并计丢弃。"""
        seg = VoiceSegment(
            seq=self._next_seq,
            started_at=started_at or datetime.now(),
            raw_text=raw_text,
        )
        with self._cond:
            self._next_seq += 1
            if self._pending is not None:
                self._state.dropped_count += 1
            self._pending = seg
            self._cond.notify()
        return seg

    # ── 工作线程侧 ──────────────────────────────────────────

    def take_pending(self, timeout: float = 0.5) -> Optional[VoiceSegment]:
        """单次等待取走最新待处理段；超时（或 stop 唤醒后无段）返回 None。

        语义约定：每次调用最多等待 timeout——worker 循环在空转返回 None 后
        自行检查 stop 标志决定是否退出，保证 stop 的响应延迟 ≤ timeout。
        """
        with self._cond:
            if self._pending is None and not self._stop.is_set():
                self._cond.wait(timeout=timeout)
            if self._pending is None:
                return None
            seg = self._pending
            self._pending = None
            return seg

    def request_stop(self) -> None:
        """请求停止：唤醒工作线程，在段边界退出。"""
        with self._cond:
            self._stop.set()
            self._cond.notify_all()

    # ── 状态访问 ────────────────────────────────────────────

    def record(self, segment: VoiceSegment) -> None:
        """处理完一段后落账（追加段 + 累计去重）。"""
        self._state.add_analysis(segment)

    def summary_for_prompt(self, max_chars: int = 1200) -> str:
        """压缩会议状态块：累计四类 + 最近 3 段校正文本（供分析 Prompt 引用）。"""
        s = self._state
        parts = []
        if s.key_points:
            parts.append("要点: " + "；".join(s.key_points))
        if s.decisions:
            parts.append("决策: " + "；".join(s.decisions))
        if s.risks:
            parts.append("风险: " + "；".join(s.risks))
        if s.open_questions:
            parts.append("待解决: " + "；".join(s.open_questions))
        recent = [seg.corrected_text or seg.raw_text for seg in s.segments[-3:]]
        if recent:
            parts.append("最近讨论: " + " / ".join(recent))
        block = "\n".join(parts)
        if len(block) > max_chars:
            block = block[:max_chars] + "…"
        return block

    @property
    def state(self) -> MeetingState:
        return self._state

    @property
    def stop(self) -> threading.Event:
        return self._stop
