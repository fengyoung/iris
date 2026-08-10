"""实时会议助理 — 主编排单元测试（互斥探测 + 全链路 mock）。"""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from iris.assistant.live import _probe_running
from iris.assistant.models import VoiceSegment

# ── 互斥探测（零写副作用） ──────────────────────────────


class TestProbeRunning:
    def test_no_pid_file(self, tmp_path):
        assert _probe_running("asr-corrector", tmp_path) is False
        # 零写副作用：探测后目录仍空
        assert list(tmp_path.iterdir()) == []

    def test_alive_pid(self, tmp_path):
        (tmp_path / "asr-corrector.pid").write_text(str(os.getpid()))
        assert _probe_running("asr-corrector", tmp_path) is True
        assert (tmp_path / "asr-corrector.pid").exists()  # 不清理

    def test_dead_pid(self, tmp_path):
        (tmp_path / "asr-corrector.pid").write_text("999999999")
        assert _probe_running("asr-corrector", tmp_path) is False

    def test_corrupt_pid(self, tmp_path):
        (tmp_path / "asr-corrector.pid").write_text("not-a-pid")
        assert _probe_running("asr-corrector", tmp_path) is False


# ── 全链路（mock 校正/检索/分析） ──────────────────────


class _StubLLM:
    """分析用 stub：返回固定 JSON。"""

    def generate(self, prompt, route_context=None, **kwargs):
        return SimpleNamespace(
            text='{"key_points": ["要点X"], "decisions": ["决策Y"],'
                 ' "risks": [], "questions": [], "suggested_questions": ["追问Z"]}'
        )


def _make_bundle(tmp_path):
    """最小 bundle：app 含 assistant 段 + root（PromptTemplateLoader 需要）。

    short_segment_chars=1：让既有分析路径测试绕过短段门控（默认 15 会误伤短测试文本）。
    """
    return SimpleNamespace(
        root=tmp_path,
        app={"assistant": {
            "top_k": 3, "poll_interval": 0.05, "output_dir": "",
            "short_segment_chars": 1,
        }},
    )


def _make_assistant(tmp_path, bundle=None, **kwargs):
    from iris.assistant.live import MeetingLiveAssistant
    kwargs.setdefault("output_path", str(tmp_path / "meeting-test.md"))
    kwargs.setdefault("llm_service", _StubLLM())
    kwargs.setdefault("pid_dir", tmp_path / "pids")
    (tmp_path / "pids").mkdir(exist_ok=True)
    return MeetingLiveAssistant(bundle or _make_bundle(tmp_path), **kwargs)


def _seg(seq=1, raw="今天讨论下半年的目标", **kw):
    return SimpleNamespace(
        seq=seq,
        started_at=datetime(2026, 8, 10, 12, 0),
        raw_text=raw,
        corrected_text="",
        analysis=None,
        **kw,
    )


