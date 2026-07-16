"""LLMResponseCache LRU 驱逐策略专项测试（v2: 内存 OrderedDict 驱逐）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from iris.llm.cache import LLMResponseCache


# ── 测试用响应对象 ────────────────────────────────────────────


class FakeResponse:
    def __init__(self, text="test", model="m", provider="p",
                 prompt_tokens=10, completion_tokens=5,
                 selected_role="base", matched_rule="rule"):
        self.text = text
        self.model = model
        self.provider = provider
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.selected_role = selected_role
        self.matched_rule = matched_rule


def _make_resp(**kw):
    return FakeResponse(**kw)


# ── stats() 字段 ──────────────────────────────────────────────


class TestStatsFields:
    def test_stats_contains_max_entries(self, tmp_path):
        cache = LLMResponseCache(tmp_path, max_entries=500)
        stats = cache.stats()
        assert "max_entries" in stats
        assert stats["max_entries"] == 500

    def test_stats_contains_evictions(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        stats = cache.stats()
        assert "evictions" in stats
        assert stats["evictions"] == 0

    def test_stats_contains_entries(self, tmp_path):
        """v2 新增：entries 字段显示当前 LRU 条目数。"""
        cache = LLMResponseCache(tmp_path, max_entries=100)
        stats = cache.stats()
        assert "entries" in stats
        assert stats["entries"] == 0

    def test_stats_initial_zeros(self, tmp_path):
        cache = LLMResponseCache(tmp_path, max_entries=100)
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["evictions"] == 0
        assert stats["max_entries"] == 100


# ── 写入即驱逐（不再需要手动 _evict_lru）───────────────────────


class TestImmediateLRUEviction:
    def test_no_eviction_when_below_limit(self, tmp_path):
        cache = LLMResponseCache(tmp_path, max_entries=100)
        cache.put("q1", {}, None, _make_resp(text="a1"))
        cache.put("q2", {}, None, _make_resp(text="a2"))
        assert cache._evictions == 0
        assert cache.stats()["entries"] == 2

    def test_evicts_oldest_when_over_limit(self, tmp_path):
        cache = LLMResponseCache(tmp_path, max_entries=1)
        cache.put("q1", {}, None, _make_resp(text="oldest"))
        time.sleep(0.01)
        cache.put("q2", {}, None, _make_resp(text="newest"))
        # 写入 q2 时 max_entries=1 触发立即驱逐 q1
        assert cache._evictions >= 1
        assert cache.get("q1", {}, None) is None

    def test_evict_keeps_newest(self, tmp_path):
        cache = LLMResponseCache(tmp_path, max_entries=1)
        cache.put("q1", {}, None, _make_resp(text="oldest"))
        time.sleep(0.01)
        cache.put("q2", {}, None, _make_resp(text="newest"))
        # q2 应保留
        result = cache.get("q2", {}, None)
        assert result is not None
        assert result["text"] == "newest"

    def test_multiple_eviction(self, tmp_path):
        """写入超出 max_entries 多条时逐条驱逐。"""
        cache = LLMResponseCache(tmp_path, max_entries=2)
        cache.put("a", {}, None, _make_resp(text="a"))
        cache.put("b", {}, None, _make_resp(text="b"))
        cache.put("c", {}, None, _make_resp(text="c"))  # 驱逐 a
        cache.put("d", {}, None, _make_resp(text="d"))  # 驱逐 b
        assert cache._evictions == 2
        assert cache.get("a", {}, None) is None
        assert cache.get("b", {}, None) is None
        assert cache.get("c", {}, None) is not None
        assert cache.get("d", {}, None) is not None


# ── 写入触发驱逐：磁盘文件数 ────────────────────────────────────


class TestDiskFileCountAfterEviction:
    def test_disk_file_count_after_eviction(self, tmp_path):
        """驱逐后磁盘文件数不超过 max_entries。"""
        cache = LLMResponseCache(tmp_path, max_entries=1)

        cache.put("q1", {}, None, _make_resp(text="first"))
        time.sleep(0.01)
        cache.put("q2", {}, None, _make_resp(text="second"))

        cache_dir = tmp_path / "cache" / "llm_responses"
        json_files = list(cache_dir.rglob("*.json"))
        assert len(json_files) <= 1

    def test_disk_files_match_lru_entries(self, tmp_path):
        """磁盘文件数应与 LRU 条目数一致。"""
        cache = LLMResponseCache(tmp_path, max_entries=5)
        for i in range(8):
            cache.put(f"q{i}", {}, None, _make_resp(text=f"item{i}"))

        cache_dir = tmp_path / "cache" / "llm_responses"
        json_files = list(cache_dir.rglob("*.json"))
        assert len(json_files) <= 5
        assert cache.stats()["entries"] == len(json_files)


# ── LRU 命中提升 ──────────────────────────────────────────────


class TestLRUAccessPromotion:
    def test_get_promotes_to_recent(self, tmp_path):
        """get() 命中后将条目提升到最近使用。"""
        cache = LLMResponseCache(tmp_path, max_entries=2)
        cache.put("a", {}, None, _make_resp(text="a"))
        cache.put("b", {}, None, _make_resp(text="b"))
        # 访问 a，将其提升为最近使用
        cache.get("a", {}, None)
        # b 变成最旧，写入 c 驱逐 b
        cache.put("c", {}, None, _make_resp(text="c"))
        assert cache.get("a", {}, None) is not None  # a 被保护
        assert cache.get("b", {}, None) is None       # b 被驱逐


# ── clear() 行为 ──────────────────────────────────────────────


class TestClearBehavior:
    def test_clear_resets_lru(self, tmp_path):
        """clear() 清空 LRU 内存状态。"""
        cache = LLMResponseCache(tmp_path, max_entries=10)
        cache.put("a", {}, None, _make_resp())
        cache.put("b", {}, None, _make_resp())
        cache.clear()
        assert cache.stats()["entries"] == 0

    def test_clear_resets_evictions(self, tmp_path):
        """clear() 重置驱逐计数（全新开始）。"""
        cache = LLMResponseCache(tmp_path, max_entries=1)
        cache.put("a", {}, None, _make_resp())
        cache.put("b", {}, None, _make_resp())
        assert cache._evictions >= 1
        cache.clear()
        assert cache._evictions == 0

    def test_clear_resets_hits_and_misses(self, tmp_path):
        cache = LLMResponseCache(tmp_path)
        cache.put("q", {}, None, _make_resp())
        cache.get("q", {}, None)   # hit
        cache.get("x", {}, None)   # miss
        assert cache.stats()["hits"] == 1
        cache.clear()
        assert cache.stats()["hits"] == 0
        assert cache.stats()["misses"] == 0
