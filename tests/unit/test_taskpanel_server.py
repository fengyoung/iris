"""taskpanel.server 测试 — 路由 / API JSON 结构 / stale 触发 / 并发请求。

用 port=0 随机端口 + urllib 真实 HTTP 断言（stdlib http.server 纯逻辑，无外部依赖）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

from iris.taskpanel.server import create_server
from iris.taskpanel.store import TaskStatus, read_history, write_current


@pytest.fixture()
def server(tmp_path):
    """启动真实服务（独立线程），yield (server, base_url)，退出时关闭。"""
    srv = create_server(tmp_path, tmp_path, port=0, version="3.27.0")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1})
    thread.daemon = True
    thread.start()
    port = srv.server_address[1]
    yield srv, f"http://127.0.0.1:{port}"
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read()


def _get_json(url: str):
    status, body = _get(url)
    return status, json.loads(body.decode("utf-8"))


def _make_running(task_id: str, pid: int, agent_id: str = "default") -> TaskStatus:
    return TaskStatus(task_id=task_id, name="build-chunks", command="build-chunks",
                      agent_id=agent_id, pid=pid, status="running",
                      phase="chunk", phase_detail="切块 1/10", progress=0.1,
                      started_at="2026-08-16T10:00:00+00:00")


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = proc.pid
    proc.wait()
    return pid


# ── TestRoutes ────────────────────────────────────────────


class TestRoutes:
    def test_index_html(self, server):
        _, url = server
        status, body = _get(url + "/")
        assert status == 200
        assert b"Iris" in body and b"text/html" not in body[:200] or b"<!DOCTYPE" in body
        assert b"task-panel" in body.lower() or b"fetch" in body

    def test_state_json_structure(self, server):
        _, url = server
        status, payload = _get_json(url + "/api/state")
        assert status == 200
        for key in ("version", "daemon", "running", "history",
                    "interrupted_now", "watchdog", "agents"):
            assert key in payload
        assert payload["version"] == "3.27.0"
        assert payload["daemon"]["running"] is True
        assert isinstance(payload["daemon"]["port"], int)

    def test_404(self, server):
        _, url = server
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(url + "/nope", timeout=5)
        assert excinfo.value.code == 404


# ── TestStateContent ──────────────────────────────────────


class TestStateContent:
    def test_running_tasks_visible(self, server, tmp_path):
        _, url = server
        write_current(tmp_path, _make_running("t-1", os.getpid()))
        _, payload = _get_json(url + "/api/state")
        assert [t["task_id"] for t in payload["running"]] == ["t-1"]
        assert payload["running"][0]["phase"] == "chunk"

    def test_history_visible(self, server, tmp_path):
        _, url = server
        write_current(tmp_path, _make_running("t-1", os.getpid()))
        from iris.taskpanel.store import finalize_task
        finalize_task(tmp_path, _make_running("t-1", os.getpid()), "success")
        _, payload = _get_json(url + "/api/state")
        assert [h["status"] for h in payload["history"]] == ["success"]

    def test_stale_task_interrupted_on_request(self, server, tmp_path):
        """running 但 pid 死 → 请求时兜底判 interrupted 并入 history。"""
        _, url = server
        write_current(tmp_path, _make_running("dead-1", _dead_pid()))
        _, payload = _get_json(url + "/api/state")
        assert payload["interrupted_now"] == ["dead-1"]
        assert payload["running"] == []
        assert read_history(tmp_path)[0].status == "interrupted"

    def test_watchdog_present(self, server):
        _, url = server
        _, payload = _get_json(url + "/api/state")
        assert any(w["name"] == "asr-corrector" for w in payload["watchdog"])

    def test_agents_collected(self, server, tmp_path):
        _, url = server
        write_current(tmp_path, _make_running("t-1", os.getpid(), agent_id="agent-b"))
        _, payload = _get_json(url + "/api/state")
        assert "agent-b" in payload["agents"]
        assert "default" in payload["agents"]


# ── TestConcurrentRequests ────────────────────────────────


class TestConcurrentRequests:
    def test_concurrent_state_requests(self, server, tmp_path):
        """10 线程并发 /api/state 无异常、无重复 finalize。"""
        _, url = server
        write_current(tmp_path, _make_running("t-1", _dead_pid()))
        errors: list[Exception] = []

        def _worker():
            try:
                status, payload = _get_json(url + "/api/state")
                assert status == 200
                assert "daemon" in payload
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        # 幂等：stale 任务只 finalize 一次
        assert len(read_history(tmp_path)) == 1
