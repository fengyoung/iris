"""实时会议助理 — 主编排单元测试（互斥探测 + 全链路 mock）。

v3.25.0: 适配音频 ASR 架构（_load_assistant_data + ASREngine mock）。
"""

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
        assert list(tmp_path.iterdir()) == []

    def test_alive_pid(self, tmp_path):
        (tmp_path / "asr-corrector.pid").write_text(str(os.getpid()))
        with patch("subprocess.run", return_value=SimpleNamespace(
                stdout="python /Users/fengyoung/MyProjects/iris3/src/iris/app/main.py")):
            assert _probe_running("asr-corrector", tmp_path) is True
        assert (tmp_path / "asr-corrector.pid").exists()

    def test_pid_reused_by_unrelated_process(self, tmp_path):
        (tmp_path / "asr-corrector.pid").write_text(str(os.getpid()))
        with patch("subprocess.run", return_value=SimpleNamespace(
                stdout="/usr/bin/some-unrelated-daemon")):
            assert _probe_running("asr-corrector", tmp_path) is False

    def test_dead_pid(self, tmp_path):
        (tmp_path / "asr-corrector.pid").write_text("999999999")
        assert _probe_running("asr-corrector", tmp_path) is False

    def test_corrupt_pid(self, tmp_path):
        (tmp_path / "asr-corrector.pid").write_text("not-a-pid")
        assert _probe_running("asr-corrector", tmp_path) is False


# ── 共享 Fixtures ──────────────────────────────────────


class _StubLLM:
    """分析用 stub：返回固定 JSON。"""
    def generate(self, prompt, route_context=None, **kwargs):
        return SimpleNamespace(
            text='{"key_points": ["要点X"], "decisions": ["决策Y"],'
                 ' "risks": [], "questions": [], "suggested_questions": ["追问Z"]}'
        )


def _make_bundle(tmp_path, **overrides):
    """最小 bundle：含 assistant 段 + asr 子段 + root。"""
    cfg = {
        "top_k": 3, "poll_interval": 0.05, "output_dir": "",
        "short_segment_chars": 1,
        "doc_rewrite_every": 1,  # 单段测试需要即时写入
    }
    cfg.update(overrides)
    return SimpleNamespace(
        root=tmp_path,
        app={
            "assistant": cfg,
            "asr": {
                "mode": "local",
                "local": {"model_dir": str(tmp_path / "fake_models")},
                "hotwords_file": "",
                "replace_dict_file": "",
                "llm_correct_enabled": True,
                "llm_correct_timeout_ms": 8000,
            }
        },
    )


def _make_assistant(tmp_path, bundle=None, **kwargs):
    from iris.assistant.live import MeetingLiveAssistant
    kwargs.setdefault("output_path", str(tmp_path / "meeting-test.md"))
    kwargs.setdefault("llm_service", _StubLLM())
    kwargs.setdefault("pid_dir", tmp_path / "pids")
    (tmp_path / "pids").mkdir(exist_ok=True)
    # 创建假的模型目录
    fake_models = tmp_path / "fake_models"
    fake_models.mkdir(exist_ok=True)
    for name in [
        "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx",
        "speech_fsmn_vad_zh-cn-16k-common-onnx",
        "punc_ct-transformer_zh-cn-common-vocab272727-onnx",
    ]:
        (fake_models / name).mkdir(exist_ok=True)
    return MeetingLiveAssistant(bundle or _make_bundle(tmp_path), **kwargs)


def _seg(seq=1, raw="今天讨论下半年的目标", **kw):
    from iris.assistant.models import SpeakerLabel
    return SimpleNamespace(
        seq=seq,
        started_at=datetime(2026, 8, 10, 12, 0),
        raw_text=raw,
        corrected_text="",
        analysis=None,
        speaker=SpeakerLabel(),
        **kw,
    )


# ── 全链路 ──────────────────────────────────────────────


