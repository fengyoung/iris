"""文本向量化：调用 OpenAI-compatible embedding API。"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from iris.core.exceptions import IrisRuntimeError

_EMBED_CACHE_MAXSIZE = 128
_EMBED_CACHE_TTL = 600.0  # embedding 结果内容不变，使用较长 TTL


class EmbedderError(IrisRuntimeError):
    """Embedding 相关错误。"""


class TextEmbedder:
    def __init__(self, api_base_url: str, api_key: str, model: str, *,
                 timeout: int = 30, max_retries: int = 2,
                 data_dir: Optional[Path] = None):
        self._api_base_url = api_base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_retries = max_retries
        self._cache: OrderedDict[str, Tuple[List[float], float]] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._data_dir = data_dir
        self._tracker = None  # 延迟初始化

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        """批量向量化，命中缓存的文本不重复请求 API。"""
        if not texts:
            return []

        results: Dict[int, List[float]] = {}
        uncached_indices: List[int] = []
        uncached_texts: List[str] = []

        for i, text in enumerate(texts):
            cached = self._get_cached(text)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        if uncached_texts:
            endpoint = self._api_base_url + "/embeddings"
            payload = {"model": self._model, "input": uncached_texts}
            data = self._post_json(endpoint, payload)
            vectors = _extract_vectors(data)
            # 记录 embedding API 用量
            self._record_usage(data, len(uncached_texts))
            for j, vec in enumerate(vectors):
                results[uncached_indices[j]] = vec
                self._put_cache(uncached_texts[j], vec)

        return [results[i] for i in range(len(texts)) if i in results]

    def embed_one(self, text: str) -> List[float]:
        results = self.embed([text])
        return results[0] if results else []

    def _post_json(self, url: str, payload: dict) -> dict:
        from iris.core.http_client import http_post_json
        return http_post_json(url, payload, {"Authorization": f"Bearer {self._api_key}"},
                             timeout=self._timeout, max_retries=self._max_retries,
                             error_factory=lambda msg: EmbedderError(f"Embedding {msg}"))

    def _cache_key(self, text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    def _get_cached(self, text: str) -> Optional[List[float]]:
        key = self._cache_key(text)
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            vec, cached_at = entry
            if time.monotonic() - cached_at > _EMBED_CACHE_TTL:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return vec

    def _put_cache(self, text: str, vec: List[float]) -> None:
        key = self._cache_key(text)
        with self._cache_lock:
            self._cache[key] = (vec, time.monotonic())
            self._cache.move_to_end(key)
            while len(self._cache) > _EMBED_CACHE_MAXSIZE:
                self._cache.popitem(last=False)

    def _record_usage(self, response_data: dict, batch_size: int) -> None:
        """记录 embedding API 调用到用量数据库（静默失败）。"""
        if self._data_dir is None:
            return
        if self._tracker is None:
            try:
                from iris.llm.usage_tracker import UsageTracker
                self._tracker = UsageTracker(self._data_dir)
            except Exception:
                return
        try:
            usage = response_data.get("usage", {})
            prompt_tokens = int(usage.get("prompt_tokens", 0)) if usage else 0
            import os
            source = os.environ.get("IRIS_CALL_SOURCE", "cli")
            self._tracker.record(
                model=self._model,
                provider=self._infer_provider(),
                route_role="embedding",
                matched_rule="embedding",
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                is_multimodal=False,
                source=source,
            )
        except Exception:
            pass  # 用量记录失败不影响主流程

    def _infer_provider(self) -> str:
        """从 api_base_url 推断提供商名称。"""
        url = self._api_base_url.lower()
        if "dashscope" in url or "bailian" in url:
            return "Bailian"
        if "deepseek" in url:
            return "Deepseek"
        if "openai" in url:
            return "OpenAI"
        return "Unknown"


def build_embedder_from_config(llm_config: dict, *,
                               data_dir: Optional[Path] = None) -> Optional[TextEmbedder]:
    """从 llm.json 的 embedding 段构造 TextEmbedder。"""
    emb_cfg = llm_config.get("embedding", {})
    if not emb_cfg.get("enabled", False):
        return None
    api_key = emb_cfg.get("api_key", "")
    if not api_key:
        return None
    return TextEmbedder(api_base_url=emb_cfg.get("api_base_url", ""),
                        api_key=api_key, model=emb_cfg.get("model", "text-embedding-v3"),
                        timeout=emb_cfg.get("timeout_seconds", 30),
                        max_retries=emb_cfg.get("max_retries", 2),
                        data_dir=data_dir)


def _extract_vectors(data: dict) -> List[List[float]]:
    data_entries = data.get("data", [])
    sorted_entries = sorted(data_entries, key=lambda item: item.get("index", 0))
    return [item["embedding"] for item in sorted_entries if "embedding" in item]
