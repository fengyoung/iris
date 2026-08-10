"""主编排：MeetingLiveAssistant 常驻进程（采集 → 校正 → 检索 → 分析 → 面板/文档）。"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from iris.utils.paths import get_project_root, resolve_data_path

from ._analyzer import SegmentAnalyzer
from ._clipboard import ClipboardWatcher
from ._corrector import CorrectorAdapter
from ._doc_writer import DocWriter
from ._panel import PanelDisplay, PanelRenderer
from ._retriever import RetrieverAdapter
from ._session import MeetingSession
from .models import AssistantConfig, VoiceSegment

# 段处理并行等待窗：LLM 深度校正与检索共享，超时各自降级
_PARALLEL_WAIT_SEC = 10.0


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
        return True
    except (ValueError, OSError):
        return False  # 残留/损坏/已死 → 视为无实例


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
    - 主线程：剪贴板轮询（submit 段）
    - 工作线程：串行消费段（校正→检索→分析→输出）；处理中 submit 只覆盖
      pending 指针 → 天然丢弃中间段（积压策略）
    - 段内并行：LLM 深度校正与知识库检索各占一个线程（ThreadPoolExecutor(2)）
    """

    def __init__(
        self,
        bundle,
        *,
        output_path: str = "",
        llm_service: Optional[object] = None,
        pid_dir: Optional[Path] = None,
    ):
        self._bundle = bundle
        self._cfg = AssistantConfig.from_app_config(bundle.app.get("assistant", {}) if bundle.app else {})
        # pid 目录 = 项目根/data（resolve_data_path 仅接受带子路径的 data/… 形式）
        self._pid_dir = Path(pid_dir) if pid_dir else (get_project_root() / "data")

        # 采集
        self._watcher = ClipboardWatcher(poll_interval=self._cfg.poll_interval)

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
                Path(out_dir) / f"{datetime.now():%Y%m%d-%H%M}-会议记录.md"
            ).expanduser()
        self._writer = DocWriter(self._doc_path, rewrite_every=self._cfg.doc_rewrite_every)

        self._pool = ThreadPoolExecutor(max_workers=2)

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

            worker = threading.Thread(target=self._worker_loop, daemon=True)
            worker.start()
            self._poll_loop()
        except KeyboardInterrupt:
            print("\n[Iris] 正在结束会议…", file=sys.stderr)
        finally:
            self._session.request_stop()
            if worker is not None:
                # 段边界退出（worker 空转间隔 ≤ poll_interval + 处理中段耗时）
                worker.join(timeout=max(5.0, self._cfg.poll_interval * 4 + 1))
            self._writer.maybe_rewrite(self._session.state, force=True)
            self._panel.render_final(self._session.state, self._doc_path)
            self._pool.shutdown(wait=True, cancel_futures=True)
            registry.unregister()
        return 0

    # ── 线程逻辑 ────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._session.stop.is_set():
            text = self._watcher.poll()
            if text:
                seg = self._session.submit(text)
                print(f"[Iris] 📋 语音段 {seg.seq} 已捕获（{len(text)} 字），排队分析…",
                      file=sys.stderr)
            time.sleep(self._cfg.poll_interval)

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
        """段处理流水线：fast 校正（立即显示）→ 并行 deep 校正+检索 → 分析 → 输出。"""
        # t1：词典快速校正（毫秒级），立即显示 + 入上下文窗口
        fast = self._corrector.fast(seg.raw_text)
        seg.corrected_text = fast
        self._corrector.push_context(fast)
        self._panel.render(PanelDisplay(status="分析中…", seg=seg, state=self._session.state))

        # t2/t3：LLM 深度校正 与 知识库检索 并行
        f_deep = self._pool.submit(self._corrector.deep, fast)
        f_retr = self._pool.submit(self._retriever.search, fast, top_k=self._cfg.top_k)
        done, _ = wait({f_deep, f_retr}, timeout=_PARALLEL_WAIT_SEC)

        deep = f_deep.result() if f_deep in done and not f_deep.cancelled() else fast
        hits = f_retr.result() if f_retr in done and not f_retr.cancelled() else []
        if deep != fast:
            seg.corrected_text = deep
            self._corrector.push_context(deep)  # deep 覆盖入窗（镜像 corrector _tick 行为）

        # t4：LLM 分析（会议状态 + 检索上下文 + 本段）
        analysis = self._analyzer.analyze(
            deep,
            RetrieverAdapter.format_context(hits),
            self._session.summary_for_prompt(),
        )
        seg.analysis = analysis
        self._session.record(seg)

        # t5：输出（面板 + 文档）
        display = PanelDisplay(
            status=f"已处理段 {seg.seq}",
            seg=seg,
            analysis_unavailable=analysis is None,
            state=self._session.state,
        )
        self._panel.render(display)
        self._writer.maybe_rewrite(self._session.state)
