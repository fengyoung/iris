"""只读首页聚合：独立分区降级，不触发 LLM、通知或任务启动。"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iris.analysis.reminders import ReminderEngine
from iris.app.cli.helpers import _compute_freshness
from iris.config.loader import load_config_bundle
from iris.decisions.store import DecisionStore
from iris.taskpanel.store import read_current_all, read_history
from iris.wiki._sensitive import is_sensitive_path, is_sensitive_title


def _visible(row: dict) -> bool:
    """仅检查身份字段，绝不将摘要、正文或错误信息用于敏感判定。"""
    if any(is_sensitive_title(str(row.get(key, ""))) for key in ("title", "target")):
        return False
    if any(
        is_sensitive_path(str(row.get(key, "")))
        for key in ("path", "source_path", "source_doc", "wiki_path")
    ):
        return False
    sources = [*(row.get("sources") or []), *(row.get("sample_paths") or [])]
    return all(
        _visible(source)
        if isinstance(source, dict)
        else not is_sensitive_path(str(source))
        for source in sources
    )


def _items(rows: list[dict], *, id_key: str) -> dict:
    visible = [row for row in rows if _visible(row)]
    return {
        "count": len(visible),
        "items": [
            {
                "id": row.get(id_key, ""),
                "title": row.get("title", ""),
                "path": row.get("path", ""),
            }
            for row in visible[:20]
        ],
    }


def _task_interval(row: dict) -> tuple[datetime, datetime] | None:
    try:
        start = datetime.fromisoformat(row["started_at"]).astimezone(timezone.utc)
        end_value = row.get("ended_at") or row.get("finished_at")
        if end_value:
            end = datetime.fromisoformat(end_value).astimezone(timezone.utc)
        elif row.get("status") in {"running", "queued"}:
            end = datetime.now(timezone.utc)
        else:
            return None  # 终态无结束时间，不能可靠确认生命周期重叠。
        return (start, end) if end >= start else None
    except (KeyError, TypeError, ValueError):
        return None


def _same_process(task: dict, job: dict) -> bool:
    if not task.get("task_id") or task.get("job_id"):
        return False  # 不把两个独立 console job 相互折叠。
    if (
        not job.get("pid")
        or task.get("pid") != job["pid"]
        or task.get("name") != job.get("name")
    ):
        return False
    left, right = _task_interval(task), _task_interval(job)
    if left is None or right is None:
        return False
    return abs((left[0] - right[0]).total_seconds()) <= 30 and max(
        left[0], right[0]
    ) <= min(left[1], right[1])


def _tasks(data_root: Path, jobs: list) -> dict:
    tasks = [
        task.to_dict() for task in read_current_all(data_root) + read_history(data_root)
    ]
    by_id = {str(row.get("task_id")): row for row in reversed(tasks)}
    for index, row in enumerate(jobs):
        if not isinstance(row, dict):
            continue
        normalized = {
            **row,
            "job_id": row.get("id", row.get("job_id")),
            "name": row.get("name", row.get("command", "")),
            "finished_at": row.get("ended_at", row.get("finished_at", "")),
        }
        match = next(
            (key for key, task in by_id.items() if _same_process(task, normalized)),
            None,
        )
        if match is not None:
            by_id[match] = {**by_id[match], **normalized}
        else:
            by_id[f"console:{normalized['job_id'] or index}"] = normalized
    rows = sorted(
        by_id.values(), key=lambda row: str(row.get("started_at", "")), reverse=True
    )

    def public(row):
        return {
            key: row.get(key)
            for key in (
                "task_id",
                "job_id",
                "name",
                "command",
                "status",
                "started_at",
                "finished_at",
                "ended_at",
                "phase",
                "progress",
            )
        }

    daily = next(
        (row for row in rows if row.get("name", row.get("command")) == "daily-start"),
        None,
    )
    return {
        "latest_daily_start": public(daily) if daily else None,
        "failed": [
            public(row)
            for row in rows
            if row.get("status") in {"failed", "interrupted"}
        ][:10],
        "running_count": sum(
            row.get("status") in {"running", "queued"} for row in rows
        ),
    }


def _usage(data_root: Path) -> dict:
    # UsageTracker 的 date 列使用 UTC；本周从 UTC 周一开始，与每日统计一致。
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    result: dict = {"timezone": "UTC", "today": {}, "week": {}}
    path = data_root / "llm_usage.db"
    for key, start in (("today", today), ("week", monday)):
        result[key] = {
            "since": start.isoformat(),
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
    if not path.exists():
        return result
    with closing(
        sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    ) as conn:
        for key in ("today", "week"):
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0) "
                "FROM api_calls WHERE date >= ? AND date <= ?",
                (result[key]["since"], today.isoformat()),
            ).fetchone()
            result[key].update(
                calls=row[0],
                prompt_tokens=row[1],
                completion_tokens=row[2],
                total_tokens=row[1] + row[2],
            )
    return result


def _decisions(data_root: Path) -> dict:
    rows = [
        {**row["decision"], "candidate_id": row["candidate_id"]}
        for row in DecisionStore(data_root / "decisions").pending()
    ]
    return _items(rows, id_key="candidate_id")


def _feed(data_root: Path) -> dict:
    path = data_root / "feed_pending.json"
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return _items(rows, id_key="topic_id")


def _wiki(data_root: Path) -> dict:
    path = data_root / "wiki_candidates_auto.jsonl"
    if not path.exists():
        return _items([], id_key="title")
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return _items([row for row in rows if not row.get("has_wiki")], id_key="title")


def _signals(data_root: Path) -> dict:
    paths = [
        path
        for path in (data_root / "signals").glob("*.md")
        if path.is_file() and not is_sensitive_path(path.name)
    ]
    latest = max(paths, key=lambda path: path.stat().st_mtime, default=None)
    if latest is None:
        return {"latest": None}
    stat = latest.stat()
    # 归档将 detail 与截断来源混排，且只保留前两个来源，无法可靠还原完整身份。
    # 不从正文猜测来源或敏感性；明确告知调用方当前仅提供文件元数据。
    return {
        "items": [],
        "content_available": False,
        "limitation": "归档摘要未保留完整来源身份，仅显示文件元数据；信号详情请通过 CLI 查看。",
        "latest": {
            "title": latest.stem,
            "path": str(latest),
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc
            ).isoformat(),
            "size_bytes": stat.st_size,
            "preview": latest.stem.endswith("-preview"),
        },
    }


def _reminders(bundle) -> dict:
    result = ReminderEngine(bundle).collect()
    signals = [row for row in result.get("signals", []) if _visible(row)]
    return {**result, "signals": signals, "signal_count": len(signals)}


def build_overview(project_root: Path, data_root: Path, jobs: list) -> dict:
    """聚合已有本地状态，每个分区独立返回 ok/error，错误不泄漏配置与正文。"""
    project_root, data_root = Path(project_root), Path(data_root)
    result: dict = {"generated_at": datetime.now(timezone.utc).isoformat()}
    bundle = None
    try:
        bundle = load_config_bundle(project_root)
    except Exception:
        pass
    sections = {
        "tasks": lambda: _tasks(data_root, jobs),
        "freshness": lambda: _compute_freshness(bundle),
        "reminders": lambda: (
            _reminders(bundle) if bundle is not None else _missing_config()
        ),
        "usage": lambda: _usage(data_root),
        "signals": lambda: _signals(data_root),
        "pending_decisions": lambda: _decisions(data_root),
        "pending_feed": lambda: _feed(data_root),
        "pending_wiki": lambda: _wiki(data_root),
    }
    for name, collect in sections.items():
        try:
            result[name] = {"status": "ok", "data": collect()}
        except Exception as exc:
            result[name] = {"status": "error", "error": type(exc).__name__}
    return result


def _missing_config():
    raise RuntimeError("configuration unavailable")


class OverviewProvider:
    """缓存慢速只读聚合 30 秒；任务状态每次请求更新。"""

    def __init__(self, project_root: Path, data_root: Path, *, ttl: float = 30):
        self.project_root = Path(project_root)
        self.data_root = Path(data_root)
        self.ttl = ttl
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._expires = 0.0

    def get(self, jobs: list) -> dict:
        with self._lock:
            if self._cached is None or time.monotonic() >= self._expires:
                self._cached = build_overview(self.project_root, self.data_root, jobs)
                self._expires = time.monotonic() + self.ttl
            result = copy.deepcopy(self._cached)
        try:
            result["tasks"] = {"status": "ok", "data": _tasks(self.data_root, jobs)}
        except Exception as exc:
            result["tasks"] = {"status": "error", "error": type(exc).__name__}
        return result
