"""终端面板渲染：ANSI 清屏 + 固定分区整帧绘制（无第三方依赖，stdout 独占）。

v3.24.3 面板重设计 —盒式布局 + 紧凑信息层级。
v3.26.2 双主题视觉方案（dark/light，整帧全区填充）：
- 底色 = 面板色，整帧填充形成「控制台仪表盘」沉浸观感
- 语义色：决策✅绿 / 提议💬黄 / 待定❓灰 / 风险⚠橙 / 冲突🔥红 /
  话题📌青 / 待办📋蓝 / 说话人🗣紫 / 建议提问💡亮黄 / 告警红底亮黄字
- 宽度计算在纯文本上进行，ANSI 包裹在填充之后（避免转义序列破坏布局）
"""

from __future__ import annotations

import re
import shutil
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from ._theme import DARK, THEMES, Theme
from .models import MeetingState, VoiceSegment

_CLEAR = "\033[2J\033[H"  # 清屏 + 光标归位

# 盒宽：自适应终端宽度（最小 48，最大 80）——每帧动态计算，适应终端缩放

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _box_width() -> int:
    """当前终端宽度（每帧重算，适应 SIGWINCH 缩放）。"""
    return min(80, max(48, shutil.get_terminal_size().columns - 2))


def _display_width(text: str) -> int:
    """计算纯文本的终端显示宽度（CJK 字符占 2 列，ASCII 占 1 列）。"""
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


def _display_width_ansi(text: str) -> int:
    """计算可能含 ANSI 序列的字符串显示宽度（剥离转义序列）。"""
    return _display_width(_ANSI_RE.sub("", text))


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
    # v3.26.1 分析进度指示
    analysis_phase: str = ""                # prefetch / analyze / suggest
    analysis_elapsed: float = 0.0           # 本轮分析已耗时（秒）
    # v3.26.1 音频电平
    rms_level: float = 0.0                 # 当前 RMS 相对阈值（0-1）
    # v3.26.1 系统告警
    alerts: list[str] = None                # 面板级告警消息列表


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


def _fill_line(text: str, width: int, *, theme: Theme, fg: int = None,
               bg: int = None, bold: bool = False, dim: bool = False,
               colored: bool = False) -> str:
    """CJK 感知的填充到指定显示宽度（左右留边距），并包裹主题色。

    text 默认须为纯文本（宽度按文本计算，填充后统一包裹 ANSI）；
    colored=True 时 text 已含 ANSI（宽度按剥离转义序列计算），
    仅用于补足背景色——空白填充同样带底色。
    """
    if colored:
        inner = text + " " * max(0, width - 2 - _display_width_ansi(text))
        return theme.style(inner, fg=fg, bg=bg)
    inner = " " + _ljust_cjk(text, width - 2) + " "
    return theme.style(inner, fg=fg, bg=bg, bold=bold, dim=dim)


# 洞察事件 → 语义色映射
_EVENT_FG = {
    "decision_confirmed": "fg_ok",
    "decision_proposed": "fg_proposed",
    "topic_change": "fg_topic",
    "risk": "fg_risk",
    "conflict": "fg_conflict",
    "todo": "fg_todo",
    "speaker_turn": "fg_speaker",
}

# 决策置信度 → 语义色
_DECISION_FG = {
    "confirmed": "fg_ok",
    "proposed": "fg_proposed",
    "tentative": "fg_tentative",
}

_CONF_ICON = {"confirmed": "✅", "proposed": "💬", "tentative": "❓"}