class TestPipelineParallel:
    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        # 隔离真实剪贴板/词典/检索/面板输出
        with patch("iris.assistant._clipboard._read_clipboard", return_value=None), \
             patch("iris.assistant.live._load_replace_dict", return_value={}), \
             patch("iris.assistant.live._load_asr_prompt", return_value=""), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_process_segment_full_pipeline(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = _seg(seq=1)
        assistant._process_segment(seg)
        assert seg.analysis is not None
        assert seg.analysis.key_points == ["要点X"]
        assert seg.analysis_status == VoiceSegment.ANALYSIS_DONE
        state = assistant._session.state
        assert len(state.segments) == 1
        assert state.key_points == ["要点X"]
        assert state.decisions == ["决策Y"]
        # 文档已写
        assert assistant._doc_path.exists()
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "要点X" in content

    def test_analyzer_uses_corrected_deep_text(self, tmp_path):
        class _FakeCorrector:
            def __init__(self):
                self.context: list[str] = []

            def fast(self, text: str) -> str:
                return text

            def deep(self, text: str) -> str:
                return "深度校正后文本"

            def push_context(self, text: str) -> None:
                self.context.append(text)

        assistant = _make_assistant(tmp_path)
        assistant._corrector = _FakeCorrector()
        seg = _seg(seq=1, raw="原始")
        with patch.object(assistant._analyzer, "analyze",
                          wraps=assistant._analyzer.analyze) as mock_analyze:
            assistant._process_segment(seg)
            called_text = mock_analyze.call_args[0][0]
            assert called_text == "深度校正后文本"
        assert assistant._corrector.context == ["原始", "深度校正后文本"]

    def test_analyzer_failure_degrades_gracefully(self, tmp_path):
        assistant = _make_assistant(tmp_path)

        class _FailingAnalyzer:
            def analyze(self, *a, **kw):
                return None

        assistant._analyzer = _FailingAnalyzer()
        seg = _seg(seq=1, raw="原始")
        assistant._process_segment(seg)  # 不应抛异常
        assert seg.analysis is None
        assert seg.analysis_status == VoiceSegment.ANALYSIS_FAILED
        state = assistant._session.state
        assert len(state.segments) == 1
        # 文档降级块
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "分析不可用" in content

    def test_run_blocked_by_asr_corrector(self, tmp_path):
        pid_dir = tmp_path / "pids"
        pid_dir.mkdir(exist_ok=True)
        (pid_dir / "asr-corrector.pid").write_text(str(os.getpid()))
        assistant = _make_assistant(tmp_path)
        assert assistant.run() == 1
        # 未创建文档（让位）
        assert not assistant._doc_path.exists()

    def test_run_blocks_duplicate_instance(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        pid_dir = tmp_path / "pids"
        (pid_dir / "meeting-live-assistant.pid").write_text(str(os.getpid()))
        assert assistant.run() == 1

    def test_run_registers_and_unregisters(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        # run() 内局部导入 iris.core.locks.ProcessRegistry，patch 源头模块
        with patch("iris.core.locks.ProcessRegistry") as mock_registry, \
             patch.object(assistant, "_poll_loop", side_effect=KeyboardInterrupt):
            mock_reg = MagicMock()
            mock_registry.return_value = mock_reg
            assert assistant.run() == 0
            mock_reg.unregister.assert_called_once()


class TestShortGate:
    """短段门控：确认语零 LLM 成本，跳过 deep/检索/分析直接落账。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant._clipboard._read_clipboard", return_value=None), \
             patch("iris.assistant.live._load_replace_dict", return_value={}), \
             patch("iris.assistant.live._load_asr_prompt", return_value=""), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def _bundle_with_gate(self, tmp_path, threshold=50):
        b = _make_bundle(tmp_path)
        b.app["assistant"]["short_segment_chars"] = threshold
        return b

    def test_short_segment_skipped_no_analyzer(self, tmp_path):
        assistant = _make_assistant(tmp_path, bundle=self._bundle_with_gate(tmp_path))
        seg = _seg(seq=1, raw="好的")
        with patch.object(assistant._analyzer, "analyze") as mock_analyze, \
             patch.object(assistant._pool, "submit") as mock_submit:
            assistant._process_segment(seg)
            mock_analyze.assert_not_called()
            mock_submit.assert_not_called()  # 不提交 deep/检索
        assert seg.analysis is None
        assert seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED
        assert len(assistant._session.state.segments) == 1  # 已落账
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "跳过分析" in content

    def test_fast_only_skips_everything(self, tmp_path):
        assistant = _make_assistant(tmp_path, fast_only=True)
        seg = _seg(seq=1, raw="这是一段足够长的正常会议发言内容需要分析")
        with patch.object(assistant._analyzer, "analyze") as mock_analyze, \
             patch.object(assistant._pool, "submit") as mock_submit:
            assistant._process_segment(seg)
            mock_analyze.assert_not_called()
            mock_submit.assert_not_called()
        assert seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED
        assert len(assistant._session.state.segments) == 1


class TestPrefetch:
    """双段流水线：poll 线程预取 futures，worker 消费。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant._clipboard._read_clipboard", return_value=None), \
             patch("iris.assistant.live._load_replace_dict", return_value={}), \
             patch("iris.assistant.live._load_asr_prompt", return_value=""), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_prefetch_registers_futures_and_worker_consumes(self, tmp_path):
        class _FakeCorrector:
            def __init__(self):
                self.context: list[str] = []

            def fast(self, text: str) -> str:
                return text

            def deep(self, text: str) -> str:
                return "预取深度校正结果"

            def push_context(self, text: str) -> None:
                self.context.append(text)

        assistant = _make_assistant(tmp_path)
        assistant._corrector = _FakeCorrector()
        # poll 线程侧：submit + 预取
        seg = assistant._session.submit("今天讨论下半年目标预算")
        assistant._prefetch(seg)
        assert seg.seq in assistant._futures
        f_deep, f_retr = assistant._futures[seg.seq]
        # worker 侧：消费预取 futures（无现场提交兜底）
        with patch.object(assistant._pool, "submit") as mock_submit:
            assistant._process_segment(seg)
            mock_submit.assert_not_called()  # 未兜底重新提交
        assert seg.seq not in assistant._futures  # 已 pop
        assert seg.analysis is not None
        assert seg.corrected_text == "预取深度校正结果"
        assert assistant._corrector.context == ["今天讨论下半年目标预算", "预取深度校正结果"]
        f_deep.cancel()
        f_retr.cancel()

    def test_prefetch_skipped_for_short_segment(self, tmp_path):
        b = _make_bundle(tmp_path)
        b.app["assistant"]["short_segment_chars"] = 15
        assistant = _make_assistant(tmp_path, bundle=b)
        seg = assistant._session.submit("好的")
        assistant._prefetch(seg)  # 短段：只 fast 校正，不提交 futures
        assert seg.seq not in assistant._futures
        assert seg.corrected_text == "好的"

    def test_stale_futures_pruned(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = assistant._session.submit("今天讨论下半年目标预算")
        assistant._prefetch(seg)
        f_deep, f_retr = assistant._futures[seg.seq]
        # 再提交一个更远的段：旧条目应被清理
        seg2 = assistant._session.submit("第二段讨论内容也是足够长的会议发言")
        assistant._prefetch(seg2)
        assert seg.seq not in assistant._futures  # 已被 prune
        assert seg2.seq in assistant._futures
        f_deep.cancel()
        f_retr.cancel()
        assistant._futures[seg2.seq][0].cancel()
        assistant._futures[seg2.seq][1].cancel()


class TestPhaseGuards:
    """phase 守卫：任一阶段异常段仍落账，不丢段。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant._clipboard._read_clipboard", return_value=None), \
             patch("iris.assistant.live._load_replace_dict", return_value={}), \
             patch("iris.assistant.live._load_asr_prompt", return_value=""), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_deep_future_exception_still_records(self, tmp_path):
        from concurrent.futures import Future

        def _boom(*a, **kw):
            raise RuntimeError("deep 挂")

        assistant = _make_assistant(tmp_path)
        # 用真实 future 模拟：完成但带异常
        f_deep = Future()
        f_deep.set_exception(RuntimeError("deep 挂"))
        assistant._futures[1] = (f_deep, Future())  # retr future 永不完成 → 超时降级
        seg = _seg(seq=1, raw="今天讨论下半年目标预算")
        assistant._process_segment(seg)
        # 段仍落账，deep 异常降级为 fast
        assert len(assistant._session.state.segments) == 1
        assert seg.analysis_status == VoiceSegment.ANALYSIS_DONE or seg.analysis is None

    def test_record_exception_does_not_crash(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = _seg(seq=1, raw="今天讨论下半年目标预算")
        with patch.object(assistant._session, "record", side_effect=RuntimeError("落账失败")), \
             patch("sys.stderr.write"):
            assistant._process_segment(seg)  # 不抛
        assert seg.analysis is not None  # 分析已完成


class TestSuggestEvery:
    """建议提问间隔化：非采样段清空 suggested_questions。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant._clipboard._read_clipboard", return_value=None), \
             patch("iris.assistant.live._load_replace_dict", return_value={}), \
             patch("iris.assistant.live._load_asr_prompt", return_value=""), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def _bundle_with_suggest(self, tmp_path, every=3):
        b = _make_bundle(tmp_path)
        b.app["assistant"]["suggest_every"] = every
        return b

    def test_non_sample_segment_clears_questions(self, tmp_path):
        assistant = _make_assistant(tmp_path, bundle=self._bundle_with_suggest(tmp_path, every=3))
        seg2 = _seg(seq=2)
        assistant._process_segment(seg2)
        assert seg2.analysis.suggested_questions == []  # 2 % 3 != 0 → 清空

    def test_sample_segment_keeps_questions(self, tmp_path):
        assistant = _make_assistant(tmp_path, bundle=self._bundle_with_suggest(tmp_path, every=3))
        seg3 = _seg(seq=3)
        assistant._process_segment(seg3)
        assert seg3.analysis.suggested_questions == ["追问Z"]  # 3 % 3 == 0 → 保留


class TestExitSummary:
    """退出总结：run() 结束时生成 AI 总结写入文档；失败自动跳过。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant._clipboard._read_clipboard", return_value=None), \
             patch("iris.assistant.live._load_replace_dict", return_value={}), \
             patch("iris.assistant.live._load_asr_prompt", return_value=""), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_exit_summary_written_to_doc(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = _seg(seq=1, raw="今天讨论下半年目标预算")
        assistant._process_segment(seg)  # 先有一段的会议状态
        with patch.object(assistant, "_poll_loop", side_effect=KeyboardInterrupt), \
             patch.object(assistant._analyzer, "summarize",
                          return_value="## 会议主题\n本场会议讨论了下半年目标") as mock_sum:
            assert assistant.run() == 0
            mock_sum.assert_called_once()
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "## 📝 会议总结（AI 生成）" in content
        assert "本场会议讨论了下半年目标" in content

    def test_exit_summary_failure_skips(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = _seg(seq=1, raw="今天讨论下半年目标预算")
        assistant._process_segment(seg)
        with patch.object(assistant, "_poll_loop", side_effect=KeyboardInterrupt), \
             patch.object(assistant._analyzer, "summarize", return_value=None) as mock_sum:
            assert assistant.run() == 0
            mock_sum.assert_called_once()
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "会议总结（AI 生成）" not in content  # 失败不写总结区

    def test_no_segments_no_summary_call(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        with patch.object(assistant, "_poll_loop", side_effect=KeyboardInterrupt), \
             patch.object(assistant._analyzer, "summarize") as mock_sum:
            assert assistant.run() == 0
            mock_sum.assert_not_called()  # 无段 → 跳过总结
