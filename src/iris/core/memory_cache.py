"""统一内存缓存 — LRU + TTL + 可选线程安全。

提供项目中所有内存缓存的统一基类，替代分散的 OrderedDict 实现。

用法:
    cache = MemoryCache[str](max_entries=128, ttl_seconds=600, thread_safe=True)
    cache.put("key", "value")
    cached = cache.get("key")
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Dict, Generic, Optional, Tuple, TypeVar

T = TypeVar("T")


class MemoryCache(Generic[T]):
    """支持 LRU 驱逐 + TTL 过期 + 可选线程安全的内存缓存。

    线程安全由构造参数控制。默认关闭以保持单线程场景的零开销。
    """

    def __init__(
        self,
        max_entries: int = 128,
        ttl_seconds: float = 600,
        thread_safe: bool = False,
    ):
        self._max_entries = max_entries
        self._ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[T, float]] = OrderedDict()
        self._lock = threading.Lock() if thread_safe else None
        self._hits = 0
        self._misses = 0

    # ── 公共 API ──────────────────────────────────────────

    def get(self, key: str) -> Optional[T]:
        """获取缓存值。过期或不存在时返回 None。"""
        if self._lock:
            with self._lock:
                return self._get_impl(key)
        return self._get_impl(key)

    def put(self, key: str, value: T) -> None:
        """写入缓存值。超限时驱逐最旧条目。"""
        if self._lock:
            with self._lock:
                self._put_impl(key, value)
        else:
            self._put_impl(key, value)

    def clear(self) -> int:
        """清空缓存，返回清除的条目数。"""
        if self._lock:
            with self._lock:
                count = len(self._cache)
                self._cache.clear()
                return count
        count = len(self._cache)
        self._cache.clear()
        return count

    def size(self) -> int:
        """返回当前缓存条目数。"""
        if self._lock:
            with self._lock:
                return len(self._cache)
        return len(self._cache)

    def stats(self) -> Dict[str, int]:
        """返回缓存统计信息。"""
        total = self._hits + self._misses
        return {
            "entries": self.size(),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(total, 1), 3),
        }

    # ── 内部实现 ──────────────────────────────────────────

    def _get_impl(self, key: str) -> Optional[T]:
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        value, cached_at = entry
        if time.monotonic() - cached_at > self._ttl_seconds:
            del self._cache[key]
            self._misses += 1
            return None
        # 命中：移到 LRU 末尾（最近使用）
        self._cache.move_to_end(key)
        self._hits += 1
        return value

    def _put_impl(self, key: str, value: T) -> None:
        # 如果 key 已存在，先移除旧的（更新场景）
        self._cache.pop(key, None)
        self._cache[key] = (value, time.monotonic())
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
