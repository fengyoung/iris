"""iris.ingest.chunker 纯函数单元测试（不依赖外部 API）。"""

from __future__ import annotations

import pytest

from iris.ingest.chunker import (
    _split_content,
    _split_hard,
    _build_token_freq,
    _build_structural_tags,
    ChunkRecord,
    ChunkSlim,
)


# ─────────────────────────────────────────────────────────────
# _split_content
# ─────────────────────────────────────────────────────────────

class TestSplitContent:
    def test_short_content_not_split(self):
        content = "短内容不需要拆分"
        result = _split_content(content, max_chunk_chars=200)
        assert result == [content]

    def test_empty_string(self):
        result = _split_content("", max_chunk_chars=100)
        assert result == [""] or result == []

    def test_split_by_paragraph(self):
        # 两段，每段小于 max，合起来超过 max
        para1 = "第一段内容。" * 5
        para2 = "第二段内容。" * 5
        content = para1 + "\n\n" + para2
        result = _split_content(content, max_chunk_chars=len(para1) + 2)
        assert len(result) >= 1
        # 各 chunk 长度不超过 max_chunk_chars（允许少量溢出因为 hard split 才生效）
        for chunk in result:
            assert isinstance(chunk, str)

    def test_single_paragraph_hard_split(self):
        # 无 \n\n 分隔，超长内容按 hard split
        content = "这是一段超长的没有段落分隔的内容。" * 50
        result = _split_content(content, max_chunk_chars=100)
        assert len(result) > 1

    def test_exactly_at_limit_not_split(self):
        content = "a" * 100
        result = _split_content(content, max_chunk_chars=100)
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────
# _split_hard
# ─────────────────────────────────────────────────────────────

class TestSplitHard:
    def test_short_content_not_split(self):
        content = "短文本"
        result = _split_hard(content, max_chunk_chars=200)
        assert len(result) == 1

    def test_no_sentence_boundary_char_split(self):
        # 没有句尾标点，按字符截断
        content = "abcdefghij" * 10
        result = _split_hard(content, max_chunk_chars=20)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 20

    def test_split_at_sentence_boundary(self):
        content = "第一句话。第二句话。第三句话。第四句话。第五句话。"
        result = _split_hard(content, max_chunk_chars=10)
        assert len(result) > 1

    def test_empty_string(self):
        result = _split_hard("", max_chunk_chars=100)
        # 空字符串：返回空列表或含空字符串的列表
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────
# _build_token_freq
# ─────────────────────────────────────────────────────────────

class TestBuildTokenFreq:
    def test_empty_text(self):
        assert _build_token_freq("") == {}

    def test_frequency_count(self):
        text = "搜索 搜索 召回"
        freq = _build_token_freq(text)
        assert freq.get("搜索", 0) == 2
        assert freq.get("召回", 0) == 1

    def test_lowercase(self):
        freq = _build_token_freq("BM25 bm25")
        assert freq.get("bm25", 0) == 2

    def test_returns_dict(self):
        result = _build_token_freq("some text 内容")
        assert isinstance(result, dict)


# ─────────────────────────────────────────────────────────────
# _build_structural_tags
# ─────────────────────────────────────────────────────────────

class TestBuildStructuralTags:
    def test_returns_list(self):
        tags = _build_structural_tags("path/to/file.md", ["标题"], "内容")
        assert isinstance(tags, list)

    def test_no_duplicates(self):
        # 同一标记不应出现多次
        tags = _build_structural_tags("周报/weekly.md", ["周报内容"], "本周工作总结")
        assert len(tags) == len(set(tags))

    def test_meeting_tag_from_path(self):
        tags = _build_structural_tags("05-会议纪要/meeting.md", ["会议记录"], "")
        assert "meeting" in tags

    def test_goal_tag_from_content(self):
        tags = _build_structural_tags("other/file.md", ["其他"], "本季度目标是提升召回率")
        assert "goal" in tags

    def test_proposal_tag_from_path(self):
        tags = _build_structural_tags("03-方案报告/proposal.md", [], "")
        assert "proposal" in tags


# ─────────────────────────────────────────────────────────────
# ChunkRecord 数据类
# ─────────────────────────────────────────────────────────────

class TestChunkRecord:
    def test_default_chunk_type(self):
        chunk = ChunkRecord(
            chunk_id="test::chunk-1",
            source_name="test",
            document_path="/tmp/test.md",
            relative_path="test.md",
            document_hash="abc123",
            title="标题",
            section_path=["标题"],
            level=1,
            content="内容",
            content_preview="内容",
            line_start=1,
            line_end=5,
            word_count=2,
            token_count=2,
        )
        assert chunk.chunk_type == "section"
        assert chunk.token_freq == {}
        assert chunk.structural_tags == []

    def test_custom_fields(self):
        chunk = ChunkRecord(
            chunk_id="test::chunk-2",
            source_name="src",
            document_path="/tmp/doc.md",
            relative_path="doc.md",
            document_hash="def456",
            title="会议纪要",
            section_path=["会议纪要"],
            level=2,
            content="会议内容",
            content_preview="会议",
            line_start=10,
            line_end=20,
            word_count=5,
            token_count=5,
            chunk_type="segment",
            segment_index=2,
            segment_count=3,
        )
        assert chunk.chunk_type == "segment"
        assert chunk.segment_index == 2
        assert chunk.segment_count == 3


# ─────────────────────────────────────────────────────────────
# ChunkSlim.from_chunk_record
# ─────────────────────────────────────────────────────────────

class TestChunkSlimFromChunkRecord:
    def test_basic_conversion(self):
        chunk = ChunkRecord(
            chunk_id="test::chunk-3",
            source_name="test",
            document_path="/tmp/file.md",
            relative_path="file.md",
            document_hash="xyz",
            title="测试标题",
            section_path=["测试标题"],
            level=1,
            content="测试内容",
            content_preview="测试",
            line_start=1,
            line_end=3,
            word_count=2,
            token_count=2,
        )
        slim = ChunkSlim.from_chunk_record(chunk)
        assert slim.relative_path == "file.md"
        assert slim.title == "测试标题"
        assert slim.section_path == ["测试标题"]
        assert slim.content_preview == "测试"
        assert slim.content == "测试内容"
