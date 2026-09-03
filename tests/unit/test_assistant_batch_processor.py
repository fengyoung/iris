"""实时会议助理 — 批处理纯逻辑单元测试（批提示 / 去重 / 分析应用 / 建议节流）。

analysis 对象用真实 `SegmentAnalysis`（Pydantic）构造，状态用真实
`MeetingSession().state` + `InsightFeed()`，只测实际行为。
"""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

from iris.assistant._batch_processor import (
    apply_analysis,
    apply_speaker,
    batch_hints,
    batch_label,
    check_off_agenda,
    dedup_hits,
    rms_level,
    segment_line,
    should_suggest,
    speaker_id_of,
)
from iris.assistant._insight import InsightFeed
from iris.assistant._session import MeetingSession
from iris.assistant.models import (
    DecisionItem,
    SegmentAnalysis,
    SpeakerLabel,
    TodoItem,
    VoiceSegment,
)


def _seg(seq: int, text: str = "", **kw) -> VoiceSegment:
    return VoiceSegment(seq=seq, started_at=datetime.now(), raw_text=text,
                        corrected_text=text, **kw)


# ── batch_hints ────────────────────────────────────────────────


class TestBatchHints:
    def test_no_signals(self):
        segs = [_seg(1, "a"), _seg(2, "b")]
        assert batch_hints(segs, force_topic_boundary=False) == []

    def test_force_topic_boundary(self):
        hints = batch_hints([_seg(1)], force_topic_boundary=True)
        assert len(hints) == 1
        assert "话题边界" in hints[0]
        assert "topic_change" in hints[0]

    def test_single_speaker_change(self):
        segs = [_seg(1, speaker_change_signal=True), _seg(2)]
        hints = batch_hints(segs, force_topic_boundary=False)
        assert len(hints) == 1
        assert "VAD 检测到" in hints[0]

    def test_multiple_speaker_changes(self):
        segs = [_seg(1, speaker_change_signal=True), _seg(2, speaker_change_signal=True)]
        hints = batch_hints(segs, force_topic_boundary=False)
        assert len(hints) == 1
        assert "多次说话人切换" in hints[0]

    def test_forced_cut_needs_two(self):
        assert batch_hints([_seg(1, forced_cut=True)], force_topic_boundary=False) == []
        hints = batch_hints([_seg(1, forced_cut=True), _seg(2, forced_cut=True)],
                            force_topic_boundary=False)
        assert len(hints) == 1
        assert "强制切段" in hints[0]

    def test_combined_order(self):
        segs = [
            _seg(1, speaker_change_signal=True, forced_cut=True),
            _seg(2, speaker_change_signal=True, forced_cut=True),
        ]
        hints = batch_hints(segs, force_topic_boundary=True)
        assert len(hints) == 3
        assert "话题边界" in hints[0]
        assert "说话人切换" in hints[1]
        assert "强制切段" in hints[2]


# ── speaker_id_of / segment_line ───────────────────────────────


class TestSegmentLine:
    def test_without_speaker(self):
        seg = _seg(3, "今天先过一下进度")
        assert speaker_id_of(seg) == ""
        assert segment_line(seg) == "段3：今天先过一下进度"

    def test_with_speaker(self):
        seg = _seg(7, "我补充一点", speaker=SpeakerLabel(speaker_id="speaker_A"))
        assert speaker_id_of(seg) == "speaker_A"
        assert segment_line(seg) == "段7（speaker_A）：我补充一点"

    def test_speaker_none_attribute(self):
        seg = SimpleNamespace(seq=1, corrected_text="x", speaker=None)
        assert speaker_id_of(seg) == ""
        assert segment_line(seg) == "段1：x"


# ── dedup_hits ─────────────────────────────────────────────────


class TestDedupHits:
    def test_dedup_preserves_order(self):
        h1 = SimpleNamespace(title="A", content_preview="同样的前 50 字" + "x" * 60 + "尾巴1")
        h2 = SimpleNamespace(title="B", content_preview="别的")
        h3 = SimpleNamespace(title="A", content_preview="同样的前 50 字" + "x" * 60 + "尾巴2")
        result = dedup_hits([h1, h2, h3])
        assert result == [h1, h2]  # h3 与 h1 前 50 字相同 → 去重

    def test_same_title_different_preview_kept(self):
        h1 = SimpleNamespace(title="A", content_preview="一")
        h2 = SimpleNamespace(title="A", content_preview="二")
        assert dedup_hits([h1, h2]) == [h1, h2]

    def test_none_preview_no_error(self):
        h1 = SimpleNamespace(title="A", content_preview=None)
        h2 = SimpleNamespace(title="A", content_preview=None)
        h3 = SimpleNamespace(title="A", content_preview="")
        assert dedup_hits([h1, h2, h3]) == [h1]

    def test_empty(self):
        assert dedup_hits([]) == []


