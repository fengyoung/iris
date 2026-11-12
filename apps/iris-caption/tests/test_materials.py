"""Standalone material parsing, retrieval and retention contract tests."""
import json
import sys
from types import SimpleNamespace

import pytest

from iris_caption import materials
from iris_caption.materials import MaterialIndex


def test_memory_lexical_budget_and_lifecycle(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    index = MaterialIndex()
    first = index.add_text("项目", "声笺会议字幕支持本地检索。")
    index.add_text("other", "bananas oranges")
    assert not index.semantic_available
    assert "lexical-only" in index.status
    assert "声笺" in index.search("声笺")[0]
    assert index.search("unrelated") == []
    assert sum(map(len, index.search("声笺", max_chars=8))) <= 8
    assert index.search("声笺", limit=0) == []
    assert index.search("") == []
    assert not list(tmp_path.iterdir())
    metadata = index.list_materials()
    metadata[0]["name"] = "changed"
    assert index.list_materials()[0]["name"] == "项目"
    index.remove(first)
    index.remove(first)
    assert index.search("声笺") == []
    index.clear()
    assert index.list_materials() == []


def test_local_embedding_injection(tmp_path, monkeypatch):
    calls = []

    class FakeModel:
        def __init__(self, path, **kwargs):
            calls.append((path, kwargs))

        def encode(self, texts):
            return [[1, 0] if text in {"automobile", "car"} else [0, 1] for text in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeModel))
    index = MaterialIndex(str(tmp_path))
    index.add_text("vehicle", "car")
    index.add_text("food", "banana")
    assert index.semantic_available
    assert index.search("automobile") == ["[vehicle] car"]
    assert calls == [(str(tmp_path.resolve()), {"local_files_only": True})]
    missing = MaterialIndex(str(tmp_path / "missing"))
    assert not missing.semantic_available
    assert len(calls) == 1


def test_embedding_failure_degrades(tmp_path, monkeypatch):
    class Broken:
        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, _texts):
            raise RuntimeError("inference unavailable")

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Broken))
    index = MaterialIndex(str(tmp_path))
    index.add_text("notes", "searchable")
    assert index.search("searchable")
    assert not index.semantic_available
    assert "inference failed" in index.status


@pytest.mark.parametrize("extension", ["txt", "md", "markdown"])
def test_text_files(tmp_path, extension):
    path = tmp_path / f"notes.{extension}"
    path.write_text("\ufeff会议资料 中文 UTF-8", encoding="utf-8")
    index = MaterialIndex()
    identifier = index.add_file(path)
    assert index.list_materials()[0]["id"] == identifier
    assert index.search("资料")


def test_file_errors_and_limits(tmp_path, monkeypatch):
    index = MaterialIndex()
    with pytest.raises(ValueError, match="does not exist"):
        index.add_file(tmp_path / "absent")
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        index.add_file(path)
    path = tmp_path / "bad.png"
    path.write_bytes(b"abc")
    with pytest.raises(ValueError, match="Unsupported"):
        index.add_file(path)
    monkeypatch.setattr(materials, "MAX_FILE_BYTES", 2)
    with pytest.raises(ValueError, match="20 MiB"):
        index.add_file(path)
    monkeypatch.setattr(materials, "MAX_TEXT_CHARS", 4)
    with pytest.raises(ValueError, match="characters"):
        index.add_text("name", "12345")
    with pytest.raises(ValueError, match="OCR"):
        index.add_text("name", " ")
    assert index.list_materials() == []


def test_pdf_text_empty_and_page_limit(tmp_path, monkeypatch):
    fitz = pytest.importorskip("fitz")
    path = tmp_path / "notes.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "caption vocabulary")
        doc.save(path)
    index = MaterialIndex()
    index.add_file(path)
    assert index.search("vocabulary")
    monkeypatch.setattr(materials, "MAX_PDF_PAGES", 0)
    with pytest.raises(ValueError, match="pages"):
        index.add_file(path)
    monkeypatch.setattr(materials, "MAX_PDF_PAGES", 200)
    empty = tmp_path / "scan.pdf"
    with fitz.open() as doc:
        doc.new_page()
        doc.save(empty)
    with pytest.raises(ValueError, match="OCR"):
        index.add_file(empty)


