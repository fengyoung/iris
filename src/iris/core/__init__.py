"""核心抽象层——协议、锁、写入守卫、存储层。"""

from .protocols import LLMProvider, MemoryStore, PromptLoader
from .locks import FileLock, FileLockError
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
    ChunkStore = None  # type: ignore
    StorageError = RuntimeError  # type: ignore

__all__ = [
    "LLMProvider",
    "MemoryStore",
    "PromptLoader",
    "FileLock",
    "FileLockError",
    "WriteGuardError",
    "resolve_allowed_paths",
    "safe_write_text",
    "validate_write_path",
    "ChunkStore",
    "StorageError",
]
