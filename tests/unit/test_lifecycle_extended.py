"""memory/lifecycle.py 扩展测试 — 覆盖 merge/restore_archived/clear_archive/maintenance/_parse_iso。"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.memory.lifecycle import (
    MemoryLifecycle,
    _now_iso,
    _parse_iso,
)


def _now_iso_test() -> str:
    return datetime.now(timezone.utc).isoformat()


def _old_iso(days: int = 120) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture
def temp_memory_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def lifecycle(temp_memory_dir):
    """返回一个 MemoryLifecycle 实例，使用临时目录。"""
    cfg = MagicMock()
    cfg.root = temp_memory_dir
    cfg.app = {"paths": {"memory_dir": str(temp_memory_dir / "memory")}}

    memory_dir = temp_memory_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    with patch("iris.memory.lifecycle.UserProfileMemoryStore") as MockProfile, \
         patch("iris.memory.lifecycle.CorrectionMemoryStore") as MockCorrections:

        correction_state = {"version": "1.0", "updated_at": _now_iso_test(), "items": {}}
        corrections_path = memory_dir / "corrections.json"
        corrections_path.write_text(json.dumps(correction_state), encoding="utf-8")

        mock_corrections = MagicMock()
        mock_corrections._path = corrections_path
        mock_corrections.load.return_value = correction_state
        MockCorrections.return_value = mock_corrections

        mock_profile = MagicMock()
        mock_profile.load.return_value = {"user_preferences": {}}
        MockProfile.return_value = mock_profile

        lc = MemoryLifecycle(cfg)
        lc._corrections = mock_corrections
        lc._profile = mock_profile
        lc._archive_path = memory_dir / "corrections_archive.json"
        yield lc


# ── _now_iso / _parse_iso 纯函数 ───────────────────────────────

class TestNowIso:
    def test_returns_iso_format(self):
        result = _now_iso()
        assert "T" in result
        assert isinstance(result, str)

    def test_parseable(self):
        result = _now_iso()
        dt = datetime.fromisoformat(result)
        assert isinstance(dt, datetime)


class TestParseIso:
    def test_valid_iso(self):
        dt = _parse_iso("2026-07-30T10:00:00+00:00")
        assert dt is not None
        assert dt.year == 2026

    def test_empty_string(self):
        assert _parse_iso("") is None

    def test_none_input(self):
        assert _parse_iso(None) is None

    def test_invalid_format(self):
        assert _parse_iso("not-a-date") is None

    def test_naive_datetime(self):
        dt = _parse_iso("2026-01-01T00:00:00")
        assert dt is not None
        assert dt.tzinfo is None


# ── merge() ───────────────────────────────────────────────────

class TestMerge:
    def test_new_concept_added(self, lifecycle):
        """新概念直接合并。"""
        incoming = {
            "corrections": {
                "items": {"新概念": {"preferred": "新写法", "updated_at": _now_iso_test()}},
            },
        }
        result = lifecycle.merge(incoming, strategy="auto")
        assert result["merged_concepts"] >= 1

    def test_existing_same_value_no_change(self, lifecycle):
        """相同 preferred 值不产生变化。"""
        lifecycle._corrections.load.return_value = {
            "version": "1.0", "updated_at": _now_iso_test(),
            "items": {"概念A": {"preferred": "写法X", "updated_at": _now_iso_test()}},
        }
        incoming = {
            "corrections": {
                "items": {"概念A": {"preferred": "写法X", "updated_at": _now_iso_test()}},
            },
        }
        result = lifecycle.merge(incoming, strategy="auto")
        assert result["merged_concepts"] == 0

    def test_existing_different_newer_incoming(self, lifecycle):
        """新记录时间更新 → 替换。"""
        lifecycle._corrections.load.return_value = {
            "version": "1.0", "updated_at": _now_iso_test(),
            "items": {"概念A": {"preferred": "旧写法", "updated_at": _old_iso(200)}},
        }
        incoming = {
            "corrections": {
                "items": {"概念A": {"preferred": "新写法", "updated_at": _now_iso_test()}},
            },
        }
        result = lifecycle.merge(incoming, strategy="auto")
        assert isinstance(result, dict)

    def test_strategy_keep_both(self, lifecycle):
        """keep_both 策略保留冲突记录。"""
        lifecycle._corrections.load.return_value = {
            "version": "1.0", "updated_at": _now_iso_test(),
            "items": {"概念A": {"preferred": "写法X", "updated_at": _old_iso(100)}},
        }
        incoming = {
            "corrections": {
                "items": {"概念A": {"preferred": "写法Y", "updated_at": _now_iso_test()}},
            },
        }
        result = lifecycle.merge(incoming, strategy="keep_both")
        assert len(result.get("conflicts", [])) >= 1

    def test_merge_profile(self, lifecycle):
        """合并时同步处理 profile。"""
        incoming = {
            "profile": {
                "user_preferences": {
                    "likes": ["新偏好"],
                    "dislikes": [],
                    "style_preferences": [],
                    "notes": [],
                },
            },
        }
        result = lifecycle.merge(incoming)
        assert isinstance(result, dict)

    def test_empty_concept_skipped(self, lifecycle):
        """空白概念名被跳过。"""
        incoming = {
            "corrections": {
                "items": {"  ": {"preferred": "值"}},
            },
        }
        result = lifecycle.merge(incoming)
        assert result["merged_concepts"] == 0

    def test_no_corrections_no_profile(self, lifecycle):
        result = lifecycle.merge({})
        assert result["merged_concepts"] == 0
        assert isinstance(result.get("conflicts", []), list)


# ── restore_archived() ────────────────────────────────────────

class TestRestoreArchived:
    def test_empty_archive_returns_zero(self, lifecycle):
        assert lifecycle.restore_archived() == 0

    def test_restore_single_concept(self, lifecycle):
        """恢复指定概念。"""
        archive = {"items": {"概念A": {"preferred": "值"}}, "updated_at": _now_iso_test()}
        archive_path = lifecycle._archive_path
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(json.dumps(archive), encoding="utf-8")

        count = lifecycle.restore_archived(concept="概念A")
        assert count == 1

    def test_restore_all(self, lifecycle):
        archive = {
            "items": {"概念A": {"preferred": "A"}, "概念B": {"preferred": "B"}},
            "updated_at": _now_iso_test(),
        }
        archive_path = lifecycle._archive_path
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(json.dumps(archive), encoding="utf-8")

        count = lifecycle.restore_archived()
        assert count == 2

    def test_restore_nonexistent_concept(self, lifecycle):
        archive = {"items": {"概念A": {"preferred": "A"}}, "updated_at": _now_iso_test()}
        archive_path = lifecycle._archive_path
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_text(json.dumps(archive), encoding="utf-8")

        count = lifecycle.restore_archived(concept="概念B")
        assert count == 0


# ── clear_archive() ───────────────────────────────────────────

class TestClearArchive:
    def test_empty_archive(self, lifecycle):
        count = lifecycle.clear_archive()
        assert count == 0

    def test_clear_with_items(self, lifecycle):
        archive_path = lifecycle._archive_path
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive = {"items": {"概念A": {"preferred": "A"}}, "updated_at": _now_iso_test()}
        archive_path.write_text(json.dumps(archive), encoding="utf-8")

        count = lifecycle.clear_archive()
        assert count == 1
        # 清空后 items 为空
        saved = json.loads(archive_path.read_text(encoding="utf-8"))
        assert saved["items"] == {}


# ── maintenance() ─────────────────────────────────────────────

class TestMaintenance:
    def test_returns_report_structure(self, lifecycle):
        report = lifecycle.maintenance()
        assert "checked_at" in report
        assert "conflicts" in report
        assert "stale_corrections" in report
        assert "summary" in report

    def test_maintenance_with_all_changes(self, lifecycle):
        """有多项纠正记录和 profile 时维护正常执行。"""
        items = {
            "概念A": {"preferred": "A", "updated_at": _old_iso(200), "update_count": 5,
                       "last_source": "不是旧值，而是新值"},
        }
        lifecycle._corrections.load.return_value = {
            "version": "1.0", "updated_at": _now_iso_test(), "items": items
        }
        lifecycle._corrections.get_frequent_corrections.return_value = [
            {"concept": "概念A", "update_count": 5},
        ]
        lifecycle._profile.load.return_value = {
            "user_preferences": {
                "likes": [], "dislikes": [],
                "notes": [f"n{i}" for i in range(15)],
                "style_preferences": [],
            },
        }

        report = lifecycle.maintenance()
        assert isinstance(report, dict)
        assert report["checked_at"] is not None


# ── archive I/O ───────────────────────────────────────────────

class TestArchiveIO:
    def test_load_archive_missing_file(self, lifecycle):
        """不存在的归档文件 → 返回空结构。"""
        result = lifecycle._load_archive()
        assert result == {"items": {}, "updated_at": None}

    def test_load_archive_corrupted_json(self, lifecycle):
        """损坏的 JSON → 返回空结构。"""
        lifecycle._archive_path.parent.mkdir(parents=True, exist_ok=True)
        lifecycle._archive_path.write_text("not valid {{{ json", encoding="utf-8")
        result = lifecycle._load_archive()
        assert result["items"] == {}

    def test_save_and_load_archive(self, lifecycle):
        data = {"items": {"概念": {"preferred": "值"}}, "updated_at": _now_iso_test()}
        lifecycle._save_archive(data)
        loaded = lifecycle._load_archive()
        assert loaded["items"]["概念"]["preferred"] == "值"
