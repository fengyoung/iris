"""主编排：MeetingLiveAssistant 常驻进程（采集 → 校正 → 检索 → 分析 → 面板/文档）。

v3.23.3 双段流水线：poll 线程预取（fast 校正 + 提交 deep/检索 futures），
worker 只做等待/分析——段 N 分析期间段 N+1 的深度校正与检索已在池中运行，
每段关键路径从 ~25s 降到 ~15s 且深度重叠。
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from iris.utils.paths import get_project_root, resolve_data_path

from ._analyzer import SegmentAnalyzer, _ANALYSIS_DEADLINE_SEC
from ._clipboard import ClipboardWatcher
from ._corrector import CorrectorAdapter
from ._doc_writer import DocWriter
from ._logging import setup_session_logger
from ._panel import PanelDisplay, PanelRenderer
from ._retriever import RetrieverAdapter
from ._session import MeetingSession
from .models import AssistantConfig, VoiceSegment

_logger = logging.getLogger(__name__)

# 段处理并行等待窗：LLM 深度校正与检索共享，超时各自降级
_PARALLEL_WAIT_SEC = 10.0
# 退出 join 上限：并行窗(10s) + 分析 deadline(15s) + 余量 —— 段边界退出
_EXIT_JOIN_SEC = _PARALLEL_WAIT_SEC + _ANALYSIS_DEADLINE_SEC + 2.0
# 预取 futures 清理规则：worker 消费段时已 pop 自己的条目；
# 仍残留且 seq < 当前 seq 的条目只可能是「pending 被覆盖」的段（结果无人消费）


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
        return False  # 残留/损坏/已死 → 视为无实例
    # 防 PID 复用误判：存活但命令行不含 "iris" 的进程不是本项目实例
    import subprocess

    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        ).stdout
        return "iris" in out
    except Exception:
        return False


def _load_replace_dict() -> Dict[str, str]:
    """加载替换词典（data/asr_replace_dict.json，build-asr-prompt --deploy 产物）；缺失→空。"""
    import json

    path = resolve_data_path("data/asr_replace_dict.json")
    if not path.exists():
        _logger.warning("替换词典不存在，仅保留原文（可先运行 build-asr-prompt --deploy）")
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("replace_map", {})
    except Exception as e:
        _logger.warning("替换词典加载失败: %s（使用空词典）", e)
        return {}


def _load_asr_prompt() -> str:
    """加载 LLM 校正 Prompt（data/asr_prompt.md）；缺失→空（降级仅词典）。"""
    path = resolve_data_path("data/asr_prompt.md")
    if not path.exists():
        _logger.warning("校正 Prompt 不存在，LLM 深度校正降级为仅词典")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        _logger.warning("校正 Prompt 加载失败: %s", e)
        return ""


class MeetingLiveAssistant:
    """实时会议助理编排核心。

    线程模型：
    - poll 线程：剪贴板轮询 → submit 段 → 预取（fast 校正入窗 + 提交 deep/检索 futures）
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
        fast_only: bool = False,
    ):
        self._bundle = bundle
        self._cfg = AssistantConfig.from_app_config(bundle.app.get("assistant", {}) if bundle.app else {})
        if fast_only:
            self._cfg = self._cfg.model_copy(update={"fast_only": True})  # CLI --fast-only > 配置
        # pid 目录 = 项目根/data（resolve_data_path 仅接受带子路径的 data/… 形式）
        self._pid_dir = Path(pid_dir) if pid_dir else (get_project_root() / "data")

        # 采集
        self._watcher = ClipboardWatcher(
            poll_interval=self._cfg.poll_interval,
            max_len=self._cfg.max_segment_chars,
            dedup_window_seconds=self._cfg.dedup_window_seconds,
        )

        # 校正（复用 AsrCorrector 双通道）
        replace_dict = _load_replace_dict()
        llm_prompt = _load_asr_prompt()
        self._corrector = CorrectorAdapter(
            replace_dict=replace_dict,
            llm_prompt=llm_prompt,
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

        # 检索 + 分析 + 输出
        self._retriever = RetrieverAdapter(bundle)
        from iris.utils.prompting import PromptTemplateLoader
        self._analyzer = SegmentAnalyzer(
            self._llm,
            PromptTemplateLoader(bundle),
            model=self._cfg.llm_model,
        )
        self._session = MeetingSession()
        self._panel = PanelRenderer()

        # 输出路径：--output > assistant.output_dir > data/meeting-live/
        if output_path:
            self._doc_path = Path(output_path).expanduser()
        else:
            out_dir = self._cfg.output_dir or str(resolve_data_path("data/meeting-live"))
            self._doc_path = (
                Path(out_dir) / f"{datetime.now():%Y%m%d-%H%M%S}-会议记录.md"
            ).expanduser()
        self._writer = DocWriter(self._doc_path, rewrite_every=self._cfg.doc_rewrite_every)

        self._pool = ThreadPoolExecutor(max_workers=2)
        # 预取 futures：seq → (deep_future, retr_future)，worker 消费后 pop
        self._futures: Dict[int, Tuple[Future, Future]] = {}
        # _futures 并发保护：poll 线程在 _publish_prefetch 中写（_cond 临界区内），
        # worker 线程在 _process_segment 中读/pop；P4-13 乐观并发在 worker 侧 peek。
        # 锁顺序：poll 线程先拿 _cond（submit）再拿 _futures_lock（on_publish）；
        # worker 只拿 _futures_lock（不拿 _cond），无死锁。
        self._futures_lock = threading.Lock()

    # ── 主流程 ──────────────────────────────────────────────

    def run(self) -> int:
        if _probe_running("asr-corrector", self._pid_dir):
            _logger.warning("asr-corrector 正在运行（独占剪贴板），请先退出后再启动会议助理")
            return 1

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
            # vocotype 热键缺失仅警告，不阻塞（可手动粘贴文本测试）
            try:
                from iris.wiki.asr.corrector import _load_vocotype_hotkey
                mask, keycode = _load_vocotype_hotkey()
                if not (mask or keycode):
                    _logger.warning("未检测到 vocotype 热键，仍可手动复制文本进行测试")
            except Exception:
                pass

            if not self._writer.initial_write(self._session.state):
                return 1

            _logger.info("实时会议助理已启动，过程文档: %s", self._doc_path)
            _logger.info("按住 vocotype 热键说话，松开即转写并分析（Ctrl+C 退出）")

            self._panel.render(PanelDisplay(status="等待语音…", state=self._session.state))

            worker = threading.Thread(target=self._worker_loop, daemon=True)
            worker.start()
            self._poll_loop()
        except KeyboardInterrupt:
            _logger.info("正在结束会议…")
        finally:
            self._session.request_stop()
            if worker is not None:
                # 段边界退出：等待当前段完成（并行窗 + 分析 deadline + 余量）
                worker.join(timeout=_EXIT_JOIN_SEC)
                if worker.is_alive():
                    _logger.warning("退出等待超时，当前段可能未完成")
            # 会议总结：退出时一次 LLM（10s deadline），失败自动跳过
            if self._cfg.summary_enabled and self._session.state.segments:
                summary = self._analyzer.summarize(self._session.state)
                if summary:
                    self._session.state.summary = summary
                    _logger.info("会议总结已生成")
                else:
                    _logger.warning("会议总结生成失败（跳过）")
            # worker 已退出（或超时），此处写文档无并发写
            self._writer.maybe_rewrite(self._session.state, force=True)
            self._panel.render_final(self._session.state, self._doc_path)
            # 池内任务均有 deadline（deep 8s / 检索 8s），有界返回
            self._pool.shutdown(wait=True, cancel_futures=True)
            registry.unregister()
        return 0

    # ── 线程逻辑 ────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._session.stop.is_set():
            text = self._watcher.poll()
            if text:
                # fast 校正（Aho-Corasick，毫秒级）在锁外执行，不阻塞 worker；
                # 预取注册通过 on_publish 在 submit 临界区内完成（与 pending 原子）
                fast = self._corrector.fast(text)
                seg = self._session.submit(
                    text,
                    on_publish=lambda s, f=fast: self._publish_prefetch(s, f),
                )
                _logger.info("语音段 %d 已捕获（%d 字），排队分析…", seg.seq, len(text))
            time.sleep(self._cfg.poll_interval)

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
            self._corrector.push_context(fast)
            if self._cfg.fast_only or len(fast) < self._cfg.short_segment_chars:
                return
            with self._futures_lock:
                self._futures[seg.seq] = (
                    self._pool.submit(self._corrector.deep, fast),
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
        while not self._session.stop.is_set():
            seg = self._session.take_pending(timeout=self._cfg.poll_interval)
            if seg is None:
                continue
            try:
                self._process_segment(seg)
            except Exception as e:
                _logger.error("段 %d 处理异常: %s", seg.seq, e)

    def _process_segment(self, seg: VoiceSegment) -> None:
        """段处理流水线：fast → deep/检索 → [乐观并发批处理] → 分析 → 落账。

        P4-13 乐观并发：在收集 seg(N) 的 deep/检索后，peek 检查 seg(N+1) 的
        预取 futures 是否已就绪。若就绪且 pending 槽未覆盖，则批处理两段分析
        （pool 并发提交），按 seq 顺序落账。单段路径不受影响。
        """
        # t0：fast 兜底
        if not seg.corrected_text:
            seg.corrected_text = self._corrector.fast(seg.raw_text)
            self._corrector.push_context(seg.corrected_text)
        fast = seg.corrected_text

        # 短段门控 / 快速模式
        if self._cfg.fast_only or len(fast) < self._cfg.short_segment_chars:
            seg.analysis_status = VoiceSegment.ANALYSIS_SKIPPED
            display = PanelDisplay(
                status=f"已处理段 {seg.seq}（跳过分析）",
                seg=seg,
                state=self._session.state,
            )
            self._panel.render(display)
            try:
                self._session.record(seg)
                self._writer.maybe_rewrite(self._session.state)
            except Exception as e:
                _logger.error("段 %d 落账异常: %s", seg.seq, e)
            return

        self._panel.render(PanelDisplay(status="分析中…", seg=seg, state=self._session.state))

        # t1/t2：deep 校正 与 检索（优先消费 poll 线程预跑的 futures）
        deep, hits = self._collect_deep_retrieval(seg, fast)

        if deep != fast:
            seg.corrected_text = deep
            self._corrector.push_context(deep)

        # P4-13 乐观并发：检查 N+1 的 deep/检索是否已就绪
        next_seg = None
        with self._futures_lock:
            # peek：只看不 pop（pop 在 _collect_deep_retrieval 中完成）
            peek_seq = seg.seq + 1
            peek_futures = self._futures.get(peek_seq)
            if peek_futures is not None:
                f_deep, f_retr = peek_futures
                if f_deep.done() and f_retr.done():
                    # N+1 预取就绪 → 原子消费 pending 槽
                    next_seg = self._session.take_pending_if(peek_seq)

        if next_seg is not None:
            # 批处理：N 和 N+1 的分析并发提交
            self._process_batch(seg, deep, hits, next_seg)
        else:
            # 单段路径（常规）
            self._analyze_and_record(seg, deep, hits)
            display = PanelDisplay(
                status=f"已处理段 {seg.seq}",
                seg=seg,
                analysis_unavailable=seg.analysis is None,
                state=self._session.state,
            )
            self._panel.render(display)
            self._writer.maybe_rewrite(self._session.state)

    def _collect_deep_retrieval(self, seg: VoiceSegment, fast: str):
        """收集 deep 校正与检索结果（含预取消费 + 降级）。"""
        with self._futures_lock:
            futures = self._futures.pop(seg.seq, None)
        if futures is None:
            try:
                futures = (
                    self._pool.submit(self._corrector.deep, fast),
                    self._pool.submit(self._retriever.search, fast, top_k=self._cfg.top_k),
                )
            except RuntimeError:
                futures = None
        deep, hits = fast, []
        if futures is not None:
            done, _ = wait(futures, timeout=_PARALLEL_WAIT_SEC)
            deep, hits = self._collect_results(futures, done, fast)
        return deep, hits

    def _process_batch(
        self,
        seg: VoiceSegment,
        deep: str,
        hits: list,
        next_seg: VoiceSegment,
    ) -> None:
        """乐观并发批处理：收集 N+1 的 deep/检索 → 并发提交两组分析 → 按序落账。"""
        # 收集 N+1 的 deep/检索（futures 已在 peek 时确认 done）
        fast_n1 = next_seg.corrected_text or next_seg.raw_text
        if not next_seg.corrected_text:
            next_seg.corrected_text = self._corrector.fast(next_seg.raw_text)
            self._corrector.push_context(next_seg.corrected_text)
            fast_n1 = next_seg.corrected_text
        deep_n1, hits_n1 = self._collect_deep_retrieval(next_seg, fast_n1)
        if deep_n1 != fast_n1:
            next_seg.corrected_text = deep_n1
            self._corrector.push_context(deep_n1)

        _logger.info("乐观并发：段 %d + 段 %d 批处理分析", seg.seq, next_seg.seq)

        # 并发提交两组 LLM 分析
        f_analysis_n = self._pool.submit(
            self._run_analysis, seg, deep, hits,
            self._session.summary_for_prompt(),
            self._session.open_questions_for_prompt(),
        )
        f_analysis_n1 = self._pool.submit(
            self._run_analysis, next_seg, deep_n1, hits_n1,
            self._session.summary_for_prompt(),  # 不包含 N 段结果（N 尚未落账）
            self._session.open_questions_for_prompt(),
        )

        # 等待两组分析完成
        done, _ = wait([f_analysis_n, f_analysis_n1], timeout=_ANALYSIS_DEADLINE_SEC + 2.0)
        for f in [f_analysis_n, f_analysis_n1]:
            if f not in done and not f.done():
                f.cancel()

        # 按 seq 顺序落账（先 N，再 N+1）
        for s in [seg, next_seg]:
            self._finalize_segment(s, deep if s is seg else deep_n1,
                                   hits if s is seg else hits_n1)
            display = PanelDisplay(
                status=f"已处理段 {s.seq}",
                seg=s,
                analysis_unavailable=s.analysis is None,
                state=self._session.state,
            )
            self._panel.render(display)
            self._writer.maybe_rewrite(self._session.state)

    def _run_analysis(
        self,
        seg: VoiceSegment,
        deep: str,
        hits: list,
        meeting_summary: str,
        open_questions: str,
    ) -> None:
        """在池线程中执行 LLM 分析并将结果写入 seg（供并发批处理使用）。"""
        seg.analysis_started_at = time.monotonic()
        try:
            seg.analysis = self._analyzer.analyze(
                deep,
                RetrieverAdapter.format_context(hits),
                meeting_summary,
                open_questions=open_questions,
            )
            seg.analysis_done_at = time.monotonic()
            seg.analysis_status = (
                VoiceSegment.ANALYSIS_DONE if seg.analysis
                else VoiceSegment.ANALYSIS_FAILED
            )
        except Exception as e:
            _logger.warning("段 %d 分析异常: %s", seg.seq, e)
            seg.analysis = None
            seg.analysis_status = VoiceSegment.ANALYSIS_FAILED

    def _analyze_and_record(self, seg: VoiceSegment, deep: str, hits: list) -> None:
        """单段分析 + 记录（常规路径）。"""
        self._run_analysis(
            seg, deep, hits,
            self._session.summary_for_prompt(),
            self._session.open_questions_for_prompt(),
        )
        self._finalize_segment(seg, deep, hits)

    def _finalize_segment(self, seg: VoiceSegment, deep: str, hits: list) -> None:
        """分析后处理：建议提问间隔化 + 落账（单段/批处理共用）。"""
        analysis = seg.analysis
        if analysis:
            elapsed = getattr(seg, 'analysis_done_at', 0.0) - getattr(seg, 'analysis_started_at', 0.0)
            _logger.info(
                "段 %d 分析完成（%.1fs）· 要点=%d 决策=%d 风险=%d 问题=%d",
                seg.seq, elapsed,
                len(analysis.key_points), len(analysis.decisions),
                len(analysis.risks), len(analysis.questions),
            )
            # 间隔化建议提问
            if (seg.seq - 1) % self._cfg.suggest_every == 0:
                try:
                    deadline = (getattr(seg, 'analysis_started_at', time.monotonic())
                                + _ANALYSIS_DEADLINE_SEC)
                    sharp = self._analyzer.suggest_questions(
                        analysis,
                        self._session.summary_for_prompt(),
                        RetrieverAdapter.format_context(hits),
                        deadline=deadline,
                    )
                    if sharp:
                        analysis.suggested_questions = sharp
                        _logger.info("段 %d 建议提问已用高温度(0.5)重新生成（%d 条）",
                                     seg.seq, len(sharp))
                except Exception:
                    pass
            else:
                analysis.suggested_questions = []

        try:
            self._session.record(seg)
        except Exception as e:
            _logger.error("段 %d 落账异常: %s", seg.seq, e)

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
