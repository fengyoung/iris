"""任务埋点 API — 上下文管理器，长任务启动/阶段/结束写状态。

用法：
    with TaskReporter("build-chunks") as r:
        r.report_phase("chunk", f"切块 {i}/{total}", progress=i / total)
    # with 正常退出 → history 记 success；异常退出 → 记 failed 并重抛

语义约定：
    正常退出      → success（写入 history 并删除 current 文件）
    异常退出      → failed（error=异常信息，异常继续向上抛，不吞）
    进程被杀/崩溃 → current 文件残留 running，由 probe 在 /api/state 时兜底判 interrupted

容错红线：所有磁盘操作失败一律静默（logging.warning），绝不向业务代码抛异常——
埋点绝不能破坏 daily-start / build-wiki 等业务命令。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from iris.taskpanel.store import TaskStatus, finalize_task, write_current
from iris.utils.paths import get_project_root
from iris.utils.shared import now_iso

logger = logging.getLogger(__name__)

# 全局禁用开关（测试隔离/用户逃生通道）：IRIS_TASK_PANEL_DISABLED=1 时全部 no-op
_DISABLED = os.environ.get("IRIS_TASK_PANEL_DISABLED", "") == "1"


def generate_task_id(name: str) -> str:
    """生成唯一 task_id：name-YYYYmmdd-HHMMSS-pid。

    含 pid 保证同名并发多实例互不冲突，时间戳前缀可排序。
    """
    return f"{name}-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}"


class TaskReporter:
    """任务状态埋点器（上下文管理器）。

    启动写 current/<task_id>.json（running），report_phase 更新阶段，
    with 退出写终态（success/failed）。
    """

    def __init__(self, name: str, *, command: str = "",
                 agent_id: Optional[str] = None,
                 task_id: Optional[str] = None,
                 data_root: Optional[Path] = None) -> None:
        """初始化埋点器。

        :param name: 任务类型名（daily-start / build-chunks / ...）
        :param command: 命令摘要，默认取 sys.argv[1:]（埋点处可覆盖为固定串）
        :param agent_id: 多 Agent 隔离标识，默认读 IRIS_AGENT_ID
        :param task_id: 显式指定，默认自动生成
        :param data_root: 数据根目录，默认 <项目根>/data
        """
        self._name = name
        self._command = command or " ".join(sys.argv[1:]) or name
        self._agent_id = agent_id or os.environ.get("IRIS_AGENT_ID", "default")
        self._task_id = task_id or generate_task_id(name)
        try:
            self._data_root = data_root or (get_project_root() / "data")
        except Exception:
            self._data_root = None  # 项目根解析失败 → 整体降级 no-op
        self._task = TaskStatus(
            task_id=self._task_id,
            name=self._name,
            command=self._command,
            agent_id=self._agent_id,
            pid=os.getpid(),
            status="running",
            started_at=now_iso(),
        )
        self._entered = False

    # ── 上下文管理器 ──────────────────────────────────────

    def __enter__(self) -> "TaskReporter":
        """启动登记：写 current 文件（失败静默，降级为 no-op 埋点）。"""
        self._entered = True
        if _DISABLED or self._data_root is None:
            return self
        try:
            write_current(self._data_root, self._task)
        except Exception as e:
            logger.warning("任务埋点启动登记失败（继续无埋点运行）: %s", e)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出终态：success / failed（异常照常上抛，不返回 True）。"""
        if not self._entered or _DISABLED or self._data_root is None:
            return
        try:
            if exc_type is None:
                finalize_task(self._data_root, self._task, "success")
            else:
                finalize_task(self._data_root, self._task, "failed",
                              error=f"{exc_type.__name__}: {exc_val}")
        except Exception as e:
            logger.warning("任务埋点终态写入失败: %s", e)
        # 不返回 True——异常继续向上传播，保持业务命令原有错误行为

    # ── 阶段上报 ──────────────────────────────────────────

    def report_phase(self, phase: str, phase_detail: str = "",
                     progress: Optional[float] = None) -> None:
        """更新任务阶段并重写 current 文件（失败静默）。

        :param phase: 机器可读阶段名（memory_sync / chunk / ...）
        :param phase_detail: 人类可读细节（"切块 128/822"）
        :param progress: 0.0~1.0，未知传 None（前端不显示进度条）
        """
        if not self._entered or _DISABLED or self._data_root is None:
            return
        self._task.phase = phase
        self._task.phase_detail = phase_detail
        self._task.progress = progress
        try:
            write_current(self._data_root, self._task)
        except Exception as e:
            logger.warning("任务埋点阶段上报失败: %s", e)

    # ── 属性 ──────────────────────────────────────────────

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def task(self) -> TaskStatus:
        return self._task
