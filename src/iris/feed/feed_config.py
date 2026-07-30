"""信息汇聚管道 — 配置加载与管理。

配置层级：
  config/feeds.json          # 运行时配置（gitignored）
  config/feeds.json.example  # 示例配置（版本控制）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from iris.feed._types import DetectedTopic

logger = logging.getLogger(__name__)

# ── 数据类 ────────────────────────────────────────────


class WatchChat:
    """关注会话的轻量数据类。"""

    def __init__(
        self,
        id: str,
        name: str,
        type: Literal["group", "single"],
        mode: Literal["auto_import", "confirm"],
        okr_tags: Optional[List[str]] = None,
    ):
        self.id = id
        self.name = name
        self.type = type
        self.mode = mode
        self.okr_tags = okr_tags or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "mode": self.mode,
            "okr_tags": self.okr_tags,
        }

    def __repr__(self) -> str:
        return f"WatchChat({self.name!r}, {self.type}, {self.mode})"


class FeedConfig:
    """信息汇聚配置。"""

    def __init__(
        self,
        watch_chats: List[WatchChat],
        topic_config: Optional[Dict[str, Any]] = None,
        okr_mapping: Optional[Dict[str, Any]] = None,
    ):
        self.watch_chats = watch_chats
        self.topic_config = topic_config or {
            "default_range_days": 3,
            "min_msg_length": 10,
            "topic_min_messages": 2,
            "max_topics_per_run": 30,
            "time_window_minutes": 30,
            "extract_docs": True,
            "doc_extract_max": 10,
        }
        self.okr_mapping = okr_mapping or {
            "enabled": True,
            "strict_match": False,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "watch_chats": [c.to_dict() for c in self.watch_chats],
            "topic_config": self.topic_config,
            "okr_mapping": self.okr_mapping,
        }


# ── 加载 / 保存 ────────────────────────────────────────


def load_feed_config(config_path: Path) -> FeedConfig:
    """从 JSON 文件加载配置。文件不存在时返回空配置。"""
    if not config_path.exists():
        logger.info("feeds.json 不存在，返回空配置")
        return FeedConfig(watch_chats=[])

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    chats = []
    for c in data.get("watch_chats", []):
        chats.append(WatchChat(
            id=c["id"],
            name=c.get("name", ""),
            type=c.get("type", "group"),
            mode=c.get("mode", "confirm"),
            okr_tags=c.get("okr_tags"),
        ))
    return FeedConfig(
        watch_chats=chats,
        topic_config=data.get("topic_config"),
        okr_mapping=data.get("okr_mapping"),
    )


def save_feed_config(config: FeedConfig, config_path: Path) -> None:
    """保存配置到 JSON 文件（原子写入 + FileLock 保护）。"""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    from iris.core.locks import FileLock
    with FileLock(config_path):
        from iris.utils.shared import atomic_write_json
        atomic_write_json(config_path, config.to_dict())
    logger.info("配置已保存到 %s", config_path)


# ── 示例配置 ────────────────────────────────────────────

def write_example_config(config_path: Path) -> None:
    """写入示例配置文件（版本控制用）。"""
    example = {
        "version": 1,
        "_comment": "这是示例配置，真实配置在 feeds.json 中（gitignored）",
        "watch_chats": [
            {
                "id": "oc_xxxxxxxxxxxxxxxxxxxxx",
                "name": "数据智能部群",
                "type": "group",
                "mode": "auto_import",
                "okr_tags": ["AI巡检", "搜推体验"],
            }
        ],
        "topic_config": {
            "default_range_days": 3,
            "min_msg_length": 10,
            "topic_min_messages": 2,
            "max_topics_per_run": 30,
            "time_window_minutes": 30,
            "extract_docs": True,
            "doc_extract_max": 10,
        },
        "okr_mapping": {
            "enabled": True,
            "strict_match": False,
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(example, f, ensure_ascii=False, indent=2)


# ── 配置管理 ────────────────────────────────────────────


class FeedConfigManager:
    """配置管理器 — 处理增删改查 + 交互式向导。"""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.config = load_feed_config(config_path)

    def reload(self) -> FeedConfig:
        self.config = load_feed_config(self.config_path)
        return self.config

    def list_chats(self) -> List[WatchChat]:
        return self.config.watch_chats

    def add_chat(
        self,
        chat_id: str,
        name: str,
        chat_type: Literal["group", "single"] = "group",
        mode: Literal["auto_import", "confirm"] = "confirm",
        okr_tags: Optional[List[str]] = None,
    ) -> WatchChat:
        """添加关注会话。"""
        chat = WatchChat(id=chat_id, name=name, type=chat_type, mode=mode, okr_tags=okr_tags)
        # 排重
        existing = [c for c in self.config.watch_chats if c.id == chat_id]
        if existing:
            logger.warning("会话 %s 已在关注列表中", name)
            return existing[0]
        self.config.watch_chats.append(chat)
        save_feed_config(self.config, self.config_path)
        return chat

    def remove_chat(self, chat_id_or_name: str) -> bool:
        """移除关注会话。"""
        before = len(self.config.watch_chats)
        self.config.watch_chats = [
            c for c in self.config.watch_chats
            if c.id != chat_id_or_name and c.name != chat_id_or_name
        ]
        if len(self.config.watch_chats) < before:
            save_feed_config(self.config, self.config_path)
            return True
        return False

    def update_chat(self, chat_id: str, **kwargs) -> Optional[WatchChat]:
        """更新某会话配置。kwargs: mode, okr_tags, name"""
        for c in self.config.watch_chats:
            if c.id == chat_id:
                if "mode" in kwargs:
                    c.mode = kwargs["mode"]
                if "okr_tags" in kwargs:
                    c.okr_tags = kwargs["okr_tags"] or []
                if "name" in kwargs:
                    c.name = kwargs["name"]
                save_feed_config(self.config, self.config_path)
                return c
        return None

    def get_pending_queue_path(self, data_dir: Path) -> Path:
        return data_dir / "feed_pending.json"

    def load_pending(self, data_dir: Path) -> List[Dict[str, Any]]:
        """加载待确认队列。"""
        path = self.get_pending_queue_path(data_dir)
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_pending(self, data_dir: Path, pending: List[Dict[str, Any]]) -> None:
        """保存待确认队列（原子写入 + FileLock 保护）。"""
        path = self.get_pending_queue_path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        from iris.core.locks import FileLock
        with FileLock(path):
            from iris.utils.shared import atomic_write_json
            atomic_write_json(path, pending)
