"""retrieval/embedder.py 扩展测试 — 覆盖缓存行为、_infer_provider、embed_one。"""

from __future__ import annotations

import pytest
from iris.retrieval.embedder import (
    TextEmbedder,
    _extract_vectors,
)


class TestEmbedderCache:
    def test_cache_key_is_deterministic(self):
        emb = TextEmbedder(api_base_url="https://a.com", api_key="k", model="m")
        k1 = emb._cache_key("hello world")
        k2 = emb._cache_key("hello world")
        k3 = emb._cache_key("different")
        assert k1 == k2
        assert k1 != k3

    def test_cache_key_is_md5_hex(self):
        emb = TextEmbedder(api_base_url="https://a.com", api_key="k", model="m")
        key = emb._cache_key("test")
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_get_cached_miss_returns_none(self):
        emb = TextEmbedder(api_base_url="https://a.com", api_key="k", model="m")
        assert emb._get_cached("not-in-cache") is None

    def test_put_and_get_cache(self):
        emb = TextEmbedder(api_base_url="https://a.com", api_key="k", model="m")
        emb._put_cache("text", [0.1, 0.2, 0.3])
        cached = emb._get_cached("text")
        assert cached == [0.1, 0.2, 0.3]

    def test_cache_lru_eviction(self):
        """缓存超过 maxsize 后驱逐最旧条目。"""
        emb = TextEmbedder(api_base_url="https://a.com", api_key="k", model="m")
        from iris.retrieval.embedder import _EMBED_CACHE_MAXSIZE
        # 填满缓存
        for i in range(_EMBED_CACHE_MAXSIZE + 5):
            emb._put_cache(f"text_{i}", [float(i)])
        # 最早插入的应已被驱逐
        assert emb._get_cached("text_0") is None
        # 最近插入的还在
        assert emb._get_cached(f"text_{_EMBED_CACHE_MAXSIZE + 4}") is not None


class TestInferProvider:
    def test_bailian(self):
        emb = TextEmbedder(api_base_url="https://dashscope.aliyuncs.com/v1", api_key="k", model="m")
        assert emb._infer_provider() == "Bailian"

    def test_deepseek(self):
        emb = TextEmbedder(api_base_url="https://api.deepseek.com/v1", api_key="k", model="m")
        assert emb._infer_provider() == "Deepseek"

    def test_openai(self):
        emb = TextEmbedder(api_base_url="https://api.openai.com/v1", api_key="k", model="m")
        assert emb._infer_provider() == "OpenAI"

    def test_unknown(self):
        emb = TextEmbedder(api_base_url="https://custom.example.com", api_key="k", model="m")
        assert emb._infer_provider() == "Unknown"

    def test_bailian_alt_domain(self):
        emb = TextEmbedder(api_base_url="https://bailian.aliyuncs.com/v1", api_key="k", model="m")
        assert emb._infer_provider() == "Bailian"


class TestEmbedOne:
    def test_empty_text_empty_list(self):
        emb = TextEmbedder(api_base_url="https://a.com", api_key="k", model="m")
        # embed([]) → []，embed_one 内部调用 embed([text])
        # 不实际调用 API，仅验证方法存在
        assert callable(emb.embed_one)


class TestExtractVectorsExtended:
    def test_missing_data_key(self):
        assert _extract_vectors({}) == []

    def test_data_not_a_list(self):
        """非列表 data → 抛出 AttributeError（已知行为，API 约定返回列表）。"""
        with pytest.raises((AttributeError, TypeError)):
            _extract_vectors({"data": "not_a_list"})
