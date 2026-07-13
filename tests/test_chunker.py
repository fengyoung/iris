"""测试 chunker 模块：iter_chunk_items 及 MarkdownChunker。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from iris.ingest.chunker import iter_chunk_items


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False))


def test_iter_chunk_items_empty_sources():
    """无数据源 → 空迭代。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        items = list(iter_chunk_items(root, {}))
    assert items == []


def test_iter_chunk_items_disabled_source():
    """禁用的数据源应跳过。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        summary = root / "disabled_source_chunk_summary.json"
        _write_json(summary, {"chunks": [{"id": "c1"}]})
        sources = {"disabled_source": {"enabled": False}}
        items = list(iter_chunk_items(root, sources))
    assert items == []


def test_iter_chunk_items_missing_file():
    """chunk_summary 不存在 → 跳过，不抛异常。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = {"ghost_source": {"enabled": True}}
        items = list(iter_chunk_items(root, sources))
    assert items == []


def test_iter_chunk_items_yields_chunks():
    """正常加载 chunk 条目。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        summary = root / "src1_chunk_summary.json"
        _write_json(summary, {
            "chunks": [
                {"chunk_id": "c1", "content": "hello", "source_name": "src1"},
                {"chunk_id": "c2", "content": "world", "source_name": "src1"},
            ]
        })
        sources = {"src1": {"enabled": True}}
        items = list(iter_chunk_items(root, sources))
    assert len(items) == 2
    assert items[0]["chunk_id"] == "c1"
    assert items[1]["chunk_id"] == "c2"


def test_iter_chunk_items_multiple_sources():
    """多个数据源合并迭代。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_json(root / "a_chunk_summary.json", {"chunks": [{"id": "a1"}]})
        _write_json(root / "b_chunk_summary.json", {"chunks": [{"id": "b1"}, {"id": "b2"}]})
        sources = {"a": {"enabled": True}, "b": {"enabled": True}}
        items = list(iter_chunk_items(root, sources))
    assert len(items) == 3


def test_iter_chunk_items_corrupted_json():
    """JSON 损坏 → 跳过该数据源，不抛异常。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "bad_chunk_summary.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not valid json {{{", encoding="utf-8")
        _write_json(root / "good_chunk_summary.json", {"chunks": [{"id": "g1"}]})
        sources = {"bad": {"enabled": True}, "good": {"enabled": True}}
        items = list(iter_chunk_items(root, sources))
    assert len(items) == 1
    assert items[0]["id"] == "g1"


def test_iter_chunk_items_empty_chunks_list():
    """数据源的 chunks 为空列表 → 无条目产出。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_json(root / "src_chunk_summary.json", {"chunks": []})
        sources = {"src": {"enabled": True}}
        items = list(iter_chunk_items(root, sources))
    assert items == []


def test_iter_chunk_items_non_dict_source_skipped():
    """非 dict 类型的 source value 跳过。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # "string_val" source — not a dict with "enabled" key → skipped
        sources = {"string_val": "not_a_dict"}
        items = list(iter_chunk_items(root, sources))
    assert items == []
