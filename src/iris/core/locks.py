"""文件锁：保护 JSON 文件并发写入安全。

使用建议：
    with FileLock("/path/to/data.json"):
        data = json.loads(path.read_text())
        # 修改 data
        path.write_text(json.dumps(data))

注意：macOS 上 fcntl.flock 对 NFS 挂载卷不可靠，本地磁盘可放心使用。
"""

from __future__ import annotations

import fcntl
import logging
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Optional, Type

logger = logging.getLogger("iris.core.locks")

_LOCK_TIMEOUT = 30  # 最大等待秒数
_LOCK_CHECK_INTERVAL = 0.1  # 轮询间隔


class FileLockError(RuntimeError):
    """文件锁相关错误。"""


class FileLock:
    """基于 fcntl.flock 的文件锁上下文管理器。

    支持：
    - 阻塞等待（带超时）
    - 进程退出时自动释放（内核管理）
    - 与 flock shell 命令互操作

    用法：
        with FileLock("/path/to/file.json"):
            # 临界区
    """

    def __init__(
        self,
        path: Path | str,
        *,
        timeout: float = _LOCK_TIMEOUT,
        blocking: bool = True,
    ):
        self._path = Path(path)
        self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        self._timeout = timeout
        self._blocking = blocking
        self._fd: Optional[int] = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        self.release()

    def acquire(self) -> None:
        """获取锁，阻塞直到成功或超时。"""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)

        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        self._fd = fd

        deadline = time.monotonic() + self._timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # 获取锁成功
                os.write(fd, str(os.getpid()).encode())
                return
            except (IOError, OSError):
                if not self._blocking:
                    os.close(fd)
                    self._fd = None
                    raise FileLockError(f"无法获取文件锁: {self._lock_path}") from None
                if time.monotonic() > deadline:
                    os.close(fd)
                    self._fd = None
                    raise FileLockError(
                        f"获取文件锁超时 ({self._timeout}s): {self._lock_path}"
                    ) from None
                time.sleep(_LOCK_CHECK_INTERVAL)

    def release(self) -> None:
        """释放锁。"""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except (IOError, OSError) as exc:
                logger.warning("释放文件锁异常: %s", exc)
            finally:
                self._fd = None
            # 清理锁文件（最佳努力）
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
