"""项目级共享工具函数 — 原子写入、时间戳等。

避免各模块重复定义 _atomic_write_json / _now_iso。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写入二进制文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as file_obj:
            file_obj.write(data)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            logger.warning("原子写入临时文件清理失败: %s", tmp_path)
        raise


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """原子写入文本文件。"""
    atomic_write_bytes(Path(path), content.encode(encoding))


def atomic_write_json(path: Path, data: Any) -> None:
    """原子写入 JSON 文件 — 先写临时文件，再 os.replace。

    保证进程崩溃或磁盘满时不会损坏已有 JSON 数据。
    与 memory/long_term.py 中的 _atomic_write_json 功能完全一致，
    此处为全项目统一的版本。
    """
    content = json.dumps(data, ensure_ascii=False, indent=2)
    atomic_write_text(Path(path), content)


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).isoformat()
