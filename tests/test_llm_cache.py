"""llm/cache.py LLMResponseCache 单元测试。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from iris.llm.cache import LLMResponseCache, _make_cache_key


# ── 测试用模拟响应 ─────────────────────────────────────────


@dataclass
class _MockResponse:
    text: str = ""
    model: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    selected_role: str = ""
    matched_rule: str = ""
    api_base_url: str = ""


def _make_response(**kwargs):
    defaults = {
        "text": "test answer",
        "model": "test-model",
        "provider": "openai",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "selected_role": "base_model",
        "matched_rule": "__default__",
    }
    defaults.update(kwargs)
    return _MockResponse(**defaults)


# ── _make_cache_key ────────────────────────────────────────


def test_cache_key_deterministic():
    key1 = _make_cache_key("hello", {"task": "qa"}, None)
    key2 = _make_cache_key("hello", {"task": "qa"}, None)
    assert key1 == key2


def test_cache_key_differs_by_prompt():
    key1 = _make_cache_key("hello", {}, None)
    key2 = _make_cache_key("world", {}, None)
    assert key1 != key2


def test_cache_key_differs_by_route_context():
    key1 = _make_cache_key("hello", {"task": "qa"}, None)
    key2 = _make_cache_key("hello", {"task": "analysis"}, None)
    assert key1 != key2


def test_cache_key_differs_by_force_model():
    key1 = _make_cache_key("hello", {}, None)
    key2 = _make_cache_key("hello", {}, "gpt-4")
    assert key1 != key2


def test_cache_key_none_context():
    key = _make_cache_key("hello", None, None)
    assert len(key) == 32  # MD5 hex


# ── LLMResponseCache ───────────────────────────────────────


class TestLLMResponseCache:

    def test_put_and_get(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        response = _make_response(text="cached answer")

        cache.put("hello", {"task": "qa"}, None, response)
        cached = cache.get("hello", {"task": "qa"}, None)

        assert cached is not None
        assert cached["text"] == "cached answer"
        assert cached["model"] == "test-model"
        assert cached["prompt_tokens"] == 10

    def test_miss_returns_none(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        assert cache.get("nonexistent", {}, None) is None

    def test_stats_tracks_hits_misses(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        response = _make_response()

        cache.put("q", {}, None, response)
        cache.get("q", {}, None)  # hit
        cache.get("x", {}, None)  # miss

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["total"] == 2
        assert stats["hit_rate"] == 0.5

    def test_clear_removes_entries(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        response = _make_response()

        cache.put("q", {}, None, response)
        assert cache.get("q", {}, None) is not None

        removed = cache.clear()
        assert removed >= 1

        # After clear, entry should be gone
        cached = cache.get("q", {}, None)
        assert cached is None

    def test_ttl_expiry_default(self, tmp_path):
        """Default TTL should keep freshly written entries."""
        cache = LLMResponseCache(tmp_path)  # default 3600s TTL
        response = _make_response()

        cache.put("q", {}, None, response)
        cached = cache.get("q", {}, None)
        assert cached is not None

    def test_cache_dir_structure(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        response = _make_response()

        cache.put("hello", {}, None, response)
        # Check two-level directory structure
        cache_dir = tmp_path / "cache" / "llm_responses"
        assert cache_dir.exists()
        subdirs = [d for d in cache_dir.iterdir() if d.is_dir()]
        assert len(subdirs) >= 1

    def test_multiple_entries(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        r1 = _make_response(text="answer1")
        r2 = _make_response(text="answer2")

        cache.put("q1", {}, None, r1)
        cache.put("q2", {}, None, r2)

        assert cache.get("q1", {}, None)["text"] == "answer1"
        assert cache.get("q2", {}, None)["text"] == "answer2"

    def test_different_route_contexts(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        r1 = _make_response(text="qa answer")
        r2 = _make_response(text="analysis answer")

        cache.put("query", {"task_type": "qa"}, None, r1)
        cache.put("query", {"task_type": "analysis"}, None, r2)

        assert cache.get("query", {"task_type": "qa"}, None)["text"] == "qa answer"
        assert cache.get("query", {"task_type": "analysis"}, None)["text"] == "analysis answer"

    def test_stats_initial(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["total"] == 0
