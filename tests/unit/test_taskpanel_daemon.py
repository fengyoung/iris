"""taskpanel.daemon 测试 — 端口解析 / plist 生成 / 只读状态判定。"""

from __future__ import annotations

import os
import plistlib
import types

import pytest

from iris.taskpanel.daemon import (
    DEFAULT_PORT,
    _resolve_port,
    do_install,
    do_status,
)


class _Args:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


# ── TestResolvePort ───────────────────────────────────────


class TestResolvePort:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("IRIS_TASK_PANEL_PORT", raising=False)
        assert _resolve_port() == DEFAULT_PORT

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("IRIS_TASK_PANEL_PORT", "9000")
        assert _resolve_port() == 9000

    def test_arg_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("IRIS_TASK_PANEL_PORT", "9000")
        assert _resolve_port(8888) == 8888

    def test_invalid_port_raises(self, monkeypatch):
        monkeypatch.delenv("IRIS_TASK_PANEL_PORT", raising=False)
        with pytest.raises(ValueError):
            _resolve_port(80)       # < 1024
        with pytest.raises(ValueError):
            _resolve_port(70000)    # > 65535


# ── TestInstall ───────────────────────────────────────────


class TestInstall:
    def test_plist_generated(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("pathlib.Path.home",
                            staticmethod(lambda: tmp_path))
        # daemon 里 Path.home() 在函数内调用，patch 后生效
        rc = do_install(_Args(project_root=str(tmp_path)))
        assert rc == 0
        plist_path = tmp_path / "Library" / "LaunchAgents" / "com.iris.task-panel.plist"
        assert plist_path.exists()
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        assert plist["Label"] == "com.iris.task-panel"
        assert plist["ProgramArguments"][-4:] == ["-m", "iris.taskpanel.daemon",
                                                 "--project-root", str(tmp_path)]
        assert plist["WorkingDirectory"] == str(tmp_path)
        assert plist["RunAtLoad"] is True
        assert plist["KeepAlive"] == {"SuccessfulExit": False}
        assert "PYTHONPATH" in plist["EnvironmentVariables"]
        out = capsys.readouterr().out
        assert "launchctl load" in out

    def test_log_dir_created_by_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home",
                            staticmethod(lambda: tmp_path))
        rc = do_install(_Args(project_root=str(tmp_path)))
        assert rc == 0
        assert (tmp_path / "data" / "tasks").is_dir()


# ── TestStatus ────────────────────────────────────────────


class TestStatus:
    def test_no_pid_file(self, tmp_path, capsys):
        rc = do_status(_Args(project_root=str(tmp_path)))
        assert rc == 0
        assert "未在运行" in capsys.readouterr().out

    def test_dead_pid_residual(self, tmp_path, capsys):
        import subprocess
        import sys
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = proc.pid
        proc.wait()
        (tmp_path / "tasks").mkdir(parents=True)
        (tmp_path / "tasks" / "task-panel.pid").write_text(str(dead_pid), encoding="utf-8")
        rc = do_status(_Args(project_root=str(tmp_path)))
        assert rc == 0
        assert "未在运行" in capsys.readouterr().out

    def test_corrupted_pid_file(self, tmp_path, capsys):
        (tmp_path / "tasks").mkdir(parents=True)
        (tmp_path / "tasks" / "task-panel.pid").write_text("junk", encoding="utf-8")
        rc = do_status(_Args(project_root=str(tmp_path)))
        assert rc == 0
        assert "未在运行" in capsys.readouterr().out
