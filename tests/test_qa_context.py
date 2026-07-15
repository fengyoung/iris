"""qa/context.py PromptContextPacker + _compress_text 单元测试。"""

from __future__ import annotations

import types

import pytest

from iris.qa.context import PromptContextPacker, PackedPromptContext, _compress_text
from iris.qa.models import AnswerBlock, Citation


# ── 测试辅助 ──────────────────────────────────────────────


def _make_config(max_chars=6000, max_blocks=6, max_wiki=3, max_block_summary=300, max_wiki_summary=200):
    """构建模拟 ConfigBundle（兼容 ConfigBundle __getitem__ + .app dict 访问）。"""
    return types.SimpleNamespace(
        app={"qa": {
            "max_prompt_context_chars": max_chars,
            "max_evidence_blocks": max_blocks,
            "max_wiki_hits": max_wiki,
            "max_block_summary_chars": max_block_summary,
            "max_wiki_summary_chars": max_wiki_summary,
        }},
    )


def _make_block(title="测试", summary="测试内容", score=1.0):
    return AnswerBlock(
        title=title,
        summary=summary,
        citation=Citation(relative_path="test/source.md", section_path=["章节1"], line_start=1, line_end=10),
        score=score,
    )


# ── _compress_text ─────────────────────────────────────────


def test_compress_text_no_truncation():
    assert _compress_text("短文本", 100) == "短文本"


def test_compress_text_truncation():
    long_text = "这是一个很长的文本" * 20
    result = _compress_text(long_text, 50)
    assert len(result) <= 50
    assert result.endswith("…")


def test_compress_text_normalizes_whitespace():
    assert _compress_text("hello   world", 100) == "hello world"


def test_compress_text_minimum():
    assert _compress_text("hello world", 1) == "h"


# ── PromptContextPacker ────────────────────────────────────


class TestPromptContextPacker:

    def test_pack_empty(self):
        packer = PromptContextPacker(_make_config())
        result = packer.pack([], [])
        assert result.metadata["selected_blocks"] == 0
        assert result.metadata["selected_wiki_hits"] == 0

    def test_pack_with_blocks(self):
        packer = PromptContextPacker(_make_config(max_chars=10000))
        blocks = [_make_block(f"block{i}", f"content of block {i}") for i in range(3)]
        result = packer.pack(blocks, [])
        assert len(result.blocks) == 3
        assert result.metadata["selected_blocks"] == 3

    def test_pack_with_wiki_hits(self):
        packer = PromptContextPacker(_make_config(max_chars=10000))
        wiki_hits = [
            {"title": f"Wiki{i}", "relative_path": f"path{i}.md", "summary": f"summary {i}"}
            for i in range(2)
        ]
        result = packer.pack([], wiki_hits)
        assert len(result.wiki_hits) == 2

    def test_pack_budget_exceeded(self):
        """预算极小时 block_truncated 标记为 True。"""
        packer = PromptContextPacker(_make_config(max_chars=200))
        blocks = [_make_block(f"block{i}", "content") for i in range(5)]
        result = packer.pack(blocks, [])
        # 预算太小时至少能装下 1 个（first-block 特殊处理），但后续会截断
        assert result.metadata["block_truncated"] is True

    def test_pack_wiki_truncated(self):
        """Many wiki hits exceeding budget。"""
        packer = PromptContextPacker(_make_config(max_chars=200, max_wiki=10))
        wiki_hits = [
            {"title": f"Wiki{i}", "relative_path": f"path{i}.md", "summary": f"long summary text {i}"}
            for i in range(10)
        ]
        result = packer.pack([], wiki_hits)
        assert result.metadata["wiki_truncated"] is True

    def test_pack_metadata_fields(self):
        packer = PromptContextPacker(_make_config(max_chars=6000))
        blocks = [_make_block()]
        result = packer.pack(blocks, [])
        assert "budget_chars" in result.metadata
        assert "used_chars" in result.metadata
        assert "original_blocks" in result.metadata
        assert "selected_blocks" in result.metadata

    def test_pack_respects_max_blocks(self):
        packer = PromptContextPacker(_make_config(max_chars=10000, max_blocks=2))
        blocks = [_make_block(f"block{i}") for i in range(5)]
        result = packer.pack(blocks, [])
        assert len(result.blocks) <= 2

    def test_pack_respects_max_wiki(self):
        packer = PromptContextPacker(_make_config(max_chars=10000, max_wiki=2))
        wiki_hits = [
            {"title": f"Wiki{i}", "relative_path": f"path{i}.md", "summary": f"summary {i}"}
            for i in range(5)
        ]
        result = packer.pack([], wiki_hits)
        assert len(result.wiki_hits) <= 2
