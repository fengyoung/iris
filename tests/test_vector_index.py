"""测试向量索引 — retrieval/vector_index.py。"""

from __future__ import annotations

import json

import pytest

from iris.retrieval.vector_index import (
    VectorIndex,
    VectorIndexModelMismatchError,
    build_vector_index,
)


class _FakeEmbedder:
    """测试用假 embedder：固定维度，无网络调用。"""

    def __init__(self, model: str = "model-a"):
        self.model = model

    def embed(self, texts):
        return [[float(len(t) % 7) + 0.1, 1.0] for t in texts]


class _FakeChunk:
    def __init__(self, chunk_id: str, preview: str = "预览文本"):
        self.chunk_id = chunk_id
        self.content_preview = preview


class TestVectorIndexInit:
    def test_load_nonexistent_returns_false(self, tmp_path):
        path = tmp_path / "nonexistent" / "index.json"
        vi = VectorIndex(path)
        assert vi.load() is False

    def test_binary_dir_path(self, tmp_path):
        path = tmp_path / "vi" / "index.json"
        vi = VectorIndex(path)
        bin_dir = vi._binary_dir()
        assert bin_dir.name == "index"


class TestVectorIndexLegacyJSON:
    def test_load_legacy_json(self, tmp_path):
        path = tmp_path / "vi.json"
        path.parent.mkdir(exist_ok=True)
        data = {
            "c1": {"vector": [0.1, 0.2, 0.3], "text": "hello"},
            "c2": {"vector": [0.4, 0.5, 0.6], "text": "world"},
        }
        path.write_text(json.dumps(data), encoding="utf-8")

        vi = VectorIndex(path)
        assert vi.load() is True
        assert len(vi._data) == 2

    def test_load_invalid_json_returns_false(self, tmp_path):
        path = tmp_path / "broken.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text("not json", encoding="utf-8")

        vi = VectorIndex(path)
        assert vi.load() is False


class TestVectorIndexBinaryRoundtrip:
    def test_save_and_load_binary(self, tmp_path):
        path = tmp_path / "vi.json"
        path.parent.mkdir(exist_ok=True)

        vi = VectorIndex(path)
        vi.upsert("a", [0.1, 0.2, 0.3], "text a")
        vi.upsert("b", [0.4, 0.5, 0.6], "text b")
        vi.save()

        # Load from binary
        vi2 = VectorIndex(path)
        assert vi2.load() is True
        assert len(vi2._data) == 2
        assert "a" in vi2._data
        assert "b" in vi2._data

    def test_binary_takes_precedence_over_json(self, tmp_path):
        path = tmp_path / "vi.json"
        path.parent.mkdir(exist_ok=True)

        vi = VectorIndex(path)
        vi.upsert("from_bin", [0.1, 0.2, 0.3], "from binary")
        vi.save()

        # Also write JSON (should be ignored)
        path.write_text(json.dumps({"from_json": {"vector": [0.4, 0.5, 0.6], "text": "from json"}}), encoding="utf-8")

        vi2 = VectorIndex(path)
        assert vi2.load() is True
        assert "from_bin" in vi2._data
        assert "from_json" not in vi2._data


class TestVectorIndexUpsert:
    def test_upsert_new_entry(self, tmp_path):
        vi = VectorIndex(tmp_path / "vi.json")
        vi.upsert("chunk1", [0.1, 0.2], "test text")
        assert "chunk1" in vi._data
        assert vi._data["chunk1"]["text"] == "test text"

    def test_upsert_updates_existing(self, tmp_path):
        vi = VectorIndex(tmp_path / "vi.json")
        vi.upsert("chunk1", [0.1, 0.2], "original")
        vi.upsert("chunk1", [0.3, 0.4], "updated")
        assert vi._data["chunk1"]["text"] == "updated"
        assert vi._data["chunk1"]["vector"] == [0.3, 0.4]

    def test_save_with_empty_data(self, tmp_path):
        vi = VectorIndex(tmp_path / "vi.json")
        vi.save()  # should not crash
        loaded = VectorIndex(tmp_path / "vi.json")
        assert loaded.load() is True
        assert loaded.size() == 0

    def test_incomplete_generation_is_not_published(self, tmp_path):
        path = tmp_path / "vi.json"
        vi = VectorIndex(path)
        vi.upsert("stable", [0.1, 0.2], "stable")
        vi.save()

        broken = vi._binary_dir() / "generations" / "broken"
        broken.mkdir(parents=True)
        (broken / "ids.json").write_text("{}", encoding="utf-8")

        loaded = VectorIndex(path)
        assert loaded.load() is True
        assert loaded.exists("stable")


