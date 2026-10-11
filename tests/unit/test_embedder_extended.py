"""retrieval/embedder.py 扩展测试 — 覆盖缓存行为、_infer_provider、embed_one。"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from iris.retrieval.embedder import (
    EMBED_BATCH_SIZE,
    TextEmbedder,
    _extract_vectors,
    build_embedder_from_config,
    unwrap_secret,
)


class TestUnwrapSecret:
    """Pydantic SecretStr 必须解成明文——掩码形态会让请求带着星号去鉴权。"""

    def test_secret_str_unwrapped_to_plaintext(self):
        assert unwrap_secret(SecretStr("sk-real-key")) == "sk-real-key"

    def test_plain_str_passthrough(self):
        assert unwrap_secret("sk-real-key") == "sk-real-key"

    def test_empty_secret_str_becomes_empty(self):
        assert unwrap_secret(SecretStr("")) == ""

    def test_none_becomes_empty(self):
        assert unwrap_secret(None) == ""

    def test_result_is_never_the_mask(self):
        """反例钉：解包结果绝不能是 SecretStr 的掩码渲染。"""
        assert unwrap_secret(SecretStr("sk-real-key")) != "**********"


class TestBuildEmbedderFromConfigCredential:
    """v3.40.0 回归防钉：配置经 model_dump() 转手后凭证仍须可用。

    当时 `_init_embedder` 改成传 `llm_cfg.model_dump()`，而 Pydantic v2 的
    model_dump() 不解包 SecretStr，api_key 被 f-string 渲染成 '**********'，
    请求必然 401，又被「向量检索降级」兜底吞掉——向量通道静默失效。
    """

    @staticmethod
    def _cfg(api_key):
        return {"embedding": {"enabled": True, "model": "text-embedding-v3",
                              "api_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                              "api_key": api_key}}

    def test_secret_str_key_yields_plaintext_credential(self):
        emb = build_embedder_from_config(self._cfg(SecretStr("sk-real-key")))
        assert emb is not None
        assert emb._api_key == "sk-real-key"

    def test_plain_str_key_yields_plaintext_credential(self):
        emb = build_embedder_from_config(self._cfg("sk-real-key"))
        assert emb is not None
        assert emb._api_key == "sk-real-key"

    def test_empty_secret_str_is_rejected_not_treated_as_configured(self):
        """空的 SecretStr 恒为真值，不解包就会绕过判空、把「未配置」拖到 401 才暴露。"""
        assert build_embedder_from_config(self._cfg(SecretStr(""))) is None

    def test_disabled_returns_none(self):
        cfg = self._cfg(SecretStr("sk-real-key"))
        cfg["embedding"]["enabled"] = False
        assert build_embedder_from_config(cfg) is None

    def test_text_embedder_direct_construction_unwraps_too(self):
        """直接构造 TextEmbedder 也不能把掩码当成凭证。"""
        emb = TextEmbedder(api_base_url="https://a.com", api_key=SecretStr("sk-real-key"), model="m")
        assert emb._api_key == "sk-real-key"


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


class TestEmbedBatching:
    """provider 对单请求输入条数有硬上限（DashScope 为 10），embedder 必须自己分批。

    回归背景：分批逻辑原先只写在 vector_index 的调用侧，okr_evidence 直接整批调用
    embed()，超过 10 条即 400，且失败被静默吞掉，连续多日无人察觉。
    """

    @staticmethod
    def _embedder() -> TextEmbedder:
        return TextEmbedder(api_base_url="https://a.com/v1", api_key="k", model="m")

    @staticmethod
    def _fake_response(texts):
        """把文本序号编进向量，便于校验返回顺序。"""
        return {"data": [{"index": i, "embedding": [float(t[1:]), 1.0]}
                         for i, t in enumerate(texts)],
                "usage": {"prompt_tokens": len(texts)}}

    def _record_sizes(self, emb: TextEmbedder) -> list:
        sizes: list = []

        def fake_post(url, payload):
            sizes.append(len(payload["input"]))
            return self._fake_response(payload["input"])

        emb._post_json = fake_post
        return sizes

    def test_splits_into_batches_of_ten(self):
        emb = self._embedder()
        sizes = self._record_sizes(emb)
        vectors = emb.embed([f"t{i}" for i in range(25)])
        assert sizes == [10, 10, 5]
        assert len(vectors) == 25

    def test_single_request_when_within_limit(self):
        emb = self._embedder()
        sizes = self._record_sizes(emb)
        emb.embed([f"t{i}" for i in range(10)])
        assert sizes == [10]

    def test_never_exceeds_provider_limit(self):
        """反例钉：任何一次请求的输入条数都不得超过 EMBED_BATCH_SIZE。"""
        emb = self._embedder()
        sizes = self._record_sizes(emb)
        emb.embed([f"t{i}" for i in range(37)])
        assert max(sizes) <= EMBED_BATCH_SIZE

    def test_cached_texts_do_not_count_toward_batches(self):
        emb = self._embedder()
        emb._post_json = lambda url, payload: self._fake_response(payload["input"])
        emb.embed([f"t{i}" for i in range(10)])  # 预热缓存
        sizes = self._record_sizes(emb)
        emb.embed([f"t{i}" for i in range(15)])  # 前 10 命中缓存，只需请求 5 条
        assert sizes == [5]

    def test_result_order_matches_input(self):
        emb = self._embedder()
        self._record_sizes(emb)
        vectors = emb.embed([f"t{i}" for i in range(12)])
        assert vectors == [[float(i), 1.0] for i in range(12)]

    def test_empty_input_makes_no_request(self):
        emb = self._embedder()
        sizes = self._record_sizes(emb)
        assert emb.embed([]) == []
        assert sizes == []
