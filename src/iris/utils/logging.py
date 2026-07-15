"""轻量结构化日志 — 支持 JSONL 文件 + JSON 控制台输出，使用 fcntl 文件锁避免归档竞态。"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TextIO

from iris.config.loader import ConfigBundle

# 日志文件大小限制（超过后自动归档）
_MAX_LOG_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# 标准化的日志级别
_LOG_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


class IrisLogger:
    """按 jsonl 写入运行日志，便于排查路由、检索与回退。

    支持两种输出模式：
      - file: JSONL 文件写入（iris.jsonl），通过 config.app.logging.log_to_file 控制
      - console: JSON 行输出到 stderr，通过 config.app.logging.log_to_console 控制
    """

    def __init__(self, config: ConfigBundle):
        self._config = config
        logging_cfg = config.app.get("logging", {}) if isinstance(config.app, dict) else {}
        self._enabled = bool(logging_cfg.get("log_to_file", False))
        self._console_enabled = bool(logging_cfg.get("log_to_console", False))
        self._level = logging_cfg.get("level", "info").lower()
        log_dir = str(config.app.get("paths", {}).get("log_dir", "./logs")) if isinstance(config.app, dict) else "./logs"
        self._log_path = config.root / log_dir.replace("./", "") / "iris.jsonl"
        self._console_stream: Optional[TextIO] = sys.stderr if self._console_enabled else None

    @property
    def log_path(self) -> Path:
        return self._log_path

    def log(self, event: str, payload: Dict[str, Any], *, level: str = "info") -> None:
        """记录一条结构化日志事件。

        Args:
            event: 事件名称（如 "wiki_build_page", "llm_generate", "qa_ask"）
            payload: 事件负载数据
            level: 日志级别（debug/info/warning/error），低于配置级别则跳过
        """
        if _LOG_LEVELS.get(level, 20) < _LOG_LEVELS.get(self._level, 20):
            return

        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "event": event,
        }
        normalized = _normalize(payload)
        record.update(normalized if isinstance(normalized, dict) else {"payload": normalized})

        # 控制台 JSON 输出
        if self._console_stream:
            try:
                self._console_stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._console_stream.flush()
            except (OSError, IOError):
                pass

        # 文件 JSONL 输出
        if self._enabled:
            self._write_file(record)

    def _write_file(self, record: Dict[str, Any]) -> None:
        """写入 JSONL 文件（带 fcntl 文件锁 + 自动归档）。"""
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        if self._log_path.exists() and self._log_path.stat().st_size > _MAX_LOG_SIZE_BYTES:
            archive = self._log_path.with_suffix(".jsonl.bak")
            self._rotate_with_lock(archive)
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
            pass

    def _rotate_with_lock(self, archive: Path) -> None:
        """使用 os.rename（原子）完成日志归档。"""
        try:
            os.rename(str(self._log_path), str(archive))
        except OSError:
            pass


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
