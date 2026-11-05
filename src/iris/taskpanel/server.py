"""本地控制台 HTTP 服务：保留任务状态协议，增加目录与受保护的任务操作。

只监听回环地址；全部请求校验 Host，写接口另校验 Origin、启动令牌和 JSON。
业务执行交给 CLI 子进程，静态资源使用固定白名单。
"""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import cast
from urllib.parse import parse_qs, urlsplit
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from iris.core.locks import FileLockError
from iris.taskpanel import probe
from iris.taskpanel.catalog import CatalogError, catalog_payload
from iris.taskpanel.runner import JobRunner, JobRunnerError, JobConflict, JobNotFound
from iris.taskpanel.overview import OverviewProvider
from iris.taskpanel.wizards import WizardService, WizardError
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        self._send(
            code,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _trusted_host(self) -> bool:
        port = cast("TaskPanelServer", self.server).state.port
        return self.headers.get("Host", "") in {
            f"127.0.0.1:{port}",
            f"localhost:{port}",
        }

    def do_GET(self) -> None:  # noqa: N802
        if not self._trusted_host():
            self._json(403, {"error": "untrusted_host"})
            return
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._handle_index()
        elif path == "/api/state":
            self._handle_state()
        elif path == "/api/overview":
            server = cast("TaskPanelServer", self.server)
            try:
                self._json(200, server.overview.get(server.runner.list_jobs()))
            except (JobRunnerError, FileLockError, OSError):
                self._json(503, {"error": "任务存储暂不可用，请重试"})
        elif path == "/api/catalog":
            self._json(200, catalog_payload())
        elif path in ("/app.js", "/app.css", "/static/app.js", "/static/app.css"):
            asset = _STATIC_INDEX.parent / path.rsplit("/", 1)[-1]
            if asset.is_file():
                kind = "text/javascript" if path.endswith(".js") else "text/css"
                self._send(200, asset.read_bytes(), kind + "; charset=utf-8")
            else:
                self._json(404, {"error": "not_found"})
        else:
            self._job_request(path, write=False)

    def do_POST(self) -> None:  # noqa: N802
        server = cast("TaskPanelServer", self.server)
        port = server.state.port
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        token = self.headers.get("X-Iris-Token", "")
        if (
            not self._trusted_host()
            or (origin is not None and origin not in allowed)
            or not secrets.compare_digest(
                token.encode("utf-8"), server.token.encode("ascii")
            )
        ):
            self._json(403, {"error": "forbidden"})
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "expected_json"})
            return
        self._job_request(urlsplit(self.path).path, write=True)

    def _body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > 1048576:
            raise ValueError("请求体过大")
        self.connection.settimeout(10)
        try:
            raw = self.rfile.read(size)
        except TimeoutError:
            raise ValueError("读取请求体超时") from None
        if len(raw) != size:
            raise ValueError("请求体长度不符")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("请求必须是 JSON 对象")
        return payload

    def _job_request(self, path: str, *, write: bool) -> None:
        runner = self.server.runner  # type: ignore[attr-defined]
        parts = path.strip("/").split("/")
        try:
            if parts[:2] == ["api", "wizards"] and len(parts) == 3:
                wizard = cast("TaskPanelServer", self.server).wizards
                if write:
                    result = wizard.action(parts[2], self._body())
                else:
                    query = parse_qs(urlsplit(self.path).query)
                    result = wizard.read(
                        parts[2], job_id=query.get("job_id", [None])[0]
                    )
                self._json(200, result)
                return
            if parts[:2] != ["api", "jobs"]:
                self._json(404, {"error": "not_found"})
                return
            payload = self._body() if write else {}
            result = self._dispatch_job(runner, parts[2:], write, payload)
            self._json(200, result)
        except JobNotFound as exc:
            self._json(404, {"error": str(exc)})
        except JobConflict as exc:
            self._json(409, {"error": str(exc)})
        except (
            CatalogError,
            JobRunnerError,
            WizardError,
            ValueError,
            TypeError,
        ) as exc:
            self._json(400, {"error": str(exc)})
        except (FileLockError, OSError):
            self._json(503, {"error": "本地数据暂不可用，请重试"})

    def _dispatch_job(self, runner, parts: list[str], write: bool, payload: dict):
        if not parts:
            if not write:
                return {"jobs": runner.list_jobs()}
            command = payload.get("command")
            params = payload.get("params", {})
            if not isinstance(command, str) or not isinstance(params, dict):
                raise ValueError("command 必须为文本，params 必须为对象")
            return runner.start(command, params)
        if len(parts) == 1 and not write:
            return runner.get(parts[0])
        if len(parts) == 2:
            if write and parts[1] == "cancel":
                return runner.cancel(parts[0])
            if write and parts[1] == "rerun":
                return runner.rerun(parts[0])
            if not write and parts[1] == "logs":
                query = parse_qs(urlsplit(self.path).query)
                return runner.read_log(
                    parts[0],
                    stream=query.get("stream", ["stdout"])[0],
                    offset=int(query.get("offset", ["0"])[0]),
                )
        raise JobNotFound("not_found")

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
        self._send(
            200,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )


class TaskPanelServer(ThreadingHTTPServer):
    """线程化 HTTP 服务：daemon_threads 防止非 daemon 请求线程阻塞 server_close。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self, addr, project_root: Path, data_root: Path, port: int, version: str
    ) -> None:
        super().__init__(addr, _Handler)
        self.token = secrets.token_urlsafe(32)
        self.runner = JobRunner(project_root, data_root)
        self.overview = OverviewProvider(project_root, data_root)
        self.wizards = WizardService(project_root, data_root, self.runner)
        index_html = _STATIC_INDEX.read_bytes().replace(
            b"__IRIS_TOKEN__", self.token.encode("ascii")
        )
        self.state = PanelState(
            project_root=project_root,
            data_root=data_root,
            port=self.server_address[1],
            version=version,
            started_at=_now_local_iso(),
            index_html=index_html,
            boot_mono=time.monotonic(),
        )


def create_server(
    project_root: Path, data_root: Path, port: int = 8765, version: str = ""
) -> TaskPanelServer:
    """创建监听 127.0.0.1:<port> 的本地控制台。"""
    return TaskPanelServer(("127.0.0.1", port), project_root, data_root, port, version)
