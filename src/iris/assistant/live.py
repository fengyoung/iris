"""主编排：MeetingLiveAssistant 常驻进程（音频采集 → ASR → 校正 → 检索 → 分析 → 面板/文档）。

v3.25.0 音频模式：本地 FunASR Paraformer 采集+转写，完全独立于 vocotype/asr-corrector。
v3.23.3 双段流水线：poll 线程预取（fast 校正 + 提交 deep/检索 futures），
worker 只做等待/分析——段 N 分析期间段 N+1 的深度校正与检索已在池中运行。
"""

from __future__ import annotations

import io
import json
import logging
import os
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from iris.utils.paths import get_project_root

from ._analyzer import SegmentAnalyzer, _ANALYSIS_DEADLINE_SEC
from ._asr import ASREngine
from ._audio import AudioCapture
from ._audio_capture import MergeBuffer, hotwords_from_lines, is_noise, rms_of
from ._batch_processor import (
    apply_analysis, batch_hints, batch_label, dedup_hits, rms_level,
    segment_line, should_suggest, speaker_id_of,
)
from ._corrector import CorrectorAdapter
from ._doc_writer import DocWriter
from ._insight import InsightFeed
from ._logging import setup_session_logger, teardown_session_logger
from ._panel import PanelDisplay, PanelRenderer
from ._retriever import RetrieverAdapter
from ._session import MeetingSession
from .models import AsrConfig, AssistantConfig, VoiceSegment

_logger = logging.getLogger(__name__)

# 模块加载时即添加控制台 handler + 提升日志级别。
# 只加到父 logger（子 logger 通过 propagation 继承），避免重复输出。
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("[Iris] %(message)s"))
_iris_logger = logging.getLogger("iris.assistant")
_iris_logger.setLevel(logging.INFO)
_iris_logger.addHandler(_console)
_iris_logger.propagate = False  # 不传播到 root（root 为 WARNING，会重复）

# 段处理并行等待窗：LLM 深度校正与检索共享，超时各自降级
_PARALLEL_WAIT_SEC = 10.0
# 退出 join 上限：并行窗(10s) + 分析 deadline(15s) + 余量 —— 段边界退出
_EXIT_JOIN_SEC = _PARALLEL_WAIT_SEC + _ANALYSIS_DEADLINE_SEC + 2.0


def _probe_running(name: str, pid_dir: Path) -> bool:
    """只读探测：pid 文件存在且 PID 存活即认为实例在运行。零写副作用。

    不能用 ProcessRegistry(name).register() 探测——asr-corrector 未运行时
    register 会写入假 pid 文件，造成「活实例」假占。
    """
    pid_file = Path(pid_dir) / f"{name}.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return False
    import subprocess
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        ).stdout
        return "iris" in out
    except Exception:
        return False


def _resolve_assistant_path(rel_path: str) -> Path:
    """解析 assistant 数据文件路径（相对于项目根目录）。"""
    path = Path(rel_path).expanduser()
    if not path.is_absolute():
        path = get_project_root() / path
    return path


def _load_assistant_data(asr_cfg: AsrConfig) -> tuple[dict, str]:
    """从 assistant.asr 配置加载替换词典和热词。

    两个文件均为 assistant 专属，独立于 asr-corrector 和 vocotype：
    - data/assistant/asr_replace_dict.json  — 音近词→正确词映射
    - data/assistant/asr_hotwords.txt       — ASR 热词表
    """
    replace_dict: dict = {}
    hotwords = ""

    if asr_cfg.replace_dict_file:
        path = _resolve_assistant_path(asr_cfg.replace_dict_file)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                replace_dict = data.get("replace_map", {})
            except Exception as e:
                _logger.warning("替换词典加载失败: %s", e)
        else:
            _logger.warning("替换词典不存在: %s（仅做原文转写）", path)

    if asr_cfg.hotwords_file:
        path = _resolve_assistant_path(asr_cfg.hotwords_file)
        if path.exists():
            try:
                lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
                         if ln.strip()]
                # v3.26.1: 热词总长校验
                _MAX_HOTWORDS_CHARS = 10000
                raw_len = sum(len(ln) + 1 for ln in lines) - 1
                if raw_len > _MAX_HOTWORDS_CHARS:
                    _logger.warning("热词过长（%d 字符），截断到 %d 字符", raw_len, _MAX_HOTWORDS_CHARS)
                hotwords = hotwords_from_lines(lines, _MAX_HOTWORDS_CHARS)
            except Exception as e:
                _logger.warning("热词文件加载失败: %s", e)
        else:
            _logger.warning("热词文件不存在: %s", path)

    return replace_dict, hotwords