# ── should_suggest ─────────────────────────────────────────────


class TestShouldSuggest:
    def test_none_analysis(self):
        assert should_suggest(None, first_seq=1, last_suggest_seq=0, suggest_every=3) is False

    def test_fixed_interval_hit(self):
        a = SegmentAnalysis()
        assert should_suggest(a, first_seq=1, last_suggest_seq=0, suggest_every=3)  # (1-1)%3==0
        assert should_suggest(a, first_seq=4, last_suggest_seq=1, suggest_every=3)  # (4-1)%3==0

    def test_too_close_to_last_even_with_tentative(self):
        a = SegmentAnalysis(decisions=[DecisionItem(text="待定", confidence="tentative")])
        assert should_suggest(a, first_seq=5, last_suggest_seq=4, suggest_every=3) is False

    def test_far_enough_with_tentative(self):
        a = SegmentAnalysis(decisions=[DecisionItem(text="待定", confidence="tentative")])
        assert should_suggest(a, first_seq=8, last_suggest_seq=4, suggest_every=3) is True

    def test_far_enough_with_questions(self):
        a = SegmentAnalysis(questions=["预算谁批？"])
        assert should_suggest(a, first_seq=8, last_suggest_seq=4, suggest_every=3) is True

    def test_far_enough_with_nothing(self):
        a = SegmentAnalysis(decisions=[DecisionItem(text="定了", confidence="confirmed")])
        assert should_suggest(a, first_seq=8, last_suggest_seq=4, suggest_every=3) is False


# ── batch_label ────────────────────────────────────────────────


class TestBatchLabel:
    def test_single(self):
        assert batch_label([_seg(5)], n_analyzed=1, n_skipped=0) == "已处理段 5"

    def test_multi_no_skip(self):
        label = batch_label([_seg(3), _seg(4), _seg(5)], n_analyzed=3, n_skipped=0)
        assert label == "已处理段 3-5（3 段合并分析）"

    def test_multi_with_skip(self):
        label = batch_label([_seg(3), _seg(4), _seg(5)], n_analyzed=2, n_skipped=1)
        assert label == "已处理段 3-5（2 段合并分析 + 1 段跳过）"


# ── rms_level ──────────────────────────────────────────────────


class TestRmsLevel:
    def test_zero_rms(self):
        assert rms_level(0.0, 0.01) == 0.0

    def test_normal_ratio(self):
        assert rms_level(0.005, 0.01) == pytest.approx(0.5)

    def test_clamped_to_one(self):
        assert rms_level(0.5, 0.01) == 1.0

    def test_zero_threshold_fallback(self):
        # threshold=0 → 用 0.005 兜底
        assert rms_level(0.0025, 0.0) == pytest.approx(0.5)


# ── apply_analysis 端到端 ──────────────────────────────────────


def _rich_analysis(topic: str = "质检域划分") -> SegmentAnalysis:
    return SegmentAnalysis(
        topic=topic,
        topic_change=True,
        topic_summary="讨论软硬一体域边界",
        key_points=["软硬一体域独立成域"],
        decisions=[
            DecisionItem(text="作业域与履约作业域同义", confidence="confirmed"),
            DecisionItem(text="管理域下周再议", confidence="proposed"),
        ],
        risks=["边界不清导致重复建设", "人力不足", "第三条不该推送"],
        todos=[
            TodoItem(text="整理域划分文档", assignee="张三"),
            TodoItem(text="约管理域评审"),
            TodoItem(text="整理域划分文档"),  # 重复
        ],
        speaker=SpeakerLabel(speaker_id="speaker_A", role_hint="主持人", is_turn_change=True),
    )