class TestVectorIndexSearch:
    def test_search_returns_top_k(self, tmp_path):
        vi = VectorIndex(tmp_path / "vi.json")
        vi.upsert("c1", [1.0, 0.0, 0.0], "first")
        vi.upsert("c2", [0.0, 1.0, 0.0], "second")
        vi.upsert("c3", [0.0, 0.0, 1.0], "third")

        results = vi.search([0.9, 0.1, 0.0], top_k=2)
        assert len(results) == 2
        # search returns [(chunk_id, score), ...]
        assert results[0][0] == "c1"

    def test_search_ranks_by_similarity(self, tmp_path):
        vi = VectorIndex(tmp_path / "vi.json")
        vi.upsert("far", [0.0, 0.1], "far")
        vi.upsert("near", [0.9, 0.1], "near")
        results = vi.search([1.0, 0.0], top_k=2)
        # "near" should rank higher than "far"
        assert results[0][0] == "near"
        assert results[0][1] > results[1][1]

    def test_search_empty_index(self, tmp_path):
        vi = VectorIndex(tmp_path / "vi.json")
        results = vi.search([1.0, 0.0], top_k=5)
        assert results == []

    def test_search_returns_scores(self, tmp_path):
        vi = VectorIndex(tmp_path / "vi.json")
        vi.upsert("a", [1.0, 0.0], "text")
        results = vi.search([1.0, 0.0], top_k=1)
        assert len(results) == 1
        # score is the second element
        assert results[0][1] > 0.99


class TestBuildVectorIndexModelGuard:
    def _build_initial(self, tmp_path, model="model-a"):
        path = tmp_path / "vi.json"
        chunks = [_FakeChunk("c1"), _FakeChunk("c2")]
        return build_vector_index("src", chunks, _FakeEmbedder(model), path), path

    def test_same_model_incremental_ok(self, tmp_path):
        idx, path = self._build_initial(tmp_path)
        chunks = [_FakeChunk("c1"), _FakeChunk("c2"), _FakeChunk("c3")]
        idx2 = build_vector_index("src", chunks, _FakeEmbedder("model-a"), path)
        assert idx2.size() == 3

    def test_model_mismatch_raises(self, tmp_path):
        _, path = self._build_initial(tmp_path, model="model-a")
        chunks = [_FakeChunk("c3")]
        with pytest.raises(VectorIndexModelMismatchError):
            build_vector_index("src", chunks, _FakeEmbedder("model-b"), path)

    def test_model_mismatch_message_actionable(self, tmp_path):
        _, path = self._build_initial(tmp_path, model="model-a")
        with pytest.raises(VectorIndexModelMismatchError, match="force-rebuild"):
            build_vector_index("src", [_FakeChunk("c3")], _FakeEmbedder("model-b"), path)

    def test_force_rebuild_switches_model(self, tmp_path):
        _, path = self._build_initial(tmp_path, model="model-a")
        chunks = [_FakeChunk("c1"), _FakeChunk("c9")]
        idx = build_vector_index("src", chunks, _FakeEmbedder("model-b"), path, force_rebuild=True)
        # 全量重建：仅保留本次 chunks，旧的 c2 被清理
        assert idx.size() == 2
        assert idx.exists("c9")
        assert not idx.exists("c2")

    def test_force_rebuild_persists_new_model(self, tmp_path):
        _, path = self._build_initial(tmp_path, model="model-a")
        build_vector_index("src", [_FakeChunk("c1")], _FakeEmbedder("model-b"), path, force_rebuild=True)
        # 重新加载后模型记录应为新模型，后续同模型增量不再报错。
        # v3.28.1 起 chunks 为全量语料语义：增量传 [c1, c2] 全量列表
        # （只传 [c2] 会把 c1 当已删除文档清理，见 TestIncrementalStaleCleanup）。
        idx = build_vector_index("src", [_FakeChunk("c1"), _FakeChunk("c2")], _FakeEmbedder("model-b"), path)
        assert idx.size() == 2


