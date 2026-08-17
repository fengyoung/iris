"""任务状态数据模型与存储层 — current/ 运行中 + history.jsonl 终态。

存储结构：
    data/tasks/current/<task_id>.json   运行中任务（原子写，进程死残留由 probe 兜底）
    data/tasks/history.jsonl            终态任务（flock 串行追加，滚动保留 200 条）
    data/tasks/task-panel.pid           守护进程 pid 文件

并发模型：
    - current/ 写：atomic_write_json（tmp + os.replace），task_id 含 pid 天然不冲突
    - history 写：finalize_task 唯一入口，fcntl.flock + 锁内幂等守卫（防双线程重复追加）
    - 读：无锁，损坏行跳过容忍
"""

from __future__ import annotations

import fcntl
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.utils.shared import atomic_write_json, now_iso

logger = logging.getLogger(__name__)

_HISTORY_MAX = 200            # 滚动保留条数
_HISTORY_TRUNCATE_AT = 250    # 超过此数触发截断重写（留出余量，避免每条都重写）

# 终态集合：这些状态的任务只存在于 history，不在 current
_FINAL_STATUSES = {"success", "failed", "interrupted"}


@dataclass
class TaskStatus:
    """单条任务状态。

    status 语义：
        running      运行中（current/ 文件存在）
        success      正常结束
        failed       异常结束（error 记录异常信息）
        interrupted  进程被杀/崩溃（probe 兜底判定，error 说明原因）
    """

    task_id: str
    name: str                       # 任务类型名：daily-start / build-chunks / ...
    command: str = ""               # 完整命令行摘要
    agent_id: str = "default"
    pid: Optional[int] = None
    status: str = "running"
    phase: str = ""                 # 当前阶段（机器可读）：memory_sync / chunk / ...
    phase_detail: str = ""          # 阶段细节（人类可读）："切块 128/822"
    progress: Optional[float] = None  # 0.0~1.0，未知为 None（前端不显示进度条）
    started_at: str = ""
    ended_at: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 JSON dict。"""
        return {
            "task_id": self.task_id,
            "name": self.name,
            "command": self.command,
            "agent_id": self.agent_id,
            "pid": self.pid,
            "status": self.status,
            "phase": self.phase,
            "phase_detail": self.phase_detail,
            "progress": self.progress,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskStatus":
        """从 JSON dict 恢复（缺字段给默认值，容忍旧版本文件）。"""
        return cls(
            task_id=str(data.get("task_id", "")),
            name=str(data.get("name", "")),
            command=str(data.get("command", "")),
            agent_id=str(data.get("agent_id", "default")),
            pid=data.get("pid"),
            status=str(data.get("status", "running")),
            phase=str(data.get("phase", "")),
            phase_detail=str(data.get("phase_detail", "")),
            progress=data.get("progress"),
            started_at=str(data.get("started_at", "")),
            ended_at=str(data.get("ended_at", "")),
            error=str(data.get("error", "")),
        )


# ── 路径 ─────────────────────────────────────────────────


def tasks_dir(data_root: Path) -> Path:
    """任务面板数据根目录：<data_root>/tasks。"""
    return data_root / "tasks"


def current_dir(data_root: Path) -> Path:
    """运行中任务目录：<data_root>/tasks/current。"""
    return tasks_dir(data_root) / "current"


def history_path(data_root: Path) -> Path:
    """终态记录文件：<data_root>/tasks/history.jsonl。"""
    return tasks_dir(data_root) / "history.jsonl"


def pid_file(data_root: Path) -> Path:
    """守护进程 pid 文件：<data_root>/tasks/task-panel.pid。"""
    return tasks_dir(data_root) / "task-panel.pid"


# ── current/ 读写 ─────────────────────────────────────────


def write_current(data_root: Path, task: TaskStatus) -> None:
    """原子写入运行中状态文件。"""
    path = current_dir(data_root) / f"{task.task_id}.json"
    atomic_write_json(path, task.to_dict())


def read_current_all(data_root: Path) -> List[TaskStatus]:
    """读取全部运行中任务（损坏文件跳过），按 started_at 排序。"""
    cdir = current_dir(data_root)
    if not cdir.is_dir():
        return []
    tasks: List[TaskStatus] = []
    for f in sorted(cdir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            tasks.append(TaskStatus.from_dict(data))
        except (json.JSONDecodeError, OSError, ValueError):
            logger.warning("跳过损坏的 current 文件: %s", f)
    tasks.sort(key=lambda t: t.started_at)
    return tasks


# ── history 终态 ──────────────────────────────────────────


def finalize_task(data_root: Path, task: TaskStatus,
                  status: str, error: str = "") -> None:
    """将任务写入终态：追加 history.jsonl + 删除 current 文件。

    唯一写 history 的入口（reporter 与 probe 共用）。
    幂等：锁内守卫——current 已删或 history 已含该 task_id 时跳过，
    防双线程竞态与 unlink 失败导致的重复追加。
    """
    if status not in _FINAL_STATUSES:
        raise ValueError(f"非法终态: {status}（应为 {sorted(_FINAL_STATUSES)}）")

    cdir = current_dir(data_root)
    current_file = cdir / f"{task.task_id}.json"
    hpath = history_path(data_root)
    hpath.parent.mkdir(parents=True, exist_ok=True)

    with open(hpath, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            # 幂等守卫 1：current 已删 → 另一线程已完成，放弃
            if not current_file.exists():
                return
            # 幂等守卫 2：history 尾部已含该 task_id → 防 unlink 失败重复追加
            if _history_contains(hpath, task.task_id):
                return
            task.status = status
            task.ended_at = now_iso()
            if error:
                task.error = error
            f.write(json.dumps(task.to_dict(), ensure_ascii=False) + "\n")
            f.flush()
            # 滚动截断：超阈值 → 重写保留最后 _HISTORY_MAX 条
            if _count_lines(hpath) > _HISTORY_TRUNCATE_AT:
                _truncate_history(hpath)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    # 先写 history 再删 current：删失败只留残留，不丢终态（下次守卫拦截）
    try:
        current_file.unlink(missing_ok=True)
    except OSError:
        logger.warning("current 文件删除失败: %s", current_file)


def read_history(data_root: Path, limit: int = _HISTORY_MAX) -> List[TaskStatus]:
    """读取最近 limit 条终态记录（保持写入顺序，旧→新）。"""
    hpath = history_path(data_root)
    if not hpath.is_file():
        return []
    tasks: List[TaskStatus] = []
    with open(hpath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tasks.append(TaskStatus.from_dict(json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                logger.warning("跳过损坏的 history 行")
    return tasks[-limit:]


# ── 内部辅助 ──────────────────────────────────────────────


def _history_contains(hpath: Path, task_id: str) -> bool:
    """检查 history 尾部 50 行是否已含该 task_id（锁内调用）。"""
    try:
        with open(hpath, "r", encoding="utf-8") as f:
            lines = f.readlines()[-50:]
    except OSError:
        return False
    for line in lines:
        try:
            if json.loads(line).get("task_id") == task_id:
                return True
        except json.JSONDecodeError:
            continue
    return False


def _count_lines(hpath: Path) -> int:
    """统计行数（锁内调用，容忍文件短暂不可读）。"""
    try:
        with open(hpath, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _truncate_history(hpath: Path) -> None:
    """重写 history 保留最后 _HISTORY_MAX 条（锁内调用）。"""
    try:
        with open(hpath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(hpath, "w", encoding="utf-8") as f:
            f.writelines(lines[-_HISTORY_MAX:])
        logger.info("history.jsonl 滚动截断: %d → %d 条", len(lines), _HISTORY_MAX)
    except OSError:
        logger.warning("history 滚动截断失败（下次终态写入时重试）")
