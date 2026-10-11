"""VocoType 进程控制：热词表只在 App 启动时加载，改完必须重启才生效。

``--deploy`` 把热词写进 ``~/Library/Application Support/VocoType/hotwords.txt``，
但 VocoType 的日志显示热词处理器只在应用启动时初始化（``初始化热词处理器``），
此后不再重载——**写完不重启等于没写**。替换词典与 Prompt 不同：它们由 Iris 的
asr-corrector 持有且每 5 秒热加载，所以只需重启 VocoType。

退出走 AppleEvent（``quit app``）而非 ``pkill``：录音/转写可能正在收尾，强杀会
丢掉那一段。整个流程带超时确认，失败显式返回错误而不是静默当作成功。
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Dict

logger = logging.getLogger(__name__)

APP_NAME = "VocoType"

#: 发出退出请求后等待进程消失的上限
QUIT_TIMEOUT = 20.0
#: 重新拉起后等待进程出现的上限
START_TIMEOUT = 20.0
#: 轮询间隔
_POLL_INTERVAL = 0.5


def _run(cmd: list, timeout: float = 15.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def is_running() -> bool:
    """VocoType 是否正在运行（按进程名精确匹配）。"""
    try:
        return _run(["pgrep", "-x", APP_NAME]).returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("检测 VocoType 进程失败: %s", exc)
        return False


def _wait(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL)
    return predicate()


def restart(*, quit_timeout: float = QUIT_TIMEOUT,
            start_timeout: float = START_TIMEOUT) -> Dict[str, Any]:
    """优雅退出并重新拉起 VocoType。

    Returns:
        ``{"status": ..., "detail": ...}``，status 取：
        ``not_running``（未在运行，无需重启——下次打开自会读到新配置）
        ``restarted`` / ``quit_failed`` / ``start_failed``
    """
    if not is_running():
        return {"status": "not_running",
                "detail": "VocoType 未在运行，新配置将在下次启动时生效"}

    try:
        quit_proc = _run(["osascript", "-e", f'quit app "{APP_NAME}"'])
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "quit_failed", "detail": f"退出请求发送失败：{exc}"}
    if quit_proc.returncode != 0:
        return {"status": "quit_failed",
                "detail": f"退出请求被拒：{(quit_proc.stderr or '').strip()[:200]}"}

    if not _wait(lambda: not is_running(), quit_timeout):
        return {"status": "quit_failed",
                "detail": f"等待 {quit_timeout:.0f}s 后进程仍在，未强制终止（可能正在录音）"}

    try:
        open_proc = _run(["open", "-a", APP_NAME])
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "start_failed", "detail": f"拉起失败：{exc}"}
    if open_proc.returncode != 0:
        return {"status": "start_failed",
                "detail": f"拉起失败：{(open_proc.stderr or '').strip()[:200]}"}

    if not _wait(is_running, start_timeout):
        return {"status": "start_failed",
                "detail": f"已发出启动请求，但 {start_timeout:.0f}s 内未检测到进程"}

    return {"status": "restarted", "detail": "已重启，新热词表生效"}
