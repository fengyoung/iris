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
        assert seg.analysis_status == VoiceSegment.ANALYSIS_PENDING  # 默认 pending

    def test_analysis_status_constants(self):
        assert VoiceSegment.ANALYSIS_DONE == "done"
        assert VoiceSegment.ANALYSIS_FAILED == "failed"
        assert VoiceSegment.ANALYSIS_SKIPPED == "skipped"

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

    def test_summary_default_empty(self):
        state = MeetingState()
        assert state.summary == ""
        state.summary = "## 会议主题\n..."
        assert state.summary.startswith("## 会议主题")


class TestAssistantConfig:
    def test_defaults(self):
        cfg = AssistantConfig.from_app_config({})
        assert cfg.output_dir == ""
        assert cfg.top_k == 5
        assert cfg.llm_model == ""
        assert cfg.poll_interval == 0.5
        assert cfg.doc_rewrite_every == 3
        # v3.23.3 新增
        assert cfg.fast_only is False
        assert cfg.short_segment_chars == 15
        assert cfg.max_segment_chars == 2000
        assert cfg.dedup_window_seconds == 30.0
        assert cfg.suggest_every == 3
        assert cfg.summary_enabled is True

    def test_overrides(self):
        cfg = AssistantConfig.from_app_config(
            {"top_k": 8, "output_dir": "/tmp/x", "unknown_field": "ignored",
             "fast_only": True, "short_segment_chars": 50, "summary_enabled": False}
        )
        assert cfg.top_k == 8
        assert cfg.output_dir == "/tmp/x"
        assert cfg.fast_only is True
        assert cfg.short_segment_chars == 50
        assert cfg.summary_enabled is False

    def test_non_dict_safe(self):
        cfg = AssistantConfig.from_app_config(None)
        assert cfg.top_k == 5
        assert cfg.fast_only is False


# ── v3.25.4 修复针对性测试 ────────────────────────────

class TestTopicStateMachine:
    """update_topic 状态机：连续切换不丢话题 / summary 不串。"""

    def test_consecutive_topic_change(self):
        """连续两次 topic_change=True 不丢话题（Bug 1 回归）。"""
        s = MeetingState()
        s.update_topic("A", True, "摘要A", 1)
        s.update_topic("B", True, "摘要B", 5)
        assert s.current_topic == "B"
        assert len(s.topics) == 2
        assert s.topics[0]["label"] == "A"
        assert s.topics[0]["end_seq"] == 5
        assert s.topics[1]["label"] == "B"
        assert s.topics[1]["start_seq"] == 5

    def test_closed_topic_keeps_own_summary(self):
        """关闭旧话题保留自身摘要，不拼入新话题摘要（Bug 2 回归）。"""
        s = MeetingState()
        s.update_topic("A", True, "摘要A", 1)
        s.update_topic("B", True, "摘要B", 5)
        assert s.topics[0]["summary"] == "摘要A"
        assert "摘要B" not in s.topics[0]["summary"]

    def test_same_topic_no_duplicate(self):
        """同话题标签不重复创建条目。"""
        s = MeetingState()
        s.update_topic("A", False, "s1", 1)
        s.update_topic("A", False, "s2", 3)
        assert len(s.topics) == 1
        assert s.topics[0]["start_seq"] == 1

    def test_topic_change_without_flag_still_switches(self):
        """topic_change=False 但标签变化 → 仍切换（兼容 LLM 漏标）。"""
        s = MeetingState()
        s.update_topic("A", False, "s1", 1)
        s.update_topic("B", False, "s2", 10)
        assert s.current_topic == "B"
        assert s.topics[0]["end_seq"] == 10
        assert s.topics[1]["start_seq"] == 10

    def test_returns_closed_topic(self):
        """切换时返回被关闭的旧话题。"""
        s = MeetingState()
        s.update_topic("A", False, "s1", 1)
        closed = s.update_topic("B", False, "s2", 5)
        assert closed is not None
        assert closed["label"] == "A"
        assert closed["end_seq"] == 5
        # 无变化返回 None
        assert s.update_topic("B", False, "s3", 7) is None


class TestConflictDetection:
    """check_conflict 保守判定：明确推翻才报，细化/约束/进展不误报。"""

    def test_refinement_not_conflict(self):
        """讨论细化/进展不判冲突（缺陷 3 回归）。"""
        s = MeetingState()
        s.check_conflict(["质检流程优化需求提出"])
        conflicts = s.check_conflict(["质检流程方案可能不满足需求"])
        assert conflicts == []

    def test_constraint_not_conflict(self):
        """约束性否定（"不能有差异"）不判冲突。"""
        s = MeetingState()
        s.check_conflict(["重点质量指标统一指标"])
        conflicts = s.check_conflict(["重点质量指标不能有地区差异"])
        assert conflicts == []

    def test_double_negation_not_conflict(self):
        """同持否定立场（补充而非推翻）不判冲突。"""
        s = MeetingState()
        s.check_conflict(["方案A不可行"])
        conflicts = s.check_conflict(["方案A没有价值"])
        assert conflicts == []

    def test_overturn_detected(self):
        """明确推翻（"不可行" vs 肯定结论）判定为冲突。"""
        s = MeetingState()
        s.check_conflict(["重点质量指标统一指标"])
        conflicts = s.check_conflict(["重点质量指标统一不可行"])
        assert len(conflicts) == 1
        assert "统一不可行" in conflicts[0]

    def test_explicit_objection_detected(self):
        """明确反对（"反对"）判定为冲突。"""
        s = MeetingState()
        s.check_conflict(["质检流程优化需求提出"])
        conflicts = s.check_conflict(["反对质检流程优化方案"])
        assert len(conflicts) == 1


class TestTodoAccumulation:
    """待办去重累计。"""

    def test_todos_deduped(self):
        s = MeetingState()
        s._dedup_append(s.todos, ["方案方介绍归拢方案"])
        s._dedup_append(s.todos, ["方案方介绍归拢方案"])
        assert len(s.todos) == 1

    def test_todos_multiple(self):
        s = MeetingState()
        s._dedup_append(s.todos, ["A", "B"])
        assert s.todos == ["A", "B"]
