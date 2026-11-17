"""Synthetic-only storage regression and hostile backup tests."""

import stat
import zipfile
import pytest
from iris_interview import storage
from iris_interview.storage import (
    ProjectStore,
    StorageError,
    RevisionConflict,
    ProjectNotFound,
    UnsafeArchive,
)


@pytest.fixture
def store(tmp_path):
    with ProjectStore(tmp_path / "store") as value:
        yield value


def test_reopen_and_revision_conflict_across_connections(tmp_path):
    root = tmp_path / "db"
    with ProjectStore(root) as a, ProjectStore(root) as b:
        doc = a.create_project(candidate="C-01")
        stale = b.get_project(doc["id"])
        doc["resume"]["blocks"] = [{"id": "b1", "text": "合成项目"}]
        doc["plan"]["questions"] = [{"custom": True}]
        saved = a.autosave(doc)
        assert saved["revision"] == 2
        stale["notes"] = ["stale"]
        with pytest.raises(RevisionConflict):
            b.save_project(stale)
        with pytest.raises(RevisionConflict):
            b.save_project(saved, expected_revision=1)
    with ProjectStore(root) as c:
        assert c.get_project(doc["id"])["resume"]["blocks"][0]["text"] == "合成项目"
    with pytest.raises(StorageError):
        c.get_project(doc["id"])


def test_lifecycle_and_explicit_delete_preserves_source(store, tmp_path):
    source = tmp_path / "resume.txt"
    source.write_text("synthetic")
    doc = store.create_project()
    doc = store.add_attachment(doc["id"], source, expected_revision=doc["revision"])
    doc = store.set_archived(doc["id"], True, doc["revision"])
    assert store.list_projects(archived=True)[0]["id"] == doc["id"]
    with pytest.raises(StorageError):
        store.permanently_delete(doc["id"], confirmation=doc["id"], expected_revision=doc["revision"])
    doc = store.trash_project(doc["id"], doc["revision"])
    assert not store.list_projects()
    assert len(store.list_projects(trashed=True)) == 1
    doc = store.restore_project(doc["id"], doc["revision"])
    doc = store.trash_project(doc["id"], doc["revision"])
    with pytest.raises(StorageError):
        store.permanently_delete(doc["id"], confirmation=True, expected_revision=doc["revision"])
    store.permanently_delete(doc["id"], confirmation=doc["id"], expected_revision=doc["revision"])
    assert source.read_text() == "synthetic"
    with pytest.raises(ProjectNotFound):
        store.get_project(doc["id"])


def test_active_project_cannot_trash(store):
    doc = store.create_project(status="interviewing")
    with pytest.raises(StorageError):
        store.trash_project(doc["id"], 1)
    doc["status"] = "ready"
    doc["active_tasks"] = ["generating"]
    doc = store.save_project(doc)
    with pytest.raises(StorageError):
        store.trash_project(doc["id"], doc["revision"])


def test_versions_snapshots_and_review_invalidation(store, tmp_path):
    source = tmp_path / "resume.txt"
    source.write_text("version1")
    doc = store.create_project()
    doc = store.add_attachment(doc["id"], source, expected_revision=doc["revision"])
    first = doc["attachments"][0]["id"]
    source.write_text("version2")
    doc = store.add_attachment(doc["id"], source, expected_revision=doc["revision"])
    assert [a["version"] for a in doc["attachments"]] == [1, 2]
    assert store.attachment_path(doc["id"], first).read_text() == "version1"
    doc["evaluation"]["draft"] = {"result": "通过", "reason": "合成证据"}
    doc["evaluation"]["suggested_level"] = "manual-only"
    doc = store.save_project(doc)
    doc = store.snapshot(doc["id"], kind="evaluation", expected_revision=doc["revision"])
    assert doc["status"] == "completed"
    assert doc["snapshots"][-1]["suggested_level"] == "manual-only"
    doc["transcript"] = [{"text": "修正后的证据"}]
    doc = store.save_project(doc)
    assert doc["evaluation"]["needs_review"]
    assert doc["snapshots"][0]["content"]["reason"] == "合成证据"
    doc["snapshots"][0]["content"]["reason"] = "tampered"
    with pytest.raises(StorageError):
        store.save_project(doc)


def test_search_and_filters(store):
    doc = store.create_project(
        "面试", candidate="C-02", job="工程师", round="一面", interview_at="2026-11-16T10:00:00"
    )
    doc["notes"] = [{"text": "罕见中文检索词"}]
    doc = store.save_project(doc)
    doc["evaluation"]["draft"] = {"result": "待定"}
    doc = store.save_project(doc)
    doc = store.snapshot(doc["id"], kind="evaluation", expected_revision=doc["revision"])
    found = store.list_projects(
        query="中文检索",
        status="completed",
        job="工程师",
        round="一面",
        result="待定",
        date_from="2026-11-16",
        date_to="2026-11-16",
    )
    assert len(found) == 1 and "中文检索" in found[0]["search_snippet"]
    assert not store.list_projects(result="通过")
    assert not store.list_projects(date_to="2026-11-15")


