"""信息汇聚管道 — 消息获取。

遍历关注会话，通过飞书 API 拉取时间范围内的消息。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from iris.feed._cursor_tracker import CursorTracker
from iris.feed._feishu_bridge import FeishuBridge
from iris.feed._types import RawMessage
from iris.feed.feed_config import WatchChat

logger = logging.getLogger(__name__)


class ChatFetchError(RuntimeError):
    """消息获取失败（网络/API错误，不同于无新消息）。"""


class ChatFetcher:
    """飞书消息获取器。"""

    def __init__(self, bridge: FeishuBridge, cursor_tracker: CursorTracker):
        self._bridge = bridge
        self._cursor = cursor_tracker

    def fetch(
        self,
        chats: List[WatchChat],
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        default_days: int = 3,
    ) -> Dict[str, List[RawMessage]]:
        """遍历关注会话，拉取消息。

        增量策略：
        - 有游标 → 从上次最后一条消息之后拉取
        - 无游标 → 拉取最近 default_days 天
        - 指定 since/until → 覆盖默认行为（显式指定时忽略游标）

        Returns:
            {chat_id: [RawMessage, ...]}
        """
        if until is None:
            until = datetime.now()
        result: Dict[str, List[RawMessage]] = {}

        fetch_errors: Dict[str, str] = {}
        for chat in chats:
            try:
                msgs = self._fetch_one(chat, since, until, default_days)
                result[chat.id] = msgs if msgs else []
            except ChatFetchError as e:
                logger.warning("会话 %s 获取失败，跳过: %s", chat.name, e)
                fetch_errors[chat.id] = str(e)
                result[chat.id] = []
        if fetch_errors:
            logger.warning("共 %d/%d 个会话获取失败: %s",
                          len(fetch_errors), len(chats),
                          ", ".join(f"{cid}: {err[:60]}" for cid, err in fetch_errors.items()))
        return result

    def _fetch_one(
        self,
        chat: WatchChat,
        since: Optional[datetime],
        until: datetime,
        default_days: int,
    ) -> List[RawMessage]:
        """拉取单个会话的消息。"""
        # 确定时间范围
        if since is not None:
            effective_since = since
        else:
            last_fetch = self._cursor.get_last_fetch(chat.id)
            if last_fetch:
                effective_since = last_fetch
            else:
                effective_since = until - timedelta(days=default_days)

        # 确保 since 不晚于 until
        if effective_since >= until:
            logger.debug("%s: since >= until，跳过", chat.name)
            return []

        logger.info("拉取 %s (%s) 消息: %s ~ %s", chat.name, chat.id[:12] + "...",
                     effective_since.strftime("%Y-%m-%d"), until.strftime("%Y-%m-%d"))

        try:
            raw_msgs = self._bridge.search_all_messages(
                chat_id=chat.id,
                since=effective_since,
                until=until,
            )
        except Exception as e:
            logger.error("拉取 %s 失败: %s", chat.name, e)
            raise ChatFetchError(f"拉取 {chat.name} 失败: {e}") from e

        if not raw_msgs:
            logger.info("  %s: 无新消息", chat.name)
            return []

        messages = [FeishuBridge.raw_to_message(m) for m in raw_msgs]

        # 按发送时间排序
        messages.sort(key=lambda m: m.send_time)

        # 更新游标
        latest = messages[-1]
        self._cursor.update(chat.id, last_msg_id=latest.msg_id)
        logger.info("  %s: %d 条消息 → 已更新游标", chat.name, len(messages))
        return messages
