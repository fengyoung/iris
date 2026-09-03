"""批处理的纯逻辑步骤：批文本组装 / 检索去重 / 分析结果应用 / 建议提问判定。

从 live.py 的 `_process_batch` 抽出：只操作会话状态、洞察流与段对象，
不碰线程池、面板、文档写入，便于独立测试。
"""

from __future__ import annotations

import logging
from typing import Iterable, List

from ._insight import InsightEvent, InsightFeed
from .models import VoiceSegment

_logger = logging.getLogger(__name__)


# ── 批文本组装 ──────────────────────────────────────────────

def batch_hints(analyzable: List[VoiceSegment], *, force_topic_boundary: bool) -> List[str]:
    """给 LLM 的批级提示行（话题边界 / 说话人切换 / 强制切段）。"""
    hints: List[str] = []
    # v3.26.1: 手动话题边界标记（m 键）
    if force_topic_boundary:
        hints.append("[用户标记：此处为话题边界，请将 topic_change 设为 true]")
    # v3.25.5: VAD 说话人切换信号；v3.26.1: 批内多次切换 → 强化提示
    speaker_change_count = sum(1 for s in analyzable if getattr(s, "speaker_change_signal", False))
    if speaker_change_count >= 2:
        hints.append(
            "[注意：批内检测到多次说话人切换，以下段落可能涉及不同微话题，"
            "请分别为各说话人段标注 topic 和 speaker 信息]"
        )
    elif speaker_change_count == 1:
        hints.append("[VAD 检测到可能的说话人切换，请确认 speaker.is_turn_change]")
    # v3.26.1: 连续 forced_cut 段 → 标注可能是同一发言的延续
    forced_count = sum(1 for s in analyzable if getattr(s, "forced_cut", False))
    if forced_count >= 2:
        hints.append("[注意：以下多段为 ASR 强制切段（非自然停顿），可能属于同一人的连续发言]")
    return hints


def speaker_id_of(seg: VoiceSegment) -> str:
    sp = getattr(seg, "speaker", None)
    return sp.speaker_id if sp else ""


def segment_line(seg: VoiceSegment) -> str:
    """段文本行：`段N（speaker）：文本`。"""
    sp_id = speaker_id_of(seg)
    sp_label = f"（{sp_id}）" if sp_id else ""
    return f"段{seg.seq}{sp_label}：{seg.corrected_text}"


def dedup_hits(hits: Iterable) -> list:
    """检索结果按 (title, preview[:50]) 去重，保序。"""
    seen = set()
    unique = []
    for h in hits:
        key = (h.title, (h.content_preview or "")[:50])
        if key not in seen:
            seen.add(key)
            unique.append(h)
    return unique


# ── 分析结果应用 ────────────────────────────────────────────

def apply_topic(state, feed: InsightFeed, analysis, first_seq: int) -> None:
    """话题变化 → 推送（以实际状态变化为准，兼容 topic_change 缺失）。"""
    if not analysis.topic:
        return
    prev_topic = state.current_topic
    state.update_topic(analysis.topic, analysis.topic_change, analysis.topic_summary, first_seq)
    if prev_topic and prev_topic != state.current_topic:
        feed.push_topic_change(analysis.topic)


def apply_decisions_and_risks(state, feed: InsightFeed, analysis) -> None:
    """决策（confirmed）/ 风险（前 2 条）/ 语义冲突 → 推送。"""
    for d in analysis.decisions:
        if d.confidence == "confirmed":
            feed.push_decision(d.text, "confirmed")
    for r in analysis.risks[:2]:
        feed.push_risk(r)
    if analysis.key_points:
        for c in state.check_conflict(analysis.key_points):
            feed.push_conflict(c)
            _logger.warning("⚠ 语义冲突: %s", c)


def apply_todos(state, feed: InsightFeed, analysis) -> None:
    """待办 → 累计去重 + 推送前 2 条。"""
    if not analysis.todos:
        return
    for t in analysis.todos:
        if t.text and t.text not in state.todos:
            state._dedup_append(state.todos, [t.text])
    for t in analysis.todos[:2]:
        assignee = f"（{t.assignee}）" if t.assignee else ""
        feed.push(InsightEvent(event_type="todo", text=f"{t.text}{assignee}"))