def test_roundtrip_new_id_and_recording_opt_in(store, tmp_path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"fake audio")
    doc = store.create_project()
    with pytest.raises(StorageError):
        store.add_attachment(doc["id"], source, kind="recording", expected_revision=1)
    doc["recording_enabled"] = True
    doc = store.save_project(doc)
    doc = store.add_attachment(doc["id"], source, kind="recording", expected_revision=doc["revision"])
    doc["status"] = "interviewing"
    doc = store.save_project(doc)
    archive = store.export_project(doc["id"], tmp_path / "backup.zip")
    imported = store.import_project(archive)
    assert imported["id"] != doc["id"] and imported["revision"] == 1
    assert imported["status"] == "review" and imported["interrupted"]
    assert imported["evaluation"]["suggested_level"] is None
    assert (
        store.attachment_path(imported["id"], imported["attachments"][0]["id"]).read_bytes() == b"fake audio"
    )
    assert len(store.list_projects()) == 2


def test_reject_source_symlink_and_path_ids(store, tmp_path):
    source = tmp_path / "source"
    source.write_text("synthetic")
    link = tmp_path / "link"
    link.symlink_to(source)
    doc = store.create_project()
    with pytest.raises((OSError, StorageError)):
        store.add_attachment(doc["id"], link, expected_revision=1)
    with pytest.raises(StorageError):
        store.get_project("../source")


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "files/../../escape", "C:/evil", "files\\evil", "files//evil"]
)
def test_reject_archive_paths(store, tmp_path, name):
    p = tmp_path / "hostile.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(name, "x")
    with pytest.raises(UnsafeArchive):
        store.import_project(p)
    assert store.list_projects() == []
    assert list(store.files.iterdir()) == []


def test_reject_symlink_duplicate_and_bomb(store, tmp_path, monkeypatch):
    p = tmp_path / "hostile.zip"
    info = zipfile.ZipInfo("project.json")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(info, "/tmp/elsewhere")
    with pytest.raises(UnsafeArchive):
        store.import_project(p)
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", "0" * 100000)
    with pytest.raises(UnsafeArchive):
        store.import_project(p)
    monkeypatch.setattr(storage, "MAX_ARCHIVE_ENTRIES", 1)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("a", "x")
        z.writestr("b", "x")
    with pytest.raises(UnsafeArchive):
        store.import_project(p)


def test_corruption_rejected_without_partial_import(store, tmp_path):
    source = tmp_path / "source"
    source.write_text("valid")
    doc = store.create_project()
    doc = store.add_attachment(doc["id"], source, expected_revision=1)
    good = store.export_project(doc["id"], tmp_path / "good.zip")
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(good) as original, zipfile.ZipFile(bad, "w") as output:
        for info in original.infolist():
            output.writestr(
                info.filename,
                b"broken" if info.filename.startswith("files/") else original.read(info.filename),
            )
    with pytest.raises(UnsafeArchive):
        store.import_project(bad)
    assert len(store.list_projects()) == 1
    assert sorted(p.name for p in store.files.iterdir()) == [doc["id"]]


