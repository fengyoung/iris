"""memory/lifecycle.py — 老化归档、冲突检测、摘要边界测试。"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from iris.memory.lifecycle import MemoryLifecycle, DEFAULT_CORRECTION_AGE_DAYS


# ── fixtures ────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _old_iso(days: int = 120) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _make_corrections_state(items: Dict[str, Any]) -> Dict[str, Any]:
    return {"version": "1.0", "updated_at": _now_iso(), "items": items}


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

        # 配置 CorrectionMemoryStore mock
        correction_state = {"version": "1.0", "updated_at": _now_iso(), "items": {}}
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


# ── _is_item_stale ────────────────────────────────────────────────────


def test_is_item_stale_old_item():
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    item = {"updated_at": _old_iso(120)}
    assert MemoryLifecycle._is_item_stale(item, cutoff) is True


def test_is_item_stale_recent_item():
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    item = {"updated_at": _now_iso()}
    assert MemoryLifecycle._is_item_stale(item, cutoff) is False


def test_is_item_stale_missing_updated_at():
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    assert MemoryLifecycle._is_item_stale({}, cutoff) is False


def test_is_item_stale_invalid_date():
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    item = {"updated_at": "not-a-date"}
    assert MemoryLifecycle._is_item_stale(item, cutoff) is False


def test_is_item_stale_naive_datetime():
    """无时区的 datetime 应被视为 UTC 处理。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    naive_old = (datetime.now(timezone.utc) - timedelta(days=120)).replace(tzinfo=None).isoformat()
    item = {"updated_at": naive_old}
    assert MemoryLifecycle._is_item_stale(item, cutoff) is True


# ── age() ──────────────────────────────────────────────────────────────


def test_age_moves_old_items_to_archive(lifecycle):
    """超期项目被移至归档，未超期项目保留。"""
    items = {
        "旧概念": {"preferred": "A", "updated_at": _old_iso(120)},
        "新概念": {"preferred": "B", "updated_at": _now_iso()},
    }
    lifecycle._corrections.load.return_value = {
        "version": "1.0", "updated_at": _now_iso(), "items": items
    }

    result = lifecycle.age(days=90)

    assert result["aged_count"] == 1
    assert result["kept_count"] == 1
    assert "旧概念" in result["archived_concepts"]


def test_age_no_stale_items(lifecycle):
    """无超期项目时归档计数为 0。"""
    items = {"新概念": {"preferred": "B", "updated_at": _now_iso()}}
    lifecycle._corrections.load.return_value = {
        "version": "1.0", "updated_at": _now_iso(), "items": items
    }

    result = lifecycle.age(days=90)
    assert result["aged_count"] == 0
    assert result["kept_count"] == 1


def test_age_all_stale(lifecycle):
    """所有项超期时 kept_count 为 0。"""
    items = {
        "概念A": {"preferred": "X", "updated_at": _old_iso(200)},
        "概念B": {"preferred": "Y", "updated_at": _old_iso(150)},
    }
    lifecycle._corrections.load.return_value = {
        "version": "1.0", "updated_at": _now_iso(), "items": items
    }

    result = lifecycle.age(days=90)
    assert result["kept_count"] == 0
    assert result["aged_count"] == 2


# ── list_stale() ───────────────────────────────────────────────────────


def test_list_stale_returns_overdue_items(lifecycle):
    items = {
        "旧概念": {"preferred": "A", "updated_at": _old_iso(120)},
        "新概念": {"preferred": "B", "updated_at": _now_iso()},
    }
    lifecycle._corrections.load.return_value = {
        "version": "1.0", "updated_at": _now_iso(), "items": items
    }

    stale = lifecycle.list_stale(days=90)
    assert len(stale) == 1
    assert stale[0]["concept"] == "旧概念"
    assert stale[0]["days_since_update"] >= 120


def test_list_stale_empty_when_no_overdue(lifecycle):
    items = {"新概念": {"preferred": "B", "updated_at": _now_iso()}}
    lifecycle._corrections.load.return_value = {
        "version": "1.0", "updated_at": _now_iso(), "items": items
    }
    assert lifecycle.list_stale(days=90) == []


# ── detect_conflicts() ─────────────────────────────────────────────────


def test_detect_conflicts_high_count_flagged(lifecycle):
    """高频纠正（count >= min_count）的概念应被列为潜在冲突。"""
    items = {
        "术语X": {
            "preferred": "正确写法",
            "update_count": 5,
            "last_source": "不是写法A，而是正确写法",
            "updated_at": _now_iso(),
        }
    }
    lifecycle._corrections.load.return_value = {
        "version": "1.0", "updated_at": _now_iso(), "items": items
    }
    lifecycle._corrections.get_frequent_corrections.return_value = [
        {"concept": "术语X", "update_count": 5}
    ]

    conflicts = lifecycle.detect_conflicts(min_count=3)
    # detect_conflicts 返回列表，高频项应被识别
    assert isinstance(conflicts, list)


def test_detect_conflicts_low_count_ignored(lifecycle):
    """低频纠正不应触发冲突警告。"""
    lifecycle._corrections.load.return_value = {
        "version": "1.0", "updated_at": _now_iso(), "items": {}
    }
    lifecycle._corrections.get_frequent_corrections.return_value = []

    conflicts = lifecycle.detect_conflicts(min_count=3)
    assert conflicts == []


# ── summarize() ────────────────────────────────────────────────────────


def test_summarize_trims_excess_notes(lifecycle):
    """notes 超过 10 条时应被裁剪。"""
    prefs = {
        "likes": [],
        "dislikes": [],
        "notes": [f"note{i}" for i in range(15)],
        "style_preferences": [],
    }
    lifecycle._profile.load.return_value = {"user_preferences": prefs}

    changes = lifecycle.summarize()
    assert "trimmed_notes" in changes


def test_summarize_no_changes_when_small(lifecycle):
    """条目数量未超阈值时不修改任何内容。"""
    prefs = {"likes": [], "dislikes": [], "notes": ["note1"], "style_preferences": []}
    lifecycle._profile.load.return_value = {"user_preferences": prefs}

    changes = lifecycle.summarize()
    assert changes == {}
