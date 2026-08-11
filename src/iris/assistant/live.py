"""主编排：MeetingLiveAssistant 常驻进程（采集 → 校正 → 检索 → 分析 → 面板/文档）。

v3.23.3 双段流水线：poll 线程预取（fast 校正 + 提交 deep/检索 futures），
worker 只做等待/分析——段 N 分析期间段 N+1 的深度校正与检索已在池中运行，
每段关键路径从 ~25s 降到 ~15s 且深度重叠。
"""

from __future__ import annotations

import os
import signal
import sys
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
from ._panel import PanelDisplay, PanelRenderer
from ._retriever import RetrieverAdapter
from ._session import MeetingSession
from .models import AssistantConfig, VoiceSegment

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
        print("[Iris] ⚠ 替换词典不存在，仅保留原文（可先运行 build-asr-prompt --deploy）",
              file=sys.stderr)
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("replace_map", {})
    except Exception as e:
        print(f"[Iris] ⚠ 替换词典加载失败: {e}（使用空词典）", file=sys.stderr)
        return {}


def _load_asr_prompt() -> str:
    """加载 LLM 校正 Prompt（data/asr_prompt.md）；缺失→空（降级仅词典）。"""
    path = resolve_data_path("data/asr_prompt.md")
    if not path.exists():
        print("[Iris] ⚠ 校正 Prompt 不存在，LLM 深度校正降级为仅词典",
              file=sys.stderr)
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[Iris] ⚠ 校正 Prompt 加载失败: {e}", file=sys.stderr)
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
                print(f"[Iris] ⚠ LLM 初始化失败，会议降级为仅词典校正: {e}",
                      file=sys.stderr)
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

    # ── 主流程 ──────────────────────────────────────────────

    def run(self) -> int:
        if _probe_running("asr-corrector", self._pid_dir):
            print("[Iris] ⚠ asr-corrector 正在运行（独占剪贴板），请先退出后再启动会议助理",
                  file=sys.stderr)
            return 1

        from iris.core.locks import ProcessRegistry
        registry = ProcessRegistry("meeting-live-assistant", self._pid_dir)
        if not registry.register():
            print("[Iris] ⚠ meeting-live-assistant 已有实例在运行，退出",
                  file=sys.stderr)
            return 1

        # Python 3.13 中默认 SIGINT 处理无法中断 time.sleep（主线程睡眠时不抛
        # KeyboardInterrupt），显式注册 handler 保证 Ctrl+C 可靠进入优雅退出
        def _sigint_handler(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGINT, _sigint_handler)

        worker = None
        try:
            # vocotype 热键缺失仅警告，不阻塞（可手动粘贴文本测试）
            try:
                from iris.wiki.asr.corrector import _load_vocotype_hotkey
                mask, keycode = _load_vocotype_hotkey()
                if not (mask or keycode):
                    print("[Iris] ⚠ 未检测到 vocotype 热键，仍可手动复制文本进行测试",
                          file=sys.stderr)
            except Exception:
                pass

            if not self._writer.initial_write(self._session.state):
                return 1

            print(f"[Iris] 实时会议助理已启动，过程文档: {self._doc_path}",
                  file=sys.stderr)
            print("[Iris] 按住 vocotype 热键说话，松开即转写并分析（Ctrl+C 退出）",
                  file=sys.stderr)

            self._panel.render(PanelDisplay(status="等待语音…", state=self._session.state))

            worker = threading.Thread(target=self._worker_loop, daemon=True)
            worker.start()
            self._poll_loop()
        except KeyboardInterrupt:
            print("\n[Iris] 正在结束会议…", file=sys.stderr)
        finally:
            self._session.request_stop()
            if worker is not None:
                # 段边界退出：等待当前段完成（并行窗 + 分析 deadline + 余量）
                worker.join(timeout=_EXIT_JOIN_SEC)
                if worker.is_alive():
                    print("[Iris] ⚠ 退出等待超时，当前段可能未完成", file=sys.stderr)
            # 会议总结：退出时一次 LLM（10s deadline），失败自动跳过
            if self._cfg.summary_enabled and self._session.state.segments:
                summary = self._analyzer.summarize(self._session.state)
                if summary:
                    self._session.state.summary = summary
                    print("[Iris] ✅ 会议总结已生成", file=sys.stderr)
                else:
                    print("[Iris] ⚠ 会议总结生成失败（跳过）", file=sys.stderr)
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
                print(f"[Iris] 📋 语音段 {seg.seq} 已捕获（{len(text)} 字），排队分析…",
                      file=sys.stderr)
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
        except Exception:
            pass  # 预取兜底失败不阻断提交（段仍以 fast-only 落账）

    def _worker_loop(self) -> None:
        while not self._session.stop.is_set():
            seg = self._session.take_pending(timeout=self._cfg.poll_interval)
            if seg is None:
                continue
            try:
                self._process_segment(seg)
            except Exception as e:
                print(f"[Iris] ⚠ 段 {seg.seq} 处理异常: {e}", file=sys.stderr)

    def _process_segment(self, seg: VoiceSegment) -> None:
        """段处理流水线：fast（poll 线程已预做）→ 等 deep/检索 futures → 分析 → 落账。

        每阶段独立降级（phase 守卫）：任何一步失败段仍落账，不丢段。
        """
        # t0：fast 兜底（正常由 _prefetch 完成；极端时序下现场补做）
        if not seg.corrected_text:
            seg.corrected_text = self._corrector.fast(seg.raw_text)
            self._corrector.push_context(seg.corrected_text)
        fast = seg.corrected_text

        # 短段门控 / 快速模式：确认语零 LLM 成本，直接落账快速排空流水线
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
                print(f"[Iris] ⚠ 段 {seg.seq} 落账异常: {e}", file=sys.stderr)
            return

        self._panel.render(PanelDisplay(status="分析中…", seg=seg, state=self._session.state))

        # t1/t2：deep 校正 与 检索（优先消费 poll 线程预跑的 futures）
        futures = self._futures.pop(seg.seq, None)
        if futures is None:
            try:
                futures = (
                    self._pool.submit(self._corrector.deep, fast),
                    self._pool.submit(self._retriever.search, fast, top_k=self._cfg.top_k),
                )
            except RuntimeError:
                futures = None  # 池已关闭：仅 fast 降级
        deep, hits = fast, []
        if futures is not None:
            done, _ = wait(futures, timeout=_PARALLEL_WAIT_SEC)
            deep, hits = self._collect_results(futures, done, fast)

        if deep != fast:
            seg.corrected_text = deep
            self._corrector.push_context(deep)  # deep 覆盖入窗（镜像 corrector _tick 行为）

        # t3：LLM 分析（会议状态 + 检索上下文 + 本段）
        analysis = None
        try:
            analysis = self._analyzer.analyze(
                deep,
                RetrieverAdapter.format_context(hits),
                self._session.summary_for_prompt(),
            )
        except Exception as e:
            print(f"[Iris] ⚠ 段 {seg.seq} 分析异常: {e}", file=sys.stderr)
        seg.analysis = analysis
        seg.analysis_status = (
            VoiceSegment.ANALYSIS_DONE if analysis else VoiceSegment.ANALYSIS_FAILED
        )
        # 间隔化：非采样段清空建议提问（省 token 减重复噪音）
        # (seq-1) 取模：首段（seq=1）保留建议提问——会议开场恰需引导提问
        if analysis and (seg.seq - 1) % self._cfg.suggest_every != 0:
            analysis.suggested_questions = []

        try:
            self._session.record(seg)
        except Exception as e:
            print(f"[Iris] ⚠ 段 {seg.seq} 落账异常: {e}", file=sys.stderr)

        # t4：输出（面板 + 文档）
        display = PanelDisplay(
            status=f"已处理段 {seg.seq}",
            seg=seg,
            analysis_unavailable=analysis is None,
            state=self._session.state,
        )
        self._panel.render(display)
        self._writer.maybe_rewrite(self._session.state)

    @staticmethod
    def _collect_results(futures: Tuple[Future, Future], done: set, fast: str):
        """并行结果收集：超时/异常/取消各自降级（deep→fast，检索→[]）。"""
        f_deep, f_retr = futures
        deep, hits = fast, []
        try:
            if f_deep in done and not f_deep.cancelled():
                deep = f_deep.result()
        except Exception as e:
            print(f"[Iris] ⚠ 深度校正异常，保留词典结果: {e}", file=sys.stderr)
        try:
            if f_retr in done and not f_retr.cancelled():
                hits = f_retr.result()
        except Exception as e:
            print(f"[Iris] ⚠ 检索异常，降级为空上下文: {e}", file=sys.stderr)
        return deep, hits
