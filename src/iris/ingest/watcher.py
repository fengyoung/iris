"""文件系统监听器 — 监控 SOURCE 目录变更，自动触发增量扫描+重建。

基于轮询实现（跨平台兼容），可选 inotify/watchdog 后端。

用法:
    from iris.ingest.watcher import SourceWatcher

    watcher = SourceWatcher(config)
    watcher.start(on_change=lambda events: incremental_build())
    # ...或单次检测：
    events = watcher.poll()

CLI:
    iris watch --poll-interval 30
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from iris.config.loader import ConfigBundle

logger = logging.getLogger(__name__)


@dataclass
class FileEvent:
    """文件变更事件。"""
    path: str
    relative_path: str
    event_type: str  # "created" | "modified" | "deleted"
    detected_at: str = field(default_factory=lambda: datetime.now(tz=timezone.utc).isoformat())


class SourceWatcher:
    """基于轮询的 SOURCE 目录文件变更检测器。

    启动时建立文件快照（路径 → mtime + size），
    poll() 时比较差异，返回 created / modified / deleted 事件列表。

    特性：
      - 跨平台（纯 Python，无系统依赖）
      - 可配置轮询间隔（默认 30s）
      - 支持 debounce（合并短时间内同一文件的多次变更）
      - 支持回调模式（start() 持续运行）和单次模式（poll()）
    """

    def __init__(self, config: ConfigBundle):
        self._config = config
        data_source = config.data_source
        self._sources: Dict[str, Path] = {}
        if isinstance(data_source, dict):
            for name, cfg in data_source.get("sources", {}).items():
                if cfg.get("enabled", True):
                    self._sources[name] = Path(cfg["path"]).resolve()
        else:
            for name in getattr(data_source, "sources", {}):
                src = data_source.sources[name]
                if getattr(src, "enabled", True):
                    self._sources[name] = Path(src.path).resolve()

        self._snapshot: Dict[str, Dict[str, float]] = {}  # source_name → {relative_path: mtime}
        self._debounce_window: float = 2.0  # 秒
        self._recent_events: Dict[str, float] = {}  # path → last event time

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        """建立当前文件系统快照（source_name → {relative_path: mtime}）。"""
        snap: Dict[str, Dict[str, float]] = {}
        for source_name, source_root in self._sources.items():
            if not source_root.exists():
                snap[source_name] = {}
                continue
            files: Dict[str, float] = {}
            for md_file in source_root.rglob("*.md"):
                if md_file.is_file():
                    try:
                        rel = str(md_file.relative_to(source_root))
                        files[rel] = md_file.stat().st_mtime
                    except OSError:
                        continue
            snap[source_name] = files
        return snap

    def poll(self, *, debounce: bool = True) -> List[FileEvent]:
        """单次轮询：比较当前文件系统与上次快照，返回差异事件。

        Args:
            debounce: 是否启用去抖动（合并短时间内重复事件）

        Returns:
            FileEvent 列表
        """
        current = self.snapshot()
        events: List[FileEvent] = []

        if not self._snapshot:
            # 首次运行：建立基线，不产生事件
            self._snapshot = current
            logger.info("文件监听基线已建立: %d 个数据源", len(current))
            return []

        for source_name, source_root in self._sources.items():
            prev_files = self._snapshot.get(source_name, {})
            curr_files = current.get(source_name, {})

            # 新增 + 修改
            for rel_path, mtime in curr_files.items():
                prev_mtime = prev_files.get(rel_path)
                if prev_mtime is None:
                    events.append(FileEvent(
                        path=str(source_root / rel_path),
                        relative_path=rel_path,
                        event_type="created",
                    ))
                elif mtime > prev_mtime + 0.1:  # 容忍亚秒级浮点误差
                    events.append(FileEvent(
                        path=str(source_root / rel_path),
                        relative_path=rel_path,
                        event_type="modified",
                    ))

            # 删除
            for rel_path in prev_files:
                if rel_path not in curr_files:
                    events.append(FileEvent(
                        path=str(source_root / rel_path),
                        relative_path=rel_path,
                        event_type="deleted",
                    ))

        # 更新快照
        self._snapshot = current

        # 去抖动
        if debounce:
            events = self._debounce(events)

        return events

    def _debounce(self, events: List[FileEvent]) -> List[FileEvent]:
        """去抖动：过滤掉短时间内同一文件的重复事件。"""
        now = time.monotonic()
        filtered: List[FileEvent] = []
        for evt in events:
            key = f"{evt.relative_path}:{evt.event_type}"
            if key not in self._recent_events:
                # 首次出现，放行
                filtered.append(evt)
                self._recent_events[key] = now
            elif now - self._recent_events[key] >= self._debounce_window:
                filtered.append(evt)
                self._recent_events[key] = now
        return filtered

    def start(
        self,
        on_change: Callable[[List[FileEvent]], Any],
        *,
        poll_interval: int = 30,
        run_once: bool = False,
    ) -> None:
        """持续监听模式：每 poll_interval 秒轮询一次，检测到变更时调用 on_change。

        Args:
            on_change: 变更回调函数，接收 FileEvent 列表
            poll_interval: 轮询间隔（秒），默认 30
            run_once: True 时仅运行一次，默认 False（持续运行）

        按 Ctrl+C 停止。
        """
        # 初始化基线
        self._snapshot = self.snapshot()
        logger.info("文件监听已启动（轮询间隔 %ds），按 Ctrl+C 停止", poll_interval)

        try:
            while True:
                time.sleep(poll_interval)
                events = self.poll()
                if events:
                    logger.info("检测到 %d 个文件变更", len(events))
                    try:
                        on_change(events)
                    except Exception as exc:
                        logger.warning("变更回调异常: %s", exc)
                if run_once:
                    break
        except KeyboardInterrupt:
            logger.info("文件监听已停止")


def build_incremental_on_change(config: ConfigBundle) -> Callable[[List[FileEvent]], Any]:
    """构造标准的「变更 → 增量构建」回调。

    当检测到 SOURCE 文件变更时，自动执行：
      1. 增量扫描（仅变更文件）
      2. 增量 chunk 重建

    Returns:
        回调函数，接收 FileEvent 列表
    """
    from iris.ingest import MarkdownChunker

    def _on_change(events: List[FileEvent]) -> None:
        affected_sources: Set[str] = set()
        for evt in events:
            for src_name, src_root in SourceWatcher(config)._sources.items():
                try:
                    Path(evt.path).relative_to(src_root)
                    affected_sources.add(src_name)
                except ValueError:
                    continue

        if not affected_sources:
            return

        chunker = MarkdownChunker(config)
        for src_name in affected_sources:
            try:
                summary = chunker.build_source_chunks(src_name, incremental=True)
                chunker.write_summary(summary)
                logger.info(
                    "自动增量构建完成 [%s]: %d 文档, %d chunk (复用 %d, 重建 %d)",
                    src_name,
                    summary.document_count,
                    summary.chunk_count,
                    summary.build_stats.get("reused_documents", 0),
                    summary.build_stats.get("rebuilt_documents", 0),
                )
            except Exception as exc:
                logger.warning("自动构建失败 [%s]: %s", src_name, exc)

    return _on_change
