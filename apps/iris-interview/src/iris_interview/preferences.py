"""Application preferences and explicit Keychain secrets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from filelock import FileLock

from .exceptions import ValidationError

DEFAULTS = {
    "theme": "light",
    "endpoint": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "asr_model": "",
    "vad_model": "",
    "microphone_id": "",
    "listening_mode": "headphones",
    "live_suggestions": False,
    "coverage_reminders": False,
    "time_reminders": False,
}


def app_root() -> Path:
    return Path(
        os.environ.get("IRIS_INTERVIEW_DATA_DIR", "~/Library/Application Support/Iris-interview")
    ).expanduser()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Preferences:
    def __init__(self, root: Path | None = None):
        self.root = root or app_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "preferences.json"
        self.lock = FileLock(str(self.root / "preferences.lock"))

    def load(self) -> dict:
        with self.lock:
            value = json.loads(self.path.read_text()) if self.path.exists() else {}
            return {**DEFAULTS, **value}

    def update(self, **values) -> dict:
        if any("key" in name.lower() or "secret" in name.lower() for name in values):
            raise ValidationError("密钥只能保存到钥匙串")
        with self.lock:
            current = json.loads(self.path.read_text()) if self.path.exists() else {}
            current.update(values)
            atomic_json(self.path, current)
            return {**DEFAULTS, **current}


def get_api_key() -> str:
    import keyring

    return keyring.get_password("Iris-interview", "cloud-api") or ""


def set_api_key(value: str) -> None:
    import keyring

    if value:
        keyring.set_password("Iris-interview", "cloud-api", value)
    elif keyring.get_password("Iris-interview", "cloud-api"):
        keyring.delete_password("Iris-interview", "cloud-api")