class PanelRenderer:
    """整帧渲染 + 洞察推送区；内部锁保证多线程下帧内容不交错。

    v3.26.1: 宽度每帧动态计算（_box_width()），适应终端缩放。
    v3.26.2: 双主题（dark/light），theme 非法时回退 dark。
    """

    def __init__(self, theme: str = "dark") -> None:
        self._lock = threading.Lock()
        self._theme = THEMES.get(theme, DARK)

    @property
    def theme(self) -> Theme:
        return self._theme

    # ── 公开接口 ──────────────────────────────────────────

    def render(self, display: PanelDisplay, feed: object = None) -> None:
        with self._lock:
            sys.stdout.write(_CLEAR + self._build(display, feed))
            sys.stdout.flush()

    def render_final(self, state: MeetingState, doc_path: Path) -> None:
        """退出统计帧（不清屏，保留面板历史输出；整帧全区填充）。"""
        analyzed = [s for s in state.segments
                    if s.analysis_started_at and s.analysis_done_at]
        total_elapsed = sum(s.analysis_done_at - s.analysis_started_at for s in analyzed)
        avg_elapsed = total_elapsed / len(analyzed) if analyzed else 0
        w = _box_width()
        t = self._theme

        lines = [
            "",
            t.style(f"╔{'═' * (w - 2)}╗", fg=t.fg_border),
            _fill_line("会议结束统计", w, theme=t, fg=t.fg_title, bold=True),
            _fill_line("", w, theme=t),
        ]
        lines.append(_fill_line(
            f"段落 {len(state.segments)}  ·  LLM 分析 {len(analyzed)} 次"
            f"  ·  总耗时 {total_elapsed:.1f}s  ·  平均 {avg_elapsed:.1f}s",
            w, theme=t))
        lines.append(_fill_line(
            f"要点 {len(state.key_points)}  ·  决策 {len(state.decisions)}"
            f"  ·  风险 {len(state.risks)}  ·  待解决 {len(state.open_questions)}",
            w, theme=t))
        if state.dropped_count:
            lines.append(_fill_line(
                f"积压丢弃 {state.dropped_count} 段", w, theme=t, fg=t.fg_risk))
        if state.speakers:
            sp_parts = [f'{s["id"]}({s["segments"]}段)' for s in state.speakers[:5]]
            lines.append(_fill_line(
                "发言：" + " · ".join(sp_parts), w, theme=t, fg=t.fg_speaker))
        summary_line = (
            f"会议总结 {'✅ 已生成' if state.summary else '— 未生成'}")
        lines.append(_fill_line(
            summary_line, w, theme=t,
            fg=t.fg_ok if state.summary else t.fg_tentative))
        lines.append(_fill_line(str(doc_path), w, theme=t, fg=t.fg_dim, dim=True))
        lines.append(t.style(f"╚{'═' * (w - 2)}╝", fg=t.fg_border))
        lines.append("")
        with self._lock:
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

    # ── 帧渲染 ────────────────────────────────────────────

    def _build(self, d: PanelDisplay, feed: object = None) -> str:
        w = _box_width()
        cw = w - 4  # 内容宽度
        t = self._theme
        state = d.state
        seg_count = len(state.segments) if state else 0
        dropped = state.dropped_count if state else 0

        # 标题行（纯文本计算宽度 → 分段包裹语义色）—— v3.26.2 彩色
        topic_label = f"📌 {d.topic} · " if d.topic else ""
        # v3.26.1: 会议时长
        elapsed_str = ""
        if state and seg_count > 0:
            elapsed = (datetime.now() - state.started_at).total_seconds()
            if elapsed >= 60:
                elapsed_str = f" · {int(elapsed // 60)}分"
            else:
                elapsed_str = f" · {int(elapsed)}秒"
        if seg_count == 0:
            title_text = f"{topic_label}实时会议助理 · 等待语音…"
        else:
            drop_part = f" · 丢 {dropped}" if dropped else ""
            title_text = f"{topic_label}实时会议助理 · {seg_count} 段{elapsed_str}{drop_part}"
        title_dw = _display_width(title_text)
        pad_right = w - 2 - title_dw - 2  # ╔╗ + 两边空格
        if pad_right > 0:
            b_l = "═" * (pad_right // 2)
            b_r = "═" * (pad_right - pad_right // 2)
        else:
            b_l = b_r = ""
        # 分段包裹：边框 FG_BORDER · 话题 FG_TOPIC · 标题 FG_TITLE(bold)
        title = (
            t.style(f"╔{b_l} ", fg=t.fg_border)
            + (t.style(topic_label, fg=t.fg_topic) if topic_label else "")
            + t.style(title_text[len(topic_label):], fg=t.fg_title, bold=True)
            + t.style(f" {b_r}╗", fg=t.fg_border)
        )

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

            lines.append(_fill_line("", w, theme=t))  # 空行

            # 语音文本（折行处理）—— 亮白主色
            for line in _wrap(text, cw):
                lines.append(_fill_line(f"💬 {line}", w, theme=t, fg=t.fg_text))
            # 状态后缀 —— 暗灰
            lines.append(_fill_line(
                " ── " + " · ".join(suffix_parts), w, theme=t, fg=t.fg_dim))

            # 分析结果（语义色块，v3.26.2）
            if d.seg.analysis is not None and d.seg.analysis.has_content:
                a = d.seg.analysis
                blocks: list[tuple[str, str]] = []  # (纯文本, 颜色字段名)
                if a.key_points:
                    blocks.append((f"✦ {' · '.join(a.key_points[:3])}", "fg_ok"))
                for dec in a.decisions[:3]:
                    icon = _CONF_ICON.get(dec.confidence, "")
                    blocks.append((f"{icon}{dec.text}", _DECISION_FG.get(
                        dec.confidence, "fg_text")))
                if a.risks:
                    blocks.append((f"⚠ {' · '.join(a.risks[:2])}", "fg_risk"))
                if a.questions:
                    blocks.append((f"❓ {' · '.join(a.questions[:2])}", "fg_todo"))
                if blocks:
                    combined_plain = "  ".join(txt for txt, _ in blocks)
                    if _display_width(combined_plain) <= cw:
                        # 全部语义色块可容纳 → 逐块着色拼接
                        colored = "  ".join(
                            t.style(txt, fg=getattr(t, fg_field))
                            for txt, fg_field in blocks
                        )
                        lines.append(_fill_line(colored, w, theme=t,
                                                fg=t.fg_text, colored=True))
                    else:
                        # 超宽 → 降级纯文本折行（保证布局正确）
                        for line in _wrap(combined_plain, cw):
                            lines.append(_fill_line(line, w, theme=t))
                # 建议提问 — 间隔线 + 亮黄高亮
                if a.suggested_questions:
                    lines.append(_fill_line(
                        " ── 追问 ──", w, theme=t, fg=t.fg_border))
                    sq = "💡 " + "  ·  ".join(a.suggested_questions[:3])
                    for line in _wrap(sq, cw):
                        lines.append(_fill_line(line, w, theme=t, fg=t.fg_suggest))
            elif d.seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
                pass  # 跳过：不额外输出
            elif d.analysis_unavailable:
                lines.append(_fill_line(
                    "⚠ LLM 调用失败或超时，已显示词典校正原文",
                    w, theme=t, fg=t.fg_risk))
            elif d.seg.analysis is None:
                # v3.26.1 分析进度指示：显示当前阶段 + 耗时
                if d.analysis_phase:
                    phase_labels = {
                        "prefetch": "🔍 校正+检索",
                        "analyze": "🤖 LLM 分析",
                        "suggest": "💡 建议生成",
                    }
                    label = phase_labels.get(d.analysis_phase, d.analysis_phase)
                    elapsed_str = f" ({d.analysis_elapsed:.0f}s)" if d.analysis_elapsed > 0 else ""
                    lines.append(_fill_line(
                        f"⏳ {label}{elapsed_str}…", w, theme=t, fg=t.fg_todo))
        else:
            # 等待语音
            lines.append(_fill_line("", w, theme=t))
            lines.append(_fill_line(
                "正在聆听…（说完自动识别，Ctrl+C 退出）", w, theme=t))

        # ── 洞察推送区（v3.25.3 / v3.26.2 按事件类型着色）──
        if feed is not None and not feed.empty:
            lines.append(_fill_line("", w, theme=t))
            lines.append(_fill_line(
                "─" * 4 + " 洞察推送 " + "─" * (cw - 10), w,
                theme=t, fg=t.fg_border))
            for evt in feed.visible:
                icon = evt.TYPE_ICONS.get(evt.event_type, "🔔")
                fg_field = _EVENT_FG.get(evt.event_type, "fg_text")
                colored = (
                    t.style(f"🔔 {evt.timestamp} ", fg=t.fg_dim)
                    + t.style(f"{icon}  ", fg=getattr(t, fg_field))
                    + t.style(evt.text, fg=t.fg_text)
                )
                lines.append(_fill_line(colored, w, theme=t,
                                        fg=t.fg_text, colored=True))

        # ── 系统告警区（v3.26.1 / v3.26.2 红底亮黄字）──
        if d.alerts:
            for alert in d.alerts:
                lines.append(_fill_line(
                    f"⚠ {alert}", w, theme=t,
                    bg=t.bg_alert, fg=t.fg_alert, bold=True))

        # ── 音频电平指示器（v3.26.1 / v3.26.2 渐变色）──
        if d.rms_level > 0:
            bar_width = max(4, cw - 6)
            filled = int(d.rms_level * bar_width)
            vu_color = t.vu_color(d.rms_level)
            colored = (
                t.style("🎤 ", fg=t.fg_dim)
                + t.style("█" * filled, fg=vu_color)
                + t.style("░" * (bar_width - filled), fg=t.fg_border)
            )
            lines.append(_fill_line(colored, w, theme=t,
                                    fg=t.fg_text, colored=True))

        # 累计统计行（紧凑一行，语义色小图标 + 操作提示）
        lines.append(_fill_line("", w, theme=t))
        lines.append(_fill_line("─" * cw, w, theme=t, fg=t.fg_border))
        if state is not None:
            cum_blocks = [
                (f"✦{len(state.key_points)}", "fg_ok"),
                (f"✔{len(state.decisions)}", "fg_ok"),
                (f"⚠{len(state.risks)}", "fg_risk"),
                (f"❓{len(state.open_questions)}", "fg_todo"),
            ]
            cum_colored = "  ".join(
                t.style(f" {txt}", fg=getattr(t, fld)) for txt, fld in cum_blocks
            )
            hint = "Ctrl+C 退出"
            spaces = cw - sum(_display_width(txt) + 2 for txt, _ in cum_blocks) - len(hint) - 2
            if spaces > 0:
                footer = (
                    t.style("累计", fg=t.fg_dim)
                    + cum_colored
                    + " " * spaces
                    + t.style(hint, fg=t.fg_dim)
                )
            else:
                footer = t.style("累计", fg=t.fg_dim) + cum_colored
            lines.append(_fill_line(footer, w, theme=t, fg=t.fg_text, colored=True))
        else:
            lines.append(_fill_line("Ctrl+C 退出", w, theme=t, fg=t.fg_dim))
        lines.append(t.style(f"╚{'═' * (w - 2)}╝", fg=t.fg_border))

        return "\n".join(lines) + "\n"
