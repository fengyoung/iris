"""向量通道健康检查 — v3.40.0 回归防钉。

v3.40.0 把 `_init_embedder` 改成传 `llm_cfg.model_dump()`，而 Pydantic v2 的
model_dump() 不解包 SecretStr，api_key 被 f-string 渲染成掩码 `'**********'`，
请求带着星号打 DashScope 必然 401——又被 `except (EmbedderError, ...)` 的
「向量检索降级」兜底吞掉，于是向量通道静默失效：索引在、维度对、模型匹配，
只有检索质量在悄悄变差，五天无人察觉。

这组测试同时钉住两件事：生产路径必须拿到明文凭证，且 `iris status` 必须能把
「看着正常、实则降级」这种状态报出来。
"""

from __future__ import annotations

import json

import pytest

from iris.app.cli.helpers import _build_vector_channel_health, _credential_looks_usable
from iris.config.loader import load_config_bundle
from iris.output.formatter import _fmt_status
from iris.retrieval.vector_index import VectorIndex

EMB_KEY = "sk-test-embedding-key"
EMB_MODEL = "text-embedding-v3"


@pytest.fixture
def embed_bundle(temp_project, minimal_app_config, minimal_llm_config,
                 minimal_data_source_config):
    """带可用 embedding 配置的 bundle，走真实 load_config_bundle 解析链路。

    关键是 api_key 写成 `${TEST_EMB_KEY}` 占位符——只有经真实链路解析，
    它才会变成 SecretStr，从而复现 model_dump() 不解包的那条路径。
    """
    minimal_llm_config["embedding"] = {
        "enabled": True, "model": EMB_MODEL,
        "api_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "${TEST_EMB_KEY}",
    }
    config_dir = temp_project / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name, data in [("app", minimal_app_config), ("llm", minimal_llm_config),
                       ("data_source", minimal_data_source_config)]:
        (config_dir / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (temp_project / ".env").write_text(
        f"TEST_EMB_KEY={EMB_KEY}\nTEST_API_KEY=sk-test-key\n", encoding="utf-8")
    return load_config_bundle(temp_project)


def _write_legacy_index(bundle, source="test_source", *, model=EMB_MODEL, count=7):
    """旧版三文件布局：meta.json 与 vectors.npy 同目录。"""
    index_dir = bundle.root / "data" / "metadata" / f"{source}_vector_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "vectors.npy").write_bytes(b"")
    (index_dir / "meta.json").write_text(
        json.dumps({"dim": 1024, "count": count, "embedder_model": model}), encoding="utf-8")
    return index_dir


class TestProductionEmbedderCredential:
    """核心回归：EnhancedRetriever 走的那条构造路径必须拿到明文凭证。"""

    def test_init_embedder_gets_plaintext_not_mask(self, embed_bundle):
        from iris.retrieval.enhanced import _init_embedder

        embedder = _init_embedder(embed_bundle)
        assert embedder is not None
        assert embedder._api_key == EMB_KEY
        assert set(embedder._api_key) != {"*"}, "凭证被渲染成掩码，请求必然 401"

    def test_enhanced_retriever_embeds_with_usable_credential(self, embed_bundle):
        """走完整构造链：EnhancedRetriever 持有的 embedder 凭证可用。"""
        from iris.retrieval.enhanced import EnhancedRetriever

        retriever = EnhancedRetriever(embed_bundle)
        assert retriever._embedder is not None
        assert retriever._embedder._api_key == EMB_KEY


class TestCredentialLooksUsable:
    def test_plaintext_ok(self):
        assert _credential_looks_usable("sk-real-key") is True

    def test_empty_not_ok(self):
        assert _credential_looks_usable("") is False
        assert _credential_looks_usable("   ") is False

    def test_mask_not_ok(self):
        """SecretStr 的掩码渲染是最典型的「看着有值、实则必然 401」。"""
        assert _credential_looks_usable("**********") is False

    def test_none_not_ok(self):
        assert _credential_looks_usable(None) is False


class TestVectorChannelHealth:
    def test_ok_when_credential_and_index_both_good(self, embed_bundle):
        _write_legacy_index(embed_bundle)
        health = _build_vector_channel_health(embed_bundle)
        assert health["status"] == "ok"
        assert health["embedder_ready"] is True
        assert health["credential_usable"] is True
        assert health["indexes"]["test_source"] == {
            "chunk_count": 7, "stored_model": EMB_MODEL, "model_ok": True}

    def test_degraded_when_credential_is_masked(self, embed_bundle, monkeypatch):
        """反例钉：凭证被掩码时必须报降级，而不是「看着有值」就算健康。"""
        _write_legacy_index(embed_bundle)

        class _Masked:
            _api_key = "**********"

        monkeypatch.setattr("iris.retrieval.enhanced._init_embedder", lambda _b: _Masked())
        health = _build_vector_channel_health(embed_bundle)
        assert health["status"] == "degraded"
        assert health["credential_usable"] is False

    def test_degraded_when_index_missing(self, embed_bundle):
        health = _build_vector_channel_health(embed_bundle)
        assert health["status"] == "degraded"
        assert health["indexes"]["test_source"]["model_ok"] is False

    def test_degraded_when_index_model_mismatch(self, embed_bundle):
        _write_legacy_index(embed_bundle, model="text-embedding-v2")
        health = _build_vector_channel_health(embed_bundle)
        assert health["status"] == "degraded"
        assert health["indexes"]["test_source"]["stored_model"] == "text-embedding-v2"
        assert health["indexes"]["test_source"]["model_ok"] is False

    def test_disabled_when_embedding_off(self, config_bundle):
        """conftest 的 config_bundle 默认 embedding.enabled=false。"""
        health = _build_vector_channel_health(config_bundle)
        assert health["status"] == "disabled"
        assert health["enabled"] is False


class TestStatusPrettyRendering:
    """降级必须在人读的 --pretty 输出里显形，并给出可执行的下一步。"""

    def test_ok_rendered(self):
        out = _fmt_status({"vector_channel": {"status": "ok"}})
        assert "向量通道：正常" in out

    def test_disabled_rendered(self):
        out = _fmt_status({"vector_channel": {"status": "disabled"}})
        assert "向量通道：未启用" in out

    def test_masked_credential_points_at_401(self):
        out = _fmt_status({"vector_channel": {
            "status": "degraded", "embedder_ready": True,
            "credential_usable": False, "indexes": {"test_source": {"model_ok": True}}}})
        assert "已降级" in out
        assert "401" in out

    def test_model_mismatch_points_at_rebuild(self):
        out = _fmt_status({"vector_channel": {
            "status": "degraded", "embedder_ready": True,
            "credential_usable": True, "indexes": {"test_source": {"model_ok": False}}}})
        assert "force-rebuild" in out

    def test_missing_index_points_at_build(self):
        out = _fmt_status({"vector_channel": {
            "status": "degraded", "embedder_ready": True,
            "credential_usable": True, "indexes": {}}})
        assert "build-vector-index" in out


class TestVectorIndexReadMeta:
    """read_meta 只读元数据不加载向量，且要兼容两种落盘布局。"""

    def test_legacy_layout(self, tmp_path):
        index_dir = tmp_path / "src_vector_index"
        index_dir.mkdir()
        (index_dir / "vectors.npy").write_bytes(b"")
        (index_dir / "meta.json").write_text(
            json.dumps({"count": 3, "embedder_model": EMB_MODEL}), encoding="utf-8")
        meta = VectorIndex(index_dir).read_meta()
        assert meta["count"] == 3
        assert meta["embedder_model"] == EMB_MODEL

    def test_generation_layout(self, tmp_path):
        index_dir = tmp_path / "src_vector_index"
        gen_dir = index_dir / "generations" / "abc123"
        gen_dir.mkdir(parents=True)
        (index_dir / "current.json").write_text(
            json.dumps({"generation": "abc123"}), encoding="utf-8")
        for name in ("vectors.npy", "ids.json"):
            (gen_dir / name).write_bytes(b"")
        (gen_dir / "meta.json").write_text(
            json.dumps({"count": 9, "embedder_model": EMB_MODEL}), encoding="utf-8")
        meta = VectorIndex(index_dir).read_meta()
        assert meta["count"] == 9

    def test_missing_index_returns_empty(self, tmp_path):
        assert VectorIndex(tmp_path / "nope_vector_index").read_meta() == {}

    def test_corrupt_meta_returns_empty(self, tmp_path):
        index_dir = tmp_path / "src_vector_index"
        index_dir.mkdir()
        (index_dir / "vectors.npy").write_bytes(b"")
        (index_dir / "meta.json").write_text("{ not json", encoding="utf-8")
        assert VectorIndex(index_dir).read_meta() == {}

    def test_read_meta_does_not_load_vectors(self, tmp_path):
        """轻量性是它的存在理由——status 不该把 7500 条向量读进内存。"""
        index_dir = tmp_path / "src_vector_index"
        index_dir.mkdir()
        (index_dir / "vectors.npy").write_bytes(b"")
        (index_dir / "meta.json").write_text(json.dumps({"count": 1}), encoding="utf-8")
        idx = VectorIndex(index_dir)
        idx.read_meta()
        assert idx.is_loaded() is False
