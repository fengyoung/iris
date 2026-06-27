"""轻量结构化日志。"""

from __future__ import annotations

import json
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
        # 检查日志文件大小，超过阈值自动归档
        if self._log_path.exists() and self._log_path.stat().st_size > _MAX_LOG_SIZE_BYTES:
            archive = self._log_path.with_suffix(".jsonl.bak")
            archive.write_text(self._log_path.read_text(encoding="utf-8"), encoding="utf-8")
            self._log_path.write_text("", encoding="utf-8")
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "payload": _normalize(payload),
        }
        with self._log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

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
