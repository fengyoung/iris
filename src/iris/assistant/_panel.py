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
from .models import CONF_ICON, DECISION_FG, MeetingState, VoiceSegment

_CLEAR = "\033[2J\033[H"  # 清屏 + 光标归位
_ENTER_ALT = "\033[?1049h"  # 进入 alternate screen（保留回滚历史）
_EXIT_ALT = "\033[?1049l"   # 退出 alternate screen

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
    """CJK 感知的文本折行。width 为显示列数。

    v3.26.3: 预计算字符宽度数组，避免每行重复扫描整段文本（O(n²)→O(n)）。
    """
    # 预计算每个字符的显示宽度（只算一次）
    char_widths = [_display_width(ch) for ch in text]
    lines = []
    pos = 0
    n = len(text)
    while pos < n:
        # 剩余字符总宽度
        remaining_w = sum(char_widths[pos:])
        if remaining_w <= width:
            lines.append(text[pos:].rstrip())
            break
        # 累积显示宽度找到断点
        w = 0
        cut = pos
        for i in range(pos, n):
            w += char_widths[i]
            if w > width:
                cut = i
                break
        else:
            cut = n
        # 回退到最近的空格/标点（在 cut-12..cut 范围内）
        for i in range(min(cut, n - 1), max(pos, cut - 12), -1):
            if text[i] in " ，。、；：！？\n-,.;:!?":
                cut = i + 1
                break
        lines.append(text[pos:cut].rstrip())
        # 跳过前导空白
        pos = cut
        while pos < n and text[pos] in " \t":
            pos += 1
    return lines


def _fill_section(lines: list[str], target: int, width: int, theme: Theme) -> list[str]:
    """用空行填充到 target 高度，使区域高度固定。

    少于 target → 底部补空行（带背景色）；等于或多于 target → 原样返回。
    这样面板在绝大多数情况下高度一致，只有内容异常多时才突破。
    """
    result = list(lines)
    while len(result) < target:
        result.append(_fill_line("", width, theme=theme))
    return result


# ── 面板各区域固高（v3.26.3 固高布局：空行填充，大概率不变）──
_VOICE_HEIGHT = 3       # 语音文本区
_ANALYSIS_HEIGHT = 2    # 分析结果区
_SUGGEST_HEIGHT = 2     # 建议提问区（含分隔线）
_FEED_HEIGHT = 4        # 洞察推送区（不含标题行）
_ALERT_HEIGHT = 2       # 系统告警区


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


# 洞察事件 → 语义色映射（与 models.DECISION_FG 正交——事件类型 ≠ 决策置信度）
_EVENT_FG = {
    "decision_confirmed": "fg_ok",
    "decision_proposed": "fg_proposed",
    "topic_change": "fg_topic",
    "risk": "fg_risk",
    "conflict": "fg_conflict",
    "todo": "fg_todo",
    "speaker_turn": "fg_speaker",
}