def apply_speaker(state, feed: InsightFeed, analysis, analyzable: List[VoiceSegment], first_seq: int) -> None:
    """v3.25.5 说话人追踪：登记/计数 → 段后验传递 → 切换推送。"""
    sp = analysis.speaker
    if not (sp and sp.speaker_id):
        return
    known = next((s for s in state.speakers if s.get("id") == sp.speaker_id), None)
    if known is None:
        state.speakers.append({
            "id": sp.speaker_id, "role": sp.role_hint,
            "first_seen": first_seq, "segments": 1,
        })
    else:
        known["segments"] = known.get("segments", 0) + len(analyzable)
    # 更新段的 speaker（后验传递，仅 VoiceSegment）
    for seg in analyzable:
        if hasattr(seg, "speaker"):
            seg.speaker = sp
    if sp.is_turn_change:
        role = f"（{sp.role_hint}）" if sp.role_hint else ""
        feed.push(InsightEvent(event_type="speaker_turn", text=f"{sp.speaker_id}{role} 发言"))


def check_off_agenda(state, analysis, agenda: str) -> None:
    """跑偏检测：有议程但当前话题偏离 → 日志提醒。"""
    if not (agenda and analysis.topic):
        return
    agenda_keywords = [kw.strip() for kw in agenda.replace("；", ";").split(";") if kw.strip()]
    topic_lower = analysis.topic.lower()
    on_agenda = any(
        kw.lower() in topic_lower or topic_lower in kw.lower() for kw in agenda_keywords
    )
    if not on_agenda and len(state.topics) >= 1:
        _logger.info("⚠ 跑偏提醒: 当前话题「%s」不在预设议程中", analysis.topic)


def apply_analysis(state, feed: InsightFeed, analysis, analyzable: List[VoiceSegment],
                   *, agenda: str) -> None:
    """v3.25.3 话题追踪 + 洞察推送 全套。"""
    first_seq = analyzable[0].seq
    apply_topic(state, feed, analysis, first_seq)
    apply_decisions_and_risks(state, feed, analysis)
    apply_todos(state, feed, analysis)
    apply_speaker(state, feed, analysis, analyzable, first_seq)
    check_off_agenda(state, analysis, agenda)


# ── 建议提问判定 ────────────────────────────────────────────

def should_suggest(analysis, first_seq: int, last_suggest_seq: int, suggest_every: int) -> bool:
    """固定间隔采样 + 事件驱动（tentative 决策 / 新问题）统一节流（v3.26.1）。

    事件驱动条件须距上次实际生成建议 ≥ suggest_every 段才触发，防止高频 LLM 调用。
    """
    if analysis is None:
        return False
    if (first_seq - 1) % suggest_every == 0:
        return True
    if first_seq - last_suggest_seq < suggest_every:
        return False
    if any(d.confidence == "tentative" for d in analysis.decisions):
        return True
    return bool(analysis.questions)


def batch_label(batch: List[VoiceSegment], n_analyzed: int, n_skipped: int) -> str:
    """面板状态标签。"""
    if len(batch) == 1:
        return f"已处理段 {batch[0].seq}"
    note = f"{n_analyzed} 段合并分析" if n_skipped == 0 else (
        f"{n_analyzed} 段合并分析 + {n_skipped} 段跳过")
    return f"已处理段 {batch[0].seq}-{batch[-1].seq}（{note}）"


def rms_level(last_rms: float, last_threshold: float) -> float:
    """音频电平归一化到 0-1（供面板 VU 条）。"""
    thr = last_threshold or 0.005
    return min(1.0, last_rms / thr) if last_rms > 0 else 0.0


__all__ = [
    "apply_analysis", "batch_hints", "batch_label", "dedup_hits", "rms_level",
    "segment_line", "should_suggest", "speaker_id_of",
]