class TestIncrementalStaleCleanup:
    """v3.28.1 回归：增量更新按 document_hash 重嵌 + 差集清理死向量。

    历史 bug：旧逻辑「exists 即跳过」——① 文档编辑后 chunk_id（路径::序号）不变，
    旧向量永不更新；② 从不删除不在本次 chunks 中的 id，已删除文档的死向量
    永久残留（v3.22.3「向量 > chunk」事故的复发通道）。
    """

    def test_stale_vectors_removed_on_incremental(self, tmp_path):
        path = tmp_path / "vi.json"
        emb = _FakeEmbedder("model-a")
        build_vector_index("src", [_FakeChunk("a.md::chunk-0"), _FakeChunk("b.md::chunk-0")], emb, path)
        # b.md 被删除：增量传入的全量列表不再包含它
        idx = build_vector_index("src", [_FakeChunk("a.md::chunk-0")], emb, path)
        assert idx.exists("a.md::chunk-0")
        assert not idx.exists("b.md::chunk-0"), "已删除文档的死向量必须被清理（回归核心断言）"
        # 落盘后重新加载同样不含死向量
        reloaded = VectorIndex(path)
        assert reloaded.load()
        assert not reloaded.exists("b.md::chunk-0")

    def test_edited_document_reembedded_by_hash(self, tmp_path):
        path = tmp_path / "vi.json"
        emb = _FakeEmbedder("model-a")
        c_v1 = _FakeChunk("a.md::chunk-0", preview="旧内容")
        c_v1.document_hash = "hash-v1"
        build_vector_index("src", [c_v1], emb, path)
        idx0 = VectorIndex(path)
        idx0.load()
        old_vec = idx0._data["a.md::chunk-0"]["vector"]

        # 文档被编辑：chunk_id 不变但 document_hash 与内容变化
        c_v2 = _FakeChunk("a.md::chunk-0", preview="新内容长度不同了")
        c_v2.document_hash = "hash-v2"
        build_vector_index("src", [c_v2], emb, path)
        idx1 = VectorIndex(path)
        idx1.load()
        new_vec = idx1._data["a.md::chunk-0"]["vector"]
        assert new_vec != old_vec, "hash 变化必须触发重嵌（回归核心断言）"
        assert idx1.doc_hash("a.md::chunk-0") == "hash-v2"

    def test_legacy_index_without_hash_not_reembedded(self, tmp_path):
        """旧索引无 doc_hash（空串）时保持旧行为不重嵌，避免升级即全量重嵌。"""
        path = tmp_path / "vi.json"
        emb = _FakeEmbedder("model-a")
        c_legacy = _FakeChunk("a.md::chunk-0")  # 无 document_hash 属性 → 空串
        build_vector_index("src", [c_legacy], emb, path)

        c_with_hash = _FakeChunk("a.md::chunk-0", preview="内容没变但现在带 hash")
        c_with_hash.document_hash = "hash-v1"
        idx = build_vector_index("src", [c_with_hash], emb, path)
        # 索引中 hash 为空 → 不触发重嵌（向量保持不变），也不报错
        assert idx.exists("a.md::chunk-0")
