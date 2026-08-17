"""taskpanel.reporter 测试 — 上下文管理器三态流转 / 阶段上报 / 容错。"""

from __future__ import annotations

import json

import pytest

from iris.taskpanel.reporter import TaskReporter, generate_task_id
from iris.taskpanel.store import read_current_all, read_history


# ── TestIdentity ──────────────────────────────────────────


class TestIdentity:
    def test_task_id_format(self):
        task_id = generate_task_id("daily-start")
        # 形如 daily-start-20260816-112459-36597（任务名本身含 "-"，从右拆）
        date, time_s, pid = task_id.rsplit("-", 3)[1:]
        assert task_id.startswith("daily-start-")
        assert len(date) == 8   # YYYYmmdd
        assert len(time_s) == 6  # HHMMSS
        assert pid.isdigit()    # pid

    def test_task_id_contains_pid(self):
        assert generate_task_id("x").endswith(f"-{__import__('os').getpid()}")

    def test_agent_id_from_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IRIS_AGENT_ID", "agent-b")
        with TaskReporter("daily-start", data_root=tmp_path) as r:
            assert r.task.agent_id == "agent-b"

    def test_agent_id_explicit_overrides_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("IRIS_AGENT_ID", "agent-b")
        with TaskReporter("daily-start", agent_id="agent-c", data_root=tmp_path) as r:
            assert r.task.agent_id == "agent-c"


# ── TestContextManager ────────────────────────────────────


class TestContextManager:
    def test_normal_exit_writes_success(self, tmp_path):
        with TaskReporter("daily-start", data_root=tmp_path):
            pass
        assert read_current_all(tmp_path) == []
        history = read_history(tmp_path)
        assert len(history) == 1
        assert history[0].status == "success"
        assert history[0].name == "daily-start"
        assert history[0].pid == __import__("os").getpid()

    def test_exception_writes_failed_and_reraises(self, tmp_path):
        with pytest.raises(RuntimeError, match="boom"):
            with TaskReporter("build-chunks", data_root=tmp_path):
                raise RuntimeError("boom")
        history = read_history(tmp_path)
        assert history[0].status == "failed"
        assert "RuntimeError: boom" in history[0].error

    def test_keyboard_interrupt_writes_failed(self, tmp_path):
        with pytest.raises(KeyboardInterrupt):
            with TaskReporter("build-wiki", data_root=tmp_path):
                raise KeyboardInterrupt()
        assert read_history(tmp_path)[0].status == "failed"

    def test_running_visible_during_with(self, tmp_path):
        with TaskReporter("daily-start", data_root=tmp_path) as r:
            current = read_current_all(tmp_path)
            assert len(current) == 1
            assert current[0].task_id == r.task_id
            assert current[0].status == "running"


# ── TestPhases ────────────────────────────────────────────


class TestPhases:
    def test_report_phase_updates_current(self, tmp_path):
        with TaskReporter("daily-start", data_root=tmp_path) as r:
            r.report_phase("memory_sync", "第1/8阶段：记忆同步", progress=0.125)
            current = read_current_all(tmp_path)[0]
            assert current.phase == "memory_sync"
            assert current.phase_detail == "第1/8阶段：记忆同步"
            assert current.progress == pytest.approx(0.125)

    def test_report_phase_before_enter_is_noop(self, tmp_path):
        r = TaskReporter("daily-start", data_root=tmp_path)
        r.report_phase("memory_sync", "不该写入")
        assert read_current_all(tmp_path) == []

    def test_progress_none_allowed(self, tmp_path):
        with TaskReporter("x", data_root=tmp_path) as r:
            r.report_phase("phase", "detail")
            assert read_current_all(tmp_path)[0].progress is None


# ── TestConcurrency ───────────────────────────────────────


class TestConcurrency:
    def test_same_name_multi_instances_coexist(self, tmp_path):
        """同名并发多实例（不同 pid/task_id）current/ 并存互不覆盖。"""
        r1 = TaskReporter("daily-start", task_id="daily-start-1", data_root=tmp_path)
        r2 = TaskReporter("daily-start", task_id="daily-start-2", data_root=tmp_path)
        with r1, r2:
            current = read_current_all(tmp_path)
            assert len(current) == 2


# ── TestResilience ────────────────────────────────────────


class TestResilience:
    def test_write_failure_silent(self, tmp_path, monkeypatch):
        """磁盘写失败静默——业务命令不受埋点影响。"""
        def _boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("iris.taskpanel.store.atomic_write_json", _boom)
        # 进入/阶段/退出全部静默，不抛异常
        with TaskReporter("daily-start", data_root=tmp_path) as r:
            r.report_phase("memory_sync", "x")
        assert read_current_all(tmp_path) == []

    def test_finalize_failure_silent(self, tmp_path, monkeypatch):
        def _boom(*args, **kwargs):
            raise OSError("disk full")

        # 注意：reporter 是直接导入引用，需 patch reporter 命名空间内的引用
        monkeypatch.setattr("iris.taskpanel.reporter.finalize_task", _boom)
        with TaskReporter("daily-start", data_root=tmp_path):
            pass  # 退出时 finalize 失败 → 静默
        assert read_history(tmp_path) == []

    def test_project_root_failure_degrades_to_noop(self, tmp_path, monkeypatch):
        def _boom():
            raise RuntimeError("no root")

        monkeypatch.setattr("iris.taskpanel.reporter.get_project_root", _boom)
        with TaskReporter("daily-start") as r:
            assert r._data_root is None  # noqa: SLF001 降级 no-op
        assert read_current_all(tmp_path) == []
