"""Standalone POSIX persistence; lock read/modify/write cycles with a sidecar."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile


class PersistenceError(ValueError):
    """An unreadable or unsupported file is preserved for recovery."""


def read_json_object(path: Path) -> dict:
    """Strict JSON: duplicate keys and non-finite numbers are corruption."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Invalid JSON number")

    try:
        value = json.loads(path.read_bytes(), object_pairs_hook=pairs,
                           parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError("Expected JSON object")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PersistenceError(f"{path.name} 数据损坏，已保留原文件；请从备份恢复。") from exc


def check_schema(value: dict, version: int, *, key: str = "schema_version") -> int:
    actual = value.get(key, 0)
    if type(actual) is not int or actual < 0:
        raise PersistenceError("数据版本无效，已保留原文件。")
    if actual > version:
        raise PersistenceError("数据来自更新版本，请升级 Iris Caption；已保留原文件。")
    return actual


def migrate_json(path: Path, backups: Path, version: int, migrate, validate, *, legacy=None) -> dict:
    """Caller holds the file's stable lock; validate, backup bytes, then replace.

    Missing files remain missing. Backup names include the original SHA-256, so
    retries reuse a byte-identical backup. Never infer the product release here.
    """
    if not path.exists():
        return migrate({})
    value = read_json_object(path)
    actual = 0 if legacy is not None and legacy(value) else check_schema(value, version)
    try:
        candidate = migrate(value) if actual < version else value
        validate(candidate)
    except (ValueError, TypeError, KeyError) as exc:
        raise PersistenceError(f"{path.name} 数据损坏，已保留原文件。") from exc
    if actual < version:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        backup = backups / f"{path.name}.schema-{actual}-{digest}.bak"
        if backup.exists():
            if backup.read_bytes() != raw:
                raise PersistenceError("迁移备份不匹配，已保留原文件。")
        else:
            atomic_write_bytes(backup, raw)
        atomic_write_json(path, candidate)
    return candidate


class FileLock:
    """Exclusive advisory lock on a stable file, never on replaced data."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a+b")
        try:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        except BaseException:
            self._file.close()
            self._file = None
            raise
        return self

    def __exit__(self, *_):
        if self._file is not None:
            try:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Flush a same-directory temporary file then atomically replace target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path: str | Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str | Path, value: object) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
