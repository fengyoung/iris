"""只读 overview 聚合与缓存隔离。"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from iris.taskpanel import overview
from iris.taskpanel.reporter import TaskReporter


def test_empty_root_is_readonly_and_sections_isolated(tmp_path):
    root = tmp_path / "missing"
    result = overview.build_overview(root, root / "data", [])
    assert not root.exists()
    assert result["freshness"]["status"] == "error"
    assert result["usage"]["data"]["today"]["total_tokens"] == 0
    assert result["pending_feed"]["data"]["count"] == 0
    assert result["tasks"]["data"]["latest_daily_start"] is None


def test_usage_utc_periods_and_closes_connection(tmp_path, monkeypatch):
    path = tmp_path / "llm_usage.db"
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday())
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE api_calls (date TEXT, prompt_tokens INTEGER, completion_tokens INTEGER)"
    )
    conn.executemany(
        "INSERT INTO api_calls VALUES (?,?,?)",
        [
            (today.isoformat(), 10, 4),
            ((monday - timedelta(days=1)).isoformat(), 100, 200),
            ((today + timedelta(days=1)).isoformat(), 1000, 2000),
        ],
    )
    conn.commit()
    conn.close()
    real_connect = sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        assert "mode=ro" in args[0]
        connection = real_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(overview.sqlite3, "connect", connect)
    result = overview._usage(tmp_path)
    assert result["timezone"] == "UTC"
    assert result["today"]["total_tokens"] == 14
    assert result["week"]["total_tokens"] == 14
    import pytest

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_pending_filters_identity_only_and_omits_bodies(tmp_path, monkeypatch):
    feed = [
        {"topic_id": "a", "title": "普通项目", "summary": "薪资只是正文内容"},
        {"topic_id": "b", "title": "调薪方案"},
        {"topic_id": "c", "title": "会议", "sources": [{"path": "人员盘点.md"}]},
    ]
    (tmp_path / "feed_pending.json").write_text(json.dumps(feed), encoding="utf-8")
    (tmp_path / "wiki_candidates_auto.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"title": "普通主题", "has_wiki": False},
                {"title": "已存在", "has_wiki": True},
                {"title": "绩效评估", "has_wiki": False},
                {"title": "一般标题", "wiki_path": "wiki/调薪方案.md"},
                {"title": "一般标题", "sample_paths": ["source/人员盘点.md"]},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        overview.DecisionStore,
        "pending",
        lambda self: [
            {
                "candidate_id": "a",
                "decision": {"title": "普通决策", "outcome": "薪资正文"},
            },
            {"candidate_id": "b", "decision": {"title": "调薪决策"}},
        ],
    )
    result = overview.build_overview(tmp_path, tmp_path, [])
    for key in ("pending_feed", "pending_wiki", "pending_decisions"):
        assert result[key]["data"]["count"] == 1
        assert set(result[key]["data"]["items"][0]) == {"id", "title", "path"}


def test_bad_section_does_not_leak_error_or_break_others(tmp_path, monkeypatch):
    (tmp_path / "feed_pending.json").write_text("broken secret body", encoding="utf-8")
    monkeypatch.setattr(
        overview, "load_config_bundle", Mock(return_value=SimpleNamespace())
    )
    monkeypatch.setattr(overview, "_compute_freshness", lambda bundle: {"ready": True})
    monkeypatch.setattr(
        overview.ReminderEngine,
        "collect",
        lambda self: {
            "signals": [
                {"target": "正常项目", "detail": "绩效仅在正文"},
                {"target": "人员盘点"},
            ]
        },
    )
    result = overview.build_overview(tmp_path, tmp_path, [])
    assert result["pending_feed"] == {"status": "error", "error": "JSONDecodeError"}
    assert result["freshness"]["data"]["ready"]
    assert result["reminders"]["data"]["signal_count"] == 1
    assert "secret" not in json.dumps(result)


def test_tasks_and_cache_refresh_without_recomputing_other_sections(
    tmp_path, monkeypatch
):
    with TaskReporter("daily-start", data_root=tmp_path, task_id="daily") as reporter:
        reporter.mark_failed("private message")
    result = overview.build_overview(tmp_path, tmp_path, [])
    assert result["tasks"]["data"]["latest_daily_start"]["status"] == "failed"
    assert len(result["tasks"]["data"]["failed"]) == 1
    assert "private message" not in json.dumps(result)
    build = Mock(return_value=result)
    monkeypatch.setattr(overview, "build_overview", build)
    clock = Mock(return_value=0)
    monkeypatch.setattr(overview.time, "monotonic", clock)
    provider = overview.OverviewProvider(tmp_path, tmp_path)
    first = provider.get([])
    first["usage"]["status"] = "mutated"
    second = provider.get(
        [{"job_id": "job", "name": "build-graph", "status": "running"}]
    )
    assert second["usage"]["status"] == "ok"
    assert second["tasks"]["data"]["running_count"] == 1
    assert build.call_count == 1
    clock.return_value = 31
    provider.get([])
    assert build.call_count == 2


def test_console_ids_and_pid_reuse_do_not_collapse(tmp_path):
    jobs = [
        {
            "id": "a",
            "command": "daily-start",
            "pid": 7,
            "started_at": "2026-01-01",
            "status": "failed",
            "ended_at": "end",
        },
        {
            "id": "b",
            "command": "build-graph",
            "pid": 7,
            "started_at": "2026-01-02",
            "status": "running",
        },
        {
            "id": "c",
            "command": "deep-eval",
            "pid": 8,
            "started_at": "2026-01-02",
            "status": "running",
        },
    ]
    tasks = overview._tasks(tmp_path, jobs)
    assert tasks["running_count"] == 2
    assert tasks["latest_daily_start"]["job_id"] == "a"
    assert tasks["latest_daily_start"]["ended_at"] == "end"


def test_signal_metadata_never_reads_body(tmp_path):
    root = tmp_path / "signals"
    root.mkdir()
    (root / "2026-01-01-abcd-preview.md").write_text("private body", encoding="utf-8")
    (root / "调薪.md").write_text("secret", encoding="utf-8")
    result = overview._signals(tmp_path)
    assert result["latest"]["preview"]
    assert result["latest"]["size_bytes"] == len("private body")
    assert "private body" not in json.dumps(result)
    assert result["content_available"] is False
    assert result["items"] == []
    assert "完整来源" in result["limitation"]


def test_task_lifecycle_deduplication(tmp_path, monkeypatch):
    task = {
        "task_id": "reporter",
        "name": "daily-start",
        "pid": 42,
        "started_at": "2026-01-01T10:00:04+00:00",
        "finished_at": "2026-01-01T10:00:20+00:00",
        "status": "failed",
    }
    monkeypatch.setattr(
        overview, "read_history", lambda root: [SimpleNamespace(to_dict=lambda: task)]
    )
    monkeypatch.setattr(overview, "read_current_all", lambda root: [])
    job = {
        "id": "console",
        "command": "daily-start",
        "pid": 42,
        "started_at": "2026-01-01T10:00:00+00:00",
        "ended_at": "2026-01-01T10:00:21+00:00",
        "status": "failed",
    }
    result = overview._tasks(tmp_path, [job])
    assert len(result["failed"]) == 1
    assert result["failed"][0]["task_id"] == "reporter"
    assert result["failed"][0]["job_id"] == "console"
    # PID 相同且时间差小，但生命周期不重叠时保留两个任务。
    for changes in (
        {
            "started_at": "2026-01-01T10:00:22+00:00",
            "ended_at": "2026-01-01T10:00:23+00:00",
        },
        {"started_at": "2026-01-01T09:59:00+00:00"},
        {"command": "build-graph"},
        {"pid": 43},
        {"started_at": "invalid"},
        {"ended_at": ""},
    ):
        assert len(overview._tasks(tmp_path, [{**job, **changes}])["failed"]) == 2
    # 同进程多个 console 记录只合并一个，避免偶然 PID 复用误删。
    assert len(overview._tasks(tmp_path, [job, {**job, "id": "second"}])["failed"]) == 2