class PanelRenderer:
    """整帧渲染 + 洞察推送区；内部锁保证多线程下帧内容不交错。

    v3.26.1: 宽度每帧动态计算（_box_width()），适应终端缩放。
    v3.26.2: 双主题（dark/light），theme 非法时回退 dark。
    """

    def __init__(self, theme: str = "dark") -> None:
        self._lock = threading.Lock()
        self._theme = THEMES.get(theme, DARK)
        self._alt_screen_active = False

    @property
    def theme(self) -> Theme:
        return self._theme

    # ── 公开接口 ──────────────────────────────────────────

    def render(self, display: PanelDisplay, feed: object = None) -> None:
        with self._lock:
            if not self._alt_screen_active:
                sys.stdout.write(_ENTER_ALT)
                self._alt_screen_active = True
            sys.stdout.write(_CLEAR + self._build(display, feed))
            sys.stdout.flush()

    def render_final(self, state: MeetingState, doc_path: Path) -> None:
        """退出统计帧（退出 alt-screen，恢复终端回滚历史；整帧全区填充）。"""
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
        # v3.26.3: 退出 alt-screen 恢复终端回滚历史
        lines.append(_EXIT_ALT)
        lines.append("")
        with self._lock:
            self._alt_screen_active = False
            sys.stdout.write("\n".join(lines))
            sys.stdout.flush()

    # ── 帧渲染 ────────────────────────────────────────────

    def _build(self, d: PanelDisplay, feed: object = None) -> str:
        """整帧渲染：标题 → 语音/分析区 → 洞察推送 → 告警 → VU → 累计统计 → 底边。"""
        w = _box_width()
        cw = w - 4  # 内容宽度
        t = self._theme
        state = d.state

        lines = [self._build_title(d, w)]
        if d.seg is not None:
            self._build_segment_block(lines, d, w, cw)
        else:
            self._build_waiting_block(lines, w)
        if feed is not None and not feed.empty:
            self._build_feed_block(lines, feed, w, cw)
        # ── 系统告警区（v3.26.1 / v3.26.2 红底亮黄字）──
        for alert in d.alerts or ():
            # v3.26.3: 长告警文本折行（防止超宽溢出）
            for aline in _wrap(f"⚠ {alert}", cw):
                lines.append(_fill_line(aline, w, theme=t, bg=t.bg_alert, fg=t.fg_alert, bold=True))
        if d.rms_level > 0:
            lines.append(self._build_vu_line(d.rms_level, w, cw))
        # 累计统计行（紧凑一行，语义色小图标 + 操作提示）
        lines.append(_fill_line("", w, theme=t))
        lines.append(_fill_line("─" * cw, w, theme=t, fg=t.fg_border))
        lines.append(self._build_footer(state, w))
        lines.append(t.style(f"╚{'═' * (w - 2)}╝", fg=t.fg_border))
        return "\n".join(lines) + "\n"

    # ---- 各区块 ----

    @staticmethod
    def _elapsed_label(state) -> str:
        """v3.26.1 会议时长：≥60s 显示分，否则显示秒。"""
        elapsed = (datetime.now() - state.started_at).total_seconds()
        return f" · {int(elapsed // 60)}分" if elapsed >= 60 else f" · {int(elapsed)}秒"

    @staticmethod
    def _truncate_to_width(text: str, max_w: int) -> str:
        """按显示宽度截断并加 "…"（v3.26.3 标题过长防边框断裂）。"""
        out, tw = "", 0
        for ch in text:
            dw = 2 if _display_width(ch) == 2 else 1
            if tw + dw > max_w:
                break
            out += ch
            tw += dw
        return out + "…"

    def _build_title(self, d: PanelDisplay, w: int) -> str:
        """标题行（纯文本计算宽度 → 分段包裹语义色）—— v3.26.2 彩色。"""
        t = self._theme
        state = d.state
        seg_count = len(state.segments) if state else 0
        dropped = state.dropped_count if state else 0
        topic_label = f"📌 {d.topic} · " if d.topic else ""
        if seg_count == 0:
            title_text = f"{topic_label}实时会议助理 · 等待语音…"
        else:
            drop_part = f" · 丢 {dropped}" if dropped else ""
            title_text = (f"{topic_label}实时会议助理 · {seg_count} 段"
                          f"{self._elapsed_label(state)}{drop_part}")
        pad_right = w - 2 - _display_width(title_text) - 2  # ╔╗ + 两边空格
        if pad_right > 0:
            b_l = "═" * (pad_right // 2)
            b_r = "═" * (pad_right - pad_right // 2)
        else:
            b_l = b_r = "═"
            title_text = self._truncate_to_width(title_text, w - 6)
        # 分段包裹：边框 FG_BORDER · 话题 FG_TOPIC · 标题 FG_TITLE(bold)
        return (
            t.style(f"╔{b_l} ", fg=t.fg_border)
            + (t.style(topic_label, fg=t.fg_topic) if topic_label else "")
            + t.style(title_text[len(topic_label):], fg=t.fg_title, bold=True)
            + t.style(f" {b_r}╗", fg=t.fg_border)
        )

    @staticmethod
    def _segment_suffix(d: PanelDisplay) -> str:
        """段状态后缀：时间 · 字数 · 说话人 · 分析状态。"""
        seg = d.seg
        text = seg.corrected_text or seg.raw_text
        parts = [f"{seg.started_at.strftime('%H:%M:%S')} · {len(text)} 字"]
        if seg.speaker and seg.speaker.speaker_id:
            parts.append(seg.speaker.speaker_id)
        if seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
            parts.append("⏭ 跳过分析")
        elif seg.analysis_status == VoiceSegment.ANALYSIS_MERGED:
            parts.append("🔗 合并分析")
        elif d.analysis_unavailable:
            parts.append("⚠ 分析不可用")
        elif seg.analysis is None:
            parts.append("⏳ 分析中…")
        else:
            parts.append(d.status)
        return " ── " + " · ".join(parts)

    def _build_segment_block(self, lines: list, d: PanelDisplay, w: int, cw: int) -> None:
        """语音文本（固高 3 行）+ 状态后缀 + 分析结果区。"""
        t = self._theme
        text = d.seg.corrected_text or d.seg.raw_text
        lines.append(_fill_line("", w, theme=t))  # 空行

        # ── 语音文本（固高 3 行，不足补空行，超出截断加 "…"）──
        voice_content = _wrap(text, cw - 2) or [""]  # -2 给 "💬 "
        if len(voice_content) > _VOICE_HEIGHT:
            voice_content = voice_content[:_VOICE_HEIGHT]
            last = voice_content[-1]
            if _display_width(last) + 2 > cw - 2:
                cut = len(last)
                while cut > 0 and _display_width(last[:cut]) + 2 > cw - 2:
                    cut -= 1
                last = last[:cut]
            voice_content[-1] = last.rstrip() + "…"
        for vline in _fill_section(voice_content, _VOICE_HEIGHT, w, t):
            label = f"💬 {vline}" if vline.strip() else ""
            lines.append(_fill_line(label, w, theme=t, fg=t.fg_text))
        # 状态后缀 —— 暗灰
        lines.append(_fill_line(self._segment_suffix(d), w, theme=t, fg=t.fg_dim))

        self._build_analysis_block(lines, d, w, cw)

    def _blank_lines(self, lines: list, n: int, w: int) -> None:
        for _ in range(n):
            lines.append(_fill_line("", w, theme=self._theme))

    def _build_analysis_block(self, lines: list, d: PanelDisplay, w: int, cw: int) -> None:
        """分析结果（语义色块，固高 2 行 + 可选追问 2 行）；无结果时按状态占位。"""
        t = self._theme
        seg = d.seg
        if seg.analysis is not None and seg.analysis.has_content:
            self._build_analysis_content(lines, seg.analysis, w, cw)
        elif seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
            self._blank_lines(lines, _ANALYSIS_HEIGHT, w)
        elif d.analysis_unavailable:
            lines.append(_fill_line("⚠ LLM 调用失败或超时，已显示词典校正原文",
                                    w, theme=t, fg=t.fg_risk))
            self._blank_lines(lines, _ANALYSIS_HEIGHT - 1, w)
        elif seg.analysis is None:
            if d.analysis_phase:
                phase_labels = {
                    "prefetch": "🔍 校正+检索",
                    "analyze": "🤖 LLM 分析",
                    "suggest": "💡 建议生成",
                }
                label = phase_labels.get(d.analysis_phase, d.analysis_phase)
                elapsed_str = f" ({d.analysis_elapsed:.0f}s)" if d.analysis_elapsed > 0 else ""
                lines.append(_fill_line(f"⏳ {label}{elapsed_str}…", w, theme=t, fg=t.fg_todo))
            # 占满固高
            self._blank_lines(lines, _ANALYSIS_HEIGHT - 1, w)

    def _build_analysis_content(self, lines: list, a, w: int, cw: int) -> None:
        """要点/决策/风险/问题 语义色块 + 可选追问。"""
        t = self._theme
        blocks: list[tuple[str, str]] = []
        if a.key_points:
            blocks.append((f"✦ {' · '.join(a.key_points[:3])}", "fg_ok"))
        for dec in a.decisions[:3]:
            icon = CONF_ICON.get(dec.confidence, "")
            blocks.append((f"{icon}{dec.text}", DECISION_FG.get(dec.confidence, "fg_text")))
        if a.risks:
            blocks.append((f"⚠ {' · '.join(a.risks[:2])}", "fg_risk"))
        if a.questions:
            blocks.append((f"❓ {' · '.join(a.questions[:2])}", "fg_todo"))
        if blocks:
            alines = _wrap("  ".join(txt for txt, _ in blocks), cw)
            if len(alines) > _ANALYSIS_HEIGHT:
                alines = alines[:_ANALYSIS_HEIGHT]
                alines[-1] = alines[-1].rstrip() + "…"
            for aline in _fill_section(alines, _ANALYSIS_HEIGHT, w, t):
                lines.append(_fill_line(aline, w, theme=t))
        else:
            self._blank_lines(lines, _ANALYSIS_HEIGHT, w)
        # 建议提问 — 固高（分隔线 + 内容）；无追问时不占位
        if a.suggested_questions:
            lines.append(_fill_line(" ── 追问 ──", w, theme=t, fg=t.fg_border))
            sq_lines = _wrap("💡 " + "  ·  ".join(a.suggested_questions[:3]), cw)
            if len(sq_lines) > _SUGGEST_HEIGHT:
                sq_lines = sq_lines[:_SUGGEST_HEIGHT]
                sq_lines[-1] = sq_lines[-1].rstrip() + "…"
            for line in _fill_section(sq_lines, _SUGGEST_HEIGHT, w, t):
                lines.append(_fill_line(line, w, theme=t, fg=t.fg_suggest))

    def _build_waiting_block(self, lines: list, w: int) -> None:
        """等待语音：占满固高区域，保持后续布局不变。"""
        t = self._theme
        lines.append(_fill_line("", w, theme=t))
        for vline in _fill_section(["正在聆听…（说完自动识别，Ctrl+C 退出）"], _VOICE_HEIGHT, w, t):
            lines.append(_fill_line(vline, w, theme=t))
        self._blank_lines(lines, _ANALYSIS_HEIGHT, w)

    def _build_feed_block(self, lines: list, feed, w: int, cw: int) -> None:
        """洞察推送区（固高 4 行，不足补空行）。"""
        t = self._theme
        lines.append(_fill_line("", w, theme=t))
        feed_header = "─" * 4 + " 洞察推送 " + "─" * (cw - 10)
        lines.append(_fill_line(feed_header, w, theme=t, fg=t.fg_border))
        feed_content: list[str] = []
        for evt in feed.visible:
            icon = evt.TYPE_ICONS.get(evt.event_type, "🔔")
            fg_field = _EVENT_FG.get(evt.event_type, "fg_text")
            prefix = (
                t.style(f"🔔 {evt.timestamp} ", fg=t.fg_dim)
                + t.style(f"{icon}  ", fg=getattr(t, fg_field))
            )
            for i, eline in enumerate(_wrap(evt.text, cw - 6) or [""]):
                head = prefix if i == 0 else t.style(" " * 6, fg=t.fg_dim)
                feed_content.append(head + t.style(eline, fg=t.fg_text))
        # 截断到固高
        if len(feed_content) > _FEED_HEIGHT:
            feed_content = feed_content[:_FEED_HEIGHT]
            feed_content[-1] = _ANSI_RE.sub("", feed_content[-1]).rstrip() + "…"
        # 补空行
        for fline in _fill_section(feed_content, _FEED_HEIGHT, w, t):
            is_colored = bool(_ANSI_RE.search(fline))
            lines.append(_fill_line(fline, w, theme=t, fg=t.fg_text, colored=is_colored))

    def _build_vu_line(self, level: float, w: int, cw: int) -> str:
        """音频电平指示器（v3.26.1 / v3.26.2 渐变色）。"""
        t = self._theme
        bar_width = max(4, cw - 10)
        filled = int(level * bar_width)
        # v3.26.3: emoji 标签提供非颜色冗余信息（色盲友好）
        vu_label = "🔊" if level >= 0.7 else ("🔉" if level >= 0.35 else "🔈")
        colored = (
            t.style(f"{vu_label} ", fg=t.fg_dim)
            + t.style("█" * filled, fg=t.vu_color(level))
            + t.style("░" * (bar_width - filled), fg=t.fg_border)
        )
        return _fill_line(colored, w, theme=t, fg=t.fg_text, colored=True)

    def _build_footer(self, state, w: int) -> str:
        """累计统计 + 操作提示（右对齐）。"""
        t = self._theme
        if state is None:
            return _fill_line("Ctrl+C 退出", w, theme=t, fg=t.fg_dim)
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
        # 计算各部分显示宽度（CJK 用 _display_width，ASCII 用 len）
        # 每个 block: " " + txt，块间 "  "（2 空格）
        cum_total_dw = (
            _display_width("累计")
            + sum(1 + _display_width(txt) for txt, _ in cum_blocks)
            + (len(cum_blocks) - 1) * 2
        )
        # _fill_line(colored=True) 填充到 w-2，可用空间 = w-2 - cum_total - hint
        spaces = max(0, w - 2 - cum_total_dw - _display_width(hint))
        footer = t.style("累计", fg=t.fg_dim) + cum_colored
        if spaces > 0:
            footer += " " * spaces + t.style(hint, fg=t.fg_dim)
        return _fill_line(footer, w, theme=t, fg=t.fg_text, colored=True)
