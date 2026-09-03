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

from iris.core.exceptions import IrisRuntimeError

logger = logging.getLogger("iris.core.locks")

_LOCK_TIMEOUT = 30  # 最大等待秒数
_LOCK_CHECK_INTERVAL = 0.1  # 轮询间隔


class FileLockError(IrisRuntimeError):
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
            # 锁文件必须保留。释放后删除会产生 inode 竞态：等待者仍锁住旧
            # inode，而后来者可创建并锁住新 inode，导致两个临界区并发执行。


# ── 进程注册表 ─────────────────────────────────────────────────


class ProcessRegistry:
    """进程注册表 — 基于 PID 文件防止守护进程重复启动。

    用法：
        registry = ProcessRegistry("asr-corrector", pid_dir)
        if not registry.register():
            raise RuntimeError("已有 asr-corrector 进程在运行")
        try:
            run_forever()
        finally:
            registry.unregister()
    """

    def __init__(self, name: str, pid_dir: Path):
        self._name = name
        self._pid_file = pid_dir / f"{name}.pid"

    def register(self) -> bool:
        """注册当前进程。返回 False 表示已有同名进程运行。"""
        self._pid_file.parent.mkdir(parents=True, exist_ok=True)
        if self._pid_file.exists():
            try:
                stale_pid = int(self._pid_file.read_text().strip())
                if self._is_alive(stale_pid):
                    logger.warning("进程注册失败: %s (PID %d 仍在运行)", self._name, stale_pid)
                    return False
                # PID 文件残留（进程已死），覆盖
                logger.debug("清理残留 PID 文件: %s (PID %d 已死)", self._name, stale_pid)
            except (ValueError, OSError):
                pass  # 文件损坏，覆盖
        self._pid_file.write_text(str(os.getpid()))
        logger.info("进程已注册: %s (PID %d)", self._name, os.getpid())
        return True

    def unregister(self) -> None:
        """注销当前进程。"""
        try:
            self._pid_file.unlink(missing_ok=True)
            logger.debug("进程已注销: %s", self._name)
        except OSError:
            pass

    @staticmethod
    def _is_alive(pid: int) -> bool:
        """检查进程是否存活（发送信号 0）。"""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
