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
from ._corrector import CorrectorAdapter
from ._doc_writer import DocWriter
from ._insight import InsightEvent, InsightFeed
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
                hotwords = " ".join(lines)
                # v3.26.1: 热词总长校验
                _MAX_HOTWORDS_CHARS = 10000
                if len(hotwords) > _MAX_HOTWORDS_CHARS:
                    _logger.warning("热词过长（%d 字符），截断到 %d 字符", len(hotwords), _MAX_HOTWORDS_CHARS)
                    truncated: list[str] = []
                    current = 0
                    for line in lines:
                        if current + len(line) + 1 > _MAX_HOTWORDS_CHARS:
                            break
                        truncated.append(line)
                        current += len(line) + 1
                    hotwords = " ".join(truncated)
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
        return 0

    # ── 线程逻辑 ────────────────────────────────────────────

    def _audio_loop(self) -> None:
        """音频采集线程：sounddevice 回调 → ASREngine.feed() → merge buffer → submit 段。

        v3.25.2 merge buffer：说话人自然停顿（思考、看数据）期间 VAD 可能切段，
        将间隔 ≤ _MERGE_WINDOW 的连续短句合并为一个段再提交，减少碎片化。
        """
        if self._asr_engine is None:
            _logger.error("ASR 引擎未初始化（mode=%s），无法启动音频采集", self._asr_cfg.mode)
            return
        mic = AudioCapture(sample_rate=self._asr_cfg.local.sample_rate)
        mic.start()
        _silent_ticks = 0
        _heartbeat_at = time.monotonic()
        _peak_rms = 0.0
        # merge buffer：将 VAD 连续短间隔输出合并后再提交
        _merge_texts: list[str] = []
        _merge_time = 0.0
        _MERGE_WINDOW = 3.0       # 3 秒内连续语音合并
        _MERGE_MAX_CHARS = 500    # 合并总长上限（防内存膨胀）
        # v3.25.5 说话人间隙门控：VAD 间隙超过阈值时不合并（优先保证不跨人）
        _SPEAKER_GAP = 0.8        # > 0.8s 可能是说话人切换
        _SPEAKER_GAP_STRONG = 2.0  # > 2.0s 几乎一定是切换
        try:
            while not self._session.stop.is_set():
                chunk = mic.read()
                if chunk is None:
                    _silent_ticks += 1
                    # 静音期间检查 merge buffer 是否过期需刷新
                    # v3.25.5: 静音 >3s 是最强的说话人切换信号 → 标记
                    if _merge_texts and (time.monotonic() - _merge_time) > _MERGE_WINDOW:
                        self._flush_merge(_merge_texts, speaker_change_signal=True)
                        _merge_texts = []
                    if _silent_ticks == 250:
                        _logger.warning(
                            "⚠ 5 秒未收到音频！请检查系统麦克风权限："
                            "偏好设置→安全性与隐私→麦克风→终端已勾选"
                        )
                    time.sleep(0.02)
                    continue
                _silent_ticks = 0
                # 追踪峰值（供心跳日志）
                import numpy as np
                rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
                self._last_rms = rms
                self._last_threshold = self._asr_engine.effective_threshold
                if rms > _peak_rms:
                    _peak_rms = rms
                # 每 10 秒输出一次心跳（让用户知道系统在监听）
                now = time.monotonic()
                if now - _heartbeat_at > 10:
                    bar = "█" * int(_peak_rms * 500)
                    thr = self._asr_engine.effective_threshold
                    nf = self._asr_engine.noise_floor
                    _logger.debug("🔊 峰值 %.4f %s（噪声 %.4f 阈值 %.4f）",
                                  _peak_rms, bar, nf, thr)
                    _peak_rms = 0.0
                    _heartbeat_at = now
                text = self._asr_engine.feed(chunk)
                if text:
                    # ── 噪音门控：拦截 ASR 幻觉/键盘噪音/碎片 ──
                    if self._is_noise(text):
                        continue
                    now = time.monotonic()
                    # ── v3.25.5 说话人边界：间隙过大时不合并 ──
                    gap = now - _merge_time if _merge_texts else float("inf")
                    speaker_change = False
                    if _merge_texts and gap > _SPEAKER_GAP:
                        if gap > _SPEAKER_GAP_STRONG:
                            speaker_change = True
                        elif len(_merge_texts) >= 2:
                            # 弱信号 + 已有累积 → 保守刷新（宁多一段不合并错人）
                            speaker_change = True
                    if speaker_change:
                        self._flush_merge(_merge_texts, speaker_change_signal=True)
                        _merge_texts = []
                        gap = float("inf")

                    # ── 内容感知合并 ──
                    cur_len = len(text)
                    prev_total = sum(len(t) for t in _merge_texts) if _merge_texts else 0
                    should_merge = False
                    if _merge_texts and gap <= _MERGE_WINDOW:
                        should_merge = True  # 正常窗口内
                    elif _merge_texts and gap <= 6.0:
                        # 放宽窗口：短段（<8字）或前段也短（<15字）继续等
                        if cur_len < 8 or prev_total < 15:
                            should_merge = True

                    if should_merge:
                        combined_len = prev_total + cur_len
                        if combined_len <= _MERGE_MAX_CHARS:
                            _merge_texts.append(text)
                            _merge_time = now
                            continue
                        self._flush_merge(_merge_texts)
                        _merge_texts = []
                    else:
                        if _merge_texts:
                            self._flush_merge(_merge_texts)
                            _merge_texts = []
                    # 启动新合并窗口
                    _merge_texts = [text]
                    _merge_time = now
        finally:
            # 退出前刷新 merge buffer 中剩余内容
            if _merge_texts:
                self._flush_merge(_merge_texts)
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
        """ASR 后置噪音检测：拦截幻觉/键盘噪音/英文碎片，不进入管线。

        以下模式视为噪音：
        1. 单字符连续重复 ≥6 次（"不不不不不不…"、"据据据据…"）
        2. 无有效内容（纯标点/空白）
        3. 极短文本（≤1 字符）
        4. 零中文字符且总长 <15（"yeah"、"OK"、"ststeteding"）
        """
        import re
        if not text or not text.strip():
            return True
        # 单字符重复（ASR 幻觉：电流噪音/键盘撞击被当成语音）
        if re.search(r"(.)\1{5,}", text):
            return True
        # 长度 ≤1 的非应答词
        stripped = text.strip()
        if len(stripped) <= 1:
            return True
        # 纯英文/拼音碎片：零中文字符且不长（"yeah", "OK", "ststeteding"）
        cjk = len(re.findall(r"[一-鿿]", stripped))
        if cjk == 0 and len(stripped) < 15:
            return True
        # v3.26.1: 混合文本噪音判定——少量中文+大量英文（疑似代码/日志误识别）
        if cjk <= 2 and len(stripped) > 30:
            cjk_ratio = cjk / len(stripped)
            if cjk_ratio < 0.1:
                return True
        return False

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

        if not analyzable:
            # 全批次跳过
            for s in skipped:
                self._session.record(s)
            last = batch[-1]
            self._panel.render(PanelDisplay(
                status=f"已处理段 {last.seq}（跳过分析）",
                seg=last, state=self._session.state,
                topic=self._session.state.current_topic),
                feed=self._feed)
            self._writer.maybe_rewrite(self._session.state)
            return

        # ── Phase 2: 各段收集 deep 校正 + 检索 ──
        first = analyzable[0]
        self._phase_start = time.monotonic()  # v3.26.3: 追踪分析实际耗时
        n = len(analyzable)

        batch_texts: list[str] = []
        all_hits: list = []
        # 批量收集 deep/检索：一次性 wait 全部 futures（避免逐段 wait 放大
        # LLM 超时——批处理 5 段最坏从 50s 降到 10s）
        results = self._collect_batch_deep_retrieval(analyzable)
        # v3.25.5: VAD 说话人切换信号 → 注入 batch_texts 供 LLM 参考
        # v3.26.1: 批内多 speaker_change_signal → 强化提示帮助 LLM 区分微话题
        # v3.26.1: 手动话题边界标记（m 键）
        if self._force_topic_boundary:
            batch_texts.append("[用户标记：此处为话题边界，请将 topic_change 设为 true]")
            self._force_topic_boundary = False
        speaker_change_count = sum(1 for s in analyzable if getattr(s, "speaker_change_signal", False))
        if speaker_change_count > 0:
            if speaker_change_count >= 2:
                batch_texts.append(
                    "[注意：批内检测到多次说话人切换，以下段落可能涉及不同微话题，"
                    "请分别为各说话人段标注 topic 和 speaker 信息]"
                )
            else:
                batch_texts.append("[VAD 检测到可能的说话人切换，请确认 speaker.is_turn_change]")
        # v3.26.1: 连续 forced_cut 段 → 标注可能是同一发言的延续
        forced_count = sum(1 for s in analyzable if getattr(s, "forced_cut", False))
        if forced_count >= 2:
            batch_texts.append("[注意：以下多段为 ASR 强制切段（非自然停顿），可能属于同一人的连续发言]")
        for seg, (deep, hits) in zip(analyzable, results):
            if deep != seg.corrected_text:
                _logger.info("🤖 LLM 校正 段%d: %s → %s",
                           seg.seq, seg.corrected_text, deep)
                seg.corrected_text = deep
                sp = getattr(seg, "speaker", None)
                sp_id = sp.speaker_id if sp else ""
                self._corrector.push_context(deep, speaker_id=sp_id)
            # v3.25.5 batch_texts 加 speaker 标签
            # v3.26.1: 连续 forced_cut 段标注连续性提示
            sp = getattr(seg, "speaker", None)
            sp_label = f"（{sp.speaker_id}）" if (sp and sp.speaker_id) else ""
            batch_texts.append(f"段{seg.seq}{sp_label}：{seg.corrected_text}")
            all_hits.extend(hits)

        # 检索去重
        seen = set()
        unique_hits = []
        for h in all_hits:
            key = (h.title, (h.content_preview or "")[:50])
            if key not in seen:
                seen.add(key)
                unique_hits.append(h)

        # ── Phase 3: 合并文本 → 一次 LLM 分析 ──
        combined_text = "\n".join(batch_texts)
        retrieval_ctx = RetrieverAdapter.format_context(unique_hits[:10])
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

        # ── v3.25.3 话题追踪 + 洞察推送 ──
        if first.analysis:
            analysis = first.analysis
            state = self._session.state
            # 话题变化 → 推送（以实际状态变化为准，兼容 topic_change 缺失）
            if analysis.topic:
                prev_topic = state.current_topic
                state.update_topic(
                    analysis.topic, analysis.topic_change,
                    analysis.topic_summary, first.seq)
                if prev_topic and prev_topic != state.current_topic:
                    self._feed.push_topic_change(analysis.topic)
            # 决策 → 推送
            for d in analysis.decisions:
                if d.confidence == "confirmed":
                    self._feed.push_decision(d.text, "confirmed")
            # 风险 → 推送（只推前 2 条）
            for r in analysis.risks[:2]:
                self._feed.push_risk(r)
            # 冲突 → 推送
            if analysis.key_points:
                conflicts = state.check_conflict(analysis.key_points)
                for c in conflicts:
                    self._feed.push_conflict(c)
                    _logger.warning("⚠ 语义冲突: %s", c)
            # 待办 → 累计 + 推送
            if analysis.todos:
                for t in analysis.todos:
                    if t.text and t.text not in state.todos:
                        state._dedup_append(state.todos, [t.text])
                for t in analysis.todos[:2]:
                    assignee = f"（{t.assignee}）" if t.assignee else ""
                    self._feed.push(InsightEvent(
                        event_type="todo", text=f"{t.text}{assignee}"))
            # ── v3.25.5 说话人追踪 ──
            if analysis.speaker and analysis.speaker.speaker_id:
                sp = analysis.speaker
                # 新说话人登记
                if sp.speaker_id not in [s.get("id") for s in state.speakers]:
                    state.speakers.append({
                        "id": sp.speaker_id, "role": sp.role_hint,
                        "first_seen": first.seq, "segments": 1,
                    })
                else:
                    for s in state.speakers:
                        if s["id"] == sp.speaker_id:
                            s["segments"] = s.get("segments", 0) + len(analyzable)
                # 更新段的 speaker（后验传递，仅 VoiceSegment）
                for seg in analyzable:
                    if hasattr(seg, "speaker"):
                        seg.speaker = sp
                # 说话人切换 → 推送洞察
                if sp.is_turn_change:
                    role = f"（{sp.role_hint}）" if sp.role_hint else ""
                    self._feed.push(InsightEvent(
                        event_type="speaker_turn",
                        text=f"{sp.speaker_id}{role} 发言"))

            # 跑偏检测：有议程但当前话题偏离
            if self._cfg.agenda and analysis.topic:
                agenda_keywords = self._cfg.agenda.replace("；", ";").split(";")
                topic_lower = analysis.topic.lower()
                on_agenda = any(
                    kw.strip().lower() in topic_lower or topic_lower in kw.strip().lower()
                    for kw in agenda_keywords if kw.strip()
                )
                if not on_agenda and len(state.topics) >= 1:
                    _logger.info("⚠ 跑偏提醒: 当前话题「%s」不在预设议程中", analysis.topic)

        # ── Phase 4: 建议提问（固定间隔 + 事件驱动，v3.26.1）──
        should_suggest = False
        if first.analysis:
            # 条件 1：固定间隔采样（保留原有逻辑）
            if (first.seq - 1) % self._cfg.suggest_every == 0:
                should_suggest = True
            else:
                # v3.26.1: 事件驱动条件（tentative 决策 / 新问题）统一节流——
                # 距上次实际生成建议 ≥ suggest_every 段才触发，防止高频 LLM 调用
                since_last = first.seq - self._last_suggest_seq
                if since_last >= self._cfg.suggest_every:
                    # 条件 2：检测到 tentative 决策 → 追问确认
                    if any(d.confidence == "tentative" for d in first.analysis.decisions):
                        should_suggest = True
                    # 条件 3：有新的未解决问题
                    elif first.analysis.questions:
                        should_suggest = True
        if should_suggest and first.analysis:
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
        elif first.analysis:
            first.analysis.suggested_questions = []

        # ── Phase 5: 按 seq 升序落账（保证 segments 列表严格时间有序）──
        # v3.26.3: 修复多段批次中 first (seq最小) 被最后 record 导致的乱序
        all_segs: list[VoiceSegment] = skipped + analyzable
        all_segs.sort(key=lambda s: s.seq)
        for s in all_segs:
            if s in analyzable and s is not first:
                s.analysis_status = VoiceSegment.ANALYSIS_MERGED
            try:
                self._session.record(s)
            except Exception as e:
                _logger.error("段 %d 落账异常: %s", s.seq, e)

        # ── Phase 6: 面板 + 文档 ──
        if len(batch) == 1:
            label = f"已处理段 {first.seq}"
        else:
            skipped_n = len(skipped)
            analyze_note = f"{n} 段合并分析" if skipped_n == 0 else (
                f"{n} 段合并分析 + {skipped_n} 段跳过")
            label = f"已处理段 {batch[0].seq}-{batch[-1].seq}（{analyze_note}）"
        # v3.26.1: 收集系统告警 + 音频电平
        alerts = []
        if self._writer.is_failing:
            alerts.append("文档写入失败（磁盘空间不足？）")
        if (hasattr(self, '_asr_engine') and self._asr_engine is not None
                and getattr(self._asr_engine, '_consecutive_failures', 0) > 0):
            alerts.append(f"ASR 异常 ({self._asr_engine._consecutive_failures} 次)")
        # 音频电平（归一化到 0-1）
        thr = self._last_threshold or 0.005
        rms_level = min(1.0, self._last_rms / thr) if self._last_rms > 0 else 0.0
        self._panel.render(PanelDisplay(
            status=label, seg=first,
            analysis_unavailable=first.analysis is None,
            state=self._session.state,
            topic=self._session.state.current_topic,
            alerts=alerts if alerts else None,
            rms_level=rms_level),
            feed=self._feed)
        self._writer.maybe_rewrite(self._session.state)

        # v3.26.1: 每 15 分钟或每 30 段触发增量迷你总结
        _MINI_SUMMARY_INTERVAL = 900.0  # 15 分钟
        _MINI_SUMMARY_MIN_SEGMENTS = 10
        if (time.monotonic() - self._last_mini_summary_at > _MINI_SUMMARY_INTERVAL
                and len(self._session.state.segments) >= _MINI_SUMMARY_MIN_SEGMENTS):
            try:
                mini = self._analyzer.mini_summarize(self._session.state)
                if mini:
                    # 带时间戳存储，渲染进文档时用户可看到生成时刻
                    self._session.state.mini_summaries.append(
                        f"[{datetime.now():%H:%M}] {mini}")
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
