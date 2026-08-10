"""实时会议助理 — 主编排单元测试（互斥探测 + 全链路 mock）。"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from iris.assistant.live import _probe_running
from iris.assistant.models import SegmentAnalysis

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
    """最小 bundle：app 含 assistant 段 + root（PromptTemplateLoader 需要）。"""
    return SimpleNamespace(
        root=tmp_path,
        app={"assistant": {"top_k": 3, "poll_interval": 0.05, "output_dir": ""}},
    )


def _make_assistant(tmp_path, bundle=None, **kwargs):
    from iris.assistant.live import MeetingLiveAssistant
    kwargs.setdefault("output_path", str(tmp_path / "meeting-test.md"))
    kwargs.setdefault("llm_service", _StubLLM())
    kwargs.setdefault("pid_dir", tmp_path / "pids")
    (tmp_path / "pids").mkdir(exist_ok=True)
    return MeetingLiveAssistant(bundle or _make_bundle(tmp_path), **kwargs)


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
        seg = SimpleNamespace(
            seq=1,
            started_at=datetime(2026, 8, 10, 12, 0),
            raw_text="今天讨论下半年的目标",
            corrected_text="",
            analysis=None,
        )
        assistant._process_segment(seg)
        assert seg.analysis is not None
        assert seg.analysis.key_points == ["要点X"]
        state = assistant._session.state
        assert len(state.segments) == 1
        assert state.key_points == ["要点X"]
        assert state.decisions == ["决策Y"]
        # 文档已写
        assert assistant._doc_path.exists()
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "要点X" in content

    def test_analyzer_uses_corrected_deep_text(self, tmp_path):
        from iris.assistant._corrector import CorrectorAdapter

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
        seg = SimpleNamespace(
            seq=1, started_at=datetime(2026, 8, 10, 12, 0),
            raw_text="原始", corrected_text="", analysis=None,
        )
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
        seg = SimpleNamespace(
            seq=1, started_at=datetime(2026, 8, 10, 12, 0),
            raw_text="原始", corrected_text="", analysis=None,
        )
        assistant._process_segment(seg)  # 不应抛异常
        assert seg.analysis is None
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
        from unittest.mock import MagicMock
        assistant = _make_assistant(tmp_path)
        # run() 内局部导入 iris.core.locks.ProcessRegistry，patch 源头模块
        with patch("iris.core.locks.ProcessRegistry") as mock_registry, \
             patch.object(assistant, "_poll_loop", side_effect=KeyboardInterrupt):
            mock_reg = MagicMock()
            mock_registry.return_value = mock_reg
            assert assistant.run() == 0
            mock_reg.unregister.assert_called_once()
