"""面板视觉主题：两套 ANSI 256 色配色（dark / light），整帧全区填充。

设计原则：
- 底色 = 面板色，整帧填充形成「控制台仪表盘」沉浸观感
- 语义色贯穿：决策✅绿 / 提议💬黄 / 待定❓灰 / 风险⚠橙 / 冲突🔥红 /
  话题📌青 / 待办📋蓝 / 说话人🗣紫 / 建议提问💡亮黄 / 告警红底亮黄字
- 256 色而非 TrueColor：兼容 macOS Terminal.app / iTerm2 / Warp
"""

from __future__ import annotations

from dataclasses import dataclass

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


def _fg(code: int) -> str:
    """256 色前景。"""
    return f"\033[38;5;{code}m"


def _bg(code: int) -> str:
    """256 色背景。"""
    return f"\033[48;5;{code}m"


@dataclass(frozen=True)
class Theme:
    """一套面板配色（256 色编号）。"""

    name: str
    # 底色
    bg: int                # 面板底色（整帧填充）
    bg_alert: int          # 告警行背景（深红底）
    # 文本
    fg_text: int           # 主文本（语音内容/正文）
    fg_dim: int            # 次要文本（状态后缀/操作提示/路径）
    fg_border: int         # 框线 / 分割条
    # 语义色
    fg_title: int          # 标题高亮（会议标题）
    fg_topic: int          # 话题 📌
    fg_ok: int             # 决策确认 ✅ / 要点 ✦ / VU 低电平
    fg_proposed: int       # 决策提议 💬
    fg_tentative: int      # 决策待定 ❓
    fg_risk: int           # 风险 ⚠ / VU 中电平
    fg_conflict: int       # 冲突 🔥 / VU 高电平
    fg_todo: int           # 待办 📋 / 问题 ❓
    fg_speaker: int        # 说话人 🗣
    fg_suggest: int        # 建议提问 💡
    fg_alert: int          # 告警文字（亮黄）

    # ── 样式组装 ──────────────────────────────────────────

    def style(self, text: str, *, fg: int = None, bg: int = None,
              bold: bool = False, dim: bool = False) -> str:
        """给文本包裹 ANSI 样式。fg/bg 为 None 时用默认（主文本色/面板底色）。"""
        fg = self.fg_text if fg is None else fg
        bg = self.bg if bg is None else bg
        prefix = _bg(bg) + _fg(fg)
        if bold:
            prefix += _BOLD
        if dim:
            prefix += _DIM
        return f"{prefix}{text}{_RESET}"

    def vu_color(self, level: float) -> int:
        """VU 电平条颜色：低绿 → 中黄 → 高红。"""
        if level >= 0.7:
            return self.fg_conflict
        if level >= 0.35:
            return self.fg_risk
        return self.fg_ok


DARK = Theme(
    name="dark",
    bg=235, bg_alert=52,
    fg_text=252, fg_dim=244, fg_border=239,
    fg_title=81, fg_topic=81,
    fg_ok=114, fg_proposed=220, fg_tentative=247,
    fg_risk=215, fg_conflict=203,
    fg_todo=111, fg_speaker=177, fg_suggest=228,
    fg_alert=229,
)

LIGHT = Theme(
    name="light",
    bg=254, bg_alert=224,
    fg_text=236, fg_dim=240, fg_border=244,
    fg_title=25, fg_topic=24,
    fg_ok=28, fg_proposed=94, fg_tentative=241,
    fg_risk=130, fg_conflict=124,
    fg_todo=25, fg_speaker=90, fg_suggest=100,
    fg_alert=124,
)

THEMES: dict[str, Theme] = {"dark": DARK, "light": LIGHT}
