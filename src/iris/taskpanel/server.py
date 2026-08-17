"""任务面板 HTTP 服务 — stdlib ThreadingHTTPServer，零外部依赖。

路由：
    GET /           单页面板（static/index.html，启动时读入内存）
    GET /api/state  任务状态 JSON（每请求实时读盘 + 顺带执行 stale 判定）
    其余            404

线程模型：daemon_threads=True 的请求线程，只读 + probe（finalize 有 flock 幂等守卫）；
每请求实时读盘不缓存任务数据，天然一致。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from iris.taskpanel import probe
from iris.taskpanel.store import read_current_all, read_history

_STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


def _now_local_iso() -> str:
    """本地时间 ISO 字符串（面板展示用，与任务 UTC 时间戳区分）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class PanelState:
    """守护进程共享状态（挂 server 实例，handler 通过 self.server.state 访问）。

    不缓存任务数据——每请求实时读盘，天然一致。
    """

    project_root: Path
    data_root: Path
    port: int
    version: str
    started_at: str
    index_html: bytes = b""
    boot_mono: float = 0.0  # 启动时刻 time.monotonic()，用于计算 uptime


class _Handler(BaseHTTPRequestHandler):
    """请求处理器（每请求实例化，覆写 log_message 防 2s 轮询刷日志）。"""

    server_version = "IrisTaskPanel/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        """静默——避免每个 2s 轮询刷一行日志。"""

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._handle_index()
        elif self.path == "/api/state":
            self._handle_state()
        else:
            self._send(404, json.dumps({"error": "not_found"}).encode("utf-8"),
                       "application/json; charset=utf-8")

    def _handle_index(self) -> None:
        state: PanelState = self.server.state  # type: ignore[attr-defined]
        self._send(200, state.index_html, "text/html; charset=utf-8")

    def _handle_state(self) -> None:
        state: PanelState = self.server.state  # type: ignore[attr-defined]
        data_root = state.data_root
        # stale 判定：running 但 pid 死的任务 → interrupted（面板轮询即节拍）
        interrupted_now = probe.probe_and_finalize_stale(data_root)
        running = [t.to_dict() for t in read_current_all(data_root)]
        history = [t.to_dict() for t in read_history(data_root)]
        watchdog = probe.probe_watchdogs(data_root)
        agents = sorted({t["agent_id"] for t in running} | {"default"})
        payload = {
            "version": state.version,
            "daemon": {
                "running": True,
                "pid": os.getpid(),
                "port": state.port,
                "started_at": state.started_at,
                "uptime_sec": int(time.monotonic() - state.boot_mono),
            },
            "running": running,
            "history": history,
            "interrupted_now": interrupted_now,
            "watchdog": watchdog,
            "agents": agents,
        }
        self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")


class TaskPanelServer(ThreadingHTTPServer):
    """线程化 HTTP 服务：daemon_threads 防止非 daemon 请求线程阻塞 server_close。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, project_root: Path, data_root: Path,
                 port: int, version: str) -> None:
        super().__init__(addr, _Handler)
        self.state = PanelState(
            project_root=project_root,
            data_root=data_root,
            port=port,
            version=version,
            started_at=_now_local_iso(),
            index_html=_STATIC_INDEX.read_bytes(),
            boot_mono=time.monotonic(),
        )


def create_server(project_root: Path, data_root: Path,
                  port: int = 8765, version: str = "") -> TaskPanelServer:
    """创建监听 127.0.0.1:<port> 的面板服务（只读本机展示）。"""
    return TaskPanelServer(("127.0.0.1", port), project_root, data_root, port, version)