class TestApplyAnalysis:
    def test_end_to_end_state_and_feed(self):
        state = MeetingSession().state
        feed = InsightFeed()
        analyzable = [_seg(1, "一"), _seg(2, "二")]
        analysis = _rich_analysis()

        apply_analysis(state, feed, analysis, analyzable, agenda="")

        # 话题
        assert state.current_topic == "质检域划分"
        assert state.topics[-1]["label"] == "质检域划分"
        assert state.topics[-1]["start_seq"] == 1
        # 待办去重累计
        assert state.todos == ["整理域划分文档", "约管理域评审"]
        # 说话人登记
        assert state.speakers == [
            {"id": "speaker_A", "role": "主持人", "first_seen": 1, "segments": 1}
        ]
        # 段后验 speaker 赋值
        assert all(s.speaker.speaker_id == "speaker_A" for s in analyzable)

        # 洞察流事件
        events = feed.visible
        types = [e.event_type for e in events]
        assert "decision_confirmed" in types
        assert types.count("decision_proposed") == 0  # proposed 不推送
        assert types.count("risk") == 2  # 只推前 2 条
        assert types.count("todo") == 2  # 只推前 2 条
        assert "speaker_turn" in types
        # 首个话题不推 topic_change（prev_topic 为空）
        assert "topic_change" not in types
        texts = {e.text for e in events}
        assert "作业域与履约作业域同义" in texts
        assert "整理域划分文档（张三）" in texts
        assert "约管理域评审" in texts
        assert "speaker_A（主持人） 发言" in texts
        assert "第三条不该推送" not in texts

    def test_topic_change_pushed_when_topic_actually_switches(self):
        state = MeetingSession().state
        feed = InsightFeed()
        apply_analysis(state, feed, _rich_analysis("质检域划分"), [_seg(1, "x")], agenda="")
        apply_analysis(state, feed, _rich_analysis("双周报邮件发送"), [_seg(2, "y")], agenda="")
        assert state.current_topic == "双周报邮件发送"
        topic_events = [e for e in feed.visible if e.event_type == "topic_change"]
        assert len(topic_events) == 1
        assert "双周报邮件发送" in topic_events[0].text

    def test_repeated_speaker_accumulates_segments(self):
        state = MeetingSession().state
        feed = InsightFeed()
        sp = SpeakerLabel(speaker_id="speaker_B", role_hint="", is_turn_change=False)
        analysis = SegmentAnalysis(speaker=sp)
        apply_speaker(state, feed, analysis, [_seg(1)], first_seq=1)
        assert state.speakers[0]["segments"] == 1
        apply_speaker(state, feed, analysis, [_seg(2), _seg(3), _seg(4)], first_seq=2)
        assert len(state.speakers) == 1
        assert state.speakers[0]["segments"] == 4
        # is_turn_change=False → 无 speaker_turn 事件
        assert feed.empty

    def test_empty_speaker_id_ignored(self):
        state = MeetingSession().state
        analysis = SegmentAnalysis(speaker=SpeakerLabel(speaker_id="", is_turn_change=True))
        apply_speaker(state, InsightFeed(), analysis, [_seg(1)], first_seq=1)
        assert state.speakers == []

    def test_no_topic_does_not_touch_state(self):
        state = MeetingSession().state
        feed = InsightFeed()
        apply_analysis(state, feed, SegmentAnalysis(), [_seg(1)], agenda="")
        assert state.current_topic == ""
        assert state.topics == []
        assert feed.empty


# ── check_off_agenda ───────────────────────────────────────────


@pytest.fixture
def caplog(caplog):
    """live.py 导入时会把 `iris` logger 设为 propagate=False（全量跑时先被收集），
    这里把 caplog handler 直接挂到目标 logger 上，保证捕获不受传播设置影响。"""
    logger = logging.getLogger("iris.assistant._batch_processor")
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


class TestCheckOffAgenda:
    LOGGER = "iris.assistant._batch_processor"

    def test_on_agenda_no_warning(self, caplog):
        state = MeetingSession().state
        state.update_topic("质检域划分", True, "", 1)
        analysis = SegmentAnalysis(topic="质检域划分")
        with caplog.at_level(logging.INFO, logger=self.LOGGER):
            check_off_agenda(state, analysis, "质检域划分；双周报")
        assert "跑偏提醒" not in caplog.text

    def test_partial_keyword_match_counts_as_on_agenda(self, caplog):
        state = MeetingSession().state
        state.update_topic("质检域划分与边界", True, "", 1)
        analysis = SegmentAnalysis(topic="质检域划分与边界")
        with caplog.at_level(logging.INFO, logger=self.LOGGER):
            check_off_agenda(state, analysis, "质检域;双周报")  # 半角分号 + 子串
        assert "跑偏提醒" not in caplog.text

    def test_off_agenda_warns(self, caplog):
        state = MeetingSession().state
        state.update_topic("午饭吃什么", True, "", 1)
        analysis = SegmentAnalysis(topic="午饭吃什么")
        with caplog.at_level(logging.INFO, logger=self.LOGGER):
            check_off_agenda(state, analysis, "质检域划分；双周报")
        records = [r for r in caplog.records if r.name == self.LOGGER]
        assert any("跑偏提醒" in r.getMessage() and "午饭吃什么" in r.getMessage()
                   for r in records)
        assert records[0].levelno == logging.INFO

    def test_no_agenda_silent(self, caplog):
        state = MeetingSession().state
        state.update_topic("午饭吃什么", True, "", 1)
        with caplog.at_level(logging.INFO, logger=self.LOGGER):
            check_off_agenda(state, SegmentAnalysis(topic="午饭吃什么"), "")
        assert "跑偏提醒" not in caplog.text

    def test_no_topics_yet_silent(self, caplog):
        state = MeetingSession().state  # topics 为空
        with caplog.at_level(logging.INFO, logger=self.LOGGER):
            check_off_agenda(state, SegmentAnalysis(topic="午饭吃什么"), "质检域划分")
        assert "跑偏提醒" not in caplog.text
