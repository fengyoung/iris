"""实时会议助理 — 过程文档写入单元测试（原子重写 + 渲染）。"""

from __future__ import annotations

from datetime import datetime

from iris.assistant._doc_writer import DocWriter
from iris.assistant.models import MeetingState, SegmentAnalysis, VoiceSegment


def _state_with(segments: list[VoiceSegment]) -> MeetingState:
    state = MeetingState(started_at=datetime(2026, 8, 10, 12, 0, 0))
    for seg in segments:
        state.add_analysis(seg)
    return state


def _seg(seq: int = 1, text: str = "讨论内容", analysis=None) -> VoiceSegment:
    return VoiceSegment(
        seq=seq,
        started_at=datetime(2026, 8, 10, 12, 1, seq),
        raw_text=text,
        corrected_text=text,
        analysis=analysis,
    )


class TestRender:
    def test_frontmatter_and_title(self):
        content = DocWriter.render(_state_with([]))
        assert "title: 实时会议记录 2026-08-10 12:00" in content
        assert "date: 2026-08-10 12:00" in content
        assert "type: 实时会议记录" in content
        assert "source: meeting-live-assistant" in content
        assert "# 实时会议记录 2026-08-10 12:00" in content

    def test_empty_cumulative_sections(self):
        content = DocWriter.render(_state_with([]))
        assert "### 关键要点\n- 无" in content
        assert "### 决策点\n- 无" in content
        assert "### 风险\n- 无" in content
        assert "### 待解决问题\n- 无" in content

    def test_segment_with_analysis(self):
        analysis = SegmentAnalysis(
            key_points=["要点A"], decisions=["决策X"],
            suggested_questions=["追问？"],
        )
        content = DocWriter.render(_state_with([_seg(1, analysis=analysis)]))
        assert "## 🎙 段 1（12:01:01）" in content
        assert "**校正文本**：讨论内容" in content
        assert "**要点**：要点A" in content
        assert "**决策点**：决策X" in content
        assert "**建议提问**：追问？" in content
        assert "**风险**：" not in content  # 空字段不输出

    def test_segment_analysis_unavailable(self):
        content = DocWriter.render(_state_with([_seg(1, analysis=None)]))
        assert "**分析**：⚠ 分析不可用" in content

    def test_dropped_count_note(self):
        state = _state_with([_seg(1)])
        state.dropped_count = 3
        content = DocWriter.render(state)
        assert "本场积压丢弃 3 段" in content


class TestAtomicWrite:
    def test_write_and_rewrite(self, tmp_path):
        path = tmp_path / "会议记录.md"
        writer = DocWriter(path, rewrite_every=1)
        assert writer.initial_write(MeetingState(started_at=datetime(2026, 8, 10)))
        assert path.exists()
        first = path.read_text(encoding="utf-8")
        assert "### 关键要点\n- 无" in first

        state = _state_with([_seg(1, analysis=SegmentAnalysis(key_points=["A"]))])
        assert writer.maybe_rewrite(state)
        second = path.read_text(encoding="utf-8")
        assert second != first
        assert "- A" in second          # 累计区要点
        assert "**要点**：A" in second  # 段内要点
        assert not list(tmp_path.glob("*.tmp"))  # tmp 不残留

    def test_exception_keeps_old_file(self, tmp_path):
        path = tmp_path / "meeting.md"
        writer = DocWriter(path, rewrite_every=1)
        writer.initial_write(MeetingState(started_at=datetime(2026, 8, 10)))
        old = path.read_text(encoding="utf-8")

        import pytest
        from unittest.mock import patch
        with patch.object(DocWriter, "_atomic_write", side_effect=OSError("disk full")):
            state = _state_with([_seg(1, analysis=SegmentAnalysis(key_points=["A"]))])
            assert writer.maybe_rewrite(state) is False
        # 旧文件原样保留
        assert path.read_text(encoding="utf-8") == old

    def test_rewrite_every_throttle(self, tmp_path):
        path = tmp_path / "meeting.md"
        writer = DocWriter(path, rewrite_every=3)
        writer.initial_write(MeetingState(started_at=datetime(2026, 8, 10)))
        # 1 段：不足 3，不重写
        assert writer.maybe_rewrite(_state_with([_seg(1)])) is False
        # 2 段：仍不足
        state2 = _state_with([_seg(1), _seg(2)])
        assert writer.maybe_rewrite(state2) is False
        # 3 段：达到阈值，重写
        state3 = _state_with([_seg(1), _seg(2), _seg(3)])
        assert writer.maybe_rewrite(state3) is True
        # force 强制
        assert writer.maybe_rewrite(state2, force=True) is True

    def test_force_final_write(self, tmp_path):
        path = tmp_path / "meeting.md"
        writer = DocWriter(path, rewrite_every=5)
        writer.initial_write(MeetingState(started_at=datetime(2026, 8, 10)))
        state = _state_with([_seg(1)])
        assert writer.maybe_rewrite(state, force=True) is True
