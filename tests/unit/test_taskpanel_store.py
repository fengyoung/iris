"""taskpanel.store 测试 — 数据模型往返 / current 读写 / history 追加截断 / 终态幂等。"""

from __future__ import annotations

import json
import threading

import pytest

from iris.taskpanel.store import (
    TaskStatus,
    finalize_task,
    history_path,
    read_current_all,
    read_history,
    write_current,
)


def _make_task(task_id: str = "daily-start-20260816-100000-123") -> TaskStatus:
    return TaskStatus(
        task_id=task_id,
        name="daily-start",
        command="daily-start",
        agent_id="default",
        pid=123,
        phase="memory_sync",
        phase_detail="第1/8阶段：记忆同步",
        progress=0.125,
        started_at="2026-08-16T10:00:00+00:00",
    )


# ── TestTaskStatus ────────────────────────────────────────


class TestTaskStatus:
    def test_roundtrip(self):
        task = _make_task()
        restored = TaskStatus.from_dict(json.loads(json.dumps(task.to_dict())))
        assert restored == task

    def test_from_dict_missing_fields_defaults(self):
        task = TaskStatus.from_dict({"task_id": "x", "name": "y"})
        assert task.command == ""
        assert task.agent_id == "default"
        assert task.pid is None
        assert task.status == "running"
        assert task.progress is None

    def test_to_dict_contains_all_fields(self):
        d = _make_task().to_dict()
        for key in ("task_id", "name", "command", "agent_id", "pid", "status",
                    "phase", "phase_detail", "progress", "started_at", "ended_at", "error"):
            assert key in d


# ── TestCurrentStore ──────────────────────────────────────


class TestCurrentStore:
    def test_write_and_read(self, tmp_path):
        write_current(tmp_path, _make_task())
        tasks = read_current_all(tmp_path)
        assert len(tasks) == 1
        assert tasks[0].task_id == "daily-start-20260816-100000-123"

    def test_read_empty_dir(self, tmp_path):
        assert read_current_all(tmp_path) == []

    def test_read_sorted_by_started_at(self, tmp_path):
        t1 = _make_task("a-1")
        t1.started_at = "2026-08-16T11:00:00+00:00"
        t2 = _make_task("b-2")
        t2.started_at = "2026-08-16T10:00:00+00:00"
        write_current(tmp_path, t1)
        write_current(tmp_path, t2)
        tasks = read_current_all(tmp_path)
        assert [t.task_id for t in tasks] == ["b-2", "a-1"]

    def test_skip_corrupted_file(self, tmp_path):
        write_current(tmp_path, _make_task("ok-1"))
        bad = tmp_path / "tasks" / "current" / "bad.json"
        bad.write_text("{not valid json", encoding="utf-8")
        tasks = read_current_all(tmp_path)
        assert [t.task_id for t in tasks] == ["ok-1"]


# ── TestFinalize ──────────────────────────────────────────


class TestFinalize:
    def test_success_flow(self, tmp_path):
        write_current(tmp_path, _make_task())
        finalize_task(tmp_path, _make_task(), "success")
        assert read_current_all(tmp_path) == []
        history = read_history(tmp_path)
        assert len(history) == 1
        assert history[0].status == "success"
        assert history[0].ended_at

    def test_failed_with_error(self, tmp_path):
        write_current(tmp_path, _make_task())
        finalize_task(tmp_path, _make_task(), "failed", error="boom")
        history = read_history(tmp_path)
        assert history[0].status == "failed"
        assert history[0].error == "boom"

    def test_idempotent_double_call(self, tmp_path):
        write_current(tmp_path, _make_task())
        finalize_task(tmp_path, _make_task(), "success")
        # 第二次：current 已删 → 守卫 1 拦截
        finalize_task(tmp_path, _make_task(), "success")
        assert len(read_history(tmp_path)) == 1

    def test_idempotent_when_current_removed_but_history_has_id(self, tmp_path):
        # 模拟 unlink 失败场景：current 残留但 history 已含 task_id → 守卫 2 拦截
        write_current(tmp_path, _make_task())
        finalize_task(tmp_path, _make_task(), "success")
        # 重新放回 current（模拟 unlink 失败），再次 finalize
        write_current(tmp_path, _make_task())
        finalize_task(tmp_path, _make_task(), "success")
        assert len(read_history(tmp_path)) == 1

    def test_invalid_status_raises(self, tmp_path):
        with pytest.raises(ValueError):
            finalize_task(tmp_path, _make_task(), "bogus")


# ── TestHistory ───────────────────────────────────────────


class TestHistory:
    def test_append_and_read_roundtrip(self, tmp_path):
        for i in range(3):
            task = _make_task(f"task-{i}")
            write_current(tmp_path, task)
            finalize_task(tmp_path, task, "success")
        history = read_history(tmp_path)
        assert [h.task_id for h in history] == ["task-0", "task-1", "task-2"]

    def test_rolling_truncate_at_200(self, tmp_path):
        for i in range(260):
            task = _make_task(f"task-{i}")
            write_current(tmp_path, task)
            finalize_task(tmp_path, task, "success")
        history = read_history(tmp_path)
        assert len(history) == 200
        assert history[0].task_id == "task-60"  # 前 60 条被截断

    def test_corrupted_line_skipped(self, tmp_path):
        write_current(tmp_path, _make_task("ok-1"))
        finalize_task(tmp_path, _make_task("ok-1"), "success")
        with open(history_path(tmp_path), "a", encoding="utf-8") as f:
            f.write("{corrupted\n")
        history = read_history(tmp_path)
        assert [h.task_id for h in history] == ["ok-1"]

    def test_concurrent_append_no_loss(self, tmp_path):
        """多线程并发 finalize 不同任务，flock 串行保证无丢失。"""
        n = 30
        for i in range(n):
            write_current(tmp_path, _make_task(f"task-{i}"))

        def _finalize(i: int) -> None:
            finalize_task(tmp_path, _make_task(f"task-{i}"), "success")

        threads = [threading.Thread(target=_finalize, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(read_history(tmp_path)) == n
