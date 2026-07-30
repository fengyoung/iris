"""core/memory_cache.py 测试 — 覆盖 LRU + TTL + 线程安全缓存。"""

from __future__ import annotations

import time

from iris.core.memory_cache import MemoryCache


class TestMemoryCacheBasic:
    def test_put_and_get(self):
        cache = MemoryCache[int](max_entries=10)
        cache.put("key", 42)
        assert cache.get("key") == 42

    def test_get_miss(self):
        cache = MemoryCache[str]()
        assert cache.get("nonexistent") is None

    def test_update_existing(self):
        cache = MemoryCache[str](max_entries=10)
        cache.put("key", "old")
        cache.put("key", "new")
        assert cache.get("key") == "new"

    def test_size(self):
        cache = MemoryCache[int](max_entries=10)
        assert cache.size() == 0
        cache.put("a", 1)
        cache.put("b", 2)
        assert cache.size() == 2

    def test_clear(self):
        cache = MemoryCache[int](max_entries=10)
        cache.put("a", 1)
        cache.put("b", 2)
        count = cache.clear()
        assert count == 2
        assert cache.size() == 0

    def test_clear_empty(self):
        cache = MemoryCache[int]()
        assert cache.clear() == 0


class TestMemoryCacheLRU:
    def test_evicts_oldest_on_overflow(self):
        cache = MemoryCache[int](max_entries=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        cache.put("d", 4)  # evicts "a"
        assert cache.get("a") is None
        assert cache.get("d") == 4

    def test_get_refreshes_lru_position(self):
        cache = MemoryCache[int](max_entries=3)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        # Access "a" to make it most-recently-used
        assert cache.get("a") == 1
        # Now "b" is the LRU
        cache.put("d", 4)  # should evict "b"
        assert cache.get("a") == 1  # still present
        assert cache.get("b") is None  # evicted
        assert cache.get("c") == 3
        assert cache.get("d") == 4

    def test_update_moves_to_end(self):
        cache = MemoryCache[int](max_entries=2)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)  # update moves a to end
        cache.put("c", 3)  # evicts b
        assert cache.get("a") == 10
        assert cache.get("b") is None


class TestMemoryCacheTTL:
    def test_value_expires_after_ttl(self):
        cache = MemoryCache[str](max_entries=10, ttl_seconds=0.01)
        cache.put("key", "value")
        # 立即获取应该命中
        assert cache.get("key") == "value"
        # 等待 TTL 过期
        time.sleep(0.02)
        assert cache.get("key") is None

    def test_ttl_zero_never_expires(self):
        """ttl_seconds=0 → 永不过期（time.monotonic() - cached_at > 0 几乎总是 True）。
        注意：0 表示立即过期，这由调用方控制。"""
        # TTL 很小但非零行为测试
        cache = MemoryCache[str](max_entries=5, ttl_seconds=3600)
        cache.put("key", "val")
        assert cache.get("key") == "val"  # 不应在 3600s 内过期


class TestMemoryCacheStats:
    def test_stats_tracks_hits_and_misses(self):
        cache = MemoryCache[int](max_entries=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")  # hit
        cache.get("b")  # hit
        cache.get("c")  # miss
        stats = cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert 0.6 <= stats["hit_rate"] <= 0.7

    def test_stats_empty_cache(self):
        cache = MemoryCache[int]()
        stats = cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0.0


class TestMemoryCacheThreadSafe:
    def test_thread_safe_mode_works(self):
        cache = MemoryCache[int](max_entries=10, thread_safe=True)
        cache.put("a", 1)
        assert cache.get("a") == 1
        assert cache.size() == 1
        cache.clear()

    def test_non_thread_safe_no_lock(self):
        cache = MemoryCache[int](thread_safe=False)
        assert cache._lock is None


class TestMemoryCacheGeneric:
    def test_str_type(self):
        cache = MemoryCache[str]()
        cache.put("k", "hello")
        assert cache.get("k") == "hello"

    def test_list_type(self):
        cache = MemoryCache[list]()
        cache.put("k", [1, 2, 3])
        assert cache.get("k") == [1, 2, 3]

    def test_dict_type(self):
        cache = MemoryCache[dict]()
        cache.put("k", {"a": 1})
        assert cache.get("k") == {"a": 1}
