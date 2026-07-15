"""P2-P3 新增模块单元测试 — async_http / metrics / workspace / watcher。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from iris.utils.metrics import MetricsExporter, _week_key
from iris.config.workspace import WorkspaceDef, WorkspaceConfig, WorkspaceManager
from iris.ingest.watcher import SourceWatcher, FileEvent


# ── _week_key ─────────────────────────────────────────────


def test_week_key_format():
    key = _week_key()
    assert key.startswith("202")
    assert "-W" in key


# ── MetricsExporter ───────────────────────────────────────


class TestMetricsExporter:

    def test_snapshot_structure(self, tmp_path):
        """验证快照包含所有必需维度。"""
        import types
        bundle = types.SimpleNamespace(
            root=tmp_path,
            wiki=None,
            data_source={"sources": {}},
            llm={"models": {}},
        )
        exporter = MetricsExporter(bundle)
        snap = exporter.snapshot()
        assert "exported_at" in snap
        assert "week" in snap
        assert "wiki" in snap
        assert "graph" in snap
        assert "source" in snap
        assert "llm" in snap

    def test_export_writes_file(self, tmp_path):
        import types
        bundle = types.SimpleNamespace(
            root=tmp_path,
            wiki=None,
            data_source={"sources": {}},
            llm={"models": {}},
        )
        exporter = MetricsExporter(bundle)
        snap = exporter.snapshot()
        path = exporter.export(snap)
        assert path.exists()
        assert path.suffix == ".json"

    def test_list_snapshots_empty(self, tmp_path):
        import types
        bundle = types.SimpleNamespace(
            root=tmp_path,
            wiki=None,
            data_source={"sources": {}},
            llm={"models": {}},
        )
        exporter = MetricsExporter(bundle)
        assert exporter.list_snapshots() == []

    def test_trend_empty(self, tmp_path):
        import types
        bundle = types.SimpleNamespace(
            root=tmp_path,
            wiki=None,
            data_source={"sources": {}},
            llm={"models": {}},
        )
        exporter = MetricsExporter(bundle)
        trend = exporter.trend()
        assert trend["weeks"] == []


# ── WorkspaceConfig ───────────────────────────────────────


class TestWorkspaceConfig:

    def test_default_config(self):
        cfg = WorkspaceConfig()
        assert cfg.default_workspace == "main"
        assert len(cfg.workspaces) == 0

    def test_resolve_main(self):
        cfg = WorkspaceConfig()
        ws = cfg.resolve("main")
        assert ws.name == "main"

    def test_resolve_default(self):
        cfg = WorkspaceConfig()
        ws = cfg.resolve()
        assert ws.name == "main"

    def test_load_missing_file(self, tmp_path):
        cfg = WorkspaceConfig.load(tmp_path)
        assert cfg.default_workspace == "main"

    def test_load_from_json(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        data = {
            "default_workspace": "project_a",
            "workspaces": {
                "project_a": {"source_root": "/tmp/src", "wiki_root": "/tmp/wiki", "description": "Test project"},
                "project_b": {"source_root": "/tmp/src2", "wiki_root": "/tmp/wiki2"},
            },
        }
        (config_dir / "workspaces.json").write_text(json.dumps(data), encoding="utf-8")
        cfg = WorkspaceConfig.load(tmp_path)
        assert cfg.default_workspace == "project_a"
        assert len(cfg.workspaces) == 2
        assert cfg.workspaces["project_a"].description == "Test project"

    def test_list_names(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        data = {"workspaces": {"a": {}, "b": {}}}
        (config_dir / "workspaces.json").write_text(json.dumps(data), encoding="utf-8")
        cfg = WorkspaceConfig.load(tmp_path)
        names = cfg.list_names()
        assert len(names) == 2


# ── WorkspaceManager ──────────────────────────────────────


class TestWorkspaceManager:

    def test_default_manager(self, tmp_path):
        mgr = WorkspaceManager(tmp_path)
        ws = mgr.resolve()
        assert ws.name == "main"

    def test_apply_no_override(self, tmp_path):
        """无自定义路径时直接返回 bundle。"""
        mgr = WorkspaceManager(tmp_path)
        bundle = type("Bundle", (), {"data_source": {}, "wiki": None})()
        result = mgr.apply(bundle)
        assert result is bundle


# ── FileEvent ─────────────────────────────────────────────


def test_file_event_fields():
    evt = FileEvent(path="/tmp/test.md", relative_path="test.md", event_type="created")
    assert evt.event_type == "created"
    assert evt.relative_path == "test.md"
    assert evt.detected_at


def test_file_event_deleted():
    evt = FileEvent(path="/tmp/old.md", relative_path="old.md", event_type="deleted")
    assert evt.event_type == "deleted"


# ── SourceWatcher ─────────────────────────────────────────


class TestSourceWatcher:

    def test_init_finds_sources(self, tmp_path):
        import types
        src_dir = tmp_path / "SOURCE"
        src_dir.mkdir()
        bundle = types.SimpleNamespace(
            root=tmp_path,
            data_source={
                "sources": {
                    "main": {"enabled": True, "path": str(src_dir)},
                    "disabled": {"enabled": False, "path": "/tmp/disabled"},
                }
            },
        )
        watcher = SourceWatcher(bundle)
        assert "main" in watcher._sources
        assert "disabled" not in watcher._sources

    def test_snapshot_creates_baseline(self, tmp_path):
        import types
        src_dir = tmp_path / "SOURCE"
        src_dir.mkdir()
        (src_dir / "test.md").write_text("# Test")
        bundle = types.SimpleNamespace(
            root=tmp_path,
            data_source={"sources": {"main": {"enabled": True, "path": str(src_dir)}}},
        )
        watcher = SourceWatcher(bundle)
        snap = watcher.snapshot()
        assert "main" in snap
        assert "test.md" in snap["main"]

    def test_poll_first_run_no_events(self, tmp_path):
        import types
        src_dir = tmp_path / "SOURCE"
        src_dir.mkdir()
        bundle = types.SimpleNamespace(
            root=tmp_path,
            data_source={"sources": {"main": {"enabled": True, "path": str(src_dir)}}},
        )
        watcher = SourceWatcher(bundle)
        # First poll creates baseline, returns empty
        events = watcher.poll()
        assert events == []

    def test_poll_detects_new_file(self, tmp_path):
        import types
        src_dir = tmp_path / "SOURCE"
        src_dir.mkdir()
        bundle = types.SimpleNamespace(
            root=tmp_path,
            data_source={"sources": {"main": {"enabled": True, "path": str(src_dir)}}},
        )
        watcher = SourceWatcher(bundle)
        # Baseline
        watcher.poll()

        # Create new file
        (src_dir / "new_file.md").write_text("# New")
        import time
        time.sleep(0.01)  # Ensure mtime differs

        events = watcher.poll()
        created = [e for e in events if e.event_type == "created"]
        assert len(created) >= 1