def test_docx_paragraphs_tables_and_expansion_limit(tmp_path, monkeypatch):
    docx = pytest.importorskip("docx")
    path = tmp_path / "notes.docx"
    doc = docx.Document()
    doc.add_paragraph("项目会议")
    doc.add_table(rows=1, cols=1).cell(0, 0).text = "specialterm"
    doc.save(path)
    index = MaterialIndex()
    index.add_file(path)
    assert index.search("specialterm")
    monkeypatch.setattr(materials, "MAX_DOCX_EXPANDED_BYTES", 1)
    with pytest.raises(ValueError, match="expanded"):
        index.add_file(path)


def test_retention_selection_and_feishu_quarantine(tmp_path):
    index = MaterialIndex()
    local = index.add_text("local", "localmaterial")
    remote = index.add_text("remote", "staleprivate", "feishu:https://example.feishu.cn/docx/abc")
    index.add_text("temporary", "notretained")
    index.save_retained(tmp_path, [local, remote])
    loaded = MaterialIndex()
    assert loaded.load_retained(tmp_path) == [local, remote]
    assert loaded.search("localmaterial")
    assert not loaded.search("staleprivate")
    assert not loaded.search("notretained")
    row = loaded.list_materials()[1]
    assert row["source_url"] == "https://example.feishu.cn/docx/abc"
    assert row["requires_verification"]
    with pytest.raises(ValueError, match="exact source URL"):
        loaded.verify_retained(remote, "https://wrong", "freshprivate")
    loaded.verify_retained(remote, row["source_url"], "freshprivate")
    assert loaded.search("freshprivate")
    assert not loaded.search("staleprivate")
    loaded.save_retained(tmp_path, [remote])
    reopened = MaterialIndex()
    reopened.load_retained(tmp_path)
    assert not reopened.search("freshprivate")
    loaded.save_retained(tmp_path, [])
    assert MaterialIndex().load_retained(tmp_path) == []


def test_malformed_retained_is_transactional(tmp_path):
    index = MaterialIndex()
    identifier = index.add_text("keep", "keepme")
    index.save_retained(tmp_path, [identifier])
    target = tmp_path / "materials.json"
    payload = json.loads(target.read_text())
    payload["documents"].append({"id": "bad"})
    target.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        index.load_retained(tmp_path)
    assert len(index.list_materials()) == 1
    assert index.search("keepme")
    with pytest.raises(ValueError, match="unknown"):
        index.save_retained(tmp_path, ["absent"])


def test_snapshot_keeps_documents_and_verification(tmp_path):
    index = MaterialIndex()
    local = index.add_text("old", "original")
    remote = index.add_text("remote", "private", "https://example.feishu.cn/docx/a")
    index.save_retained(tmp_path, [local, remote])
    index.load_retained(tmp_path)
    captured = index.snapshot()
    index.verify_retained(remote, "https://example.feishu.cn/docx/a", "fresh")
    index.remove(local)
    index.add_text("new", "replacement")
    assert captured.search("original")
    assert not captured.search("private")
    assert not captured.search("fresh")
    assert not captured.search("replacement")
    captured.clear()
    assert index.search("fresh")


