"""测试 iris.retrieval.embedder — 文本向量化模块。"""

import pytest
from iris.retrieval.embedder import (
    EmbedderError,
    TextEmbedder,
    build_embedder_from_config,
    _extract_vectors,
)


class TestTextEmbedder:
    def test_constructor(self):
        emb = TextEmbedder(
            api_base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="text-embedding-v3",
        )
        assert emb._model == "text-embedding-v3"
        assert emb._api_base_url == "https://api.example.com/v1"
        assert emb._timeout == 30
        assert emb._max_retries == 2

    def test_constructor_strips_trailing_slash(self):
        emb = TextEmbedder(
            api_base_url="https://api.example.com/v1/",
            api_key="sk-test",
            model="test",
        )
        assert emb._api_base_url == "https://api.example.com/v1"

    def test_constructor_custom_timeout(self):
        emb = TextEmbedder(
            api_base_url="https://a.com", api_key="k", model="m",
            timeout=60, max_retries=5,
        )
        assert emb._timeout == 60
        assert emb._max_retries == 5

    def test_embed_empty_list_returns_empty(self):
        emb = TextEmbedder(api_base_url="https://a.com", api_key="k", model="m")
        assert emb.embed([]) == []


class TestBuildEmbedderFromConfig:
    def test_embedding_disabled_returns_none(self):
        cfg = {"embedding": {"enabled": False}}
        assert build_embedder_from_config(cfg) is None

    def test_no_embedding_section_returns_none(self):
        assert build_embedder_from_config({}) is None

    def test_no_api_key_returns_none(self):
        cfg = {"embedding": {"enabled": True, "api_key": ""}}
        assert build_embedder_from_config(cfg) is None

    def test_valid_config_returns_embedder(self):
        cfg = {"embedding": {
            "enabled": True,
            "api_key": "sk-test",
            "api_base_url": "https://api.example.com/v1",
            "model": "text-embedding-v3",
            "timeout_seconds": 10,
            "max_retries": 3,
        }}
        emb = build_embedder_from_config(cfg)
        assert emb is not None
        assert emb._model == "text-embedding-v3"
        assert emb._timeout == 10
        assert emb._max_retries == 3

    def test_defaults_when_optional_fields_missing(self):
        cfg = {"embedding": {
            "enabled": True,
            "api_key": "sk-test",
            "api_base_url": "https://api.example.com/v1",
        }}
        emb = build_embedder_from_config(cfg)
        assert emb is not None
        assert emb._model == "text-embedding-v3"  # default
        assert emb._timeout == 30  # default
        assert emb._max_retries == 2  # default


class TestExtractVectors:
    def test_extracts_from_standard_response(self):
        data = {"data": [
            {"index": 0, "embedding": [0.1, 0.2, 0.3]},
            {"index": 1, "embedding": [0.4, 0.5, 0.6]},
        ]}
        result = _extract_vectors(data)
        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    def test_sorts_by_index(self):
        data = {"data": [
            {"index": 2, "embedding": [0.7, 0.8]},
            {"index": 0, "embedding": [0.1, 0.2]},
            {"index": 1, "embedding": [0.4, 0.5]},
        ]}
        result = _extract_vectors(data)
        assert result == [[0.1, 0.2], [0.4, 0.5], [0.7, 0.8]]

    def test_skips_missing_embedding(self):
        data = {"data": [
            {"index": 0, "embedding": [0.1]},
            {"index": 1},  # 无 embedding
            {"index": 2, "embedding": [0.3]},
        ]}
        result = _extract_vectors(data)
        assert result == [[0.1], [0.3]]

    def test_empty_data_returns_empty(self):
        assert _extract_vectors({"data": []}) == []
        assert _extract_vectors({}) == []


class TestEmbedderError:
    def test_is_runtime_error(self):
        err = EmbedderError("test error")
        assert isinstance(err, RuntimeError)
        assert str(err) == "test error"
