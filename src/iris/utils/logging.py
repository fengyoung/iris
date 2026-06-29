"""轻量结构化日志 — 使用 fcntl 文件锁避免归档竞态。"""

from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from iris.config.loader import ConfigBundle

# 日志文件大小限制（超过后自动归档）
_MAX_LOG_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


class IrisLogger:
    """按 jsonl 写入运行日志，便于排查路由、检索与回退。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._enabled = bool(config.app["logging"].get("log_to_file", False))
        self._log_path = config.root / config.app["paths"]["log_dir"].replace("./", "") / "iris.jsonl"

    def log(self, event: str, payload: Dict[str, Any]) -> None:
        if not self._enabled:
            return
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        # 原子归档：用 os.rename（Unix 原子操作）+ fcntl 锁避免竞态
        if self._log_path.exists() and self._log_path.stat().st_size > _MAX_LOG_SIZE_BYTES:
            archive = self._log_path.with_suffix(".jsonl.bak")
            self._rotate_with_lock(archive)
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "payload": _normalize(payload),
        }
        try:
            with self._log_path.open("a", encoding="utf-8") as file:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX)
                try:
                    file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    file.flush()
                    os.fsync(file.fileno())
                finally:
                    fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass  # 日志写入失败不应中断主流程

    def _rotate_with_lock(self, archive: Path) -> None:
        """使用 os.rename（原子）完成日志归档。"""
        try:
            os.rename(str(self._log_path), str(archive))
        except OSError:
            pass  # 归档失败跳过，日志继续写入（可能超限）

    @property
    def log_path(self) -> Path:
        return self._log_path


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize(item) for item in value)
    return value
