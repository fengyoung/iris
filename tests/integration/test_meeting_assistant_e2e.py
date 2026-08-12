"""实时会议助理 — 端到端集成测试（真实 config_bundle + mock 剪贴板/LLM）。"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from iris.app.cli.handlers import COMMAND_HANDLERS
from iris.assistant.live import MeetingLiveAssistant

_ASR_SEGMENTS = [
    "我们今天讨论下半年的目标和预算安排",
    "下半年预算大概需要增加百分之二十",
]

_ANALYSIS_JSON = (
    '{"key_points": ["讨论下半年目标"], "risks": ["预算可能不足"],'
    ' "questions": ["预算缺口怎么补？"], "decisions": [],'
    ' "suggested_questions": ["预算缺口的具体金额是多少？"]}'
)


class _FakeLLM:
    """分析用 Fake：固定 JSON 或抛异常。"""

    def __init__(self, *, raise_on_generate: bool = False):
        self.raise_on_generate = raise_on_generate

    def generate(self, prompt, route_context=None, **kwargs):
        if self.raise_on_generate:
            raise RuntimeError("fake LLM failure")
        return SimpleNamespace(text=_ANALYSIS_JSON)


def _make_assistant(config_bundle, tmp_path, *, llm, pid_dir=None):
    assistant = MeetingLiveAssistant(
        config_bundle,
        output_path=str(tmp_path / "meeting-e2e.md"),
        llm_service=llm,
        pid_dir=pid_dir or (tmp_path / "pids"),
    )
    # 固定建议提问间隔=1：保证每段都生成，e2e 断言与用户 app.json 配置解耦
    assistant._cfg = assistant._cfg.model_copy(update={"suggest_every": 1})
    (tmp_path / "pids").mkdir(exist_ok=True)
    return assistant


def _process_drained(assistant, seg):
    """直调 _process_batch([seg]) 后清空 pending 槽（真实运行由 worker take_pending 消费，
    测试直调不经过 worker，残留 pending 会让下一次 submit 误计 dropped_count）。"""
    assistant._process_batch([seg])
    assistant._session.take_pending(timeout=0)


class TestEndToEnd:
    """两段语音 → 校正 → 检索（mock）→ 分析 → 文档完整链路。"""

    @pytest.fixture(autouse=True)
    def _isolate_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_two_segments_full_pipeline(self, config_bundle, tmp_path):
        assistant = _make_assistant(config_bundle, tmp_path, llm=_FakeLLM())
        # 显式驱动两段（避免 worker 积压丢弃语义干扰端到端断言）
        seg1 = assistant._session.submit(_ASR_SEGMENTS[0])
        _process_drained(assistant, seg1)
        seg2 = assistant._session.submit(_ASR_SEGMENTS[1])
        _process_drained(assistant, seg2)

        state = assistant._session.state
        assert len(state.segments) == 2
        assert state.key_points == ["讨论下半年目标"]
        assert state.open_questions == ["预算缺口怎么补？"]
        # 文档包含 frontmatter + 段块 + 累计
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "source: meeting-live-assistant" in content
        assert "段 1（" in content and "段 2（" in content
        assert "讨论下半年目标" in content
        assert "建议提问" in content

    def test_run_lifecycle_graceful_exit(self, config_bundle, tmp_path):
        """run() 完整生命周期：注册 → 轮询 → Ctrl+C 优雅退出（文档最终写 + 统计帧）。"""
        assistant = _make_assistant(config_bundle, tmp_path, llm=_FakeLLM())
        with patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt), \
             patch("iris.assistant.live.PanelRenderer.render_final") as mock_final:
            assert assistant.run() == 0
        assert assistant._doc_path.exists()
        mock_final.assert_called_once()


class TestDegrade:
    """LLM 分析失败 → 降级不中断，文档写降级块。"""

    @pytest.fixture(autouse=True)
    def _isolate_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True), \
             patch("iris.assistant.live.RetrieverAdapter.search", return_value=[]), \
             patch("iris.assistant.live.PanelRenderer.render"), \
             patch("iris.assistant.live.PanelRenderer.render_final"):
            yield

    def test_llm_failure_degrades(self, config_bundle, tmp_path):
        assistant = _make_assistant(
            config_bundle, tmp_path, llm=_FakeLLM(raise_on_generate=True))
        # 先处理一段（分析失败 → 降级块），再优雅退出
        seg = assistant._session.submit(_ASR_SEGMENTS[0])
        _process_drained(assistant, seg)
        assert seg.analysis is None
        with patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt):
            assert assistant.run() == 0
        content = assistant._doc_path.read_text(encoding="utf-8")
        assert "分析不可用" in content  # 降级块
        assert assistant._session.state.dropped_count == 0  # 进程不崩


class TestMutexStartup:
    """启动互斥：asr-corrector 在跑 → 让位；残留死 pid → 正常启动。"""

    @pytest.fixture(autouse=True)
    def _isolate_io(self):
        with patch("iris.assistant.live._load_assistant_data", return_value=({}, "")), \
             patch("iris.assistant.live.ASREngine", autospec=True), \
             patch("iris.assistant.live.AudioCapture", autospec=True):
            yield

    def test_asr_corrector_no_longer_blocks(self, config_bundle, tmp_path):
        """v3.25.0 本地音频 ASR 不再依赖剪贴板，asr-corrector 可同时运行。"""
        pid_dir = tmp_path / "pids"
        pid_dir.mkdir(exist_ok=True)
        (pid_dir / "asr-corrector.pid").write_text(str(os.getpid()))
        assistant = _make_assistant(config_bundle, tmp_path, llm=_FakeLLM(),
                                    pid_dir=pid_dir)
        with patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt):
            assert assistant.run() == 0  # 启动成功，不再被 asr-corrector 阻塞

    def test_dead_pid_allows_start(self, config_bundle, tmp_path):
        pid_dir = tmp_path / "pids"
        pid_dir.mkdir(exist_ok=True)
        (pid_dir / "asr-corrector.pid").write_text("999999999")
        assistant = _make_assistant(config_bundle, tmp_path, llm=_FakeLLM(),
                                    pid_dir=pid_dir)
        with patch.object(assistant, "_audio_loop", side_effect=KeyboardInterrupt):
            assert assistant.run() == 0


class TestCliRegistration:
    """命令注册与参数解析。"""

    def test_handler_registered(self):
        assert "meeting-live-assistant" in COMMAND_HANDLERS

    def test_parser_accepts_command_and_output(self):
        from iris.app._cli_main import build_parser
        parser = build_parser()
        args = parser.parse_args(
            ["meeting-live-assistant", "--output", "data/meeting.md"])
        assert args.command == "meeting-live-assistant"
        assert args.output == "data/meeting.md"

    def test_parser_accepts_asr_mode(self):
        from iris.app._cli_main import build_parser
        parser = build_parser()
        args = parser.parse_args(["meeting-live-assistant", "--asr", "local"])
        assert args.asr == "local"
        args2 = parser.parse_args(["meeting-live-assistant"])
        assert args2.asr == ""
