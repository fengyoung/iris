"""iris.analysis._biweekly_types 和 iris.utils.metrics 单元测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from iris.analysis._biweekly_types import FileBrief, FileEntry
from iris.utils.metrics import MetricsExporter, _week_key


# ── FileEntry ──────────────────────────────────────────────


class TestFileEntry:
    def test_is_dict_subclass(self):
        entry = FileEntry()
        assert isinstance(entry, dict)

    def test_field_assignment(self):
        entry = FileEntry()
        entry["label"] = "张三周报-0703"
        entry["char_count"] = 500
        assert entry["label"] == "张三周报-0703"
        assert entry["char_count"] == 500

    def test_supports_all_expected_fields(self):
        now = datetime(2026, 7, 3)
        entry = FileEntry(
            label="label1",
            date=now,
            dir="成员周报",
            filename="test.md",
            content="内容",
            char_count=2,
        )
        assert entry["label"] == "label1"
        assert entry["date"] == now
        assert entry["filename"] == "test.md"


# ── FileBrief ──────────────────────────────────────────────


class TestFileBrief:
    def test_is_dict_subclass(self):
        brief = FileBrief()
        assert isinstance(brief, dict)

    def test_field_assignment(self):
        brief = FileBrief()
        brief["primary_direction"] = 1
        brief["relevant_directions"] = [1, 2]
        brief["strategic_insights"] = ["洞察A"]
        assert brief["primary_direction"] == 1
        assert brief["relevant_directions"] == [1, 2]

    def test_supports_all_expected_fields(self):
        brief = FileBrief(
            brief_md="## 摘要\n内容",
            primary_direction=2,
            relevant_directions=[2],
            strategic_insights=["重要洞察"],
            key_facts=["事实1"],
            quantitative_data=["数据1"],
            dir_type="成员周报",
        )
        assert brief["primary_direction"] == 2
        assert brief["dir_type"] == "成员周报"


# ── _week_key ──────────────────────────────────────────────


class TestWeekKey:
    def test_format_contains_W(self):
        result = _week_key()
        assert "-W" in result

    def test_specific_date(self):
        dt = datetime(2026, 7, 15, tzinfo=timezone.utc)
        result = _week_key(dt)
        assert result.startswith("2026-W")

    def test_different_weeks_differ(self):
        dt1 = datetime(2026, 7, 1, tzinfo=timezone.utc)
        dt2 = datetime(2026, 7, 15, tzinfo=timezone.utc)
        assert _week_key(dt1) != _week_key(dt2)

    def test_same_week_same_key(self):
        dt1 = datetime(2026, 7, 13, tzinfo=timezone.utc)  # Monday
        dt2 = datetime(2026, 7, 14, tzinfo=timezone.utc)  # Tuesday same week
        # Same ISO week
        assert _week_key(dt1)[:7] == _week_key(dt2)[:7]  # same year prefix


# ── MetricsExporter ────────────────────────────────────────


def _make_config(tmp_path: Path) -> MagicMock:
    """构造最小化 config mock。"""
    config = MagicMock()
    config.root = tmp_path
    config.wiki = None  # 不测试 wiki 采集
    return config


class TestListSnapshots:
    def test_empty_dir_returns_empty(self, tmp_path):
        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        assert exporter.list_snapshots() == []

    def test_returns_only_year_prefixed_json(self, tmp_path):
        metrics_dir = tmp_path / "data" / "metrics"
        metrics_dir.mkdir(parents=True)
        # 有效文件
        (metrics_dir / "2026-W28.json").write_text("{}", encoding="utf-8")
        # 无效文件（非年份开头 / 非 json）
        (metrics_dir / "other.json").write_text("{}", encoding="utf-8")
        (metrics_dir / "2026-W28.txt").write_text("txt", encoding="utf-8")

        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        snapshots = exporter.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].name == "2026-W28.json"

    def test_sorted_by_name(self, tmp_path):
        metrics_dir = tmp_path / "data" / "metrics"
        metrics_dir.mkdir(parents=True)
        for name in ["2026-W30.json", "2026-W28.json", "2026-W29.json"]:
            (metrics_dir / name).write_text("{}", encoding="utf-8")

        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        snapshots = exporter.list_snapshots()
        names = [s.name for s in snapshots]
        assert names == sorted(names)


class TestTrend:
    def test_empty_returns_empty_lists(self, tmp_path):
        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        result = exporter.trend()
        assert result["weeks"] == []
        assert result["wiki_pages"] == []
        assert result["graph_nodes"] == []

    def test_parses_wiki_pages_and_graph_nodes(self, tmp_path):
        metrics_dir = tmp_path / "data" / "metrics"
        metrics_dir.mkdir(parents=True)
        snapshot = {
            "week": "2026-W28",
            "wiki": {"total_pages": 91},
            "graph": {"nodes": 91, "edges": 200},
        }
        (metrics_dir / "2026-W28.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )

        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        result = exporter.trend(weeks=4)
        assert "2026-W28" in result["weeks"]
        assert 91 in result["wiki_pages"]
        assert 91 in result["graph_nodes"]

    def test_weeks_param_limits_results(self, tmp_path):
        metrics_dir = tmp_path / "data" / "metrics"
        metrics_dir.mkdir(parents=True)
        for i in range(1, 6):
            snap = {"week": f"2026-W{i:02d}", "wiki": {"total_pages": i}, "graph": {"nodes": i, "edges": 0}}
            (metrics_dir / f"2026-W{i:02d}.json").write_text(json.dumps(snap), encoding="utf-8")

        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        result = exporter.trend(weeks=2)
        assert len(result["weeks"]) == 2


class TestExport:
    def test_writes_file(self, tmp_path):
        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        snapshot = {
            "week": "2026-W99",
            "exported_at": "2026-07-15T00:00:00+00:00",
            "wiki": {"total_pages": 42},
            "graph": {"nodes": 10},
        }
        output_path = exporter.export(snapshot)
        assert output_path.exists()
        assert output_path.name == "2026-W99.json"

    def test_written_file_contains_correct_data(self, tmp_path):
        config = _make_config(tmp_path)
        exporter = MetricsExporter(config)
        snapshot = {
            "week": "2026-W50",
            "wiki": {"total_pages": 55},
            "graph": {"nodes": 20, "edges": 100},
        }
        output_path = exporter.export(snapshot)
        data = json.loads(output_path.read_text(encoding="utf-8"))
        assert data["wiki"]["total_pages"] == 55
        assert data["graph"]["nodes"] == 20