def test_search_holds_mutation_lock(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    index = MaterialIndex()
    index.add_text("name", "original")
    entered, release, attempted = Event(), Event(), Event()
    tokenize = materials._tokens

    def blocked_tokens(text):
        entered.set()
        assert release.wait(5)
        return tokenize(text)

    def mutate():
        attempted.set()
        index.clear()

    monkeypatch.setattr(materials, "_tokens", blocked_tokens)
    with ThreadPoolExecutor(max_workers=2) as pool:
        search = pool.submit(index.search, "original")
        try:
            assert entered.wait(5)
            mutation = pool.submit(mutate)
            assert attempted.wait(5)
            assert not mutation.done()
            # Main thread cannot acquire a lock held by the retrieval worker.
            acquired = index._lock.acquire(blocking=False)
            if acquired:
                index._lock.release()
            assert not acquired
        finally:
            release.set()
        assert search.result(timeout=5) == ["[name] original"]
        mutation.result(timeout=5)
    assert index.list_materials() == []


def test_snapshot_model_inference_is_serialized(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()

    class BlockingModel:
        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, texts):
            entered.set()
            assert release.wait(5)
            return [[1, 0] for _ in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        SimpleNamespace(SentenceTransformer=BlockingModel))
    index = MaterialIndex(str(tmp_path))
    index.add_text("old", "original")
    captured = index.snapshot()
    assert captured._model_lock is index._model_lock
    with ThreadPoolExecutor(max_workers=1) as pool:
        search = pool.submit(captured.search, "original")
        try:
            assert entered.wait(5)
            acquired = index._model_lock.acquire(blocking=False)
            if acquired:
                index._model_lock.release()
            assert not acquired
            # Captured search must not hold the live index mutation lock.
            index.clear()
            index.add_text("new", "replacement")
        finally:
            release.set()
        assert search.result(timeout=5) == ["[old] original"]
    assert index.search("replacement")
    assert captured.search("original")


def test_reload_preserves_docs_quarantine_and_old_snapshots(tmp_path, monkeypatch):
    class Model:
        def __init__(self, path, **kwargs):
            assert kwargs == {"local_files_only": True}
            self.path = path

        def encode(self, texts):
            return [[1, 0] for _ in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    index = MaterialIndex(str(tmp_path))
    local = index.add_text("local", "original")
    remote = index.add_text("remote", "private", "https://example.feishu.cn/docx/a")
    index.save_retained(tmp_path, [local, remote])
    index.load_retained(tmp_path)
    index.search("original")
    captured = index.snapshot()
    old_model, old_lock = captured._model, captured._model_lock
    records = index.list_materials()
    index.reload_embeddings(str(tmp_path))
    assert index.list_materials() == records
    assert index.semantic_available
    assert not index._vectors
    assert index._model is not old_model
    assert index._model_lock is not old_lock
    assert captured._model is old_model
    assert captured._model_lock is old_lock
    assert all("private" not in snippet for snippet in index.search("original"))
    index.reload_embeddings(str(tmp_path / "missing"))
    assert not index.semantic_available
    assert "directory does not exist" in index.status
    assert index.list_materials() == records
    assert index.search("original")
    assert captured.semantic_available
    assert captured.search("original")
    index.reload_embeddings(None)
    assert "no local embedding model" in index.status


def test_reload_missing_dependency_and_load_failure(tmp_path, monkeypatch):
    index = MaterialIndex()
    index.add_text("local", "original")
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    index.reload_embeddings(str(tmp_path))
    assert not index.semantic_available
    assert "install sentence-transformers" in index.status

    def broken(*_args, **_kwargs):
        raise RuntimeError("invalid local model")

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=broken))
    index.reload_embeddings(str(tmp_path))
    assert not index.semantic_available
    assert "could not be loaded" in index.status
    assert index.search("original")


def test_latest_reload_wins_without_blocking_mutation(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()

    class SlowModel:
        def __init__(self, *_args, **_kwargs):
            entered.set()
            assert release.wait(5)

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=SlowModel))
    index = MaterialIndex()
    with ThreadPoolExecutor(max_workers=1) as pool:
        reload = pool.submit(index.reload_embeddings, str(tmp_path))
        try:
            assert entered.wait(5)
            index.add_text("local", "original")
            assert index.search("original")
            index.reload_embeddings(None)
        finally:
            release.set()
        reload.result(timeout=5)
    assert not index.semantic_available
    assert index._model is None
    assert "no local embedding model" in index.status
    assert index.search("original")


def test_snapshots_reuse_embeddings_but_never_stale_text(tmp_path, monkeypatch):
    calls = []

    class Model:
        def __init__(self, *_args, **_kwargs):
            pass

        def encode(self, texts):
            calls.extend(texts)
            return [[1, 0] for _ in texts]

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=Model))
    index = MaterialIndex(str(tmp_path))
    identifier = index.add_text("remote", "original", "https://example.feishu.cn/docx/a")
    old = index.snapshot()
    old.search("query one")
    index.snapshot().search("query two")
    assert calls.count("original") == 1
    assert index._vectors is old._vectors
    index.verify_retained(identifier, "https://example.feishu.cn/docx/a", "refreshed")
    index.snapshot().search("query three")
    assert calls.count("refreshed") == 1
    assert "original" in old.search("query four")[0]
    assert "refreshed" in index.snapshot().search("query five")[0]
    old_cache = old._vectors
    index.reload_embeddings(str(tmp_path))
    assert index._vectors is not old_cache
    index.snapshot().search("query six")
    assert calls.count("refreshed") == 2


def test_capacity_rejection_is_transactional(monkeypatch):
    index = MaterialIndex()
    index.add_text("one", "first")
    monkeypatch.setattr(materials, "MAX_DOCUMENTS", 1)
    with pytest.raises(ValueError, match="documents"):
        index.add_text("two", "second")
    assert len(index.list_materials()) == 1
