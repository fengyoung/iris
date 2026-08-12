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


def _display_width(text: str) -> int:
    """计算字符串的终端显示宽度（CJK 字符占 2 列，ASCII 占 1 列）。"""
    w = 0
    for ch in text:
        cp = ord(ch)
        # CJK 统一表意文字 + 全角符号 + 中文标点
        if (0x1100 <= cp <= 0x115F or    # Hangul Jamo
            0x2E80 <= cp <= 0xA4CF or    # CJK Radicals ~ Yi
            0xA960 <= cp <= 0xA97C or    # Hangul
            0xAC00 <= cp <= 0xD7A3 or    # Hangul Syllables
            0xF900 <= cp <= 0xFAFF or    # CJK Compatibility
            0xFE10 <= cp <= 0xFE19 or    # Vertical forms
            0xFE30 <= cp <= 0xFE6F or    # CJK Compatibility Forms
            0xFF01 <= cp <= 0xFF60 or    # Fullwidth Forms
            0xFFE0 <= cp <= 0xFFE6 or    # Fullwidth Signs
            0x1F300 <= cp <= 0x1F64F or  # Emoticons
            0x20000 <= cp <= 0x2FFFF or  # CJK Extension
            0x30000 <= cp <= 0x3FFFF):   # CJK Extension
            w += 2
        else:
            w += 1
    return w


def _ljust_cjk(text: str, width: int) -> str:
    """CJK 感知的左对齐填充（用空格补足到终端显示宽度 width）。"""
    need = width - _display_width(text)
    return text + " " * max(0, need)


@dataclass
class PanelDisplay:
    """一帧面板的数据：状态行 + 当前段 + 分析占位 + 会议状态。"""

    status: str = ""                        # 如 "分析中…" / "已处理"
    seg: Optional[VoiceSegment] = None
    analysis_unavailable: bool = False      # LLM 降级标记
    state: Optional[MeetingState] = None
    topic: str = ""                         # v3.25.3 当前话题标签


def _wrap(text: str, width: int) -> list[str]:
    """CJK 感知的文本折行。width 为显示列数。"""
    lines = []
    while _display_width(text) > width:
        # 累积显示宽度找到断点
        w = 0
        cut = 0
        for i, ch in enumerate(text):
            dw = 2 if _display_width(ch) == 2 else 1  # noqa: PLR2004
            if w + dw > width:
                cut = i
                break
            w += dw
        else:
            cut = len(text)
        # 回退到最近的空格/标点
        for i in range(min(cut, len(text) - 1), max(0, cut - 12), -1):
            if text[i] in " ，。、；：！？\n-,.;:!?":
                cut = i + 1
                break
        lines.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        lines.append(text)
    return lines


def _fill_line(text: str, width: int, *, pad: str = " ") -> str:
    """CJK 感知的填充到指定显示宽度（左右留边距）。"""
    return pad + _ljust_cjk(text, width - 2) + pad


def _dim(text: str) -> str:
    return f"{_DIM}{text}{_RESET}"


def _bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


class PanelRenderer:
    """整帧渲染 + 洞察推送区；内部锁保证多线程下帧内容不交错。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._w = _BOX_WIDTH

    # ── 公开接口 ──────────────────────────────────────────

    def render(self, display: PanelDisplay, feed: object = None) -> None:
        with self._lock:
            sys.stdout.write(_CLEAR + self._build(display, feed))
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
        if state.speakers:
            sp_parts = [f'{s["id"]}({s["segments"]}段)' for s in state.speakers[:5]]
            lines.append(_fill_line("发言：" + " · ".join(sp_parts), w))
        lines.append(_fill_line(
            f"会议总结 {'✅ 已生成' if state.summary else '— 未生成'}", w))
        lines.append(_fill_line(_dim(str(doc_path)), w))
        lines.append(f"╚{'═' * (w - 2)}╝")
        lines.append("")
        with self._lock:
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

    # ── 帧渲染 ────────────────────────────────────────────

    def _build(self, d: PanelDisplay, feed: object = None) -> str:
        w = self._w
        cw = w - 4  # 内容宽度
        state = d.state
        seg_count = len(state.segments) if state else 0
        dropped = state.dropped_count if state else 0

        # 标题行（CJK 感知宽度）—— v3.25.3 话题标签
        topic_label = f"📌 {d.topic} · " if d.topic else ""
        if seg_count == 0:
            title = f"{topic_label}实时会议助理 · 等待语音…"
        else:
            drop_part = f" · 丢 {dropped}" if dropped else ""
            title = f"{topic_label}实时会议助理 · {seg_count} 段{drop_part}"
        title_dw = _display_width(title)
        pad_right = w - 2 - title_dw - 2  # ╔╗ + 两边空格
        if pad_right > 0:
            title = f"╔{'═' * (pad_right // 2)} {title} {'═' * (pad_right - pad_right // 2)}╗"
        else:
            title = f"╔═ {title} ═╗"

        lines = [title]

        if d.seg is not None:
            text = d.seg.corrected_text or d.seg.raw_text
            timestamp = d.seg.started_at.strftime("%H:%M:%S")
            char_count = len(text)

            # 状态后缀
            suffix_parts = [f"{timestamp} · {char_count} 字"]
            if d.seg.speaker and d.seg.speaker.speaker_id:
                suffix_parts.append(d.seg.speaker.speaker_id)
            if d.seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
                suffix_parts.append("⏭ 跳过分析")
            elif d.seg.analysis_status == VoiceSegment.ANALYSIS_MERGED:
                suffix_parts.append("🔗 合并分析")
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
                    conf_icon = {"confirmed": "✅", "proposed": "💬", "tentative": "❓"}
                    d_parts = [f"{conf_icon.get(d.confidence, '')}{d.text}" for d in a.decisions[:3]]
                    tags.append(" · ".join(d_parts))
                if a.risks:
                    tags.append("⚠ " + " · ".join(a.risks[:2]))
                if a.questions:
                    tags.append("❓ " + " · ".join(a.questions[:2]))
                if tags:
                    combined = "  ".join(tags)
                    for line in _wrap(combined, cw):
                        lines.append(_fill_line(line, w))
                # 建议提问 — 间隔线 + 高亮
                if a.suggested_questions:
                    lines.append(_fill_line(_dim(" ── 追问 ──"), w))
                    sq = "💡 " + "  ·  ".join(a.suggested_questions[:3])
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

        # ── 洞察推送区（v3.25.3）──
        if feed is not None and not feed.empty:
            lines.append(_fill_line("", w))
            lines.append(_fill_line("─" * 4 + " 洞察推送 " + "─" * (cw - 10), w))
            for evt_line in feed.render_lines(cw):
                lines.append(_fill_line(evt_line, w))

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
