"""轻量会话记忆。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from iris.config.loader import ConfigBundle

logger = logging.getLogger(__name__)


class SessionMemoryStore:
    """保存最近问题、主题与命中来源，供后续问答参考。"""

    def __init__(self, config: ConfigBundle):
        from iris.utils.paths import get_agent_data_dir
        session_cfg = config.app["session"]
        self._enabled = bool(session_cfg.get("enable_session_memory", True))
        base_dir = config.root / session_cfg["session_summary_dir"].replace("./", "")
        # 多 Agent 隔离：按 IRIS_AGENT_ID 分目录
        agent_dir = get_agent_data_dir(base_dir.parent)
        self._path = agent_dir / "latest_session.json"
        # 向后兼容：迁移旧路径数据到 agent 隔离目录
        self._migrate_from_legacy(base_dir / "latest_session.json")

    def _migrate_from_legacy(self, legacy_path: Path) -> None:
        """向后兼容：若旧路径有数据且新路径不存在，迁移到 agent 隔离目录。"""
        if self._path.exists() or not legacy_path.exists():
            return
        try:
            import shutil
            self._path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy_path, self._path)
        except OSError as exc:
            logger.warning("会话记忆旧路径迁移失败 (%s → %s): %s", legacy_path, self._path, exc)
            # 迁移失败不阻塞正常流程

    def load(self) -> Dict[str, Any]:
        if not self._enabled or not self._path.exists():
            return {
                "recent_questions": [],
                "recent_topics": [],
                "topic_threads": {},
                "recent_summary": "",
                "updated_at": None,
            }
        return json.loads(self._path.read_text(encoding="utf-8"))

    def save_interaction(self, *, question: str, mode: str, blocks: List[Any], wiki_hits: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self._enabled:
            return self.load()

        from iris.core.locks import FileLock
        with FileLock(self._path):
            state = self.load()
            questions = [question] + [item for item in state.get("recent_questions", []) if item != question]
            topics = _build_topics(blocks, wiki_hits) + state.get("recent_topics", [])
            deduped_topics: List[str] = []
            for topic in topics:
                if topic and topic not in deduped_topics:
                    deduped_topics.append(topic)

            topic_threads = _update_topic_threads(state.get("topic_threads", {}), question=question, topics=deduped_topics, mode=mode)
            payload = {
                "recent_questions": questions[:8],
                "recent_topics": deduped_topics[:12],
                "topic_threads": topic_threads,
                "recent_summary": _build_recent_summary(questions[:5], deduped_topics[:6], topic_threads),
                "last_mode": mode,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            from iris.memory.long_term import _atomic_write_json
            _atomic_write_json(self._path, payload)
        return payload


def _build_topics(blocks: List[Any], wiki_hits: List[Dict[str, Any]]) -> List[str]:
    topics: List[str] = []
    for hit in wiki_hits[:3]:
        title = str(hit.get("title", "")).strip()
        if title:
            topics.append(title)
    for block in blocks[:4]:
        title = getattr(block, "title", "").strip()
        if title:
            topics.append(title)
    return topics


def _update_topic_threads(state: Dict[str, Any], *, question: str, topics: List[str], mode: str) -> Dict[str, Any]:
    threads: Dict[str, Dict[str, Any]] = {}
    for key, value in state.items():
        if not isinstance(value, dict):
            continue
        threads[key] = {
            "count": int(value.get("count", 0)),
            "last_question": str(value.get("last_question", "")),
            "last_mode": str(value.get("last_mode", "")),
        }

    for topic in topics[:3]:
        thread = threads.get(topic, {"count": 0, "last_question": "", "last_mode": ""})
        thread["count"] = int(thread["count"]) + 1
        thread["last_question"] = question
        thread["last_mode"] = mode
        threads[topic] = thread

    sorted_items = sorted(threads.items(), key=lambda item: (-item[1]["count"], item[0]))
    return {key: value for key, value in sorted_items[:10]}


def _build_recent_summary(questions: List[str], topics: List[str], threads: Dict[str, Any]) -> str:
    if not questions and not topics:
        return "暂无会话摘要"
    top_threads = list(threads.keys())[:3]
    return (
        f"最近问题：{' | '.join(questions[:3]) if questions else '无'}；"
        f"高频主题：{' | '.join(top_threads) if top_threads else (' | '.join(topics[:3]) if topics else '无')}"
    )
