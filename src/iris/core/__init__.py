"""核心抽象层——数据类型、锁、写入守卫、存储层。"""

from .llm_types import LLMRequest, LLMResponse  # 从 protocols 迁移，消除 Any 类型标注
from .locks import FileLock, FileLockError
from .thread_pool import shared_pool
from .write_guard import (
    WriteGuardError,
    resolve_allowed_paths,
    safe_write_text,
    validate_write_path,
)

# 可选：SQLite 存储（高性能场景替代 JSON）
try:
    from .storage import ChunkStore, StorageError
except ImportError:
    from typing import Any as _AnyType
    ChunkStore: _AnyType = None
    class StorageError(RuntimeError):
        """SQLite 存储不可用时的占位异常类型。"""
        pass

__all__ = [
    "LLMRequest",
    "LLMResponse",
    "FileLock",
    "FileLockError",
    "WriteGuardError",
    "resolve_allowed_paths",
    "safe_write_text",
    "validate_write_path",
    "ChunkStore",
    "StorageError",
    "shared_pool",
]
