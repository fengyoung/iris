"""置顶 CLI、展示和导入元数据保护。"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from iris.app._cli_main import build_parser
from iris.app.cli.handlers import COMMAND_HANDLERS
from iris.memory.long_term import CorrectionMemoryStore
from iris.memory.manager import LongTermMemoryManager
from iris.output.formatter import _fmt_memory_list, _fmt_memory_pin, _fmt_memory_status


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(root=tmp_path, app={"paths": {"memory_dir": "memory"}})


@pytest.fixture
def store(config):
    store = CorrectionMemoryStore(config)
    store.save({"items": {**{f"普通{i:02}": {"preferred": "值"} for i in range(12)},
                          "置顶规则": {"preferred": "发布要求\n第二行", "confirmed": True,
                                     "confirmed_at": "2026-01-01"}}})
    return store


def test_cli_pin_unpin_dispatch_and_feedback(config, store):
    parser = build_parser()
    for flags, value in [([], True), (["--unpin"], False)]:
        args = parser.parse_args(["memory-pin", "--concept", "置顶规则", *flags])
        with patch("iris.app.cli._handlers._system._emit_output") as emit:
            assert COMMAND_HANDLERS[args.command](args, config, None) == 0
        assert store.load()["items"]["置顶规则"]["pinned"] is value
        payload = emit.call_args.args[1]
        assert payload["updated"] and payload["pinned"] is value
        assert "成功" in _fmt_memory_pin(payload)


@pytest.mark.parametrize("concept", ["", "   ", "不存在"])
def test_cli_missing_concept_is_error(config, store, concept):
    args = build_parser().parse_args(["memory-pin", "--concept", concept])
    with pytest.raises(ValueError):
        COMMAND_HANDLERS[args.command](args, config, None)


@pytest.mark.parametrize("nested", [False, True])
def test_list_shows_pin_beyond_ten_without_using_slot(config, store, nested):
    store.pin("置顶规则")
    payload = LongTermMemoryManager(config).list_memory("all" if nested else "corrections")
    text = _fmt_memory_list(payload)
    assert text.index("[置顶] 置顶规则") < text.index("普通00")
    assert "普通09" in text and "普通10" not in text
    assert "\n第二行" not in text


def test_status_includes_pin_plus_five_normal(config, store):
    store.pin("置顶规则")
    args = build_parser().parse_args(["memory-status"])
    with patch("iris.app.cli._handlers._system._emit_output") as emit:
        COMMAND_HANDLERS[args.command](args, config, None)
    payload = emit.call_args.args[1]
    assert len(payload["sample_corrections"]) == 6
    assert payload["sample_corrections"][0]["concept"] == "置顶规则"
    assert "[置顶] 置顶规则" in _fmt_memory_status(payload)


def test_import_merge_preserves_metadata_and_allows_explicit_unpin(config, store, tmp_path):
    store.pin("置顶规则")
    manager = LongTermMemoryManager(config)
    path = tmp_path / "import.json"
    path.write_text(json.dumps({"corrections": {"items": {"置顶规则": {"preferred": "新值"}}}}))
    manager.import_from_file(path)
    item = store.load()["items"]["置顶规则"]
    assert item["pinned"] and item["pinned_at"]
    assert item["confirmed"] and item["confirmed_at"] == "2026-01-01"
    path.write_text(json.dumps({"corrections": {"items": {"置顶规则": {
        "preferred": "新值", "pinned": False, "pinned_at": ""}}}}))
    manager.import_from_file(path)
    assert store.load()["items"]["置顶规则"]["pinned"] is False


def test_export_import_roundtrip_retains_pin(config, store, tmp_path):
    store.pin("置顶规则")
    manager = LongTermMemoryManager(config)
    output = manager.export_to_file(tmp_path / "export.json")
    config2 = SimpleNamespace(root=tmp_path / "other", app=config.app)
    LongTermMemoryManager(config2).import_from_file(output)
    assert CorrectionMemoryStore(config2).load()["items"]["置顶规则"]["pinned"] is True


def test_first_merge_import_initializes_top_level_timestamp(config):
    manager = LongTermMemoryManager(config)
    manager._merge_corrections({"items": {"新规则": {"preferred": "内容"}}})
    assert CorrectionMemoryStore(config).load()["updated_at"]
