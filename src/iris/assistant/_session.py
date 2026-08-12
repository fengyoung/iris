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

    def submit(
        self,
        raw_text: str,
        started_at: Optional[datetime] = None,
        on_publish: Optional[object] = None,
    ) -> VoiceSegment:
        """提交新语音段；若已有未消费段则覆盖并计丢弃。

        on_publish：临界区内（notify 前）调用的发布回调——预取注册
        （futures 入表、上下文入窗）与 pending 设置原子化，保证 worker
        取走段时预取产物必已就绪（消除「worker 抢跑 → 现场重提 → 双份
        LLM 成本」竞态）。调用方须保证回调 µs 级且不抛未捕获异常
        （live 侧内部自兜底）。
        """
        seg = VoiceSegment(
            seq=self._next_seq,
            started_at=started_at or datetime.now(),
            raw_text=raw_text,
        )
        with self._cond:
            self._next_seq += 1
            if self._pending is not None:
                self._state.dropped_count += 1
                # 保留被丢弃段的原文（上限 20 条，防附录膨胀）
                if len(self._state.dropped_texts) < MeetingState._MAX_DROPPED_TEXTS:
                    self._state.dropped_texts.append(
                        self._pending.raw_text[:200]
                    )
            self._pending = seg
            if on_publish is not None:
                on_publish(seg)
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
        # v3.25.5 说话人历史：注入已识别的说话人，帮助 LLM 跨批保持 speaker_id 一致
        if s.speakers:
            sp_parts = [f'{sp["id"]}（{sp.get("role", "") or "参与者"}）'
                        for sp in s.speakers[-4:]]
            parts.append("已识别说话人: " + " / ".join(sp_parts))
        block = "\n".join(parts)
        # v3.26.1: 硬上限 3000 字符保护（防止累积项过多导致分析 prompt 过大）
        _HARD_CAP = 3000
        if len(block) > _HARD_CAP:
            block = block[:_HARD_CAP // 2] + "\n…\n" + block[-_HARD_CAP // 2:]
        elif len(block) > max_chars:
            block = block[:max_chars] + "…"
        return block

    def open_questions_for_prompt(self) -> str:
        """待解决问题列表（供分析 Prompt 的 resolved_questions 判定）。"""
        if not self._state.open_questions:
            return "（暂无）"
        return "\n".join(f"- {q}" for q in self._state.open_questions)

    def adjacent_context(self, current_seq: int) -> str:
        """当前段紧邻上文（原始校正文本），供 LLM 理解碎片化短句的语境。

        返回当前段之前 1-2 个已记录段的校正文本；段间间隔超过 30s 则只取最近 1 段。
        """
        segments = self._state.segments
        if not segments:
            return ""
        # 取当前段之前的段（按 seq 排序，最近的在最后）
        prev = [s for s in segments if s.seq < current_seq]
        if not prev:
            return ""
        # 最多取 2 段，时间跨度 ≤ 30s（以当前真实时间为基准，v3.26.1 修正）
        recent = []
        now = datetime.now()
        for s in reversed(prev):
            if len(recent) >= 2:
                break
            if (now - s.started_at).total_seconds() > 30:
                break
            text = s.corrected_text or s.raw_text
            if text.strip():
                recent.insert(0, text)
        if not recent:
            return ""
        return "上文：" + " / ".join(recent)

    @property
    def state(self) -> MeetingState:
        return self._state

    @property
    def stop(self) -> threading.Event:
        return self._stop
