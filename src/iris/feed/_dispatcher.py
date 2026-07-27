"""信息汇聚管道 — 分发。

mode=auto_import 的会话 → 直接保存
mode=confirm 的会话 → 暂存到待确认队列 + 推送飞书通知
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.feed._feishu_bridge import FeishuBridge, IRIS_BOT_USER_ID
from iris.feed._types import DetectedTopic
from iris.feed.feed_config import FeedConfigManager, WatchChat

logger = logging.getLogger(__name__)


class DispatchResult:
    """分发结果。"""

    def __init__(self):
        self.auto_imported: List[str] = []
        self.pending: List[str] = []


class Dispatcher:
    """分发器。"""

    def __init__(
        self,
        bridge: FeishuBridge,
        config_manager: FeedConfigManager,
        data_dir: Path,
    ):
        self._bridge = bridge
        self._config = config_manager
        self._data_dir = data_dir

    def dispatch(
        self,
        topics: List[DetectedTopic],
        brief_files: List[Path],
        send_notifications: bool = False,
        import_mode: Optional[str] = None,
    ) -> DispatchResult:
        """分发话题。

        Args:
            import_mode: 覆盖导入模式，None 使用各会话配置
        """
        result = DispatchResult()

        # 建立话题 ID → 文件路径映射
        topic_map: Dict[str, DetectedTopic] = {t.topic_id: t for t in topics}
        file_map: Dict[str, Path] = {}
        for f in brief_files:
            # 从 frontmatter 提取 topic_id
            try:
                content = f.read_text(encoding="utf-8")
                import re
                m = re.search(r'topic_id:\s*(\S+)', content)
                if m:
                    file_map[m.group(1)] = f
            except Exception:
                pass

        pending_queue = []
        for topic in topics:
            mode = import_mode or self._get_mode_for_topic(topic)
            if mode == "auto_import":
                result.auto_imported.append(topic.topic_id)
            else:
                result.pending.append(topic.topic_id)
                pending_queue.append({
                    "topic_id": topic.topic_id,
                    "title": topic.title,
                    "summary": topic.summary[:200],
                    "sources": [s.name for s in topic.source_chats],
                    "brief_path": str(file_map.get(topic.topic_id, "")),
                    "created": datetime.now().isoformat(),
                    "status": "pending",
                })

        # 保存待确认队列
        if pending_queue:
            self._save_pending(pending_queue)

        # 发送通知
        if send_notifications and pending_queue:
            for item in pending_queue:
                self._send_confirm_notification(item)

        return result

    def _get_mode_for_topic(self, topic: DetectedTopic) -> str:
        """判定话题的导入模式。

        策略：取话题来源中最保守的模式（有任一 confirm 即为 confirm）。
        """
        source_names = {s.name for s in topic.source_chats}
        watches = self._config.list_chats()
        has_confirm = False
        for w in watches:
            if w.name in source_names and w.mode == "confirm":
                has_confirm = True
                break
        return "confirm" if has_confirm else "auto_import"

    def _save_pending(self, pending: List[Dict[str, Any]]) -> None:
        """保存待确认队列。"""
        self._config.save_pending(self._data_dir, pending)

    def _send_confirm_notification(self, item: Dict[str, Any]) -> None:
        """发送飞书确认通知。"""
        md = f"""📋 **新话题待确认**

**【{item['title']}】**
来源：{' + '.join(item.get('sources', []))}

摘要：{item.get('summary', '')}

---
处理方式：
- `iris feed-confirm {item['topic_id']}` 确认入库
- `iris feed-ignore {item['topic_id']}` 忽略"""
        self._bridge.send_markdown_to_user(IRIS_BOT_USER_ID, md)
