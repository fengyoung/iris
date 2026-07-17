"""SharedThreadPool 单元测试。"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait

import pytest

from iris.core.thread_pool import SharedThreadPool, shared_pool


class TestSharedThreadPool:
    """SharedThreadPool 测试 — 注意：不与全局 singleton shared_pool 冲突。"""

    def test_executor_context_manager(self):
        """executor() 返回可用的上下文管理器。"""
        pool = SharedThreadPool()
        with pool.executor(max_workers=2) as ex:
            future = ex.submit(lambda x: x * 2, 21)
            assert future.result() == 42
        pool.shutdown()

    def test_executor_caches_by_worker_count(self):
        """相同 worker 数返回缓存的池实例。"""
        pool = SharedThreadPool()
        with pool.executor(max_workers=3) as ex1:
            pass
        with pool.executor(max_workers=3) as ex2:
            pass
        # 相同 worker 数的池应被缓存复用
        assert ex1 is ex2
        pool.shutdown()

    def test_executor_different_workers_different_pools(self):
        """不同 worker 数创建不同的池。"""
        pool = SharedThreadPool()
        with pool.executor(max_workers=2) as ex1:
            pass
        with pool.executor(max_workers=4) as ex2:
            pass
        assert ex1 is not ex2
        pool.shutdown()

    def test_get_executor_returns_usable_executor(self):
        """get_executor() 返回可用的 ThreadPoolExecutor。"""
        pool = SharedThreadPool()
        ex = pool.get_executor(max_workers=2)
        future = ex.submit(sum, [1, 2, 3])
        assert future.result() == 6
        pool.shutdown()

    def test_shutdown_clears_all_pools(self):
        """shutdown() 清空所有缓存的池。"""
        pool = SharedThreadPool()
        pool.get_executor(max_workers=2)
        pool.get_executor(max_workers=4)
        assert pool.stats() != {}

        pool.shutdown()
        # 关闭后 stats 应为空
        assert pool.stats() == {}

        # 再次 shutdown 应无操作
        pool.shutdown()

    def test_respects_min_workers_floor(self):
        """worker 数不低于 _MIN_WORKERS (2)。"""
        pool = SharedThreadPool()
        ex = pool.get_executor(max_workers=1)
        # _MIN_WORKERS=2，传入 1 应被提升到 2
        assert ex._max_workers >= 2
        pool.shutdown()

    def test_concurrent_access(self):
        """并发访问不产生死锁。"""
        pool = SharedThreadPool()

        def worker(n):
            with pool.executor(max_workers=2) as ex:
                f = ex.submit(lambda x: x * x, n)
                return f.result()

        import threading
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        pool.shutdown()

    def test_after_shutdown_creates_temporary_pool(self):
        """shutdown 后 _get_or_create 返回临时池。"""
        pool = SharedThreadPool()
        pool.shutdown()

        # 关闭后获取 executor 应仍可用（临时池）
        ex = pool.get_executor(max_workers=2)
        future = ex.submit(lambda: 42)
        assert future.result() == 42


class TestSharedPoolSingleton:
    """全局 shared_pool 单例测试。"""

    def test_shared_pool_is_singleton(self):
        """shared_pool 是 SharedThreadPool 实例。"""
        assert isinstance(shared_pool, SharedThreadPool)
