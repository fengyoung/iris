"""任务面板守护进程 — 进程管理（start/stop/status/install）+ 常驻服务本体。

进程管理：
    do_start    daemonize 启动（subprocess.Popen + start_new_session，日志重定向 panel.log）
    do_stop     读 pid 文件 + SIGTERM + 5s 轮询
    do_status   只读探测 + /api/state 取 uptime
    do_install  生成 launchd LaunchAgent plist（开机自启，KeepAlive 崩溃自动拉起）

守护进程本体（python -m iris.taskpanel.daemon）：
    ProcessRegistry 互斥 + 显式 SIGTERM handler（stop_event）+ 分离线程 shutdown
    （shutdown() 必须在非 serve_forever 线程调用，否则死锁）
"""

from __future__ import annotations

import os
import plistlib
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from iris.core.locks import ProcessRegistry
from iris.taskpanel.probe import is_pid_alive
from iris.taskpanel.server import create_server
from iris.taskpanel.store import pid_file

DEFAULT_PORT = 8765
_READY_POLL_SEC = 0.2
_READY_POLL_MAX = 25       # 最多等 5s
_STOP_WAIT_SEC = 5.0
_LAUNCHD_LABEL = "com.iris.task-panel"


# ── 端口解析 ──────────────────────────────────────────────


def _resolve_port(port: Optional[int] = None) -> int:
    """端口优先级：--port > IRIS_TASK_PANEL_PORT > 8765；非法值抛 ValueError。"""
    if port is not None:
        value = port
    else:
        env_val = os.environ.get("IRIS_TASK_PANEL_PORT", "")
        value = int(env_val) if env_val.strip() else DEFAULT_PORT
    if not 1024 <= value <= 65535:
        raise ValueError(f"端口非法: {value}（应为 1024-65535）")
    return value


# ── 进程管理命令 ──────────────────────────────────────────


def _daemon_running(pid_path: Path, port: int) -> bool:
    """只读探测：pid 文件存在 + 进程存活 + TCP 端口在服务。

    注意：严禁用 ProcessRegistry.register() 探测——未运行时会写入假 pid 文件，
    造成「活实例」假占（live.py _probe_running 注释点名的坑）。
    """
    if not pid_path.exists():
        return False
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
    except (ValueError, OSError):
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _panel_log(project_root: Path) -> Path:
    """守护进程日志文件（stdout/stderr 重定向目标）。"""
    log_path = project_root / "data" / "tasks" / "panel.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return log_path


