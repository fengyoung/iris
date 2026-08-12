"""面板渲染单元测试：CJK 宽度 / 文本折行 / 帧渲染 / 左对齐。

纯逻辑测试，无 ANSI 终端依赖——测试 _display_width / _wrap / _ljust_cjk
以及 PanelRenderer._build / render_final 的静态内容组装。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


from iris.assistant._panel import (
    PanelDisplay,
    PanelRenderer,
    _display_width,
    _ljust_cjk,
    _wrap,
)
from iris.assistant.models import MeetingState, SegmentAnalysis, VoiceSegment


# ── TestDisplayWidth ──────────────────────────────────────────

class TestDisplayWidth:
    """CJK 字符显示宽度。"""

    def test_ascii_width(self):
        """纯 ASCII 宽度 = len。"""
        assert _display_width("hello") == 5
        assert _display_width("abc123") == 6

    def test_cjk_width(self):
        """中文字符宽度 = 2 × len。"""
        assert _display_width("测试") == 4
        assert _display_width("实时会议助理") == 12

    def test_mixed_width(self):
        """中英混排宽度计算正确。"""
        assert _display_width("abc测试123") == 3 + 4 + 3  # 10


# ── TestLjustCJK ──────────────────────────────────────────────

class TestLjustCJK:
    """CJK 感知左对齐填充。"""

    def test_padding_correct(self):
        """CJK 文本左对齐填充空格数正确。"""
        result = _ljust_cjk("测试", 10)
        assert result == "测试" + " " * 6  # 显示宽度 4，需 6 空格到 10

    def test_no_padding_when_wider(self):
        """文本已超出宽度时不截断。"""
        result = _ljust_cjk("测试文本内容", 4)
        assert result == "测试文本内容"


# ── TestWrap ──────────────────────────────────────────────────

class TestWrap:
    """CJK 感知文本折行。"""

    def test_short_text_not_wrapped(self):
        """短于宽度的文本不折行。"""
        result = _wrap("简短文本", 20)
        assert result == ["简短文本"]

    def test_long_text_wrapped_at_boundary(self):
        """长文本在标点处折行。"""
        # 15 个显示宽度：每个 CJK 字符 = 2 宽度
        # "今天讨论图像，" = 6 字符 = 12 宽度（在 15 以内，逗号是断点）
        text = "今天讨论图像，识别算法优化方案。"
        result = _wrap(text, 15)
        assert len(result) >= 2  # 至少折成两行
        # 第一行应以标点结尾
        assert result[0].endswith("，") or result[0].endswith("。")

    def test_no_boundary_fallback(self):
        """无标点断点时强制按宽度切。"""
        text = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        result = _wrap(text, 5)
        assert len(result) > 1
        # 每个片段 <= 5 字符
        for line in result[:-1]:
            assert len(line) <= 5


# ── TestPanelRenderer ─────────────────────────────────────────

def _make_state(*, segments=None, key_points=None, decisions=None,
                risks=None, open_questions=None, summary=""):
    """构造 MeetingState（不触发 pydantic 校验的快捷方式）。"""
    return MeetingState(
        segments=segments or [],
        key_points=key_points or [],
        decisions=decisions or [],
        risks=risks or [],
        open_questions=open_questions or [],
        summary=summary,
    )


def _make_seg(seq=1, raw="测试文本", corrected="测试文本（已校正）",
              analysis=None, analysis_status="done"):
    """构造 VoiceSegment。"""
    return VoiceSegment(
        seq=seq,
        started_at=datetime(2026, 8, 12, 10, 0, seq),
        raw_text=raw,
        corrected_text=corrected,
        analysis=analysis,
        analysis_status=analysis_status,
    )


def _make_analysis(**kwargs):
    """构造 SegmentAnalysis。"""
    defaults = dict(
        key_points=[], risks=[], questions=[],
        decisions=[], suggested_questions=[], resolved_questions=[],
    )
    defaults.update(kwargs)
    return SegmentAnalysis(**defaults)


class TestPanelRenderer:
    """帧渲染内容验证。"""

    def test_waiting_state(self):
        """无段时显示'等待语音'。"""
        renderer = PanelRenderer()
        state = _make_state()
        display = PanelDisplay(status="等待语音…", state=state)
        frame = renderer._build(display)
        assert "等待语音" in frame
        assert "实时会议助理" in frame

    def test_segment_with_analysis(self):
        """有分析结果时显示要点/决策。"""
        renderer = PanelRenderer()
        analysis = _make_analysis(
            key_points=["提升图像识别准确率"],
            decisions=["采用 Paraformer 模型"],
        )
        seg = _make_seg(analysis=analysis, analysis_status="done")
        state = _make_state(segments=[seg], key_points=["提升图像识别准确率"],
                           decisions=["采用 Paraformer 模型"])
        display = PanelDisplay(status="已处理段 1", seg=seg, state=state)
        frame = renderer._build(display)
        assert "提升图像识别准确率" in frame
        assert "采用 Paraformer 模型" in frame
        assert "✦1" in frame or "✦ 1" in frame  # 累计统计

    def test_segment_analysis_unavailable(self):
        """LLM 失败时显示'分析不可用'。"""
        renderer = PanelRenderer()
        seg = _make_seg(analysis=None, analysis_status="failed")
        state = _make_state(segments=[seg])
        display = PanelDisplay(status="已处理段 1", seg=seg,
                               analysis_unavailable=True, state=state)
        frame = renderer._build(display)
        assert "分析不可用" in frame

    def test_segment_skipped(self):
        """跳过分析时显示 skip 标记。"""
        renderer = PanelRenderer()
        seg = _make_seg(analysis=None, analysis_status="skipped")
        state = _make_state(segments=[seg])
        display = PanelDisplay(status="已处理段 1", seg=seg, state=state)
        frame = renderer._build(display)
        assert "⏭" in frame

    def test_final_frame_statistics(self):
        """退出统计帧包含段数/耗时/要点数。"""
        renderer = PanelRenderer()
        seg = _make_seg(analysis=_make_analysis(
            key_points=["要点1"], decisions=["决策1"],
        ))
        seg.analysis_started_at = 100.0
        seg.analysis_done_at = 105.0
        state = _make_state(
            segments=[seg],
            key_points=["要点1"],
            decisions=["决策1"],
            summary="会议总结内容",
        )
        # 捕获 stdout
        import io
        import sys
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            renderer.render_final(state, Path("/tmp/test.md"))
        finally:
            sys.stdout = old
        output = buf.getvalue()
        assert "会议结束统计" in output
        assert "段落 1" in output
        assert "LLM 分析 1 次" in output
        assert "要点 1" in output
        assert "决策 1" in output
        assert "已生成" in output


# ── TestPanelTheme（v3.26.2 双主题）────────────────────────

from iris.assistant._theme import DARK, LIGHT, THEMES  # noqa: E402


class TestPanelTheme:
    """双主题切换 + 语义色应用。"""

    def test_theme_resolution_valid(self):
        """合法主题名解析到对应 Theme。"""
        assert PanelRenderer("dark").theme is DARK
        assert PanelRenderer("light").theme is LIGHT

    def test_theme_resolution_invalid_falls_back_dark(self):
        """非法主题名回退 dark。"""
        assert PanelRenderer("neon").theme is DARK
        assert PanelRenderer("").theme is DARK

    def test_dark_light_have_distinct_colors(self):
        """dark/light 两套配色互不相同。"""
        assert DARK.bg != LIGHT.bg
        assert DARK.fg_text != LIGHT.fg_text
        assert DARK.fg_ok != LIGHT.fg_ok
        assert len(THEMES) == 2

    def test_frame_contains_ansi_escapes(self):
        """渲染帧包含 ANSI 颜色转义（256 色）。"""
        renderer = PanelRenderer("dark")
        state = _make_state()
        frame = renderer._build(PanelDisplay(status="等待语音…", state=state))
        assert "\033[38;5;" in frame   # 前景色
        assert "\033[48;5;" in frame   # 背景色（整帧填充）

    def test_light_theme_uses_light_background(self):
        """light 主题背景色为浅色 254 号。"""
        renderer = PanelRenderer("light")
        state = _make_state()
        frame = renderer._build(PanelDisplay(status="等待语音…", state=state))
        assert f"\033[48;5;{LIGHT.bg}m" in frame

    def test_semantic_colors_in_analysis(self):
        """分析区语义色：要点绿 / 决策绿 / 风险橙。"""
        renderer = PanelRenderer("dark")
        t = renderer.theme
        analysis = _make_analysis(
            key_points=["提升图像识别准确率"],
            decisions=["采用 Paraformer 模型"],
            risks=["模型延迟高"],
        )
        seg = _make_seg(analysis=analysis, analysis_status="done")
        state = _make_state(segments=[seg], key_points=["提升图像识别准确率"],
                           decisions=["采用 Paraformer 模型"], risks=["模型延迟高"])
        frame = renderer._build(PanelDisplay(status="已处理段 1", seg=seg, state=state))
        # 要点块 → 绿色
        assert f"\033[38;5;{t.fg_ok}m" in frame
        # 风险块 → 橙色
        assert f"\033[38;5;{t.fg_risk}m" in frame

    def test_alert_uses_alert_background(self):
        """告警区使用红底亮黄字。"""
        renderer = PanelRenderer("dark")
        t = renderer.theme
        state = _make_state()
        display = PanelDisplay(status="等待语音…", state=state,
                               alerts=["文档写入失败（磁盘空间不足？）"])
        frame = renderer._build(display)
        assert f"\033[48;5;{t.bg_alert}m" in frame
        assert f"\033[38;5;{t.fg_alert}m" in frame

    def test_final_frame_fills_background(self):
        """退出统计帧整帧全区填充（含底色）。"""
        renderer = PanelRenderer("dark")
        t = renderer.theme
        state = _make_state(summary="总结")
        import io
        import sys
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            renderer.render_final(state, Path("/tmp/test.md"))
        finally:
            sys.stdout = old
        output = buf.getvalue()
        assert f"\033[48;5;{t.bg}m" in output
        assert "会议结束统计" in output
