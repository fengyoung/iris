"""taskpanel.probe 测试 — pid 存活 / stale 兜底 / watchdog 探测。"""

from __future__ import annotations

import os
import subprocess
import sys


from iris.taskpanel.probe import (
    is_pid_alive,
    probe_and_finalize_stale,
    probe_registered_process,
    probe_task,
    probe_watchdogs,
    process_command,
)
from iris.taskpanel.store import TaskStatus, read_current_all, read_history, write_current


def _make_running(task_id: str, pid: int) -> TaskStatus:
    return TaskStatus(task_id=task_id, name="build-chunks", pid=pid,
                      status="running", started_at="2026-08-16T10:00:00+00:00")


def _dead_pid() -> int:
    """生成一个几乎肯定不存在的 pid。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = proc.pid
    proc.wait()
    return pid  # 子进程已退出 → pid 已死


# ── TestPidAlive ──────────────────────────────────────────


class TestPidAlive:
    def test_self_is_alive(self):
        assert is_pid_alive(os.getpid())

    def test_dead_pid_not_alive(self):
        assert not is_pid_alive(_dead_pid())

    def test_process_command_contains_python(self):
        assert "python" in process_command(os.getpid())


# ── TestProbeTask ─────────────────────────────────────────


class TestProbeTask:
    def test_running_alive_unchanged(self):
        task = _make_running("t-1", os.getpid())
        assert probe_task(task).status == "running"

    def test_running_dead_marked_interrupted(self):
        task = probe_task(_make_running("t-1", _dead_pid()))
        assert task.status == "interrupted"
        assert task.error

    def test_non_running_untouched(self):
        task = _make_running("t-1", _dead_pid())
        task.status = "success"
        assert probe_task(task).status == "success"

    def test_pid_none_untouched(self):
        task = _make_running("t-1", _dead_pid())
        task.pid = None
        assert probe_task(task).status == "running"


# ── TestFinalizeStale ─────────────────────────────────────


class TestFinalizeStale:
    def test_dead_task_finalized(self, tmp_path):
        write_current(tmp_path, _make_running("dead-1", _dead_pid()))
        interrupted = probe_and_finalize_stale(tmp_path)
        assert interrupted == ["dead-1"]
        assert read_current_all(tmp_path) == []
        history = read_history(tmp_path)
        assert history[0].task_id == "dead-1"
        assert history[0].status == "interrupted"
        assert history[0].ended_at

    def test_alive_task_kept(self, tmp_path):
        write_current(tmp_path, _make_running("alive-1", os.getpid()))
        assert probe_and_finalize_stale(tmp_path) == []
        assert len(read_current_all(tmp_path)) == 1

    def test_empty_dir(self, tmp_path):
        assert probe_and_finalize_stale(tmp_path) == []


# ── TestWatchdog ──────────────────────────────────────────


class TestWatchdog:
    def test_no_pid_file(self, tmp_path):
        assert probe_registered_process("asr-corrector", tmp_path) is None

    def test_dead_pid_file(self, tmp_path):
        pid_file = tmp_path / "asr-corrector.pid"
        pid_file.write_text(str(_dead_pid()), encoding="utf-8")
        assert probe_registered_process("asr-corrector", tmp_path) is None

    def test_alive_pid_file(self, tmp_path):
        pid_file = tmp_path / "asr-corrector.pid"
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        info = probe_registered_process("asr-corrector", tmp_path)
        assert info == {"name": "asr-corrector", "pid": os.getpid(), "status": "running"}

    def test_corrupted_pid_file(self, tmp_path):
        pid_file = tmp_path / "asr-corrector.pid"
        pid_file.write_text("not-a-pid", encoding="utf-8")
        assert probe_registered_process("asr-corrector", tmp_path) is None

    def test_probe_watchdogs_stopped_entry(self, tmp_path):
        result = probe_watchdogs(tmp_path)
        assert any(w["name"] == "asr-corrector" and w["status"] == "stopped"
                   for w in result)
