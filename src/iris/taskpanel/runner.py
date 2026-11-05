"""Persistent console CLI jobs; only owned child processes can be cancelled."""

from __future__ import annotations

import codecs
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from iris.core.exceptions import IrisRuntimeError
from iris.core.locks import FileLock
from iris.taskpanel.catalog import CatalogError, build_argv, get_command
from iris.taskpanel.probe import is_pid_alive
from iris.taskpanel.store import read_current_all
from iris.utils.shared import atomic_write_json, now_iso


class JobRunnerError(IrisRuntimeError):
    """A console job operation failed."""


class JobConflict(JobRunnerError):
    """Capacity, exclusivity or process ownership prevents the operation."""


class JobNotFound(JobRunnerError):
    """The requested job does not exist."""


class JobRunner:
    """Run up to four commands; constructor injection is intended for tests.

    Records survive restarts, but unknown exit codes never imply success.
    Log offsets and limits count bytes. All return values are JSON-compatible.
    """

    def __init__(
        self,
        project_root: Path,
        data_root: Path,
        *,
        cli_script: Path | None = None,
        python: str | None = None,
        cancel_timeout: float = 3.0,
    ):
        self.project_root = Path(project_root).resolve()
        self.data_root = Path(data_root).resolve()
        self.cli_script = (
            Path(cli_script).resolve()
            if cli_script is not None
            else self.project_root / "scripts" / "run_cli.py"
        )
        self.python = python or sys.executable
        self.cancel_timeout = max(0.0, float(cancel_timeout))
        self.jobs_dir = self.data_root / "tasks" / "console" / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.jobs_dir.parent / "runner"
        self._mutex = threading.RLock()
        self._children: dict[str, subprocess.Popen] = {}

    def _path(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise JobNotFound("任务不存在")
        return self.jobs_dir / f"{job_id}.json"

    def _load(self, job_id: str) -> dict[str, Any]:
        try:
            job = json.loads(self._path(job_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise JobNotFound(f"任务不存在: {job_id}") from None
        except (OSError, ValueError) as exc:
            raise JobRunnerError(f"无法读取任务: {job_id}") from exc
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise JobRunnerError(f"任务记录损坏: {job_id}")
        return job

    def _save(self, job: dict[str, Any]) -> None:
        try:
            atomic_write_json(self._path(job["id"]), job)
        except OSError as exc:
            raise JobRunnerError("无法持久化任务") from exc

    @staticmethod
    def _alive(pid: Any) -> bool:
        return (
            isinstance(pid, int)
            and not isinstance(pid, bool)
            and pid > 0
            and is_pid_alive(pid)
        )

    def _refresh(self, job: dict[str, Any]) -> dict[str, Any]:
        child = self._children.get(job["id"])
        if job.get("status") == "running":
            if child is not None:
                code = child.poll()
                if code is not None:
                    job.update(
                        status="success" if code == 0 else "failed",
                        returncode=code,
                        ended_at=now_iso(),
                        can_cancel=False,
                    )
                    self._save(job)
                    self._children.pop(job["id"], None)
            elif not self._alive(job.get("pid")):
                job.update(
                    status="interrupted",
                    ended_at=now_iso(),
                    error="Runner 已重启或进程已丢失，无法确认退出码",
                )
                self._save(job)
        job["can_cancel"] = job.get("status") == "running" and child is not None
        return job

    def _all(self) -> list[dict[str, Any]]:
        jobs = [
            self._refresh(self._load(path.stem))
            for path in self.jobs_dir.glob("*.json")
        ]
        return sorted(jobs, key=lambda job: job.get("started_at", ""), reverse=True)

    def _admit(self, exclusive: str) -> None:
        running = [job for job in self._all() if job["status"] == "running"]
        if len(running) >= 4:
            raise JobConflict("最多同时运行 4 个控制台任务")
        if not exclusive:
            return
        if any(job.get("exclusive") == exclusive for job in running):
            raise JobConflict(f"互斥组已有运行任务: {exclusive}")
        for task in read_current_all(self.data_root):
            if task.status != "running" or not self._alive(task.pid):
                continue
            try:
                group = get_command(task.name).exclusive
            except CatalogError:
                continue
            if group == exclusive:
                raise JobConflict(f"互斥组已有 Reporter 任务: {task.name}")

    def start(
        self, command: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Validate and launch without a shell; return the new record."""
        if params is not None and not isinstance(params, dict):
            raise JobRunnerError("params 必须是对象")
        try:
            saved_params = json.loads(json.dumps(params or {}, ensure_ascii=False))
            argv = build_argv(command, saved_params)
            spec = get_command(command)
        except (CatalogError, TypeError, ValueError) as exc:
            raise JobRunnerError(str(exc)) from exc
        with self._mutex, FileLock(self._lock_path):
            self._admit(spec.exclusive)
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "command": command,
                "params": saved_params,
                "argv": [self.python, str(self.cli_script), *argv],
                "exclusive": spec.exclusive,
                "status": "running",
                "pid": None,
                "started_at": now_iso(),
                "ended_at": "",
                "returncode": None,
                "error": "",
                "can_cancel": False,
            }
            self._save(job)
            child = None
            try:
                with (
                    self._log_path(job_id, "stdout").open("wb") as stdout,
                    self._log_path(job_id, "stderr").open("wb") as stderr,
                ):
                    child = subprocess.Popen(
                        job["argv"],
                        cwd=self.project_root,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        shell=False,
                        start_new_session=True,
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    )
                self._children[job_id] = child
                job.update(pid=child.pid, can_cancel=True)
                self._save(job)
            except (OSError, JobRunnerError) as exc:
                if child is not None:
                    self._terminate(child)
                    self._children.pop(job_id, None)
                job.update(
                    status="failed",
                    ended_at=now_iso(),
                    error=str(exc),
                    can_cancel=False,
                )
                self._save(job)
                raise JobRunnerError(f"启动任务失败: {exc}") from exc
            threading.Thread(
                target=self._watch,
                args=(job_id, child),
                daemon=True,
                name=f"console-job-{job_id}",
            ).start()
            return dict(job)

    def _watch(self, job_id: str, child: subprocess.Popen) -> None:
        while True:
            with self._mutex, FileLock(self._lock_path):
                self._refresh(self._load(job_id))
                if child.poll() is not None:
                    self._children.pop(job_id, None)
                    return
            time.sleep(0.05)

    def list_jobs(self) -> list[dict[str, Any]]:
        """Newest-first records, with child exit statuses refreshed."""
        with self._mutex, FileLock(self._lock_path):
            return self._all()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._mutex, FileLock(self._lock_path):
            return self._refresh(self._load(job_id))

    def _log_path(self, job_id: str, stream: str) -> Path:
        self._path(job_id)
        if stream not in {"stdout", "stderr"}:
            raise JobRunnerError("stream 必须是 stdout 或 stderr")
        return self.jobs_dir / f"{job_id}.{stream}.log"

    def read_log(
        self, job_id: str, stream: str = "stdout", offset: int = 0, limit: int = 65536
    ) -> dict[str, Any]:
        """Read at most 1 MiB; offset/next_offset is the next byte to read."""
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 1048576
        ):
            raise JobRunnerError("offset 必须非负，limit 必须在 1..1048576 内")
        job = self.get(job_id)
        path = self._log_path(job_id, stream)
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read(limit)
                size = os.fstat(handle.fileno()).st_size
        except FileNotFoundError:
            data, size = b"", 0
        except OSError as exc:
            raise JobRunnerError("读取日志失败") from exc
        final = job["status"] != "running" and offset + len(data) >= size
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        text = decoder.decode(data, final=final)
        pending = len(decoder.getstate()[0])
        # A tiny page must still make progress when its first character spans
        # several bytes. Read at most three extra bytes to finish that character.
        if pending and pending == len(data) and offset + len(data) < size:
            with path.open("rb") as handle:
                handle.seek(offset + len(data))
                for _ in range(3):
                    extra = handle.read(1)
                    if not extra:
                        break
                    data += extra
                    text += decoder.decode(extra, final=False)
                    if not decoder.getstate()[0]:
                        break
            pending = len(decoder.getstate()[0])
        next_offset = offset + len(data) - pending
        return {
            "text": text,
            "offset": next_offset,
            "next_offset": next_offset,
            "start_offset": offset,
            "eof": next_offset >= size,
            "stream": stream,
        }

    logs = read_log

    def _terminate(self, child: subprocess.Popen) -> None:
        if child.poll() is not None:
            return
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        # Do not reap the leader during the grace period. Its PID stays reserved,
        # even if TERM exits the leader while a descendant ignores the signal.
        # Every poll/wait in this runner is serialized by _mutex.
        time.sleep(self.cancel_timeout)
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # macOS can reject a signal to a zombie-only group. No further
            # signals are sent after reaping; a still-running leader preserves
            # the error instead of reporting cancellation success.
            if child.poll() is None:
                raise
        child.wait(timeout=max(1.0, self.cancel_timeout))

    def cancel(self, job_id: str) -> dict[str, Any]:
        """TERM then bounded KILL, only for a child owned by this runner."""
        with self._mutex, FileLock(self._lock_path):
            job = self._refresh(self._load(job_id))
            if job["status"] != "running":
                return job
            child = self._children.get(job_id)
            if child is None:
                raise JobConflict(
                    "无法取消非当前 Runner 启动的进程（PID 身份未经确认）"
                )
            try:
                self._terminate(child)
            except (OSError, subprocess.SubprocessError) as exc:
                raise JobRunnerError(f"取消任务失败: {exc}") from exc
            job.update(
                status="cancelled",
                returncode=child.returncode,
                ended_at=now_iso(),
                can_cancel=False,
            )
            self._save(job)
            self._children.pop(job_id, None)
            return job

    def rerun(self, job_id: str) -> dict[str, Any]:
        """Revalidate original parameters and create a distinct job."""
        job = self.get(job_id)
        if job["status"] == "running":
            raise JobConflict("运行中的任务不能重跑")
        return self.start(job["command"], job["params"])
