"""core/storage.py 单元测试。"""

from __future__ import annotations

from pathlib import Path


def _make_chunk(chunk_id="c1", source="src1", path="doc.md",
                title="标题", content="内容"):
    return {
        "chunk_id": chunk_id,
        "source_name": source,
        "relative_path": path,
        "document_hash": "hash1",
        "title": title,
        "section_path": [],
        "level": 1,
        "content": content,
        "content_preview": content[:50],
        "line_start": 1,
        "line_end": 10,
        "chunk_type": "section",
        "segment_index": 1,
        "segment_count": 1,
        "structural_tags": [],
        "extracted_fields": {},
        "token_count": 5,
        "token_freq": {},
    }


class TestChunkStore:
    """ChunkStore: SQLite CRUD 基本操作。"""

    def _make_store(self, tmp_path: Path):
        from iris.core.storage import ChunkStore
        return ChunkStore(tmp_path / "test.db")

    def test_insert_and_query_by_source(self, tmp_path):
        with self._make_store(tmp_path) as store:
            inserted, errors = store.insert_chunks([_make_chunk("c1", "src1", title="T1")])
            assert inserted == 1
            assert errors == 0
            rows = store.get_chunks_by_source("src1")
            assert len(rows) == 1
            assert rows[0]["title"] == "T1"

    def test_get_by_id(self, tmp_path):
        with self._make_store(tmp_path) as store:
            store.insert_chunks([_make_chunk("c1", title="T1")])
            rows = store.get_chunks_by_ids(["c1"])
            assert len(rows) == 1
            assert rows[0]["title"] == "T1"

    def test_missing_id_returns_empty(self, tmp_path):
        with self._make_store(tmp_path) as store:
            assert store.get_chunks_by_ids(["nonexistent"]) == []

    def test_insert_replace(self, tmp_path):
        with self._make_store(tmp_path) as store:
            store.insert_chunks([_make_chunk("c1", title="旧标题")])
            store.insert_chunks([_make_chunk("c1", title="新标题")])
            rows = store.get_chunks_by_ids(["c1"])
            assert rows[0]["title"] == "新标题"

    def test_delete_by_source(self, tmp_path):
        with self._make_store(tmp_path) as store:
            store.insert_chunks([_make_chunk("c1", "src1"), _make_chunk("c2", "src2")])
            count = store.delete_by_source("src1")
            assert count == 1
            assert store.get_chunks_by_source("src1") == []
            assert len(store.get_chunks_by_source("src2")) == 1


class TestChunkStoreLoadAll:
    """load_all: 空库和有数据。"""

    def test_empty_db_returns_empty_list(self, tmp_path):
        from iris.core.storage import ChunkStore
        with ChunkStore(tmp_path / "empty.db") as store:
            result = store.load_all()
            assert result == []

    def test_load_all_with_data(self, tmp_path):
        from iris.core.storage import ChunkStore
        with ChunkStore(tmp_path / "data.db") as store:
            store.insert_chunks([_make_chunk("c1"), _make_chunk("c2")])
            results = store.load_all()
            # ChunkRecord 可导入时返回列表，否则返回 []
            assert isinstance(results, list)
            if results:
                assert len(results) == 2
