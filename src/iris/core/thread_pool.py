"""共享线程池 — 避免各模块重复创建 ThreadPoolExecutor。

用法:
    from iris.core.thread_pool import shared_pool

    with shared_pool.executor(max_workers=6) as executor:
        futures = {executor.submit(fn, arg): arg for arg in items}
        for future in as_completed(futures, timeout=timeout):
            result = future.result()

设计：
  - 惰性创建，按 max_workers 缓存不同大小的池
  - atexit 注册自动 shutdown
  - 线程安全（threading.Lock 保护创建）
"""

from __future__ import annotations

import atexit
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 默认最大工作线程数
_DEFAULT_WORKERS = 8
# 最小保持的线程数
_MIN_WORKERS = 2


class SharedThreadPool:
    """模块级可复用线程池。

    按 max_workers 缓存不同大小的池实例，避免每次操作创建/销毁线程池的开销。
    """

    def __init__(self):
        self._pools: Dict[int, ThreadPoolExecutor] = {}
        self._lock = threading.Lock()
        self._shutdown = False
        atexit.register(self.shutdown)

    @contextmanager
    def executor(self, max_workers: Optional[int] = None):
        """获取指定大小的线程池上下文管理器。

        用法:
            with shared_pool.executor(max_workers=6) as ex:
                futures = {ex.submit(fn, a): a for a in items}
        """
        workers = max(max_workers or _DEFAULT_WORKERS, _MIN_WORKERS)
        pool = self._get_or_create(workers)
        try:
            yield pool
        except Exception:
            # 池内线程异常不影响池本身，仅传播
            raise

    def _get_or_create(self, workers: int) -> ThreadPoolExecutor:
        if self._shutdown:
            # 已关闭则创建临时池（不应发生，安全兜底）
            return ThreadPoolExecutor(max_workers=workers,
                                       thread_name_prefix=f"iris-tmp-{workers}")

        if workers not in self._pools:
            with self._lock:
                if workers not in self._pools:
                    self._pools[workers] = ThreadPoolExecutor(
                        max_workers=workers,
                        thread_name_prefix=f"iris-pool-{workers}",
                    )
                    logger.debug("创建共享线程池 workers=%d", workers)
        return self._pools[workers]

    def shutdown(self, wait: bool = True) -> None:
        """关闭所有缓存的线程池。atexit 自动调用。"""
        if self._shutdown:
            return
        self._shutdown = True
        with self._lock:
            for workers, pool in self._pools.items():
                try:
                    pool.shutdown(wait=wait)
                    logger.debug("关闭共享线程池 workers=%d", workers)
                except Exception as exc:
                    logger.debug("关闭线程池异常 workers=%d: %s", workers, exc)
            self._pools.clear()

    def stats(self) -> Dict[str, int]:
        """返回线程池统计信息。"""
        with self._lock:
            return {str(w): p._work_queue.qsize() if hasattr(p, '_work_queue') else -1
                    for w, p in self._pools.items()}


# 全局单例
shared_pool = SharedThreadPool()
