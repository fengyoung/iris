"""置顶规则跨提取、问答、老化与内容更新的行为回归。"""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from iris.memory.long_term import CorrectionMemoryStore
from iris.memory.lifecycle import MemoryLifecycle
from iris.qa.memory_updater import MemoryUpdater


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(root=tmp_path, app={"paths": {"memory_dir": "./memory"}})


@pytest.fixture
def store(config):
    store = CorrectionMemoryStore(config)
    items = {f"normal{i:02}": {"preferred": f"value{i}", "updated_at": "2000-01-01"}
             for i in range(12)}
    items["双周报沉淀与发布规则"] = {
        "preferred": "发布前必须审阅", "updated_at": "2000-01-01", "update_count": 1,
    }
    store.save({"items": items})
    return store


def test_pin_is_persistent_idempotent_and_does_not_edit_content(store, config):
    before = deepcopy(store.load()["items"]["双周报沉淀与发布规则"])
    assert store.pin("双周报沉淀与发布规则")
    first = store.load()
    assert store.pin("双周报沉淀与发布规则")
    assert store.load() == first
    item = CorrectionMemoryStore(config).load()["items"]["双周报沉淀与发布规则"]
    assert item["pinned"] is True
    assert item["pinned_at"]
    assert {key: item[key] for key in before} == before
    assert not store.pin("不存在")
    assert not store.pin("")


def test_tail_pin_exempt_and_ordinary_order_unchanged(store):
    store.pin("双周报沉淀与发布规则")
    rows = store.select_for_prompt()
    assert len(rows) == 11
    assert rows[0]["concept"] == "双周报沉淀与发布规则"
    assert [row["concept"] for row in rows[1:]] == [f"normal{i:02}" for i in range(10)]
    assert len(store.load()["items"]) == 13


@pytest.mark.parametrize("limit", [0, -1, 1, 10])
def test_all_pinned_never_truncated(store, limit):
    for key in store.load()["items"]:
        store.pin(key)
    assert len(store.select_for_prompt(limit=limit)) == 13


def test_unpin_returns_to_normal_limit(store):
    store.pin("双周报沉淀与发布规则")
    assert store.pin("双周报沉淀与发布规则", pinned=False)
    assert len(store.select_for_prompt()) == 10
    assert all(row["concept"] != "双周报沉淀与发布规则" for row in store.select_for_prompt())
    assert store.load()["items"]["双周报沉淀与发布规则"]["pinned_at"] == ""


def test_query_always_includes_pin_without_duplicates(store):
    store.pin("双周报沉淀与发布规则")
    rows = store.get_relevant("normal00", top_k=1)
    assert [r["concept"] for r in rows] == ["双周报沉淀与发布规则", "normal00"]
    assert store.get_relevant("双周报沉淀与发布规则", top_k=0)[0]["concept"] == "双周报沉淀与发布规则"
    assert store.render_for_prompt("其他问题").startswith("- 双周报沉淀与发布规则")


def test_extraction_prompt_includes_pin_after_twelve_ordinary_items(store, config):
    store.pin("双周报沉淀与发布规则")
    updater = MemoryUpdater(config)
    with patch("iris.utils.prompting.PromptTemplateLoader") as loader:
        updater._build_extraction_prompt("问题", "回答")
    text = loader.return_value.render.call_args.args[1]["existing_corrections"]
    assert text.splitlines()[0] == "  双周报沉淀与发布规则 => 发布前必须审阅"
    assert len(text.splitlines()) == 11
    assert "normal09" in text and "normal10" not in text


def test_aging_skips_pin_until_unpinned(store, config):
    store.pin("双周报沉淀与发布规则")
    lifecycle = MemoryLifecycle(config)
    assert len(lifecycle.list_stale()) == 12
    report = lifecycle.age()
    assert report["aged_count"] == 12
    assert list(store.load()["items"]) == ["双周报沉淀与发布规则"]
    store.pin("双周报沉淀与发布规则", pinned=False)
    assert lifecycle.age()["archived_concepts"] == ["双周报沉淀与发布规则"]


def test_content_updates_preserve_pin_and_model_cannot_unpin(store, config):
    store.pin("双周报沉淀与发布规则")
    before = store.load()["items"]["双周报沉淀与发布规则"]["pinned_at"]
    store.apply_text_update("纠正：双周报沉淀与发布规则=新的发布要求")
    updater = MemoryUpdater(config)
    updater._apply_corrections([{"concept": "双周报沉淀与发布规则", "preferred": "再更新", "pinned": False}])
    item = store.load()["items"]["双周报沉淀与发布规则"]
    assert item["pinned"] is True and item["pinned_at"] == before
    assert item["preferred"] == "再更新"
    updater._apply_corrections([{"concept": "新规则", "preferred": "内容", "pinned": True}])
    assert not store.load()["items"]["新规则"].get("pinned")


def test_lifecycle_merge_preserves_pin_when_legacy_input_omits_it(store, config):
    store.pin("双周报沉淀与发布规则")
    MemoryLifecycle(config).merge({"corrections": {"items": {
        "双周报沉淀与发布规则": {"preferred": "新要求", "updated_at": "2026-01-01"},
    }}})
    item = store.load()["items"]["双周报沉淀与发布规则"]
    assert item["pinned"] is True
    assert item["preferred"] == "新要求"


def test_archive_restore_preserves_current_manual_metadata(store, config):
    concept = "双周报沉淀与发布规则"
    store.pin(concept)
    store.confirm(concept)
    before = store.load()["items"][concept]
    lifecycle = MemoryLifecycle(config)
    lifecycle._save_archive({"items": {concept: {"preferred": "归档内容", "pinned": False}}})
    assert lifecycle.restore_archived(concept) == 1
    item = store.load()["items"][concept]
    assert item["preferred"] == "归档内容"
    for field in ("pinned", "pinned_at", "confirmed", "confirmed_at"):
        assert item[field] == before[field]