def test_copy_size_failure_is_atomic(store, tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_bytes(b"12345")
    doc = store.create_project()
    monkeypatch.setattr(storage, "MAX_FILE_BYTES", 3)
    with pytest.raises(StorageError):
        store.add_attachment(doc["id"], source, expected_revision=1)
    assert store.get_project(doc["id"])["revision"] == 1
    assert list(store._directory(doc["id"]).iterdir()) == []


def test_export_does_not_replace_existing_file_on_failure(store, tmp_path):
    source = tmp_path / "source"
    source.write_text("original")
    doc = store.create_project()
    doc = store.add_attachment(doc["id"], source, expected_revision=1)
    store.attachment_path(doc["id"], doc["attachments"][0]["id"]).write_text("tampered")
    destination = tmp_path / "existing.zip"
    destination.write_bytes(b"previous backup")
    with pytest.raises(StorageError):
        store.export_project(doc["id"], destination)
    assert destination.read_bytes() == b"previous backup"
    assert not list(tmp_path.glob(".export-*"))
    with pytest.raises(StorageError):
        store.export_project(doc["id"], store.root / "projects.sqlite3")


def test_delete_database_failure_restores_files(store, tmp_path):
    import sqlite3

    source = tmp_path / "source"
    source.write_text("original")
    doc = store.create_project()
    doc = store.add_attachment(doc["id"], source, expected_revision=1)
    doc = store.trash_project(doc["id"], doc["revision"])
    store._conn.execute(
        "CREATE TRIGGER deny_delete BEFORE DELETE ON projects BEGIN SELECT RAISE(ABORT, 'failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.permanently_delete(doc["id"], confirmation=doc["id"], expected_revision=doc["revision"])
    assert store.get_project(doc["id"])["trashed_at"]
    assert store.attachment_path(doc["id"], doc["attachments"][0]["id"]).read_text() == "original"


def test_import_database_failure_leaves_no_published_directory(store, tmp_path, monkeypatch):
    doc = store.create_project()
    archive = store.export_project(doc["id"], tmp_path / "backup.zip")

    def fail(_):
        raise StorageError("simulated write failure")

    monkeypatch.setattr(store, "_put", fail)
    with pytest.raises(StorageError):
        store.import_project(archive)
    assert len(store.list_projects()) == 1
    assert list(store.files.iterdir()) == []


def test_duplicate_entries_and_duplicate_json_keys_rejected(store, tmp_path):
    archive = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning), zipfile.ZipFile(archive, "w") as output:
        output.writestr("project.json", "{}")
        output.writestr("project.json", "{}")
    with pytest.raises(UnsafeArchive):
        store.import_project(archive)
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("project.json", '{"version":1,"version":2}')
    with pytest.raises(UnsafeArchive):
        store.import_project(archive)


def test_recording_registration_roundtrip_and_free_notes(store, tmp_path):
    doc = store.create_project(free_notes="独特自由笔记")
    assert len(store.list_projects(query="自由笔记")) == 1
    with pytest.raises(StorageError):
        store.begin_recording_session(doc["id"], 1)
    doc["recording_enabled"] = True
    doc = store.save_project(doc)
    doc = store.begin_recording_session(doc["id"], doc["revision"])
    sid = doc["recording_sessions"][-1]["id"]
    directory = store.recording_directory(doc["id"], sid)
    assert not directory.exists()
    directory.mkdir()
    (directory / "local.wav").write_bytes(b"synthetic local")
    (directory / "remote.wav").write_bytes(b"synthetic remote")
    with pytest.raises(StorageError):
        store.export_project(doc["id"], tmp_path / "pending.zip")
    doc = store.finalize_recording_session(doc["id"], sid, doc["revision"])
    assert not directory.exists() and len(doc["attachments"]) == 2
    assert store.finalize_recording_session(doc["id"], sid, doc["revision"]) == doc
    restored = store.import_project(store.export_project(doc["id"], tmp_path / "complete.zip"))
    assert len(restored["attachments"]) == 2
    assert (
        store.attachment_path(restored["id"], restored["attachments"][0]["id"]).read_bytes()
        == b"synthetic local"
    )


def test_recording_retry_after_failure_and_empty_cancel(store, monkeypatch):
    doc = store.create_project(recording_enabled=True)
    doc = store.begin_recording_session(doc["id"], 1)
    sid = doc["recording_sessions"][0]["id"]
    doc = store.cancel_empty_recording_session(doc["id"], sid, doc["revision"])
    assert not doc["recording_sessions"]
    doc = store.begin_recording_session(doc["id"], doc["revision"])
    sid = doc["recording_sessions"][0]["id"]
    directory = store.recording_directory(doc["id"], sid)
    directory.mkdir()
    source = directory / "local.wav"
    source.write_bytes(b"12345")
    with pytest.raises(StorageError):
        store.cancel_empty_recording_session(doc["id"], sid, doc["revision"])
    original = store._put

    def fail(_):
        raise StorageError("simulated commit failure")

    monkeypatch.setattr(store, "_put", fail)
    with pytest.raises(StorageError):
        store.finalize_recording_session(doc["id"], sid, doc["revision"])
    assert source.read_bytes() == b"12345"
    assert list(store._directory(doc["id"]).iterdir()) == [directory]
    monkeypatch.setattr(store, "_put", original)
    monkeypatch.setattr(storage, "MAX_FILE_BYTES", 3)
    with pytest.raises(StorageError):
        store.finalize_recording_session(doc["id"], sid, doc["revision"])
    assert source.read_bytes() == b"12345"
    monkeypatch.setattr(storage, "MAX_FILE_BYTES", 1024**3)
    recovered = store.finalize_recording_session(doc["id"], sid, doc["revision"])
    assert recovered["recording_sessions"][0]["state"] == "finalized"


@pytest.mark.parametrize(
    "field,value",
    [
        ("notes", [{"text": "changed"}]),
        ("free_notes", "changed"),
        ("leadership_standard", {"version": "2.0"}),
    ],
)
def test_formal_evaluation_invalidated_by_notes_and_standard(store, field, value):
    doc = store.create_project()
    doc["evaluation"]["draft"] = {"result": "通过"}
    doc = store.save_project(doc)
    doc = store.snapshot(doc["id"], kind="evaluation", expected_revision=doc["revision"])
    assert not doc["evaluation"]["needs_review"]
    doc[field] = value
    doc = store.save_project(doc)
    assert doc["evaluation"]["needs_review"]
    assert doc["snapshots"][-1]["content"]["result"] == "通过"
