"""实时会议助理 — 会话状态机（积压丢弃）单元测试。"""

from __future__ import annotations

import threading
import time

from iris.assistant._session import MeetingSession


class TestBacklogDiscard:
    def test_single_submit_then_take(self):
        session = MeetingSession()
        seg = session.submit("第一段")
        assert seg.seq == 1
        taken = session.take_pending(timeout=0.1)
        assert taken is seg

    def test_submit_while_pending_discards_older(self):
        """核心：worker 未消费时连续 submit 3 段 → 只消费最新，中间段丢弃。"""
        session = MeetingSession()
        session.submit("段A")
        session.submit("段B")
        session.submit("段C")
        assert session.state.dropped_count == 2  # B、C 各自覆盖了前一段
        taken = session.take_pending(timeout=0.1)
        assert taken.raw_text == "段C"
        assert taken.seq == 3
        assert session.take_pending(timeout=0.05) is None  # 已消费完

    def test_seq_increments_across_discard(self):
        session = MeetingSession()
        session.submit("A")
        session.submit("B")
        assert session.take_pending(timeout=0.1).seq == 2  # A 被丢弃，序号不回收

    def test_take_pending_idles_when_empty(self):
        session = MeetingSession()
        start = time.monotonic()
        assert session.take_pending(timeout=0.1) is None
        assert time.monotonic() - start >= 0.05

    def test_request_stop_wakes_worker(self):
        session = MeetingSession()
        result = []

        def worker():
            result.append(session.take_pending(timeout=0.5))

        t = threading.Thread(target=worker)
        t.start()
        session.request_stop()
        t.join(timeout=2)
        assert result == [None]  # 被 stop 唤醒返回 None
        assert session.stop.is_set()

    def test_submit_after_stop_still_allowed(self):
        """stop 只影响 take 阻塞；submit 幂等（最后一段仍可取）。"""
        session = MeetingSession()
        session.request_stop()
        seg = session.submit("最后一段")
        taken = session.take_pending(timeout=0.1)
        assert taken is seg


class TestSummaryPrompt:
    def _seed(self, session: MeetingSession) -> None:
        from datetime import datetime
        from iris.assistant.models import SegmentAnalysis
        seg1 = session.submit("先说要点A", started_at=datetime(2026, 8, 10, 12, 0))
        seg1.corrected_text = "校正后A"
        seg1.analysis = SegmentAnalysis(
            key_points=["要点A"], decisions=["决策A"], risks=["风险A"], questions=["问题A？"]
        )
        session.record(seg1)
        seg2 = session.submit("再说要点B", started_at=datetime(2026, 8, 10, 12, 1))
        seg2.corrected_text = "校正后B"
        seg2.analysis = SegmentAnalysis(key_points=["要点B"])
        session.record(seg2)

    def test_contains_cumulative_sections(self):
        session = MeetingSession()
        self._seed(session)
        summary = session.summary_for_prompt()
        assert "要点: 要点A；要点B" in summary
        assert "决策: 决策A" in summary
        assert "风险: 风险A" in summary
        assert "待解决: 问题A？" in summary
        assert "最近讨论: 校正后A / 校正后B" in summary

    def test_truncates_to_max_chars(self):
        session = MeetingSession()
        self._seed(session)
        summary = session.summary_for_prompt(max_chars=20)
        assert len(summary) <= 21  # max_chars + 省略号
        assert summary.endswith("…")

    def test_empty_state(self):
        session = MeetingSession()
        assert session.summary_for_prompt() == ""
