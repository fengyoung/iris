"""进程探测兜底 — ps 存活判定 + stale 兜底 + 未埋点常驻进程 watchdog。

职责：
    1. is_pid_alive / process_command — 基础探测原语
    2. probe_and_finalize_stale — current/ 中 running 但 pid 已死的任务判 interrupted
       （进程被杀/崩溃时 TaskReporter 的 __exit__ 不会执行，此处兜底）
    3. probe_registered_process — 未埋点常驻进程（asr-corrector 等）只读探测

stale 判定时机：由 server 每次 /api/state 请求顺带执行（面板 2s 轮询即节拍），
不做独立定时线程。

macOS 兼容：只使用 `ps -p <pid> -o command=`（BSD/GNU 通用，live.py:70 先例），
禁用 ps aux/ps -ef 列解析（列格式随平台变化）。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from iris.taskpanel.store import (
    TaskStatus,
    finalize_task,
    read_current_all,
)

logger = logging.getLogger(__name__)

# 未埋点的常驻进程清单：pid 文件在 <data_root>/<name>.pid（ProcessRegistry 惯例），
# 仅做只读探测（running/未运行 + pid），无阶段信息。
WATCHDOG_PROCESSES = ["asr-corrector"]

_PS_TIMEOUT_SEC = 2


def is_pid_alive(pid: int) -> bool:
    """检查进程是否存活（os.kill(pid, 0)，微秒级零子进程开销）。"""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def process_command(pid: int) -> str:
    """获取进程命令行（ps -p <pid> -o command=），失败返回空串。"""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=_PS_TIMEOUT_SEC,
        ).stdout
        return out.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


# ── stale 兜底 ────────────────────────────────────────────


def probe_task(task: TaskStatus) -> TaskStatus:
    """判定单任务：running 且 pid 死 → 标记 interrupted；否则原样返回。

    pid 复用罕见场景保守处理：os.kill 存活即视为 running（不误杀）。
    """
    if task.status == "running" and task.pid is not None and not is_pid_alive(task.pid):
        task.status = "interrupted"
        task.ended_at = ""  # 由 finalize_task 补全
        task.error = "进程被终止或崩溃（pid 不存在）"
    return task


def probe_and_finalize_stale(data_root: Path) -> List[str]:
    """遍历 current/：running 且 pid 死的任务 finalize 为 interrupted。

    返回本次被判中断的 task_id 列表（供前端黄条提示）。
    """
    interrupted_ids: List[str] = []
    for task in read_current_all(data_root):
        probed = probe_task(task)
        if probed.status == "interrupted":
            try:
                finalize_task(data_root, task, "interrupted",
                              error=task.error)
                interrupted_ids.append(task.task_id)
            except Exception as e:
                logger.warning("stale 任务终态写入失败 %s: %s", task.task_id, e)
    return interrupted_ids


# ── watchdog（未埋点常驻进程）──────────────────────────────


def probe_registered_process(name: str, data_root: Path) -> Optional[Dict[str, object]]:
    """只读探测 pid 文件注册的常驻进程（live.py _probe_running 同款逻辑）。

    返回 {"name", "pid", "status"} 或 None（未运行）。
    """
    pid_file = Path(data_root) / f"{name}.pid"
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None
    if not is_pid_alive(pid):
        return None
    return {"name": name, "pid": pid, "status": "running"}


def probe_watchdogs(data_root: Path) -> List[Dict[str, object]]:
    """探测全部 watchdog 进程。"""
    found: List[Dict[str, object]] = []
    for name in WATCHDOG_PROCESSES:
        info = probe_registered_process(name, data_root)
        if info is not None:
            found.append(info)
        else:
            found.append({"name": name, "pid": None, "status": "stopped"})
    return found
