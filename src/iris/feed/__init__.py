"""信息汇聚管道 — Iris Feed。

从飞书聊天记录（群聊 + 单聊）中自动挖掘有价值话题，
生成话题简报归档到知识库（SOURCE）。

主要类:
  FeedPipeline    — Pipeline 编排（主入口）
  FeedConfig      — 配置数据类
  FeedConfigManager — 配置管理器
  ChatFetcher     — 飞书消息获取
  MessageFilter   — 噪音过滤
  TopicDetector   — 话题检测+LLM 聚合
  BriefGenerator  — 简报生成
  Dispatcher      — 分发（auto/confirm）
  FeishuBridge    — 飞书 API 桥接层
  CursorTracker   — 游标追踪（增量）
"""

from .feed_pipeline import FeedPipeline
from .feed_config import (
    FeedConfig,
    FeedConfigManager,
    WatchChat,
    load_feed_config,
    save_feed_config,
)
from ._chat_fetcher import ChatFetcher
from ._message_filter import MessageFilter
from ._topic_detector import TopicDetector
from ._brief_generator import BriefGenerator
from ._dispatcher import Dispatcher
from ._feishu_bridge import FeishuBridge
from ._cursor_tracker import CursorTracker
from ._doc_extractor import DocExtractor
from ._okr_loader import OKRLoader, OKRDocument
from ._types import (
    ConvertedDoc,
    DetectedTopic,
    PipelineResult,
    Quote,
    RawMessage,
    SourceRef,
)

__all__ = [
    "FeedPipeline",
    "FeedConfig",
    "FeedConfigManager",
    "WatchChat",
    "load_feed_config",
    "save_feed_config",
    "ChatFetcher",
    "MessageFilter",
    "TopicDetector",
    "BriefGenerator",
    "Dispatcher",
    "FeishuBridge",
    "CursorTracker",
    "DocExtractor",
    "OKRLoader",
    "OKRDocument",
    "ConvertedDoc",
    "DetectedTopic",
    "PipelineResult",
    "Quote",
    "RawMessage",
    "SourceRef",
]
