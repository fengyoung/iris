"""iris.memory.session — SessionMemoryStore 及纯函数单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.memory.session import (
    SessionMemoryStore,
    _build_topics,
    _build_recent_summary,
    _update_topic_threads,
)


# ── 辅助 ──────────────────────────────────────────────────────


def _make_config(tmp_path: Path, enabled: bool = True) -> MagicMock:
    config = MagicMock()
    config.root = tmp_path
    session_dir = tmp_path / "data" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    config.app = {
        "session": {
            "enable_session_memory": enabled,
            "session_summary_dir": "./data/session",
        }
    }
    return config


# ── SessionMemoryStore ─────────────────────────────────────────


class TestSessionMemoryStoreLoad:
    def test_load_returns_empty_when_file_missing(self, tmp_path):
        store = SessionMemoryStore(_make_config(tmp_path))
        state = store.load()
        assert state["recent_questions"] == []
        assert state["recent_topics"] == []
        assert state["recent_summary"] == ""
        assert state["updated_at"] is None

    def test_load_returns_saved_data(self, tmp_path):
        config = _make_config(tmp_path)
        payload = {"recent_questions": ["Q1"], "recent_topics": ["T1"],
                   "topic_threads": {}, "recent_summary": "摘要", "updated_at": "2026-01-01T00:00:00"}
        path = tmp_path / "data" / "session" / "latest_session.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        store = SessionMemoryStore(config)
        state = store.load()
        assert state["recent_questions"] == ["Q1"]
        assert state["recent_summary"] == "摘要"

    def test_load_returns_empty_when_disabled(self, tmp_path):
        config = _make_config(tmp_path, enabled=False)
        path = tmp_path / "data" / "session" / "latest_session.json"
        path.write_text(json.dumps({"recent_questions": ["Q"]}), encoding="utf-8")
        store = SessionMemoryStore(config)
        state = store.load()
        assert state["recent_questions"] == []  # disabled → 空状态


class TestSessionMemoryStoreSave:
    def test_save_interaction_returns_payload(self, tmp_path):
        store = SessionMemoryStore(_make_config(tmp_path))
        with patch("iris.memory.long_term._atomic_write_json"):
            result = store.save_interaction(
                question="什么是 BM25？", mode="llm", blocks=[], wiki_hits=[]
            )
        assert result["recent_questions"][0] == "什么是 BM25？"
        assert result["last_mode"] == "llm"

    def test_save_interaction_deduplicates_questions(self, tmp_path):
        config = _make_config(tmp_path)
        payload = {"recent_questions": ["旧问题", "什么是 BM25？"],
                   "recent_topics": [], "topic_threads": {}, "recent_summary": ""}
        path = tmp_path / "data" / "session" / "latest_session.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        store = SessionMemoryStore(config)
        with patch("iris.memory.long_term._atomic_write_json"):
            result = store.save_interaction(
                question="什么是 BM25？", mode="local", blocks=[], wiki_hits=[]
            )
        questions = result["recent_questions"]
        assert questions.count("什么是 BM25？") == 1
        assert questions[0] == "什么是 BM25？"

    def test_save_interaction_disabled_returns_load(self, tmp_path):
        store = SessionMemoryStore(_make_config(tmp_path, enabled=False))
        result = store.save_interaction(question="Q", mode="local", blocks=[], wiki_hits=[])
        assert result["recent_questions"] == []

    def test_save_interaction_includes_wiki_topics(self, tmp_path):
        store = SessionMemoryStore(_make_config(tmp_path))
        wiki_hits = [{"title": "Wiki概念-搜索"}, {"title": "Wiki项目-Alpha"}]
        with patch("iris.memory.long_term._atomic_write_json"):
            result = store.save_interaction(
                question="搜索是什么？", mode="local", blocks=[], wiki_hits=wiki_hits
            )
        assert "Wiki概念-搜索" in result["recent_topics"]


# ── 纯函数 ────────────────────────────────────────────────────


class TestBuildTopics:
    def test_wiki_hits_titles_added(self):
        wiki_hits = [{"title": "项目-Alpha"}, {"title": "概念-检索"}]
        topics = _build_topics([], wiki_hits)
        assert "项目-Alpha" in topics
        assert "概念-检索" in topics

    def test_blocks_titles_added(self):
        block = MagicMock()
        block.title = "技术文档"
        topics = _build_topics([block], [])
        assert "技术文档" in topics

    def test_empty_title_skipped(self):
        wiki_hits = [{"title": ""}, {"title": "  "}]
        topics = _build_topics([], wiki_hits)
        assert topics == []

    def test_max_wiki_hits_3(self):
        wiki_hits = [{"title": f"T{i}"} for i in range(6)]
        topics = _build_topics([], wiki_hits)
        assert len(topics) <= 3


class TestUpdateTopicThreads:
    def test_new_topic_created(self):
        threads = _update_topic_threads({}, question="Q", topics=["新主题"], mode="llm")
        assert "新主题" in threads
        assert threads["新主题"]["count"] == 1
        assert threads["新主题"]["last_question"] == "Q"

    def test_existing_topic_incremented(self):
        state = {"旧主题": {"count": 2, "last_question": "OldQ", "last_mode": "local"}}
        threads = _update_topic_threads(state, question="NewQ", topics=["旧主题"], mode="llm")
        assert threads["旧主题"]["count"] == 3
        assert threads["旧主题"]["last_question"] == "NewQ"

    def test_invalid_value_in_state_skipped(self):
        state = {"broken": "not_a_dict"}
        threads = _update_topic_threads(state, question="Q", topics=[], mode="local")
        assert "broken" not in threads

    def test_max_10_threads_kept(self):
        state = {f"T{i}": {"count": i, "last_question": "", "last_mode": ""} for i in range(12)}
        threads = _update_topic_threads(state, question="Q", topics=[], mode="local")
        assert len(threads) <= 10


class TestBuildRecentSummary:
    def test_empty_returns_default(self):
        result = _build_recent_summary([], [], {})
        assert "暂无" in result

    def test_questions_and_topics_included(self):
        result = _build_recent_summary(["Q1", "Q2"], ["主题A", "主题B"], {})
        assert "Q1" in result
        assert "主题A" in result

    def test_top_threads_preferred_over_raw_topics(self):
        threads = {"热门主题": {"count": 5, "last_question": "Q", "last_mode": "llm"}}
        result = _build_recent_summary(["Q"], ["其他主题"], threads)
        assert "热门主题" in result
