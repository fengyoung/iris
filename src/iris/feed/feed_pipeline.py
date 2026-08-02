"""信息汇聚管道 — Pipeline 编排（主入口）。

7 步顺序执行：
  Step 1 - 消息获取
  Step 2 - 噪音过滤
  Step 3 - 话题检测
  Step 4 - OKR 标签解析
  Step 5 - 文档提取（飞书文档链接 → 本地 Markdown）
  Step 6 - 简报生成
  Step 7 - 分发
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from iris.feed._chat_fetcher import ChatFetcher
from iris.feed._brief_generator import BriefGenerator
from iris.feed._cursor_tracker import CursorTracker
from iris.feed._dispatcher import Dispatcher
from iris.feed._doc_extractor import DocExtractor
from iris.feed._feishu_bridge import FeishuBridge
from iris.feed._message_filter import MessageFilter
from iris.feed._okr_loader import OKRLoader, extract_dept_keyword
from iris.feed._topic_detector import TopicDetector
from iris.feed._types import PipelineResult
from iris.feed.feed_config import FeedConfig, FeedConfigManager, load_feed_config

logger = logging.getLogger(__name__)


class FeedPipeline:
    """信息汇聚 Pipeline。"""

    def __init__(self, bundle, llm_service, source_dir: Optional[Path] = None):
        """初始化 Pipeline。

        Args:
            bundle: ConfigBundle（或兼容字典对象）
            llm_service: LLMService 实例
            source_dir: SOURCE 根目录路径
        """
        self._root = Path(bundle.root) if hasattr(bundle, 'root') else Path(str(bundle.root))
        self._config_dir = self._root / "config"
        self._data_dir = self._root / "data"
        self._source_dir = source_dir or self._resolve_source_dir(bundle)
        self._llm = llm_service
        self._bundle = bundle

        # 初始化各组件
        self._bridge = FeishuBridge()
        self._config_manager = FeedConfigManager(self._config_dir / "feeds.json")
        self._cursor_tracker = CursorTracker(self._data_dir)
        self._chat_fetcher = ChatFetcher(self._bridge, self._cursor_tracker)
        self._okr_loader = OKRLoader(
            source_root=self._source_dir,
            dept_keyword=extract_dept_keyword(bundle),
        )

    @staticmethod
    def _resolve_source_dir(bundle) -> Path:
        """解析 SOURCE 目录路径。

        优先级：IRIS_WORK_DOCS_DIR > bundle.default_source_path > bundle.root/SOURCE
        """
        import os
        # 1. 环境变量
        work_docs = os.environ.get("IRIS_WORK_DOCS_DIR", "")
        if work_docs:
            return Path(work_docs)
        # 2. ConfigBundleV2 的 source path
        if hasattr(bundle, 'default_source_path'):
            try:
                return bundle.default_source_path
            except Exception:
                pass
        # 3. fallback
        root = bundle.root if hasattr(bundle, 'root') else str(bundle.root)
        return Path(root) / "SOURCE"

    def run(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        chat_filter: Optional[list[str]] = None,
        dry_run: bool = False,
        send_notifications: bool = False,
        import_mode: Optional[str] = None,
        extract_docs: Optional[bool] = None,
    ) -> PipelineResult:
        """执行一次完整的信息汇聚。

        Args:
            since: 起始日期（默认 3 天前）
            until: 截止日期（默认今天）
            chat_filter: 限定关注的会话 ID 列表（None 表示所有）
            dry_run: 仅预览，不实际写入
            send_notifications: 是否发送飞书确认通知
            import_mode: 覆盖导入模式（'auto_import' | 'confirm'），None 使用各会话配置
            extract_docs: 是否提取飞书文档（None 使用 topic_config 配置）

        Returns:
            PipelineResult
        """
        config = self._config_manager.config
        if not config.watch_chats:
            return PipelineResult.empty("没有配置关注会话，请先运行 iris feed-setup")

        # 过滤会话
        chats = config.watch_chats
        if chat_filter:
            chats = [c for c in chats if c.id in chat_filter]
        if not chats:
            return PipelineResult.empty("没有匹配的会话")

        if until is None:
            until = datetime.now()
        if since is None:
            since = until - timedelta(days=config.topic_config.get("default_range_days", 3))

        logger.info("=== 信息汇聚开始 ===")
        logger.info("时间范围: %s ~ %s", since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d"))
        logger.info("关注会话: %d 个", len(chats))

        # ── Step 1: 消息获取 ──
        raw_messages = self._chat_fetcher.fetch(chats, since=since, until=until)
        fetched_count = sum(len(v) for v in raw_messages.values())
        logger.info("Step 1: 获取到 %d 条消息", fetched_count)
        if fetched_count == 0:
            return PipelineResult.empty("没有新消息")

        # ── Step 2: 噪音过滤 ──
        msg_filter = MessageFilter(min_msg_length=config.topic_config.get("min_msg_length", 10))
        filtered = msg_filter.filter(raw_messages)
        filtered_count = sum(len(v) for v in filtered.values())
        logger.info("Step 2: 过滤后剩余 %d 条", filtered_count)
        if filtered_count == 0:
            return PipelineResult.empty("所有消息被过滤，无有效内容")

        # ── Step 3: 话题检测 ──
        okr_context = self._okr_loader.load()
        okr_prompt_context = okr_context.to_prompt_context() if okr_context else ""
        detector = TopicDetector(
            self._llm,
            brief_dir=self._source_dir / "09-工作简报",
            topic_config=config.topic_config,
            okr_context=okr_prompt_context,
        )
        topics = detector.detect(filtered)
        logger.info("Step 3: 检测到 %d 个话题", len(topics))
        if not topics:
            return PipelineResult.empty("未检测到有价值话题")

        # Step 3b: OKR 标签解析（将 kr_id 解析为实际描述）
        if okr_context:
            for t in topics:
                resolved = okr_context.resolve_tags(t.okr_tags)
                # 将 okr_tags 从 ["O1-KR1"] 扩展为 ["O1-KR1: 实际描述"]
                t.okr_tags = [f"{tag}: {desc}" for tag, desc in resolved.items()]

        # ── Step 5: 文档提取 ──
        do_extract = extract_docs
        if do_extract is None:
            do_extract = config.topic_config.get("extract_docs", True)
        converted_docs: list = []
        if do_extract:
            doc_extractor = DocExtractor(self._source_dir, self._bundle)
            doc_max = config.topic_config.get("doc_extract_max", 10)
            converted_docs = doc_extractor.extract(topics, dry_run=dry_run, max_docs=doc_max)
        else:
            logger.info("Step 5: 文档提取已禁用，跳过")

        # ── Step 6: 简报生成 ──
        exec_date = until.strftime("%Y%m%d")
        generator = BriefGenerator(self._source_dir)
        brief_files = generator.generate(topics, converted_docs, exec_date, dry_run=dry_run)
        logger.info("Step 6: 生成了 %d 份简报", len(brief_files))

        if dry_run:
            logger.info("=== dry-run 模式，简报未写入磁盘 ===")
            return PipelineResult(
                fetched_count=fetched_count,
                filtered_count=filtered_count,
                topics=topics,
                brief_files=brief_files,
                converted_docs=converted_docs,
            )

        # ── Step 7: 分发 ──
        dispatcher = Dispatcher(self._bridge, self._config_manager, self._data_dir)
        dispatch_result = dispatcher.dispatch(
            topics, brief_files, send_notifications=send_notifications,
            import_mode=import_mode,
        )
        logger.info("Step 7: 自动入库 %d, 待确认 %d",
                     len(dispatch_result.auto_imported), len(dispatch_result.pending))

        logger.info("=== 信息汇聚完成 ===")
        return PipelineResult(
            fetched_count=fetched_count,
            filtered_count=filtered_count,
            topics=topics,
            brief_files=brief_files,
            converted_docs=converted_docs,
            auto_imported=dispatch_result.auto_imported,
            pending=dispatch_result.pending,
        )
