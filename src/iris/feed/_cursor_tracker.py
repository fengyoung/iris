"""信息汇聚管道 — 游标追踪。

记录每个会话的拉取进度，支持增量获取。
存储：data/feed_cursors.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CursorTracker:
    """飞书消息拉取游标追踪。"""

    def __init__(self, data_dir: Path):
        self._path = data_dir / "feed_cursors.json"
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"chats": {}}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("游标文件损坏，重置: %s", e)
            return {"chats": {}}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get_cursor(self, chat_id: str) -> Optional[str]:
        """获取上次消息 ID（用于增量拉取）。"""
        entry = self._data.get("chats", {}).get(chat_id, {})
        return entry.get("last_msg_id")

    def get_last_fetch(self, chat_id: str) -> Optional[datetime]:
        """获取上次拉取时间。"""
        entry = self._data.get("chats", {}).get(chat_id, {})
        ts = entry.get("last_fetch_time")
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                return None
        return None

    def get_last_page_token(self, chat_id: str) -> Optional[str]:
        """获取上次分页 token。"""
        entry = self._data.get("chats", {}).get(chat_id, {})
        return entry.get("last_page_token")

    def update(self, chat_id: str, last_msg_id: Optional[str] = None,
               page_token: Optional[str] = None) -> None:
        """更新游标。"""
        entry = self._data.setdefault("chats", {}).setdefault(chat_id, {})
        entry["last_fetch_time"] = datetime.now().isoformat()
        if last_msg_id:
            entry["last_msg_id"] = last_msg_id
        if page_token is not None:
            entry["last_page_token"] = page_token
        self._save()
