"""taskpanel 守护进程集成测试 — start→status→stop 全链路 + 埋点→面板 e2e。

真实启动守护子进程（python -m iris.taskpanel.daemon），
通过 127.0.0.1 真实 HTTP 断言。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from iris.taskpanel.daemon import _resolve_port
from iris.taskpanel.reporter import TaskReporter
from iris.taskpanel.store import history_path, pid_file, read_history

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def task_panel(tmp_path, monkeypatch):
    """启动真实守护进程（独立项目根 tmp_path），yield 控制对象。"""
    # 独立端口避免冲突
    port = _resolve_port() + 1000 + (os.getpid() % 500)
    # 守护进程从 tmp_path 找不到 src → 用真实项目根的 PYTHONPATH
    env = {
        **os.environ,
        "PYTHONPATH": str(_PROJECT_ROOT / "src"),
        "IRIS_TASK_PANEL_PORT": str(port),
    }
    log = open(tmp_path / "panel.log", "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-m", "iris.taskpanel.daemon",
         "--project-root", str(tmp_path)],
        cwd=str(tmp_path), env=env,
        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    # 就绪轮询
    deadline = time.monotonic() + 10
    ready = False
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/api/state", timeout=1):
                ready = True
                break
        except Exception:
            time.sleep(0.2)
    assert ready, "守护进程 10s 内未就绪"
    yield {
        "proc": proc, "port": port, "base_url": base_url,
        "tmp_path": tmp_path, "log": log,
    }
    # 清理：SIGTERM + 等待退出
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    log.close()


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── TestDaemonLifecycle ───────────────────────────────────


class TestDaemonLifecycle:
    def test_state_endpoint_alive(self, task_panel):
        payload = _get_json(task_panel["base_url"] + "/api/state")
        assert payload["daemon"]["running"] is True
        assert payload["daemon"]["port"] == task_panel["port"]
        assert payload["running"] == []

    def test_index_served(self, task_panel):
        with urllib.request.urlopen(task_panel["base_url"] + "/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
        assert "Iris 任务面板" in body

    def test_reporter_lifecycle_visible(self, task_panel):
        """埋点 → 面板可见 → 终态归档 全链路。"""
        tmp_path = task_panel["tmp_path"]
        base_url = task_panel["base_url"]
        # 1. 运行中：埋点启动 + 阶段更新
        with TaskReporter("daily-start", command="daily-start",
                          data_root=tmp_path / "data") as r:
            r.report_phase("memory_sync", "第1/8阶段：记忆同步", progress=0.125)
            payload = _get_json(base_url + "/api/state")
            running = payload["running"]
            assert len(running) == 1
            assert running[0]["name"] == "daily-start"
            assert running[0]["phase"] == "memory_sync"
            assert running[0]["progress"] == pytest.approx(0.125)
        # 2. 退出后：current 清空 + history 记 success
        payload = _get_json(base_url + "/api/state")
        assert payload["running"] == []
        assert payload["history"][-1]["status"] == "success"

    def test_stale_task_interrupted_via_panel(self, task_panel):
        """进程被杀（无 __exit__）→ 面板请求兜底判 interrupted。"""
        tmp_path = task_panel["tmp_path"]
        base_url = task_panel["base_url"]
        # 模拟被杀进程残留：直接写一个 dead pid 的 current 文件
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        dead_pid = proc.pid
        proc.wait()
        from iris.taskpanel.store import TaskStatus, write_current
        write_current(tmp_path / "data", TaskStatus(
            task_id="dead-task-1", name="build-wiki", pid=dead_pid,
            status="running", started_at="2026-08-16T10:00:00+00:00"))
        payload = _get_json(base_url + "/api/state")
        assert payload["interrupted_now"] == ["dead-task-1"]
        assert payload["running"] == []
        assert read_history(tmp_path / "data")[-1].status == "interrupted"


# ── TestCliScript ─────────────────────────────────────────


class TestCliScript:
    def test_start_stop_roundtrip(self, tmp_path):
        """scripts/task_panel.py 子进程 start → status → stop。"""
        script = _PROJECT_ROOT / "scripts" / "task_panel.py"
        port = _resolve_port() + 2000 + (os.getpid() % 500)
        env = {**os.environ, "IRIS_TASK_PANEL_PORT": str(port)}

        def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(script), "--project-root", str(tmp_path), *args],
                capture_output=True, text=True, timeout=30, env=env, cwd=str(cwd),
            )

        try:
            # start（daemon 子进程 cwd=tmp_path，但 src 在真实项目 → PYTHONPATH）
            env["PYTHONPATH"] = str(_PROJECT_ROOT / "src")
            r1 = _run("start", cwd=tmp_path)
            assert r1.returncode == 0, r1.stderr
            assert "已启动" in r1.stdout

            # 重复 start 幂等
            r2 = _run("start", cwd=tmp_path)
            assert r2.returncode == 0
            assert "已在运行" in r2.stdout

            # status
            r3 = _run("status", cwd=tmp_path)
            assert r3.returncode == 0
            assert "运行中" in r3.stdout

            # stop
            r4 = _run("stop", cwd=tmp_path)
            assert r4.returncode == 0
            assert "已停止" in r4.stdout

            # stop 后 status → 未运行
            r5 = _run("status", cwd=tmp_path)
            assert "未在运行" in r5.stdout

            # 守护进程优雅退出后 pid 文件清理
            assert not pid_file(tmp_path / "data").exists()
        finally:
            # 兜底清理：杀掉可能残留的守护进程
            pid_path = pid_file(tmp_path / "data")
            if pid_path.exists():
                try:
                    pid = int(pid_path.read_text().strip())
                    os.kill(pid, 9)
                except (ValueError, OSError):
                    pass
