"""实时会议助理 — 数据模型单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

from iris.assistant.models import (
    AssistantConfig,
    MeetingState,
    SegmentAnalysis,
    VoiceSegment,
)


def _make_segment(seq: int = 1, text: str = "今天讨论下半年的目标",
                  analysis: SegmentAnalysis | None = None) -> VoiceSegment:
    return VoiceSegment(
        seq=seq,
        started_at=datetime(2026, 8, 10, 12, 0, 0) + timedelta(seconds=seq),
        raw_text=text,
        corrected_text=text,
        analysis=analysis,
    )


class TestSegmentAnalysis:
    def test_defaults_to_empty_lists(self):
        a = SegmentAnalysis()
        assert a.key_points == []
        assert a.risks == []
        assert a.questions == []
        assert a.decisions == []
        assert a.suggested_questions == []

    def test_has_content(self):
        assert not SegmentAnalysis().has_content
        assert SegmentAnalysis(key_points=["x"]).has_content


class TestVoiceSegment:
    def test_required_fields(self):
        seg = VoiceSegment(seq=1, started_at=datetime(2026, 8, 10), raw_text="文本")
        assert seg.corrected_text == ""
        assert seg.analysis is None

    def test_missing_required_raises(self):
        import pytest
        with pytest.raises(Exception):
            VoiceSegment(seq=1)


class TestMeetingState:
    def test_add_analysis_appends_and_dedups(self):
        state = MeetingState()
        seg1 = _make_segment(1, analysis=SegmentAnalysis(key_points=["A", "A"], decisions=["D1"]))
        state.add_analysis(seg1)
        seg2 = _make_segment(2, analysis=SegmentAnalysis(key_points=["A", "B"]))
        state.add_analysis(seg2)
        assert len(state.segments) == 2
        assert state.key_points == ["A", "B"]  # "A" 去重
        assert state.decisions == ["D1"]

    def test_open_questions_merged_and_removed_when_answered(self):
        state = MeetingState()
        state.add_analysis(_make_segment(1, analysis=SegmentAnalysis(questions=["预算多少？"])))
        assert state.open_questions == ["预算多少？"]
        # 后续段决策完整覆盖 → 移出待解决
        state.add_analysis(_make_segment(2, analysis=SegmentAnalysis(
            questions=["截止时间？"], decisions=["预算 100 万"]
        )))
        assert "预算 100 万" in state.decisions
        # 原问题未被整串覆盖，仍在
        assert "预算多少？" in state.open_questions
        # 整串覆盖测试
        state.add_analysis(_make_segment(3, analysis=SegmentAnalysis(decisions=["预算多少？"])))
        assert "预算多少？" not in state.open_questions

    def test_empty_analysis_segment_still_appended(self):
        state = MeetingState()
        state.add_analysis(_make_segment(1, analysis=None))
        assert len(state.segments) == 1
        assert state.key_points == []

    def test_dropped_count_independent(self):
        state = MeetingState()
        state.dropped_count = 3
        state.add_analysis(_make_segment(1, analysis=SegmentAnalysis(key_points=["x"])))
        assert state.dropped_count == 3


class TestAssistantConfig:
    def test_defaults(self):
        cfg = AssistantConfig.from_app_config({})
        assert cfg.output_dir == ""
        assert cfg.top_k == 5
        assert cfg.llm_model == ""
        assert cfg.poll_interval == 0.5
        assert cfg.doc_rewrite_every == 1

    def test_overrides(self):
        cfg = AssistantConfig.from_app_config(
            {"top_k": 8, "output_dir": "/tmp/x", "unknown_field": "ignored"}
        )
        assert cfg.top_k == 8
        assert cfg.output_dir == "/tmp/x"

    def test_non_dict_safe(self):
        cfg = AssistantConfig.from_app_config(None)
        assert cfg.top_k == 5
