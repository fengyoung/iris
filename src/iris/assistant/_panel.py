"""终端面板渲染：ANSI 清屏 + 固定分区整帧绘制（无第三方依赖，stdout 独占）。

v3.24.3 面板重设计 —盒式布局 + 紧凑信息层级：
- 顶部：盒框 + 紧凑标题行（段数/丢弃数）
- 中部：语音文本突出展示 + 分析结果紧凑内联
- 底部：累计统计一行 + 操作提示
- 高亮：ANSI 粗体标题 + 暗色分隔线
"""

from __future__ import annotations

import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .models import MeetingState, VoiceSegment

_CLEAR = "\033[2J\033[H"  # 清屏 + 光标归位
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"

# 盒宽：自适应终端宽度（最小 48，最大 80）
_BOX_WIDTH = min(80, max(48, shutil.get_terminal_size().columns - 2))


@dataclass
class PanelDisplay:
    """一帧面板的数据：状态行 + 当前段 + 分析占位 + 会议状态。"""

    status: str = ""                        # 如 "分析中…" / "已处理"
    seg: Optional[VoiceSegment] = None
    analysis_unavailable: bool = False      # LLM 降级标记
    state: Optional[MeetingState] = None


def _wrap(text: str, width: int) -> list[str]:
    """中文友好的文本折行（textwrap.wrap 对 CJK 宽度计算不准，手动处理）。"""
    lines = []
    while len(text) > width:
        # 在宽度处查找最近的空格/标点作为断点
        cut = width
        for i in range(width, max(0, width - 20), -1):
            if text[i] in " ，。、；：！？\n-,.;:!?":
                cut = i + 1
                break
        lines.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        lines.append(text)
    return lines


def _fill_line(text: str, width: int, *, pad: str = " ") -> str:
    """填充文本到指定宽度（左右留边距）。"""
    return pad + text.ljust(width - 2) + pad


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


