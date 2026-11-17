"""Private SQLite JSON documents, revision-safe autosave and bounded portable ZIPs."""

from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import threading
from uuid import uuid4
import zipfile
from filelock import FileLock
from .exceptions import InterviewError

DEFAULT_ROOT = Path.home() / "Library/Application Support/Iris-interview"
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 2000
MAX_COMPRESSION_RATIO = 200
STATUSES = {"preparing", "ready", "interviewing", "review", "completed"}


class StorageError(InterviewError):
    """Invalid or unsuccessful storage operation."""


class RevisionConflict(StorageError):
    """Reload and merge; never silently overwrite a newer revision."""


class ProjectNotFound(StorageError):
    """Project does not exist."""


class UnsafeArchive(StorageError):
    """Backup violates format or safety limits."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise StorageError("Finite JSON data required") from exc
    if len(text.encode()) > MAX_DOCUMENT_BYTES:
        raise StorageError("Document exceeds size limit")
    return text


def _id(value):
    if not isinstance(value, str) or len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise StorageError("Invalid managed ID")
    return value


def _text(value):
    if isinstance(value, dict):
        return "\n".join(_text(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    return str(value) if value is not None else ""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UnsafeArchive("Duplicate JSON key")
        result[key] = value
    return result


class ProjectStore:
    """Detached JSON documents. Save defaults expected_revision to document.revision.

    resume.blocks, plan.questions, transcript and notes permit caller-defined JSON.
    Files and snapshots are immutable, lifecycle fields use dedicated methods.
    Local substring search handles Chinese and returns search_snippet on matches.
    """

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = self.root.resolve()
        self.files = self.root / "projects"
        if self.files.is_symlink():
            raise StorageError("Unsafe attachment root")
        self.files.mkdir(exist_ok=True, mode=0o700)
        self._mutex = threading.RLock()
        self._lock = FileLock(str(self.root / ".store.lock"))
        self._closed = False
        if (self.root / "projects.sqlite3").is_symlink():
            raise StorageError("Database must not be a symlink")
        self._conn = sqlite3.connect(self.root / "projects.sqlite3", check_same_thread=False)
        try:
            with self._locked(), self._conn:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, document TEXT NOT NULL)"
                )
            os.chmod(self.root / "projects.sqlite3", 0o600)
        except BaseException:
            self._conn.close()
            self._closed = True
            raise

    @contextmanager
    def _locked(self):
        with self._mutex, self._lock:
            if self._closed:
                raise StorageError("Store is closed")
            yield

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        with self._mutex:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def _get(self, project_id):
        row = self._conn.execute("SELECT document FROM projects WHERE id=?", (_id(project_id),)).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return json.loads(row[0])

    def get_project(self, project_id):
        with self._locked():
            return self._get(project_id)

    @staticmethod
    def _revision(doc, revision):
        if type(revision) is not int or doc["revision"] != revision:
            raise RevisionConflict(f"Expected {revision}; current revision is {doc['revision']}")

    @staticmethod
    def _validate(doc):
        if not isinstance(doc, dict) or doc.get("schema_version") != 1:
            raise StorageError("Unsupported project schema")
        _id(doc.get("id"))
        if doc.get("status") not in STATUSES:
            raise StorageError("Invalid project status")
        for key in ("name", "candidate", "job", "round", "interview_at", "created_at", "updated_at"):
            if not isinstance(doc.get(key), str):
                raise StorageError(f"{key} must be text")
        for key in ("resume", "job_snapshot", "plan", "evaluation"):
            if not isinstance(doc.get(key), dict):
                raise StorageError(f"{key} must be an object")
        for key in ("transcript", "notes", "attachments", "snapshots"):
            if not isinstance(doc.get(key), list):
                raise StorageError(f"{key} must be a list")
        if type(doc.get("revision")) is not int or doc["revision"] < 1:
            raise StorageError("Invalid revision")
        for key in ("archived", "recording_enabled"):
            if type(doc.get(key)) is not bool:
                raise StorageError(f"{key} must be boolean")
        if doc.get("trashed_at") is not None and not isinstance(doc["trashed_at"], str):
            raise StorageError("Invalid trash timestamp")
        ProjectStore._validate_sessions(doc)
        ProjectStore._validate_managed(doc)
        _json(doc)

    @staticmethod
    def _validate_sessions(doc):
        sessions = doc.get("recording_sessions", [])
        if not isinstance(sessions, list):
            raise StorageError("Invalid recording sessions")
        ids = set()
        for session in sessions:
            if not isinstance(session, dict):
                raise StorageError("Invalid recording session")
            sid = _id(session.get("id"))
            if sid in ids or session.get("state") not in {"pending", "finalized"}:
                raise StorageError("Invalid recording state")
            ids.add(sid)
            if not isinstance(session.get("attachment_ids"), list):
                raise StorageError("Invalid recording attachments")
            for aid in session["attachment_ids"]:
                _id(aid)

    @staticmethod
    def _validate_managed(doc):
        if not isinstance(doc["evaluation"].get("confirmed"), list):
            raise StorageError("Confirmed evaluation IDs must be a list")
        for item in doc["attachments"]:
            if not isinstance(item, dict):
                raise StorageError("Attachment metadata must be an object")
            _id(item.get("id"))
            if (
                not isinstance(item.get("name"), str)
                or not item["name"]
                or "/" in item["name"]
                or "\\" in item["name"]
                or item["name"] in {".", ".."}
                or "\x00" in item["name"]
                or not isinstance(item.get("kind"), str)
                or type(item.get("version")) is not int
                or item["version"] < 1
                or type(item.get("size")) is not int
                or not 0 <= item["size"] <= MAX_FILE_BYTES
                or not isinstance(item.get("sha256"), str)
                or len(item["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in item["sha256"])
            ):
                raise StorageError("Invalid attachment metadata")
        snapshot_ids = set()
        for snap in doc["snapshots"]:
            if not isinstance(snap, dict) or snap.get("kind") not in {"plan", "evaluation"}:
                raise StorageError("Invalid snapshot")
            _id(snap.get("id"))
            if snap["id"] in snapshot_ids or not isinstance(snap.get("content"), dict):
                raise StorageError("Invalid snapshot content")
            snapshot_ids.add(snap["id"])
        if any(sid not in snapshot_ids for sid in doc["evaluation"]["confirmed"]):
            raise StorageError("Missing confirmed snapshot")
        _json(doc)

    def _put(self, doc):
        self._validate(doc)
        self._conn.execute("INSERT OR REPLACE INTO projects VALUES (?,?)", (doc["id"], _json(doc)))
        return deepcopy(doc)

    def _updated(self, doc):
        doc["revision"] += 1
        doc["updated_at"] = _now()
        return self._put(doc)

    def create_project(self, name="", candidate="", job="", **fields):
        now = _now()
        doc = dict(
            schema_version=1,
            id=uuid4().hex,
            revision=1,
            created_at=now,
            updated_at=now,
            name=name,
            candidate=candidate,
            job=job,
            round="",
            interview_at="",
            status="preparing",
            archived=False,
            trashed_at=None,
            recording_enabled=False,
            resume={"text": "", "blocks": []},
            job_snapshot={},
            plan={"questions": []},
            transcript=[],
            notes=[],
            evaluation={"draft": {}, "confirmed": [], "needs_review": False, "suggested_level": None},
            attachments=[],
            recording_sessions=[],
            snapshots=[],
        )
        if {
            "id",
            "revision",
            "schema_version",
            "created_at",
            "updated_at",
            "attachments",
            "recording_sessions",
            "snapshots",
            "trashed_at",
        }.intersection(fields):
            raise StorageError("Cannot initialize managed fields")
        doc.update(deepcopy(fields))
        with self._locked(), self._conn:
            return self._put(doc)

    def save_project(self, document, expected_revision=None):
        doc = deepcopy(document)
        with self._locked(), self._conn:
            old = self._get(doc.get("id"))
            self._revision(old, doc.get("revision") if expected_revision is None else expected_revision)
            if doc.get("recording_sessions", []) != old.get("recording_sessions", []):
                raise StorageError("Use recording APIs to modify session metadata")
            for key in ("schema_version", "created_at", "attachments", "snapshots", "archived", "trashed_at"):
                if doc.get(key) != old[key]:
                    raise StorageError(f"Use dedicated API to modify {key}")
            if doc.get("evaluation", {}).get("confirmed") != old["evaluation"].get("confirmed"):
                raise StorageError("Confirmed evaluations are immutable")
            if old["evaluation"].get("confirmed") and any(
                doc.get(k) != old.get(k)
                for k in (
                    "transcript",
                    "job_snapshot",
                    "resume",
                    "evaluation",
                    "notes",
                    "free_notes",
                    "leadership_standard",
                )
            ):
                doc["evaluation"]["needs_review"] = True
            doc["revision"] = old["revision"]
            return self._updated(doc)

    autosave = save_project

    def list_projects(
        self,
        *,
        query="",
        status=None,
        job=None,
        round=None,
        result=None,
        date_from=None,
        date_to=None,
        archived=None,
        trashed=False,
    ):
        with self._locked():
            docs = [json.loads(r[0]) for r in self._conn.execute("SELECT document FROM projects")]
        found = []
        for doc in docs:
            if bool(doc["trashed_at"]) != trashed or (archived is not None and doc["archived"] != archived):
                continue
            if any(
                v is not None and doc.get(k) != v
                for k, v in [("status", status), ("job", job), ("round", round)]
            ):
                continue
            formal = [s for s in doc["snapshots"] if s["kind"] == "evaluation"]
            if result is not None and (formal[-1]["content"].get("result") if formal else None) != result:
                continue
            date = doc.get("interview_at") or doc["created_at"]
            if (date_from and date < date_from) or (date_to and date[: len(date_to)] > date_to):
                continue
            text = _text(
                {
                    k: doc.get(k)
                    for k in (
                        "name",
                        "candidate",
                        "job",
                        "resume",
                        "transcript",
                        "notes",
                        "free_notes",
                        "evaluation",
                        "snapshots",
                    )
                }
            )
            if query:
                pos = text.casefold().find(query.casefold())
                if pos < 0:
                    continue
                doc["search_snippet"] = text[max(0, pos - 40) : pos + len(query) + 80]
            found.append(doc)
        return sorted(found, key=lambda d: d["updated_at"], reverse=True)

    @staticmethod
    def _idle(doc):
        if doc["status"] == "interviewing" or doc.get("active_tasks"):
            raise StorageError("Stop active tasks before deletion")

    def set_archived(self, project_id, archived, expected_revision):
        if type(archived) is not bool:
            raise StorageError("archived must be boolean")
        with self._locked(), self._conn:
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            doc["archived"] = archived
            return self._updated(doc)

    def trash_project(self, project_id, expected_revision):
        with self._locked(), self._conn:
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            self._idle(doc)
            doc["trashed_at"] = _now()
            return self._updated(doc)

    def restore_project(self, project_id, expected_revision):
        with self._locked(), self._conn:
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            doc["trashed_at"] = None
            return self._updated(doc)

    def _directory(self, project_id):
        path = self.files / _id(project_id)
        if path.is_symlink() or path.resolve().parent != self.files:
            raise StorageError("Unsafe managed directory")
        return path

    def permanently_delete(self, project_id, *, confirmation, expected_revision):
        with self._locked():
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            self._idle(doc)
            if confirmation != project_id or not doc["trashed_at"]:
                raise StorageError("Requires trash and explicit full project-ID confirmation")
            directory = self._directory(project_id)
            tombstone = None
            if directory.exists():
                tombstone = self.files / (".deleted-" + uuid4().hex)
                os.replace(directory, tombstone)
            try:
                with self._conn:
                    self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
            except BaseException:
                if tombstone is not None:
                    os.replace(tombstone, directory)
                raise
            if tombstone is not None:
                # Validated generated path under the private managed root.
                if tombstone.resolve().parent != self.files:
                    raise StorageError("Unsafe deletion staging path")
                shutil.rmtree(tombstone)

    @staticmethod
    def _copy_atomic(stream, target, limit):
        fd, temporary = tempfile.mkstemp(prefix=".copy-", dir=target.parent)
        digest, size = hashlib.sha256(), 0
        try:
            with os.fdopen(fd, "wb") as output:
                while chunk := stream.read(min(1024 * 1024, limit - size + 1)):
                    size += len(chunk)
                    if size > limit:
                        raise StorageError("File exceeds size limit")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            return digest.hexdigest(), size
        finally:
            Path(temporary).unlink(missing_ok=True)

    def add_attachment(self, project_id, source, *, kind="resume", expected_revision):
        source = Path(source)
        with self._locked():
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            if kind == "recording" and not doc["recording_enabled"]:
                raise StorageError("Recording persistence is disabled")
            directory = self._directory(project_id)
            directory.mkdir(exist_ok=True, mode=0o700)
            attachment_id = uuid4().hex
            target = directory / attachment_id
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise StorageError("Regular file required")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    digest, size = self._copy_atomic(stream, target, MAX_FILE_BYTES)
            finally:
                os.close(fd)
            version = 1 + max((a["version"] for a in doc["attachments"] if a["kind"] == kind), default=0)
            doc["attachments"].append(
                dict(
                    id=attachment_id,
                    name=source.name,
                    kind=kind,
                    version=version,
                    size=size,
                    sha256=digest,
                    created_at=_now(),
                )
            )
            try:
                with self._conn:
                    return self._updated(doc)
            except BaseException:
                target.unlink(missing_ok=True)
                raise

    def _session_directory(self, project_id, session_id):
        directory = self._directory(project_id) / ("session-" + _id(session_id))
        if directory.is_symlink() or directory.resolve().parent != self._directory(project_id):
            raise StorageError("Unsafe recording session directory")
        return directory

    def begin_recording_session(self, project_id, expected_revision):
        """Explicit opt-in: persist intent before runtime writes either WAV track.

        Return updated document; its final recording_sessions entry contains the ID.
        Only call finalize after runtime.stop has closed its WAV writers.
        """
        with self._locked():
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            if not doc["recording_enabled"] or doc["trashed_at"]:
                raise StorageError("Explicit recording opt-in required")
            if any(s["state"] == "pending" for s in doc.get("recording_sessions", [])):
                raise StorageError("Recover pending recording before starting another")
            session = dict(id=uuid4().hex, state="pending", created_at=_now(), attachment_ids=[])
            directory = self._session_directory(project_id, session["id"])
            directory.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            doc.setdefault("recording_sessions", []).append(session)
            with self._conn:
                return self._updated(doc)

    def recording_directory(self, project_id, session_id):
        """Path belongs to the store; the capture runtime may write WAV files here."""
        with self._locked():
            doc = self._get(project_id)
            session = next((s for s in doc.get("recording_sessions", []) if s["id"] == session_id), None)
            if session is None or session["state"] != "pending":
                raise StorageError("No pending recording session")
            return self._session_directory(project_id, session_id)

    def cancel_empty_recording_session(self, project_id, session_id, expected_revision):
        """Remove only an empty pending reservation; never discard recorded bytes."""
        with self._locked(), self._conn:
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            session = next((s for s in doc.get("recording_sessions", []) if s["id"] == session_id), None)
            if session is None or session["state"] != "pending":
                raise StorageError("No pending recording session")
            directory = self._session_directory(project_id, session_id)
            if directory.exists():
                entries = list(directory.iterdir())
                if entries:
                    self._validate_empty_recording(directory, entries)
                for path in entries:
                    path.unlink()
                directory.rmdir()
            doc["recording_sessions"].remove(session)
            return self._updated(doc)

    @staticmethod
    def _validate_empty_recording(directory, entries):
        if any(
            p.name not in {"manifest.json", ".recording.lock"} or p.is_symlink() or not p.is_file()
            for p in entries
        ):
            raise StorageError("Recording data exists; recover instead of cancelling")
        manifest = directory / "manifest.json"
        try:
            if not manifest.is_file() or manifest.stat().st_size > 1024 * 1024:
                raise ValueError("missing/oversized manifest")
            value = json.loads(manifest.read_text(encoding="utf-8"))
            if (
                value.get("version") != 1
                or value.get("clock") != "session_relative_ns"
                or value.get("chunks") != []
                or not isinstance(value.get("gaps"), list)
            ):
                raise ValueError("nonempty or invalid timeline")
            lock = directory / ".recording.lock"
            if lock.exists() and lock.stat().st_size:
                raise ValueError("unexpected lock content")
        except (ValueError, AttributeError, TypeError) as exc:
            raise StorageError("Cannot safely cancel recording metadata; sources preserved") from exc

    @staticmethod
    def _file_digest(path):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        digest, size = hashlib.sha256(), 0
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise StorageError("Regular recording file required")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise StorageError("Recording exceeds 1 GiB per-file limit; source preserved")
                    digest.update(chunk)
        finally:
            os.close(fd)
        return digest.hexdigest(), size

    @staticmethod
    def _validate_recording_manifest(path, filenames):
        if path.stat().st_size > 64 * 1024 * 1024:
            raise StorageError("Recording manifest is too large")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("version") != 1 or value.get("clock") != "session_relative_ns":
                raise ValueError("manifest version/clock")
            chunks = value["chunks"]
            if not isinstance(chunks, list):
                raise ValueError("manifest chunks")
            for chunk in chunks:
                if chunk["file"] not in filenames or chunk["source"] not in {"local", "remote"}:
                    raise ValueError("manifest file/source")
                for key in ("timestamp_ns", "file_frame", "frames", "sample_rate", "channels"):
                    if type(chunk[key]) is not int or chunk[key] < 0:
                        raise ValueError("manifest numeric field")
                if not chunk["sample_rate"] or not chunk["channels"] or not chunk["frames"]:
                    raise ValueError("manifest dimensions")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise StorageError("Invalid recording timeline; sources preserved") from exc

    def finalize_recording_session(self, project_id, session_id, expected_revision):
        """Register closed WAVs without a second large copy; failures preserve sources.

        Hard-link publication occurs on the same managed volume. Pending sources stay
        intact until the document commits, so interruption before commit is retryable.
        Already finalized calls are idempotent and clean any leftover session directory.
        """
        with self._locked():
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            session = next((s for s in doc.get("recording_sessions", []) if s["id"] == session_id), None)
            if session is None:
                raise StorageError("Unknown recording session")
            directory = self._session_directory(project_id, session_id)
            if session["state"] == "finalized":
                if directory.exists():
                    shutil.rmtree(directory)
                return doc
            if doc["status"] == "interviewing":
                raise StorageError("Stop capture and set review before finalizing")
            entries = sorted(directory.iterdir())
            paths = [p for p in entries if p.suffix.lower() == ".wav"]
            manifest = directory / "manifest.json"
            allowed = set(paths + [manifest, directory / ".recording.lock"])
            if not paths or len(paths) > 16 or any(p not in allowed or p.is_symlink() for p in entries):
                raise StorageError(
                    "Expected closed WAV tracks and optional timeline manifest; sources preserved"
                )
            if manifest.exists():
                self._validate_recording_manifest(manifest, {p.name for p in paths})
                paths.append(manifest)
            additions = []
            for path in paths:
                digest, size = self._file_digest(path)
                additions.append(
                    dict(
                        id=uuid4().hex,
                        name=path.name,
                        kind="recording_manifest" if path.name == "manifest.json" else "recording",
                        version=1,
                        size=size,
                        sha256=digest,
                        created_at=_now(),
                        session_id=session_id,
                    )
                )
            if (
                sum(a["size"] for a in doc["attachments"] + additions)
                > MAX_ARCHIVE_BYTES - MAX_DOCUMENT_BYTES
            ):
                raise StorageError("Project recordings exceed backup size limit; sources preserved")
            published = []
            try:
                for path, item in zip(paths, additions):
                    target = self._directory(project_id) / item["id"]
                    os.link(path, target, follow_symlinks=False)
                    published.append(target)
                    item["version"] = 1 + max(
                        (a["version"] for a in doc["attachments"] if a["kind"] == "recording"), default=0
                    )
                    doc["attachments"].append(item)
                session.update(
                    state="finalized", finalized_at=_now(), attachment_ids=[a["id"] for a in additions]
                )
                with self._conn:
                    saved = self._updated(doc)
            except BaseException:
                for target in published:
                    target.unlink(missing_ok=True)
                raise
            shutil.rmtree(directory)
            return saved

    def attachment_path(self, project_id, attachment_id):
        with self._locked():
            doc = self._get(project_id)
            if not any(a["id"] == attachment_id for a in doc["attachments"]):
                raise StorageError("Unknown attachment")
            path = self._directory(project_id) / _id(attachment_id)
            if path.is_symlink() or not path.is_file():
                raise StorageError("Unsafe or missing attachment")
            return path

    def snapshot(self, project_id, *, kind, expected_revision):
        if kind not in {"plan", "evaluation"}:
            raise StorageError("Invalid snapshot kind")
        with self._locked(), self._conn:
            doc = self._get(project_id)
            self._revision(doc, expected_revision)
            content = deepcopy(doc["plan"] if kind == "plan" else doc["evaluation"].get("draft", {}))
            snap = dict(
                id=uuid4().hex, kind=kind, created_at=_now(), source_revision=doc["revision"], content=content
            )
            if kind == "evaluation":
                snap["suggested_level"] = doc["evaluation"].get("suggested_level")
                doc["evaluation"].setdefault("confirmed", []).append(snap["id"])
                doc["evaluation"]["needs_review"] = False
                doc["status"] = "completed"
            doc["snapshots"].append(snap)
            return self._updated(doc)

    def export_project(self, project_id, destination):
        destination = Path(destination)
        if destination.resolve().is_relative_to(self.root):
            raise StorageError("Export outside the live store directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            doc = self._get(project_id)
            if any(s["state"] == "pending" for s in doc.get("recording_sessions", [])):
                raise StorageError("Finalize pending recordings before full backup")
            package = _json(dict(format="iris-interview-project", version=1, document=doc))
            if (
                len(doc["attachments"]) + 1 > MAX_ARCHIVE_ENTRIES
                or sum(a["size"] for a in doc["attachments"]) + len(package.encode()) > MAX_ARCHIVE_BYTES
            ):
                raise StorageError("Project exceeds portable archive limits")
            fd, temporary = tempfile.mkstemp(prefix=".export-", dir=destination.parent)
            os.close(fd)
            try:
                with zipfile.ZipFile(temporary, "w") as archive:
                    archive.writestr(
                        "project.json", _json(dict(format="iris-interview-project", version=1, document=doc))
                    )
                    for item in doc["attachments"]:
                        path = self.attachment_path(project_id, item["id"])
                        digest, size = hashlib.sha256(), 0
                        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                        with (
                            os.fdopen(fd, "rb") as stream,
                            archive.open("files/" + item["id"], "w", force_zip64=True) as output,
                        ):
                            while chunk := stream.read(1024 * 1024):
                                size += len(chunk)
                                if size > MAX_FILE_BYTES:
                                    raise StorageError("Attachment exceeds export limit")
                                digest.update(chunk)
                                output.write(chunk)
                        if size != item["size"] or digest.hexdigest() != item["sha256"]:
                            raise StorageError("Attachment integrity mismatch")
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return destination

    @staticmethod
    def _members(archive):
        members, total = {}, 0
        if len(archive.infolist()) > MAX_ARCHIVE_ENTRIES:
            raise UnsafeArchive("Too many entries")
        for info in archive.infolist():
            name, mode = info.filename, info.external_attr >> 16
            if (
                not name
                or "\\" in name
                or "\x00" in name
                or PurePosixPath(name).is_absolute()
                or any(p in {"", ".", ".."} for p in name.split("/"))
                or ":" in name
                or info.is_dir()
                or name in members
                or info.flag_bits & 1
                or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
            ):
                raise UnsafeArchive("Unsafe archive entry")
            limit = MAX_DOCUMENT_BYTES if name == "project.json" else MAX_FILE_BYTES
            if info.file_size > limit or info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                raise UnsafeArchive("Archive entry exceeds limits")
            total += info.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise UnsafeArchive("Expanded archive exceeds limit")
            members[name] = info
        return members

    def import_project(self, source):
        """Validate before staging; publish a fresh ID, never extract archive paths."""
        stage = None
        try:
            with self._locked(), zipfile.ZipFile(source) as archive:
                members = self._members(archive)
                if "project.json" not in members:
                    raise UnsafeArchive("Missing document")
                with archive.open("project.json") as stream:
                    raw = stream.read(MAX_DOCUMENT_BYTES + 1)
                if len(raw) > MAX_DOCUMENT_BYTES:
                    raise UnsafeArchive("Document exceeds limit")
                package = json.loads(raw, object_pairs_hook=_pairs)
                if package.get("format") != "iris-interview-project" or package.get("version") != 1:
                    raise UnsafeArchive("Unsupported archive format")
                doc = package["document"]
                self._validate(doc)
                if any(s["state"] == "pending" for s in doc.get("recording_sessions", [])):
                    raise UnsafeArchive("Backup contains an incomplete recording session")
                expected, ids = {"project.json"}, set()
                for item in doc["attachments"]:
                    aid = _id(item["id"])
                    if (
                        aid in ids
                        or not isinstance(item["name"], str)
                        or Path(item["name"]).name != item["name"]
                    ):
                        raise UnsafeArchive("Invalid attachment metadata")
                    ids.add(aid)
                    expected.add("files/" + aid)
                if set(members) != expected:
                    raise UnsafeArchive("Missing or undeclared files")
                stage = Path(tempfile.mkdtemp(prefix=".import-", dir=self.files))
                for item in doc["attachments"]:
                    with archive.open("files/" + item["id"]) as stream:
                        digest, size = self._copy_atomic(stream, stage / item["id"], MAX_FILE_BYTES)
                    if digest != item["sha256"] or size != item["size"]:
                        raise UnsafeArchive("Attachment checksum mismatch")
                doc["imported_from"] = dict(id=doc["id"], imported_at=_now())
                doc["id"], doc["revision"], doc["updated_at"] = uuid4().hex, 1, _now()
                doc["active_tasks"] = []
                if doc["status"] == "interviewing":
                    doc["status"], doc["interrupted"] = "review", True
                published = self._directory(doc["id"])
                os.replace(stage, published)
                stage = None
                try:
                    with self._conn:
                        return self._put(doc)
                except BaseException:
                    shutil.rmtree(published)
                    raise
        except (zipfile.BadZipFile, KeyError, TypeError, ValueError, UnicodeError, RuntimeError) as exc:
            if isinstance(exc, StorageError):
                raise
            raise UnsafeArchive("Invalid project backup") from exc
        finally:
            if (
                stage is not None
                and stage.resolve().parent == self.files
                and stage.name.startswith(".import-")
            ):
                shutil.rmtree(stage)