class MeetingLiveAssistant:
    """实时会议助理编排核心。

    v3.25.0 音频模式：sounddevice 采集 → FunASR Paraformer 转写 → 校正 → 检索 → 分析。
    完全独立于 vocotype 和 asr-corrector（零 import、独立配置、独立数据）。

    线程模型：
    - 音频线程：sounddevice 回调采集 → ASREngine.feed() → submit 段
    - 工作线程：串行消费段（等 futures → 分析 → 落账）；处理中 submit 只覆盖
      pending 指针 → 天然丢弃中间段（积压策略）
    - 段内并行：LLM 深度校正与知识库检索各占一个线程（ThreadPoolExecutor(2)），
      且与上一段的分析重叠（双段流水线）
    """

    def __init__(
        self,
        bundle,
        *,
        output_path: str = "",
        llm_service: Optional[object] = None,
        pid_dir: Optional[Path] = None,
        asr_mode: str = "",
    ):
        self._bundle = bundle
        self._cfg = AssistantConfig.from_app_config(bundle.app.get("assistant", {}) if bundle.app else {})
        # ASR 配置
        asr_raw = (bundle.app or {}).get("asr", {}) if bundle.app else {}
        self._asr_cfg = AsrConfig.from_app_config(asr_raw)
        if asr_mode:
            self._asr_cfg.mode = asr_mode  # CLI --asr > 配置
        # pid 目录
        self._pid_dir = Path(pid_dir) if pid_dir else (get_project_root() / "data")

        # 校正引擎（自实现 Aho-Corasick，零 asr-corrector 依赖）
        replace_dict, hotwords = _load_assistant_data(self._asr_cfg)
        self._corrector = CorrectorAdapter(
            replace_dict=replace_dict,
            llm_prompt=self._load_llm_prompt(),
            llm_timeout_ms=self._asr_cfg.llm_correct_timeout_ms,
        )

        # LLM：外部注入（测试用）或 LLMService 构造
        if llm_service is not None:
            self._llm = llm_service
        else:
            try:
                from iris.llm.service import LLMService
                self._llm = LLMService(bundle)
            except Exception as e:
                _logger.warning("LLM 初始化失败，会议降级为仅词典校正: %s", e)
                self._llm = None
        if self._llm is not None:
            self._corrector.set_llm_service(self._llm)
            # v3.25.4: assistant 独立熔断器（阈值 2，60s 自动恢复）
            # 会议场景 LLM 调用高频（每批分析 1 次），快速跳过问题模型，
            # 60s 后自动半开重试首选模型
            try:
                from iris.llm.provider import _CircuitBreaker
                provider = getattr(self._llm, "get_provider", lambda: None)()
                if provider is not None and hasattr(provider, "set_circuit_breaker"):
                    provider.set_circuit_breaker(_CircuitBreaker(threshold=2, reset_after=60))
                    _logger.info("LLM 熔断器已配置（阈值 2，恢复 60s）")
            except Exception:
                pass  # 测试环境或无 provider 时静默跳过

        # 检索 + 分析 + 输出
        self._retriever = RetrieverAdapter(bundle)
        from iris.utils.prompting import PromptTemplateLoader
        self._analyzer = SegmentAnalyzer(
            self._llm,
            PromptTemplateLoader(bundle),
            model=self._cfg.llm_model,
        )
        self._session = MeetingSession()
        # v3.26.2 双主题面板（dark/light，配置驱动）
        self._panel = PanelRenderer(theme=self._cfg.panel_theme)
        self._feed = InsightFeed()      # v3.25.3 洞察推送引擎

        # 输出路径：--output > assistant.output_dir > data/meeting-live/
        if output_path:
            self._doc_path = Path(output_path).expanduser()
            self._doc_path_auto = False  # 用户指定路径，不自动清理
        else:
            out_dir = self._cfg.output_dir or str(get_project_root() / "data" / "meeting-live")
            self._doc_path = (
                Path(out_dir) / f"{datetime.now():%Y%m%d-%H%M%S}-会议记录.md"
            ).expanduser()
            self._doc_path_auto = True   # 自动生成路径，空会议可清理
        self._writer = DocWriter(self._doc_path, rewrite_every=self._cfg.doc_rewrite_every)

        # ASR 引擎（local 模式）
        if self._asr_cfg.mode == "local":
            model_dir = self._asr_cfg.local.model_dir or ASREngine.auto_detect_model_dir()
            if not model_dir:
                raise FileNotFoundError(
                    "未找到 ASR 模型目录。请配置 assistant.asr.local.model_dir "
                    "或安装 vocotype 后自动检测。"
                )
            self._asr_engine = ASREngine(
                model_dir=model_dir,
                hotwords=hotwords,
                device=self._asr_cfg.local.device,
                energy_threshold=self._asr_cfg.local.energy_threshold,
                batch_size_s=self._asr_cfg.local.batch_size_s,
            )
            if not self._asr_engine.is_available():
                raise FileNotFoundError(f"ASR 模型不完整: {model_dir}")
            _logger.info("ASR 引擎就绪（本地 Paraformer）· 模型 %s · 热词 %d 字",
                         model_dir, len(hotwords))
        else:
            self._asr_engine = None
            _logger.info("ASR 模式: %s（remote 待实现）", self._asr_cfg.mode)

        self._pool = ThreadPoolExecutor(max_workers=2)
        # 预取 futures：seq → (deep_future, retr_future)，worker 消费后 pop
        self._futures: Dict[int, Tuple[Future, Future]] = {}
        # _futures 并发保护：音频线程在 _audio_loop 中写，worker 在 _process_segment 中读/pop
        self._futures_lock = threading.Lock()
        # v3.26.1 音频电平追踪（音频线程写，worker 线程读，单值替换无竞态）
        self._last_rms: float = 0.0
        self._last_threshold: float = 0.005
        # v3.26.1 建议提问事件驱动追踪
        self._last_suggest_seq: int = 0  # 上次生成建议的段号
        # v3.26.1 增量总结追踪
        self._last_mini_summary_at: float = time.monotonic()  # 上次迷你总结时间
        # v3.26.1 手动话题边界标记
        self._force_topic_boundary: bool = False

    def _load_llm_prompt(self) -> str:
        """加载 LLM 校正 Prompt；缺失时使用内嵌兜底 Prompt（不依赖 asr-corrector 的 build-asr-prompt 产物）。"""
        path = get_project_root() / "data" / "assistant" / "asr_prompt.md"
        if path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except Exception as e:
                _logger.warning("LLM 校正 Prompt 加载失败: %s", e)
        # 内嵌兜底 Prompt（与 asr-corrector 默认 prompt 逻辑一致）
        return (
            "你是语音转录修正官。将以下语音转写文本中的错别字、同音字修正为正确文本。\n"
            "规则：修正所有明显的音近错字；保持原意和句子结构；只输出修正后的文本，不要解释。\n"
            "{{context}}\n"
            "待修正文本：{{text}}"
        )

    # ── 主流程 ──────────────────────────────────────────────

    def run(self) -> int:
        # v3.25.0 起使用本地麦克风 + FunASR Paraformer，不再依赖剪贴板，
        # 与 asr-corrector 可同时运行（无资源冲突）

        from iris.core.locks import ProcessRegistry
        registry = ProcessRegistry("meeting-live-assistant", self._pid_dir)
        if not registry.register():
            _logger.warning("meeting-live-assistant 已有实例在运行，退出")
            return 1

        # 任务埋点：register 成功即开始（面板实时显示会议进度；
        # Ctrl+C 正常结束 → success，进程被杀 → probe 兜底 interrupted）。
        # v3.28.1：data_root 即 _pid_dir（默认 <项目根>/data，与 ProcessRegistry
        # 的 data/<name>.pid 同级）——旧代码误传 .parent（项目根），埋点写到
        # <项目根>/tasks/，面板 daemon 读 <项目根>/data/tasks/，任务永不可见。
        from iris.taskpanel.reporter import TaskReporter
        with TaskReporter("meeting-live-assistant", command="meeting-live-assistant",
                          data_root=self._pid_dir) as _tr:
            self._task_reporter = _tr

            # Python 3.13 中默认 SIGINT 处理无法中断 time.sleep（主线程睡眠时不抛
            # KeyboardInterrupt），显式注册 handler 保证 Ctrl+C 可靠进入优雅退出
            def _sigint_handler(signum, frame):
                raise KeyboardInterrupt

            signal.signal(signal.SIGINT, _sigint_handler)

            # 会话日志：文件输出到过程文档同目录（session_id 取自文档文件名去扩展名）
            session_id = self._doc_path.stem
            setup_session_logger(self._doc_path.parent, session_id)
            _logger.info("会话日志已启动: %s.log", session_id)

            worker = None
            try:
                if not self._writer.initial_write(self._session.state):
                    return 1

                _logger.info("实时会议助理已启动，过程文档: %s", self._doc_path)
                _logger.info("正在聆听…（说完自动识别，Ctrl+C 退出）")
                _tr.report_phase("listening", f"聆听中… 过程文档: {self._doc_path.name}")

                self._panel.render(PanelDisplay(status="等待语音…", state=self._session.state),
                                  feed=self._feed)

                worker = threading.Thread(target=self._worker_loop, daemon=True)
                worker.start()
                kb_listener = threading.Thread(target=self._keyboard_listener, daemon=True)
                kb_listener.start()
                self._audio_loop()
            except KeyboardInterrupt:
                _logger.info("正在结束会议…")
            finally:
                # 安全关闭：SIG_IGN 防止清理过程被二次 Ctrl+C 中断。
                # 清理完成后直接退出，不恢复 handler（进程即将结束）。
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                self._session.request_stop()
                if worker is not None:
                    worker.join(timeout=_EXIT_JOIN_SEC)
                    if worker.is_alive():
                        _logger.warning("退出等待超时，当前段可能未完成")
                if self._cfg.summary_enabled and self._session.state.segments:
                    try:
                        summary = self._analyzer.summarize(self._session.state)
                        if summary:
                            self._session.state.summary = summary
                            _logger.info("会议总结已生成")
                            _tr.report_phase("summary", "会议总结已生成")
                        else:
                            _logger.warning("会议总结生成失败（跳过）")
                    except Exception:
                        _logger.warning("会议总结异常（跳过）")
                self._writer.maybe_rewrite(self._session.state, force=True)
                self._panel.render_final(self._session.state, self._doc_path)
                self._pool.shutdown(wait=True, cancel_futures=True)
                # v3.26.1: 空会议清理——无语音段时删除自动生成的过程文档
                if not self._session.state.segments and self._doc_path_auto:
                    try:
                        self._doc_path.unlink(missing_ok=True)
                        _logger.info("空会议（无语音段），已清理过程文档")
                    except Exception:
                        pass
                registry.unregister()
                # v3.26.3: 清理 session logger 文件 handler（e2e 测试防句柄泄漏）
                teardown_session_logger()
            self._task_reporter = None
        return 0

    # ── 线程逻辑 ────────────────────────────────────────────

    def _audio_loop(self) -> None:
        """音频采集线程：sounddevice 回调 → ASREngine.feed() → MergeBuffer → submit 段。

        合并/说话人边界策略见 `_audio_capture.MergeBuffer`。
        """
        if self._asr_engine is None:
            _logger.error("ASR 引擎未初始化（mode=%s），无法启动音频采集", self._asr_cfg.mode)
            return
        mic = AudioCapture(sample_rate=self._asr_cfg.local.sample_rate)
        mic.start()
        _silent_ticks = 0
        _heartbeat_at = time.monotonic()
        _peak_rms = 0.0
        buf = MergeBuffer()
        try:
            while not self._session.stop.is_set():
                chunk = mic.read()
                if chunk is None:
                    _silent_ticks += 1
                    for flush in buf.on_silence(time.monotonic()):
                        self._flush_merge(flush.texts, speaker_change_signal=flush.speaker_change_signal)
                    if _silent_ticks == 250:
                        _logger.warning(
                            "⚠ 5 秒未收到音频！请检查系统麦克风权限："
                            "偏好设置→安全性与隐私→麦克风→终端已勾选"
                        )
                    time.sleep(0.02)
                    continue
                _silent_ticks = 0
                # 追踪峰值（供心跳日志）
                rms = rms_of(chunk)
                self._last_rms = rms
                self._last_threshold = self._asr_engine.effective_threshold
                _peak_rms = max(_peak_rms, rms)
                # 每 10 秒输出一次心跳（让用户知道系统在监听）
                now = time.monotonic()
                if now - _heartbeat_at > 10:
                    bar = "█" * int(_peak_rms * 500)
                    _logger.debug("🔊 峰值 %.4f %s（噪声 %.4f 阈值 %.4f）",
                                  _peak_rms, bar, self._asr_engine.noise_floor,
                                  self._asr_engine.effective_threshold)
                    _peak_rms = 0.0
                    _heartbeat_at = now
                text = self._asr_engine.feed(chunk)
                # 噪音门控：拦截 ASR 幻觉/键盘噪音/碎片
                if text and not self._is_noise(text):
                    for flush in buf.push(text, time.monotonic()):
                        self._flush_merge(flush.texts, speaker_change_signal=flush.speaker_change_signal)
        finally:
            # 退出前刷新 merge buffer 中剩余内容
            for flush in buf.drain():
                self._flush_merge(flush.texts)
            mic.stop()

    def _flush_merge(self, texts: list[str], speaker_change_signal: bool = False) -> None:
        """将 merge buffer 中累积的连续短句合并提交为一个段。"""
        merged = "".join(texts)
        # v3.26.1: 单段长度上限检查（使用可配的 max_segment_chars）
        if len(merged) > self._cfg.max_segment_chars:
            _logger.warning("段过长（%d 字符 > %d），已截断",
                           len(merged), self._cfg.max_segment_chars)
            merged = merged[:self._cfg.max_segment_chars] + "…"
        # 合并后也过一遍噪音检测（极端情况：连续噪音段被合并）
        if self._is_noise(merged):
            _logger.debug("合并段被噪音门控拦截（%d 字，%d 句）",
                         len(merged), len(texts))
            return
        fast = self._corrector.fast(merged)
        # AC 词典校正变化 → 日志可见
        if fast != merged:
            _logger.info("📖 AC 校正: %s → %s", merged, fast)
        seg = self._session.submit(
            merged,
            on_publish=lambda s, f=fast: self._publish_prefetch(s, f),
        )
        seg.speaker_change_signal = speaker_change_signal
        # v3.26.1: 长段（>300 字）标记为可能非自然停顿
        if len(merged) > 300:
            seg.forced_cut = True
        _logger.info("语音段 %d 已识别（%d 字%s），排队分析…",
                     seg.seq, len(merged),
                     f"，合并 {len(texts)} 句" if len(texts) > 1 else "")

    @staticmethod
    def _is_noise(text: str) -> bool:
        """ASR 后置噪音检测（见 `_audio_capture.is_noise`）。"""
        return is_noise(text)

    # ── 键盘交互（v3.25.3）────────────────────────────────

    def _keyboard_listener(self) -> None:
        """非阻塞单键监听（daemon 线程）。?=帮助 d=决策 t=话题 q=退出。

        v3.25.4 修复：使用 select 替代阻塞 read(1)，Ctrl+C 退出时 stop 标志
        可被及时检测，保证终端属性恢复（tcsetattr）。
        """
        import select
        import sys
        import termios
        import tty
        try:
            fd = sys.stdin.fileno()
        except (io.UnsupportedOperation, AttributeError):
            return
        try:
            old = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except termios.error:
            return
        try:
            while not self._session.stop.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.5)
                if r:
                    ch = sys.stdin.read(1)
                    if ch:
                        self._handle_key(ch)
        except (EOFError, OSError, ValueError):
            pass
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    def _handle_key(self, ch: str) -> None:
        """处理单键命令。"""
        if ch == "?":
            _logger.info(
                "⌨ 快捷键: d=已决策  t=话题  a=待办  m=标记话题  s=暂停推送  q=退出")
        elif ch == "d":
            state = self._session.state
            # 从各段分析中收集 confirmed 决策（state.decisions 是累计去重字符串，无置信度）
            confirmed = []
            for s in state.segments:
                if s.analysis:
                    for d in s.analysis.decisions:
                        if d.confidence == "confirmed" and d.text not in confirmed:
                            confirmed.append(d.text)
            if confirmed:
                _logger.info("✅ 已确认决策（%d 条）:", len(confirmed))
                for d in confirmed[-10:]:
                    _logger.info("  · %s", d)
            else:
                _logger.info("📋 暂无已确认决策")
        elif ch == "t":
            state = self._session.state
            if state.current_topic:
                _logger.info("📌 当前话题: %s", state.current_topic)
            if state.topics:
                _logger.info("📌 已讨论话题（%d 个）:", len(state.topics))
                for t in state.topics[-5:]:
                    _logger.info("  · %s (段 %d-%d)", t["label"], t["start_seq"], t.get("end_seq", 0))
        elif ch == "a":
            state = self._session.state
            if state.open_questions:
                _logger.info("❓ 待解决问题（%d 条）:", len(state.open_questions))
                for q in state.open_questions[-8:]:
                    _logger.info("  · %s", q)
            else:
                _logger.info("❓ 暂无待解决问题")
        elif ch == "m":
            # v3.26.1: 手动标记话题边界（下一批分析时注入提示）
            self._force_topic_boundary = True
            _logger.info("📌 已标记话题边界（下批分析生效）")
        elif ch == "q":
            _logger.info("⌨ 收到退出指令")
            self._session.request_stop()
        elif ch == "s":
            paused = self._feed.toggle_pause()
            _logger.info("⌨ 洞察推送已%s", "暂停" if paused else "恢复")

    def _publish_prefetch(self, seg: VoiceSegment, fast: str) -> None:
        """预取发布（submit 临界区内调用）：fast 入窗 + 提交 deep/检索 futures。

        - 在临界区内执行 ⇒ worker 取走段时 futures 必已注册，且清理循环
          迭代期间无其他线程改写 _futures（worker 在 cond 上等待）——双段
          流水线无竞态、无双跑
        - 短段门控（< short_segment_chars）与 fast_only：跳过全部 LLM 前置
        - 过期段（pending 被覆盖）的 futures 无 worker 消费，结果自然废弃
          （与积压丢弃哲学一致；LLM 成本有界：deep 8s / 检索 8s deadline）
        - 回调须 µs 级：此处只有 dict 操作与 deque.append（均无锁开销）
        - 整体兜底：回调在 submit 临界区内执行，任何异常不得阻断 pending 设置
          （session 状态机契约：on_publish 不抛未捕获异常）
        """
        try:
            seg.corrected_text = fast
            sp = getattr(seg, "speaker", None)
            sp_id = sp.speaker_id if sp else ""
            self._corrector.push_context(fast, speaker_id=sp_id)
            if self._cfg.fast_only or len(fast) < self._cfg.short_segment_chars:
                return
            with self._futures_lock:
                if self._asr_cfg.llm_correct_enabled:
                    self._futures[seg.seq] = (
                        self._pool.submit(self._corrector.deep, fast, speaker_id=sp_id),
                        self._pool.submit(self._retriever.search, fast, top_k=self._cfg.top_k),
                    )
                else:
                    # LLM 校正关闭：仅提交检索（deep 降级为返回原文）
                    from concurrent.futures import Future as _Future
                    _f = _Future()
                    _f.set_result(fast)
                    self._futures[seg.seq] = (
                        _f,
                        self._pool.submit(self._retriever.search, fast, top_k=self._cfg.top_k),
                    )
                # 清理过期条目：worker 消费段时已 pop 自己的 futures；
                # 仍残留且 seq 更小的条目 = pending 被覆盖的段，结果无人消费
                for old_seq in [k for k in self._futures if k < seg.seq]:
                    self._futures.pop(old_seq, None)
        except RuntimeError:
            pass  # 池已关闭（退出窗口）：worker 侧现场提交兜底
        except Exception as e:
            _logger.warning(
                "预取发布异常（段 %d 仍以 fast-only 落账）: %s",
                seg.seq, e, exc_info=True,
            )

    def _worker_loop(self) -> None:
        """工作线程：累积段 → 批量分析（一次 LLM 调用覆盖多段，减少碎片化分析）。

        v3.25.2 批量分析：替代逐段分析。最多等 2s 或攒够 5 段后，
        合并所有校正文本为一次 LLM 分析输入，提取跨段的完整要点/决策/风险。
        """
        _BATCH_MAX = 5
        _BATCH_TIMEOUT = 2.0   # 最多等 2s 累积更多段

        while not self._session.stop.is_set():
            seg = self._session.take_pending(timeout=self._cfg.poll_interval)
            if seg is None:
                continue

            batch = [seg]
            deadline = time.monotonic() + _BATCH_TIMEOUT
            while len(batch) < _BATCH_MAX:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                next_seg = self._session.take_pending(timeout=min(remaining, 0.3))
                if next_seg is None:
                    break
                batch.append(next_seg)

            try:
                self._process_batch(batch)
            except Exception as e:
                _logger.error("批次（段 %d-%d）处理异常: %s",
                             batch[0].seq, batch[-1].seq, e)

    def _process_batch(self, batch: list[VoiceSegment]) -> None:
        """批量处理：各段独立 fast/deep/检索 → 合并文本一次 LLM 分析 → 结果共享。

        v3.25.2 四层架构 Layer 4：替代逐段分析。短段跳过 LLM 但仍记录原文，
        可分析段拼接校正文本后一次 LLM 调用提取跨段要点/决策/风险/问题。
        首个可分析段承载分析结果，其余段标记 ANALYSIS_MERGED。
        """
        # ── Phase 1: fast 兜底 + 短段跳过 ──
        skipped, analyzable = self._split_batch(batch)
        if not analyzable:
            self._finish_skipped_batch(batch, skipped)
            return

        # ── Phase 2: 各段收集 deep 校正 + 检索 ──
        first = analyzable[0]
        self._phase_start = time.monotonic()  # v3.26.3: 追踪分析实际耗时
        combined_text, retrieval_ctx = self._assemble_batch_input(analyzable)

        # ── Phase 3: 合并文本 → 一次 LLM 分析 ──
        self._analyze_batch(first, analyzable, combined_text, retrieval_ctx)

        # ── v3.25.3 话题追踪 + 洞察推送 ──
        if first.analysis:
            apply_analysis(self._session.state, self._feed, first.analysis, analyzable,
                           agenda=self._cfg.agenda)

        # ── Phase 4: 建议提问（固定间隔 + 事件驱动，v3.26.1）──
        self._maybe_suggest(first, retrieval_ctx)

        # ── Phase 5: 按 seq 升序落账（保证 segments 列表严格时间有序）──
        self._record_batch(skipped, analyzable, first)

        # ── Phase 6: 面板 + 文档 ──
        self._render_batch(batch, first, len(analyzable), len(skipped))
        self._maybe_mini_summary()

    def _split_batch(self, batch: list[VoiceSegment]) -> tuple[list[VoiceSegment], list[VoiceSegment]]:
        """fast 兜底 + 短段/fast_only 跳过 → (skipped, analyzable)。"""
        skipped: list[VoiceSegment] = []
        analyzable: list[VoiceSegment] = []
        for seg in batch:
            if not seg.corrected_text:
                seg.corrected_text = self._corrector.fast(seg.raw_text)
                self._corrector.push_context(seg.corrected_text)
            if self._cfg.fast_only or len(seg.corrected_text) < self._cfg.short_segment_chars:
                seg.analysis_status = VoiceSegment.ANALYSIS_SKIPPED
                skipped.append(seg)
            else:
                analyzable.append(seg)
        return skipped, analyzable

    def _finish_skipped_batch(self, batch: list[VoiceSegment], skipped: list[VoiceSegment]) -> None:
        """全批次跳过：直接落账 + 面板 + 文档。"""
        for s in skipped:
            self._session.record(s)
        last = batch[-1]
        self._panel.render(PanelDisplay(
            status=f"已处理段 {last.seq}（跳过分析）",
            seg=last, state=self._session.state,
            topic=self._session.state.current_topic),
            feed=self._feed)
        self._writer.maybe_rewrite(self._session.state)

    def _assemble_batch_input(self, analyzable: list[VoiceSegment]) -> tuple[str, str]:
        """收集 deep 校正 + 检索，组装 LLM 分析输入 → (combined_text, retrieval_ctx)。"""
        # 批量收集 deep/检索：一次性 wait 全部 futures（避免逐段 wait 放大
        # LLM 超时——批处理 5 段最坏从 50s 降到 10s）
        results = self._collect_batch_deep_retrieval(analyzable)
        batch_texts = batch_hints(analyzable, force_topic_boundary=self._force_topic_boundary)
        self._force_topic_boundary = False
        all_hits: list = []
        for seg, (deep, hits) in zip(analyzable, results):
            if deep != seg.corrected_text:
                _logger.info("🤖 LLM 校正 段%d: %s → %s", seg.seq, seg.corrected_text, deep)
                seg.corrected_text = deep
                self._corrector.push_context(deep, speaker_id=speaker_id_of(seg))
            batch_texts.append(segment_line(seg))
            all_hits.extend(hits)
        unique_hits = dedup_hits(all_hits)
        return "\n".join(batch_texts), RetrieverAdapter.format_context(unique_hits[:10])

    def _analyze_batch(self, first: VoiceSegment, analyzable: list[VoiceSegment],
                       combined_text: str, retrieval_ctx: str) -> None:
        """一次 LLM 分析，结果挂在首个可分析段；异常 → ANALYSIS_FAILED。"""
        first.analysis_started_at = time.monotonic()
        # v3.26.1: 更新面板显示分析进度
        # v3.26.3: analysis_elapsed 传递实际耗时（不再恒为 0）
        phase_elapsed = time.monotonic() - getattr(self, '_phase_start', time.monotonic())
        self._panel.render(PanelDisplay(
            status="LLM 分析中…", seg=first,
            analysis_phase="analyze", analysis_elapsed=phase_elapsed,
            state=self._session.state, topic=self._session.state.current_topic),
            feed=self._feed)
        try:
            first.analysis = self._analyzer.analyze(
                combined_text,
                retrieval_ctx,
                self._session.summary_for_prompt(),
                open_questions=self._session.open_questions_for_prompt(),
                adjacent_context=self._session.adjacent_context(first.seq),
                agenda=self._cfg.agenda,
            )
            first.analysis_done_at = time.monotonic()
            first.analysis_status = (
                VoiceSegment.ANALYSIS_DONE if first.analysis
                else VoiceSegment.ANALYSIS_FAILED
            )
        except Exception as e:
            _logger.warning("批次分析异常（段 %d-%d）: %s",
                           first.seq, analyzable[-1].seq, e)
            first.analysis = None
            first.analysis_status = VoiceSegment.ANALYSIS_FAILED

    def _maybe_suggest(self, first: VoiceSegment, retrieval_ctx: str) -> None:
        """建议提问：满足节流条件时调 LLM；否则清空。"""
        if first.analysis is None:
            return
        if not should_suggest(first.analysis, first.seq, self._last_suggest_seq, self._cfg.suggest_every):
            first.analysis.suggested_questions = []
            return
        try:
            deadline = (getattr(first, 'analysis_started_at', time.monotonic())
                        + _ANALYSIS_DEADLINE_SEC)
            sharp = self._analyzer.suggest_questions(
                first.analysis,
                self._session.summary_for_prompt(),
                retrieval_ctx,
                deadline=deadline,
            )
            if sharp:
                first.analysis.suggested_questions = sharp
                self._last_suggest_seq = first.seq
        except Exception:
            pass

    def _record_batch(self, skipped: list[VoiceSegment], analyzable: list[VoiceSegment],
                      first: VoiceSegment) -> None:
        """按 seq 升序落账；非首段标记 ANALYSIS_MERGED。

        v3.26.3: 修复多段批次中 first (seq最小) 被最后 record 导致的乱序。
        """
        all_segs: list[VoiceSegment] = sorted(skipped + analyzable, key=lambda s: s.seq)
        for s in all_segs:
            if s in analyzable and s is not first:
                s.analysis_status = VoiceSegment.ANALYSIS_MERGED
            try:
                self._session.record(s)
            except Exception as e:
                _logger.error("段 %d 落账异常: %s", s.seq, e)

    def _render_batch(self, batch: list[VoiceSegment], first: VoiceSegment,
                      n_analyzed: int, n_skipped: int) -> None:
        """面板 + 文档 + 任务埋点。"""
        # v3.26.1: 收集系统告警 + 音频电平
        alerts = []
        if self._writer.is_failing:
            alerts.append("文档写入失败（磁盘空间不足？）")
        engine = getattr(self, '_asr_engine', None)
        failures = getattr(engine, '_consecutive_failures', 0) if engine is not None else 0
        if failures > 0:
            alerts.append(f"ASR 异常 ({failures} 次)")
        self._panel.render(PanelDisplay(
            status=batch_label(batch, n_analyzed, n_skipped), seg=first,
            analysis_unavailable=first.analysis is None,
            state=self._session.state,
            topic=self._session.state.current_topic,
            alerts=alerts if alerts else None,
            rms_level=rms_level(self._last_rms, self._last_threshold)),
            feed=self._feed)
        self._writer.maybe_rewrite(self._session.state)
        # 任务埋点：每批处理后上报累计段数（常驻任务无总量，progress 留空）
        _tr = getattr(self, "_task_reporter", None)
        if _tr is not None:
            _tr.report_phase("analyze", f"已处理 {len(self._session.state.segments)} 段")

    def _maybe_mini_summary(self) -> None:
        """v3.26.1: 每 15 分钟且 ≥10 段触发增量迷你总结。"""
        _MINI_SUMMARY_INTERVAL = 900.0  # 15 分钟
        _MINI_SUMMARY_MIN_SEGMENTS = 10
        if not (time.monotonic() - self._last_mini_summary_at > _MINI_SUMMARY_INTERVAL
                and len(self._session.state.segments) >= _MINI_SUMMARY_MIN_SEGMENTS):
            return
        try:
            mini = self._analyzer.mini_summarize(self._session.state)
            if mini:
                # 带时间戳存储，渲染进文档时用户可看到生成时刻
                self._session.state.mini_summaries.append(f"[{datetime.now():%H:%M}] {mini}")
                _logger.info("📝 阶段性总结: %s", mini)
            self._last_mini_summary_at = time.monotonic()
        except Exception:
            pass

    def _collect_batch_deep_retrieval(self, segments: list[VoiceSegment]) -> list:
        """批量收集 deep/检索：一次性 wait 全部 futures（v3.25.4 性能修复）。

        预取 futures 在音频线程已并行提交（线程池），此处统一 pop + 一次 wait，
        避免逐段 wait 在 LLM 全线超时时把等待放大 N 倍。
        """
        entries: list[tuple[VoiceSegment, Optional[Tuple[Future, Future]]]] = []
        with self._futures_lock:
            for seg in segments:
                futures = self._futures.pop(seg.seq, None)
                if futures is None:
                    try:
                        sp = getattr(seg, "speaker", None)
                        sp_id = sp.speaker_id if sp else ""
                        futures = (
                            self._pool.submit(self._corrector.deep,
                                              seg.corrected_text, speaker_id=sp_id),
                            self._pool.submit(self._retriever.search,
                                              seg.corrected_text, top_k=self._cfg.top_k),
                        )
                    except RuntimeError:
                        futures = None
                entries.append((seg, futures))
        # 一次性 wait 所有 futures（共享同一超时窗）
        flat = [f for _, fs in entries if fs is not None for f in fs]
        done_all = set()
        if flat:
            done_all, _ = wait(flat, timeout=_PARALLEL_WAIT_SEC)
        results = []
        for seg, futures in entries:
            deep, hits = seg.corrected_text, []
            if futures is not None:
                done = {f for f in futures if f in done_all}
                deep, hits = self._collect_results(futures, done, seg.corrected_text)
            results.append((deep, hits))
        return results

    @staticmethod
    def _collect_results(futures: Tuple[Future, Future], done: set, fast: str):
        """并行结果收集：超时/异常/取消各自降级（deep→fast，检索→[]）。

        超时未完成的 future 显式 cancel 释放线程池槽位，防止连续超时场景
        下池中槽位被「已放弃但仍在跑」的调用占满。
        """
        f_deep, f_retr = futures
        deep, hits = fast, []
        try:
            if f_deep in done and not f_deep.cancelled():
                deep = f_deep.result()
        except Exception as e:
            _logger.warning("深度校正异常，保留词典结果: %s", e)
        else:
            if not f_deep.done():
                f_deep.cancel()  # 超时未完成 → 释放槽位
        try:
            if f_retr in done and not f_retr.cancelled():
                hits = f_retr.result()
        except Exception as e:
            _logger.warning("检索异常，降级为空上下文: %s", e)
        else:
            if not f_retr.done():
                f_retr.cancel()  # 超时未完成 → 释放槽位
        return deep, hits
