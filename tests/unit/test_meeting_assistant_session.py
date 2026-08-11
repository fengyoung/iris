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

    def test_on_publish_called_before_pending_visible(self):
        """v3.24: on_publish 在临界区内（notify 前）调用——worker 取段时
        预取产物（futures 注册）必已完成，消除「worker 抢跑 → 双份 LLM」竞态。"""
        session = MeetingSession()
        order = []

        def publish(seg):
            order.append(("publish", seg.seq))
            # 临界区内已可见 pending 指向本段
            assert session.state.dropped_count >= 0

        seg = session.submit("段A", on_publish=publish)
        assert order == [("publish", 1)]
        assert seg.corrected_text == ""  # 回调内未设置时保持空（live 侧负责填充）

    def test_on_publish_exception_propagates_but_pending_set(self):
        """回调异常传播给调用方（live 侧回调内部自兜底，session 契约不吞），
        但 pending 已设置、锁已释放——后续 take 不阻塞、状态一致。"""
        session = MeetingSession()

        def bad_publish(_seg):
            raise RuntimeError("预取兜底失败")

        raised = False
        try:
            session.submit("段A", on_publish=bad_publish)
        except RuntimeError:
            raised = True
        assert raised
        taken = session.take_pending(timeout=0.1)
        assert taken is not None and taken.seq == 1  # pending 已设置，无锁泄漏

    def test_on_publish_runs_before_worker_take(self):
        """并发验证：worker 在 notify 后取段时，on_publish 已完成。"""
        import threading
        session = MeetingSession()
        events = []
        publish_done = threading.Event()

        def publish(seg):
            events.append(("publish", seg.seq))
            publish_done.set()

        def worker():
            taken = session.take_pending(timeout=1.0)
            events.append(("take", taken.seq if taken else None))

        t = threading.Thread(target=worker)
        t.start()
        session.submit("段A", on_publish=publish)
        t.join(timeout=2)
        # 发布必须先于取走（临界区顺序保证）
        pub_idx = events.index(("publish", 1))
        take_idx = events.index(("take", 1))
        assert pub_idx < take_idx


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