class PanelRenderer:
    """整帧渲染；内部锁保证多线程下帧内容不交错。日志走 stderr 互不污染。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._w = _BOX_WIDTH

    # ── 公开接口 ──────────────────────────────────────────

    def render(self, display: PanelDisplay) -> None:
        with self._lock:
            sys.stdout.write(_CLEAR + self._build(display))
            sys.stdout.flush()

    def render_final(self, state: MeetingState, doc_path: Path) -> None:
        """退出统计帧（不清屏，保留面板历史输出）。"""
        analyzed = [s for s in state.segments
                    if s.analysis_started_at and s.analysis_done_at]
        total_elapsed = sum(s.analysis_done_at - s.analysis_started_at for s in analyzed)
        avg_elapsed = total_elapsed / len(analyzed) if analyzed else 0
        w = self._w

        lines = [
            "",
            f"╔{'═' * (w - 2)}╗",
            _fill_line(_bold("会议结束统计"), w),
            _fill_line("", w),
        ]
        lines.append(_fill_line(
            f"段落 {len(state.segments)}  ·  LLM 分析 {len(analyzed)} 次"
            f"  ·  总耗时 {total_elapsed:.1f}s  ·  平均 {avg_elapsed:.1f}s", w))
        lines.append(_fill_line(
            f"要点 {len(state.key_points)}  ·  决策 {len(state.decisions)}"
            f"  ·  风险 {len(state.risks)}  ·  待解决 {len(state.open_questions)}", w))
        if state.dropped_count:
            lines.append(_fill_line(f"积压丢弃 {state.dropped_count} 段", w))
        lines.append(_fill_line(
            f"会议总结 {'✅ 已生成' if state.summary else '— 未生成'}", w))
        lines.append(_fill_line(_dim(str(doc_path)), w))
        lines.append(f"╚{'═' * (w - 2)}╝")
        lines.append("")
        with self._lock:
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

    # ── 帧渲染 ────────────────────────────────────────────

    def _build(self, d: PanelDisplay) -> str:
        w = self._w
        cw = w - 4  # 内容宽度
        state = d.state
        seg_count = len(state.segments) if state else 0
        dropped = state.dropped_count if state else 0

        # 标题行
        if seg_count == 0:
            title = "实时会议助理 · 等待语音…"
        else:
            drop_part = f" · 丢 {dropped}" if dropped else ""
            title = f"实时会议助理 · {seg_count} 段{drop_part}"
        # 视觉填充到盒宽
        pad_right = w - 2 - len(title) - 2  # 2 for ╔╗
        if pad_right > 0:
            title = f"╔{'═' * (pad_right // 2)} {title} {'═' * (pad_right - pad_right // 2)}╗"
        else:
            title = f"╔{'═' * 2} {title} ═╗"

        lines = [title]

        if d.seg is not None:
            text = d.seg.corrected_text or d.seg.raw_text
            timestamp = d.seg.started_at.strftime("%H:%M:%S")
            char_count = len(text)

            # 状态后缀
            suffix_parts = [f"{timestamp} · {char_count} 字"]
            if d.seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
                suffix_parts.append("⏭ 跳过分析")
            elif d.analysis_unavailable:
                suffix_parts.append("⚠ 分析不可用")
            elif d.seg.analysis is None:
                suffix_parts.append("⏳ 分析中…")
            else:
                suffix_parts.append(d.status)

            lines.append(_fill_line("", w))  # 空行

            # 语音文本（折行处理）
            for line in _wrap(text, cw):
                lines.append(_fill_line(f"💬 {line}", w))
            lines.append(_fill_line(_dim(" ── " + " · ".join(suffix_parts)), w))

            # 分析结果
            if d.seg.analysis is not None and d.seg.analysis.has_content:
                a = d.seg.analysis
                # 要点/决策/风险/问题 — 紧凑内联
                tags = []
                if a.key_points:
                    tags.append("✦ " + " · ".join(a.key_points[:3]))
                if a.decisions:
                    tags.append("✔ " + " · ".join(a.decisions[:3]))
                if a.risks:
                    tags.append("⚠ " + " · ".join(a.risks[:2]))
                if a.questions:
                    tags.append("❓ " + " · ".join(a.questions[:2]))
                if tags:
                    combined = "  ".join(tags)
                    for line in _wrap(combined, cw):
                        lines.append(_fill_line(line, w))
                # 建议提问 — 单独一行高亮
                if a.suggested_questions:
                    sq = "💡 追问：" + "  ·  ".join(a.suggested_questions[:3])
                    for line in _wrap(sq, cw):
                        lines.append(_fill_line(line, w))
            elif d.seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
                pass  # 跳过：不额外输出
            elif d.analysis_unavailable:
                lines.append(_fill_line("⚠ LLM 调用失败或超时，已显示词典校正原文", w))
            elif d.seg.analysis is None:
                pass  # 分析中：只显示文本
        else:
            # 等待语音
            lines.append(_fill_line("", w))
            lines.append(_fill_line("正在聆听…（说完自动识别，Ctrl+C 退出）", w))

        # 累计统计行（紧凑一行）
        lines.append(_fill_line("", w))
        lines.append(_fill_line("─" * cw, w))
        if state is not None:
            cum_parts = [
                f"✦{len(state.key_points)}",
                f"✔{len(state.decisions)}",
                f"⚠{len(state.risks)}",
                f"❓{len(state.open_questions)}",
            ]
            footer = "累计 " + "  ".join(cum_parts)
            # 右侧放操作提示
            hint = "Ctrl+C 退出"
            spaces = cw - len(footer) - len(hint) - 2
            if spaces > 0:
                footer = footer + " " * spaces + hint
            lines.append(_fill_line(_dim(footer), w))
        else:
            lines.append(_fill_line(_dim("Ctrl+C 退出"), w))
        lines.append(f"╚{'═' * (w - 2)}╝")

        return "\n".join(lines) + "\n"