class TestPipelineParallel:
    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_process_segment_full_pipeline(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = _seg(seq=1)
        assistant._process_batch([seg])
        assert seg.analysis is not None
        assert seg.analysis.key_points == ["要点X"]
        assert seg.analysis_status == VoiceSegment.ANALYSIS_DONE
        state = assistant._session.state
        assert len(state.segments) == 1
        assert state.key_points == ["要点X"]
        assert state.decisions == ["决策Y"]
        assert assistant._doc_path.exists()
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "要点X" in content

    def test_analyzer_uses_corrected_deep_text(self, tmp_path):
        class _FakeCorrector:
            def __init__(self):
                self.context: list[str] = []
            def fast(self, text: str) -> str:
                return text
            def deep(self, text: str, speaker_id: str = "") -> str:
                return "深度校正后文本"
            def push_context(self, text: str, speaker_id: str = "") -> None:
                self.context.append(text)

        assistant = _make_assistant(tmp_path)
        assistant._corrector = _FakeCorrector()
        seg = _seg(seq=1, raw="原始")
        with patch.object(assistant._analyzer, "analyze",
                          wraps=assistant._analyzer.analyze) as mock_analyze:
            assistant._process_batch([seg])
            called_text = mock_analyze.call_args[0][0]
            assert called_text == "段1：深度校正后文本"
        assert assistant._corrector.context == ["原始", "深度校正后文本"]

    def test_analyzer_failure_degrades_gracefully(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        class _FailingAnalyzer:
            def analyze(self, *a, **kw):
                return None
        assistant._analyzer = _FailingAnalyzer()
        seg = _seg(seq=1, raw="原始")
        assistant._process_batch([seg])
        assert seg.analysis is None
        assert seg.analysis_status == VoiceSegment.ANALYSIS_FAILED
        assert len(assistant._session.state.segments) == 1
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "分析不可用" in content

    def test_asr_corrector_no_longer_blocks(self, tmp_path):
        """v3.25.0 本地音频 ASR 不再依赖剪贴板，asr-corrector 运行时不阻止启动。"""
        pid_dir = tmp_path / "pids"
        pid_dir.mkdir(exist_ok=True)
        (pid_dir / "asr-corrector.pid").write_text(str(os.getpid()))
        assistant = _make_assistant(tmp_path)
        with patch("subprocess.run", return_value=SimpleNamespace(
                stdout="python /Users/fengyoung/MyProjects/iris3/src/iris/app/main.py")), \
             patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt):
            assert assistant.run() == 0  # 不再返回 1，正常执行

    def test_run_blocks_duplicate_instance(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        pid_dir = tmp_path / "pids"
        (pid_dir / "meeting-live-assistant.pid").write_text(str(os.getpid()))
        assert assistant.run() == 1

    def test_run_registers_and_unregisters(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        with patch("iris.core.locks.ProcessRegistry") as mock_registry, \
             patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt):
            mock_reg = MagicMock()
            mock_registry.return_value = mock_reg
            assert assistant.run() == 0
            mock_reg.unregister.assert_called_once()


class TestShortGate:
    """短段门控：确认语零 LLM 成本，跳过 deep/检索/分析直接落账。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def _bundle_with_gate(self, tmp_path, threshold=50):
        return _make_bundle(tmp_path, short_segment_chars=threshold)

    def test_short_segment_skipped_no_analyzer(self, tmp_path):
        assistant = _make_assistant(tmp_path, bundle=self._bundle_with_gate(tmp_path))
        seg = _seg(seq=1, raw="好的")
        with patch.object(assistant._analyzer, "analyze") as mock_analyze, \
             patch.object(assistant._pool, "submit") as mock_submit:
            assistant._process_batch([seg])
            mock_analyze.assert_not_called()
            mock_submit.assert_not_called()
        assert seg.analysis is None
        assert seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED
        assert len(assistant._session.state.segments) == 1
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "跳过分析" in content


class TestPrefetch:
    """双段流水线：poll 线程预取 futures，worker 消费。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_prefetch_registers_futures_and_worker_consumes(self, tmp_path):
        class _FakeCorrector:
            def __init__(self):
                self.context: list[str] = []
            def fast(self, text: str) -> str:
                return text
            def deep(self, text: str, speaker_id: str = "") -> str:
                return "预取深度校正结果"
            def push_context(self, text: str, speaker_id: str = "") -> None:
                self.context.append(text)

        assistant = _make_assistant(tmp_path)
        assistant._corrector = _FakeCorrector()
        fast = assistant._corrector.fast("今天讨论下半年目标预算")
        seg = assistant._session.submit(
            "今天讨论下半年目标预算",
            on_publish=lambda s: assistant._publish_prefetch(s, fast),
        )
        assert seg.seq in assistant._futures
        f_deep, f_retr = assistant._futures[seg.seq]
        with patch.object(assistant._pool, "submit") as mock_submit:
            assistant._process_batch([seg])
            mock_submit.assert_not_called()
        assert seg.seq not in assistant._futures
        assert seg.analysis is not None
        assert seg.corrected_text == "预取深度校正结果"
        assert assistant._corrector.context == ["今天讨论下半年目标预算", "预取深度校正结果"]
        f_deep.cancel()
        f_retr.cancel()

    def test_worker_take_sees_futures_atomic(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        with patch.object(assistant._pool, "submit", wraps=assistant._pool.submit):
            fast = assistant._corrector.fast("今天讨论下半年目标预算")
            seg = assistant._session.submit(
                "今天讨论下半年目标预算",
                on_publish=lambda s: assistant._publish_prefetch(s, fast),
            )
            taken = assistant._session.take_pending(timeout=0.1)
            assert taken is seg
            futures = assistant._futures.pop(seg.seq, None)
            assert futures is not None
            futures[0].cancel()
            futures[1].cancel()

    def test_prefetch_skipped_for_short_segment(self, tmp_path):
        b = _make_bundle(tmp_path, short_segment_chars=15)
        assistant = _make_assistant(tmp_path, bundle=b)
        fast = assistant._corrector.fast("好的")
        seg = assistant._session.submit(
            "好的", on_publish=lambda s: assistant._publish_prefetch(s, fast))
        assert seg.seq not in assistant._futures
        assert seg.corrected_text == "好的"

    def test_stale_futures_pruned(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = assistant._session.submit(
            "今天讨论下半年目标预算",
            on_publish=lambda s: assistant._publish_prefetch(s, s.raw_text))
        f_deep, f_retr = assistant._futures[seg.seq]
        seg2 = assistant._session.submit(
            "第二段讨论内容也是足够长的会议发言",
            on_publish=lambda s: assistant._publish_prefetch(s, s.raw_text))
        assert seg.seq not in assistant._futures
        assert seg2.seq in assistant._futures
        f_deep.cancel()
        f_retr.cancel()
        assistant._futures[seg2.seq][0].cancel()
        assistant._futures[seg2.seq][1].cancel()


class TestPhaseGuards:
    """phase 守卫：任一阶段异常段仍落账，不丢段。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_deep_future_exception_still_records(self, tmp_path):
        from concurrent.futures import Future
        def _boom(*a, **kw):
            raise RuntimeError("deep 挂")
        assistant = _make_assistant(tmp_path)
        f_deep = Future()
        f_deep.set_exception(RuntimeError("deep 挂"))
        assistant._futures[1] = (f_deep, Future())
        seg = _seg(seq=1, raw="今天讨论下半年目标预算")
        assistant._process_batch([seg])
        assert len(assistant._session.state.segments) == 1
        assert seg.analysis_status == VoiceSegment.ANALYSIS_DONE or seg.analysis is None

    def test_record_exception_does_not_crash(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = _seg(seq=1, raw="今天讨论下半年目标预算")
        with patch.object(assistant._session, "record", side_effect=RuntimeError("落账失败")), \
             patch("sys.stderr.write"):
            assistant._process_batch([seg])
        assert seg.analysis is not None


class TestSuggestEvery:
    """建议提问间隔化：非采样段清空 suggested_questions。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def _bundle_with_suggest(self, tmp_path, every=3):
        return _make_bundle(tmp_path, suggest_every=every)

    def test_non_sample_segment_clears_questions(self, tmp_path):
        assistant = _make_assistant(tmp_path, bundle=self._bundle_with_suggest(tmp_path, every=3))
        seg2 = _seg(seq=2)
        assistant._process_batch([seg2])
        assert seg2.analysis.suggested_questions == []

    def test_sample_segment_keeps_questions(self, tmp_path):
        assistant = _make_assistant(tmp_path, bundle=self._bundle_with_suggest(tmp_path, every=3))
        seg4 = _seg(seq=4)
        assistant._process_batch([seg4])
        assert seg4.analysis.suggested_questions == ["追问Z"]

    def test_first_segment_keeps_questions(self, tmp_path):
        assistant = _make_assistant(tmp_path, bundle=self._bundle_with_suggest(tmp_path, every=3))
        seg1 = _seg(seq=1)
        assistant._process_batch([seg1])
        assert seg1.analysis.suggested_questions == ["追问Z"]


class _TentativeLLM:
    """返回带 tentative 决策 + 建议提问的 stub（suggest 调用返回同一 JSON）。"""
    def generate(self, prompt, route_context=None, **kwargs):
        return SimpleNamespace(
            text='{"key_points": ["要点X"], "questions": [],'
                 ' "decisions": [{"text": "方案X", "confidence": "tentative"}],'
                 ' "suggested_questions": ["追问Z"]}'
        )


class TestSuggestEventDriven:
    """v3.26.1 事件驱动建议提问节流：距上次生成 < suggest_every 段不触发。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_tentative_throttled_when_recent(self, tmp_path):
        """tentative 决策但距上次建议 < suggest_every（3）→ 不触发，清空。"""
        assistant = _make_assistant(tmp_path, bundle=_make_bundle(tmp_path, suggest_every=3),
                                    llm_service=_TentativeLLM())
        assistant._last_suggest_seq = 1  # 上次建议在 seq 1
        seg = _seg(seq=2)
        assistant._process_batch([seg])
        assert seg.analysis.suggested_questions == []

    def test_tentative_triggered_after_gap(self, tmp_path):
        """tentative 决策且距上次建议 ≥ suggest_every → 触发，保留建议。"""
        assistant = _make_assistant(tmp_path, bundle=_make_bundle(tmp_path, suggest_every=3),
                                    llm_service=_TentativeLLM())
        assistant._last_suggest_seq = 1  # 上次建议在 seq 1
        seg = _seg(seq=5)  # since_last=4 ≥ 3 → 触发
        assistant._process_batch([seg])
        assert seg.analysis.suggested_questions == ["追问Z"]


class TestExitSummary:
    """退出总结：run() 结束时生成 AI 总结写入文档；失败自动跳过。"""

    @pytest.fixture(autouse=True)
    def _no_real_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_exit_summary_written_to_doc(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        seg = _seg(seq=1, raw="今天讨论下半年目标预算")
        assistant._process_batch([seg])
        with patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt), \
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
        assistant._process_batch([seg])
        with patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt), \
             patch.object(assistant._analyzer, "summarize", return_value=None) as mock_sum:
            assert assistant.run() == 0
            mock_sum.assert_called_once()
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "会议总结（AI 生成）" not in content

    def test_no_segments_no_summary_call(self, tmp_path):
        assistant = _make_assistant(tmp_path)
        with patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt), \
             patch.object(assistant._analyzer, "summarize") as mock_sum:
            assert assistant.run() == 0
            mock_sum.assert_not_called()


# ── Phase 1 新增：噪音门控 ──────────────────────────────

class TestNoiseGate:
    """_is_noise：拦截 ASR 幻觉/键盘噪音/英文碎片。"""

    def test_repeating_char_detected(self):
        from iris.assistant.live import MeetingLiveAssistant
        assert MeetingLiveAssistant._is_noise("不不不不不不不不不不不")
        assert MeetingLiveAssistant._is_noise("据据据据据据据据据据据据")
        assert MeetingLiveAssistant._is_noise("这这这这这这这这这")

    def test_single_char_detected(self):
        from iris.assistant.live import MeetingLiveAssistant
        assert MeetingLiveAssistant._is_noise("有")
        assert MeetingLiveAssistant._is_noise("呃")

    def test_english_fragment_detected(self):
        from iris.assistant.live import MeetingLiveAssistant
        assert MeetingLiveAssistant._is_noise("yeah")
        assert MeetingLiveAssistant._is_noise("OK")
        assert MeetingLiveAssistant._is_noise("ststeteding")

    def test_valid_chinese_passes(self):
        from iris.assistant.live import MeetingLiveAssistant
        assert not MeetingLiveAssistant._is_noise("今天讨论下半年目标")
        assert not MeetingLiveAssistant._is_noise("好的感谢高明")
        assert not MeetingLiveAssistant._is_noise("重质量检率统一")
        assert not MeetingLiveAssistant._is_noise("iPhone17入仓战略")  # 混合中英文，合法

    def test_empty_text_detected(self):
        from iris.assistant.live import MeetingLiveAssistant
        assert MeetingLiveAssistant._is_noise("")
        assert MeetingLiveAssistant._is_noise("   ")


# ── Phase 1 新增：累计区容量控制 ────────────────────────

class TestCumulativeCap:
    """MeetingState 累计列表上限 25，超限淘汰最旧条目。"""

    def test_cap_enforced_on_key_points(self):
        from iris.assistant.models import MeetingState
        state = MeetingState()
        # 添加 30 条不同要点
        for i in range(30):
            state._dedup_append(state.key_points, [f"要点{i}"])
        assert len(state.key_points) == 25
        # 最旧的 5 条被淘汰
        assert "要点0" not in state.key_points
        assert "要点29" in state.key_points  # 最新的保留

    def test_dedup_before_cap(self):
        from iris.assistant.models import MeetingState
        state = MeetingState()
        # 25 条去重后只有 1 条（不会被截断）
        for _ in range(30):
            state._dedup_append(state.key_points, ["同一个要点"])
        assert len(state.key_points) == 1