def do_start(args) -> int:
    """daemonize 启动守护进程。"""
    project_root = Path(args.project_root).resolve()
    data_root = project_root / "data"
    pid_path = pid_file(data_root)
    try:
        port = _resolve_port(getattr(args, "port", None))
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    if _daemon_running(pid_path, port):
        print(f"任务面板已在运行: http://127.0.0.1:{port}")
        return 0

    # 脱离终端启动（start_new_session → 关终端不杀）
    env = {**os.environ,
           "PYTHONPATH": str(project_root / "src")
           + os.pathsep + os.environ.get("PYTHONPATH", "")}
    if os.environ.get("IRIS_TASK_PANEL_PORT") or port != DEFAULT_PORT:
        env["IRIS_TASK_PANEL_PORT"] = str(port)  # 子进程按同一端口启动
    log_path = _panel_log(project_root)
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            subprocess.Popen(
                [sys.executable, "-m", "iris.taskpanel.daemon",
                 "--project-root", str(project_root)],
                cwd=str(project_root), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
    except OSError as e:
        print(f"错误: 启动守护进程失败: {e}", file=sys.stderr)
        return 1

    # 就绪轮询：pid 文件出现 + TCP 可连
    for _ in range(_READY_POLL_MAX):
        time.sleep(_READY_POLL_SEC)
        if _daemon_running(pid_path, port):
            print(f"任务面板已启动: http://127.0.0.1:{port}")
            return 0

    print(f"错误: 任务面板启动失败，最近日志（{log_path}）:", file=sys.stderr)
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()[-20:]
        for line in lines:
            print(f"  {line}", file=sys.stderr)
    except OSError:
        pass
    return 1


def do_stop(args) -> int:
    """SIGTERM 停止守护进程 + 5s 轮询等待退出。"""
    project_root = Path(args.project_root).resolve()
    data_root = project_root / "data"
    pid_path = pid_file(data_root)

    def _not_running(msg: str) -> int:
        print(msg)
        return 0

    if not pid_path.exists():
        return _not_running("任务面板未在运行")
    try:
        pid = int(pid_path.read_text().strip())
    except ValueError:
        pid_path.unlink(missing_ok=True)
        return _not_running("任务面板未在运行（已清理损坏的 pid 文件）")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return _not_running("任务面板未在运行（已清理残留的 pid 文件）")

    deadline = time.monotonic() + _STOP_WAIT_SEC
    while time.monotonic() < deadline:
        time.sleep(_READY_POLL_SEC)
        if not is_pid_alive(pid):
            print(f"任务面板已停止 (pid {pid})")
            return 0
    print(f"警告: 进程 {pid} 未在 {_STOP_WAIT_SEC:.0f}s 内退出"
          "（残留 pid 文件由下次 start 自动清理）", file=sys.stderr)
    return 1


def do_status(args) -> int:
    """只读状态：pid + 端口 + uptime（经 /api/state 获取）。"""
    project_root = Path(args.project_root).resolve()
    data_root = project_root / "data"
    pid_path = pid_file(data_root)
    port = DEFAULT_PORT
    try:
        port = _resolve_port(getattr(args, "port", None))
    except ValueError:
        pass  # status 对非法端口不失败，仅影响 URL 展示

    if not pid_path.exists():
        print("任务面板未在运行（执行 `iris task-panel start` 启动）")
        return 0
    try:
        pid = int(pid_path.read_text().strip())
    except ValueError:
        print("任务面板未在运行（pid 文件损坏，执行 start 自动清理）")
        return 0
    if not is_pid_alive(pid):
        print("任务面板未在运行（pid 文件残留，执行 start 自动清理）")
        return 0

    info = {"running": True, "pid": pid, "url": f"http://127.0.0.1:{port}"}
    try:
        with urllib.request.urlopen(f"{info['url']}/api/state", timeout=2) as resp:
            import json
            daemon = json.loads(resp.read().decode("utf-8")).get("daemon", {})
            info["uptime_sec"] = daemon.get("uptime_sec")
            info["version"] = daemon.get("version", "")
    except Exception:
        pass  # API 不通只影响 uptime 展示，不改变运行判定

    uptime = info.get("uptime_sec")
    uptime_str = ""
    if uptime is not None:
        m, s = divmod(int(uptime), 60)
        h, m = divmod(m, 60)
        uptime_str = f"，已运行 {h}小时{m}分" if h else (f"，已运行 {m}分{s}秒" if m else f"，已运行 {s}秒")
    print(f"任务面板运行中 (pid {info['pid']}，{info['url']}{uptime_str})")
    return 0


def do_install(args) -> int:
    """生成 launchd LaunchAgent plist（开机自启 + 崩溃自动拉起，不自动 load）。"""
    project_root = Path(args.project_root).resolve()
    launch_dir = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_dir / f"{_LAUNCHD_LABEL}.plist"
    log_path = _panel_log(project_root)

    plist = {
        "Label": _LAUNCHD_LABEL,
        "ProgramArguments": [sys.executable, "-m", "iris.taskpanel.daemon",
                             "--project-root", str(project_root)],
        "WorkingDirectory": str(project_root),
        "EnvironmentVariables": {"PYTHONPATH": str(project_root / "src")},
        "RunAtLoad": True,
        # 优雅退出（exit 0，stop 命令）不重启；崩溃（非零退出）自动拉起
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }
    try:
        launch_dir.mkdir(parents=True, exist_ok=True)
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)
    except OSError as e:
        print(f"错误: 生成 plist 失败: {e}", file=sys.stderr)
        return 1
    print(f"已生成开机自启配置: {plist_path}")
    print(f"启用: launchctl load {plist_path}")
    print("注意: 若 venv 路径迁移需重新执行 install")
    return 0


# ── 守护进程本体 ──────────────────────────────────────────


def _iris_protocol_version() -> str:
    """读取协议版本（面板 header 展示）。"""
    try:
        import iris
        return str(iris.__version__)
    except Exception:
        return ""


def main() -> int:
    """守护进程本体（由 python -m iris.taskpanel.daemon 执行）。

    --project-root 显式指定项目根（do_start/launchd 均传入）；
    缺省回退 get_project_root()（按包位置推断）。
    进程生命周期由 ProcessRegistry 互斥，SIGTERM/SIGINT → stop_event →
    分离线程 shutdown → 优雅退出。
    """
    import argparse as _argparse

    from iris.utils.paths import get_project_root

    _parser = _argparse.ArgumentParser(add_help=False)
    _parser.add_argument("--project-root", default=None)
    _cli_args, _ = _parser.parse_known_args()
    project_root = (Path(_cli_args.project_root).resolve()
                    if _cli_args.project_root else get_project_root())
    data_root = project_root / "data"
    try:
        port = _resolve_port()
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2

    registry = ProcessRegistry("task-panel", data_root / "tasks")
    if not registry.register():
        print("task-panel 已在运行（ProcessRegistry 拒绝重复注册）", file=sys.stderr)
        return 1

    stop_event = threading.Event()

    def _on_stop(signum, frame) -> None:  # noqa: ARG001
        """SIGTERM/SIGINT → 置位 stop_event（Python 3.13 sleep 不被信号中断的坑）。"""
        stop_event.set()

    signal.signal(signal.SIGTERM, _on_stop)
    signal.signal(signal.SIGINT, _on_stop)

    server = create_server(project_root, data_root, port, _iris_protocol_version())

    # shutdown() 必须在非 serve_forever 线程调用（否则死锁）
    def _wait_stop() -> None:
        stop_event.wait()
        server.shutdown()
        server.server_close()

    threading.Thread(target=_wait_stop, daemon=True).start()

    print(f"任务面板守护进程已启动: http://127.0.0.1:{port} (pid {os.getpid()})",
          flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        registry.unregister()
        print("任务面板守护进程已退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
