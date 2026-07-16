"""LLM 响应缓存 — 基于 prompt hash 的磁盘缓存，避免重复调用 API。

设计：
  - 缓存键 = MD5(prompt + str(sorted(route_context)) + str(force_model))
  - 仅缓存 temperature=0（确定性调用）的响应，temperature>0 不缓存
  - 磁盘存储：data/cache/llm_responses/{hash[:2]}/{hash}.json
  - TTL 可配置，默认 3600 秒（1 小时）
  - max_entries 可配置，超限按 LRU（OrderedDict 内存维护）驱逐，默认 2000 条
  - 提供 stats 方法用于监控命中率

用法:
    cache = LLMResponseCache(data_dir, ttl_seconds=3600, max_entries=2000)
    cached = cache.get(prompt, route_context, force_model)
    if cached:
        return cached
    response = call_llm(...)
    cache.put(prompt, route_context, force_model, response)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TTL = 3600
_DEFAULT_MAX_ENTRIES = 2000


def _make_cache_key(
    prompt: str,
    route_context: Optional[Dict[str, Any]] = None,
    force_model: Optional[str] = None,
) -> str:
    """基于 prompt + route_context + force_model 生成缓存键。"""
    ctx_str = json.dumps(
        sorted((route_context or {}).items()), sort_keys=True, ensure_ascii=False
    )
    key_parts = f"{prompt}|{ctx_str}|{force_model or ''}"
    return hashlib.md5(key_parts.encode("utf-8")).hexdigest()


class LLMResponseCache:
    """基于 prompt hash 的磁盘缓存，LRU 驱逐使用内存 OrderedDict 维护。

    仅缓存 temperature=0 的确定性 LLM 调用。
    """

    def __init__(
        self,
        data_dir: Path,
        ttl_seconds: int = _DEFAULT_TTL,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ):
        self._cache_dir = data_dir / "cache" / "llm_responses"
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        # 内存 LRU: OrderedDict[cache_key → cached_at_ts]
        # 最近访问的在末尾，最旧的在开头
        self._lru: OrderedDict[str, float] = OrderedDict()
        self._available = self._init_dir()

    def _init_dir(self) -> bool:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as exc:
            logger.warning("LLMResponseCache 目录创建失败，缓存不可用: %s", exc)
            return False

    def _entry_path(self, cache_key: str) -> Path:
        """两级目录：{cache_dir}/{key[:2]}/{key}.json。"""
        return self._cache_dir / cache_key[:2] / f"{cache_key}.json"

    def get(
        self,
        prompt: str,
        route_context: Optional[Dict[str, Any]] = None,
        force_model: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """检查缓存，命中则返回缓存条目 dict，未命中返回 None。

        缓存条目包含: text, model, provider, prompt_tokens, completion_tokens,
                      selected_role, matched_rule, cached_at (unix timestamp)
        """
        if not self._available:
            return None

        key = _make_cache_key(prompt, route_context, force_model)
        entry_path = self._entry_path(key)
        if not entry_path.exists():
            self._misses += 1
            return None

        try:
            data = json.loads(entry_path.read_text(encoding="utf-8"))
            cached_at = data.get("cached_at", 0)
            if time.time() - cached_at > self._ttl:
                # TTL 过期，删除旧条目并从 LRU 移除
                try:
                    entry_path.unlink()
                except OSError:
                    logger.debug("TTL 过期缓存删除失败: %s", entry_path)
                self._lru.pop(key, None)
                self._misses += 1
                return None

            # 命中：移到 LRU 末尾（最近使用）
            self._lru.pop(key, None)
            self._lru[key] = cached_at
            self._hits += 1
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("LLMResponseCache 读取失败: %s", exc)
            self._lru.pop(key, None)
            self._misses += 1
            return None

    def put(
        self,
        prompt: str,
        route_context: Optional[Dict[str, Any]],
        force_model: Optional[str],
        response: Any,
    ) -> None:
        """写入缓存条目。超限时通过内存 LRU 驱逐最旧条目。

        response 可以是 LLMResponse（provider 返回）或 GenerationResult（service 返回）。
        """
        if not self._available:
            return

        key = _make_cache_key(prompt, route_context, force_model)
        entry_path = self._entry_path(key)

        # 从不同响应类型中提取字段
        text = getattr(response, "text", "")
        model = getattr(response, "model", "")
        provider = getattr(response, "provider", "")
        prompt_tokens = getattr(response, "prompt_tokens", 0)
        completion_tokens = getattr(response, "completion_tokens", 0)
        selected_role = getattr(response, "selected_role", "")
        matched_rule = getattr(response, "matched_rule", "")
        now = time.time()

        entry = {
            "text": text,
            "model": model,
            "provider": provider,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "selected_role": selected_role,
            "matched_rule": matched_rule,
            "cached_at": now,
            "cache_key": key,
        }

        try:
            entry_path.parent.mkdir(parents=True, exist_ok=True)
            entry_path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")

            # 更新 LRU（如果 key 已存在，移到最后；否则插入）
            self._lru.pop(key, None)
            self._lru[key] = now

            # 超过限制时驱逐最旧条目
            if self._max_entries > 0 and len(self._lru) > self._max_entries:
                oldest_key, _ = self._lru.popitem(last=False)
                try:
                    self._entry_path(oldest_key).unlink(missing_ok=True)
                    self._evictions += 1
                except OSError:
                    logger.debug("LRU 驱逐删除失败: %s", oldest_key)
        except OSError as exc:
            logger.debug("LLMResponseCache 写入失败: %s", exc)

    def stats(self) -> Dict[str, Any]:
        """返回缓存统计信息（命中/未命中/命中率/驱逐数/当前条目数）。"""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
            "ttl_seconds": self._ttl,
            "max_entries": self._max_entries,
            "entries": len(self._lru),
            "evictions": self._evictions,
        }

    def clear(self) -> int:
        """清空所有缓存条目，返回删除文件数。"""
        removed = 0
        if not self._cache_dir.exists():
            return 0
        for subdir in self._cache_dir.iterdir():
            if subdir.is_dir():
                for entry in subdir.iterdir():
                    try:
                        entry.unlink()
                        removed += 1
                    except OSError:
                        logger.debug("清空缓存删除失败: %s", entry)
                try:
                    subdir.rmdir()
                except OSError:
                    logger.debug("清空缓存目录删除失败: %s", subdir)
        self._lru.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        return removed
