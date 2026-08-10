"""终端面板渲染：ANSI 清屏 + 固定分区整帧绘制（无第三方依赖，stdout 独占）。"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import MeetingState, VoiceSegment

_CLEAR = "\033[2J\033[H"  # 清屏 + 光标归位


@dataclass
class PanelDisplay:
    """一帧面板的数据：状态行 + 当前段 + 分析占位 + 会议状态。"""

    status: str = ""                        # 如 "分析中…" / "已处理"
    seg: Optional[VoiceSegment] = None
    analysis_unavailable: bool = False      # LLM 降级标记
    state: Optional[MeetingState] = None


class PanelRenderer:
    """整帧渲染；内部锁保证多线程下帧内容不交错。日志走 stderr 互不污染。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def render(self, display: PanelDisplay) -> None:
        with self._lock:
            sys.stdout.write(_CLEAR + self._build(display))
            sys.stdout.flush()

    def render_final(self, state: MeetingState, doc_path: Path) -> None:
        """退出统计帧（不清屏，保留面板）。"""
        with self._lock:
            lines = [
                "",
                "╔══════ 会议结束统计 ══════",
                f"  段落: {len(state.segments)}",
                f"  关键要点: {len(state.key_points)}",
                f"  决策点: {len(state.decisions)}",
                f"  风险: {len(state.risks)}",
                f"  待解决: {len(state.open_questions)}",
                f"  积压丢弃: {state.dropped_count}",
                f"  会议总结: {'✅ 已生成' if state.summary else '— 未生成（失败或未开启）'}",
                f"  过程文档: {doc_path}",
                "════════════════════════════",
                "",
            ]
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

    def _build(self, d: PanelDisplay) -> str:
        state = d.state
        seg_count = len(state.segments) if state else 0
        dropped = state.dropped_count if state else 0
        drop_note = f"（积压丢弃 {dropped} 段）" if dropped else ""
        lines = [
            f"╔═══ 实时会议助理 · 已处理 {seg_count} 段{drop_note} · {d.status} ═══",
        ]
        if d.seg is not None:
            text = d.seg.corrected_text or d.seg.raw_text
            lines.append(f"  [{d.seg.started_at:%H:%M:%S}] 校正文本：{text}")
            lines.append("  ── 本段分析 ──")
            if d.seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
                lines.append("  ⏭ 短反馈/快速模式，跳过分析")
            elif d.analysis_unavailable:
                lines.append("  ⚠ 分析不可用（LLM 调用失败或超时），已显示词典校正原文")
            elif d.seg.analysis is not None and d.seg.analysis.has_content:
                a = d.seg.analysis
                if a.key_points:
                    lines.append("  ✦ 要点：" + "；".join(a.key_points))
                if a.decisions:
                    lines.append("  ✔ 决策：" + "；".join(a.decisions))
                if a.risks:
                    lines.append("  ⚠ 风险：" + "；".join(a.risks))
                if a.questions:
                    lines.append("  ❓ 问题：" + "；".join(a.questions))
                if a.suggested_questions:
                    lines.append("  💡 建议提问：" + "；".join(a.suggested_questions))
            else:
                lines.append("  … 分析中")
        else:
            lines.append("  等待语音…（按住 vocotype 热键说话，松开即分析）")
        if state is not None:
            lines.append("  ── 会议累计（实时）──")
            if state.key_points:
                lines.append("  ✦ 要点(" + str(len(state.key_points)) + ")：" + "；".join(state.key_points[:5]))
            if state.decisions:
                lines.append("  ✔ 决策(" + str(len(state.decisions)) + ")：" + "；".join(state.decisions[:5]))
            if state.risks:
                lines.append("  ⚠ 风险(" + str(len(state.risks)) + ")：" + "；".join(state.risks[:5]))
            if state.open_questions:
                lines.append("  ❓ 待解决(" + str(len(state.open_questions)) + ")：" + "；".join(state.open_questions[:5]))
        lines.append("  ────────────────────────────────────────")
        lines.append("  Ctrl+C 退出")
        return "\n".join(lines) + "\n"
