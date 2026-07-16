"""测试文件锁 — core/locks.py。"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from iris.core.locks import FileLock, FileLockError


class TestFileLockBasic:
    """FileLock 基本功能测试。"""

    def test_acquire_and_release(self, temp_project):
        """获取和释放锁。"""
        path = temp_project / "test.json"
        lock = FileLock(path)
        lock.acquire()
        # 锁文件应存在
        assert lock._lock_path.exists()
        lock.release()
        # 释放后 _fd 应为 None
        assert lock._fd is None

    def test_context_manager(self, temp_project):
        """上下文管理器自动获取和释放。"""
        path = temp_project / "test.json"
        with FileLock(path) as lock:
            assert lock._lock_path.exists()
        assert lock._fd is None

    def test_nonblocking_fails_when_locked(self, temp_project):
        """非阻塞模式在锁已被持有时应失败。"""
        path = temp_project / "test.json"
        lock1 = FileLock(path)
        lock1.acquire()
        try:
            lock2 = FileLock(path, blocking=False, timeout=0.5)
            with pytest.raises(FileLockError, match="无法获取文件锁"):
                lock2.acquire()
        finally:
            lock1.release()

    def test_timeout_raises(self, temp_project):
        """超时后抛出异常。"""
        path = temp_project / "test.json"
        lock1 = FileLock(path)
        lock1.acquire()
        try:
            lock2 = FileLock(path, timeout=0.3)
            with pytest.raises(FileLockError, match="获取文件锁超时"):
                lock2.acquire()
        finally:
            lock1.release()

    def test_lock_exclusive(self, temp_project):
        """锁应为互斥的（同一进程内）。"""
        path = temp_project / "test.json"
        results = []
        errors = []

        def worker(worker_id):
            try:
                with FileLock(path, timeout=5):
                    results.append(f"enter-{worker_id}")
                    time.sleep(0.15)
                    results.append(f"exit-{worker_id}")
            except FileLockError as e:
                errors.append(str(e))

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)

        # 两个线程都应成功进入（串行化，不会交织）
        assert len(results) == 4  # enter + exit × 2
        # 验证执行顺序是串行的：先进入的必须先退出，不允许 enter-enter 或 exit-exit 交错
        enters = [i for i, r in enumerate(results) if r.startswith("enter")]
        exits = [i for i, r in enumerate(results) if r.startswith("exit")]
        assert len(enters) == 2
        assert len(exits) == 2
        # 第一个 enter 的线程必须先 exit
        first_thread = results[enters[0]].split("-")[1]
        assert results[exits[0]] == f"exit-{first_thread}"
        # 不允许两个 enter 连续出现
        assert enters[1] - enters[0] >= 2  # 中间至少有一个 exit

    def test_release_twice_is_safe(self, temp_project):
        """重复释放不应崩溃。"""
        path = temp_project / "test.json"
        lock = FileLock(path)
        lock.acquire()
        lock.release()
        lock.release()  # 第二次不应抛异常
        assert lock._fd is None


class TestFileLockDataIntegrity:
    """通过锁保护 JSON 写入的一致性测试。"""

    def test_atomic_json_write(self, temp_project):
        """通过锁保护的并发 JSON 写入应保持数据一致。"""
        path = temp_project / "counter.json"
        path.write_text(json.dumps({"count": 0}), encoding="utf-8")

        iterations = 10
        thread_count = 3
        errors = []

        def increment():
            for _ in range(iterations):
                try:
                    with FileLock(path, timeout=10):
                        data = json.loads(path.read_text(encoding="utf-8"))
                        data["count"] += 1
                        path.write_text(json.dumps(data), encoding="utf-8")
                except Exception as e:
                    errors.append(str(e))

        threads = [threading.Thread(target=increment) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors
        final = json.loads(path.read_text(encoding="utf-8"))
        assert final["count"] == thread_count * iterations

    def test_lock_file_cleaned_up(self, temp_project):
        """释放后锁文件被清理。"""
        path = temp_project / "test.json"
        with FileLock(path):
            pass
        # 锁文件应在 release 中通过 unlink(missing_ok=True) 清理
        assert not path.with_suffix(path.suffix + ".lock").exists()

    def test_lock_for_directory_creation(self, temp_project):
        """锁目录自动创建。"""
        nested = temp_project / "sub" / "deep" / "test.json"
        with FileLock(nested):
            pass
        assert nested.parent.exists()
