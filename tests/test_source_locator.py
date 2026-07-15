"""iris.evaluation._source_locator.SourceLocator 单元测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from iris.evaluation._source_locator import SourceLocator


# ── 测试数据 ────────────────────────────────────────────────

SAMPLE_CHUNKS = {
    "chunks": [
        {
            "relative_path": "docs/file1.md",
            "line_start": 1,
            "line_end": 20,
            "content": "文件1 第一个 chunk 内容，讲述项目背景",
        },
        {
            "relative_path": "docs/file1.md",
            "line_start": 21,
            "line_end": 40,
            "content": "文件1 第二个 chunk，关于实现细节",
        },
        {
            "relative_path": "docs/file2.md",
            "line_start": 1,
            "line_end": 15,
            "content": "文件2 唯一 chunk，数据分析结果",
        },
        {
            "relative_path": "other/file3.md",
            "line_start": 1,
            "line_end": 10,
            "content": "其他目录文件3 chunk",
        },
    ]
}

SECOND_SAMPLE_CHUNKS = {
    "chunks": [
        {
            "relative_path": "extra/fileX.md",
            "line_start": 1,
            "line_end": 5,
            "content": "额外文件 X",
        }
    ]
}


@pytest.fixture
def chunk_summary_file(tmp_path):
    """创建单个 chunk summary JSON 文件，返回路径。"""
    p = tmp_path / "main_chunk_summary.json"
    p.write_text(json.dumps(SAMPLE_CHUNKS, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def second_chunk_file(tmp_path):
    p = tmp_path / "extra_chunk_summary.json"
    p.write_text(json.dumps(SECOND_SAMPLE_CHUNKS, ensure_ascii=False), encoding="utf-8")
    return p


# ── load() ──────────────────────────────────────────────────


class TestLoad:
    def test_load_builds_index(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        locator.load()
        paths = locator.get_all_source_paths()
        assert "docs/file1.md" in paths
        assert "docs/file2.md" in paths
        assert "other/file3.md" in paths

    def test_missing_file_silently_skipped(self, tmp_path):
        nonexistent = str(tmp_path / "nonexistent.json")
        locator = SourceLocator([nonexistent])
        locator.load()  # 不应抛出
        assert locator.get_all_source_paths() == []

    def test_multiple_files_merged(self, chunk_summary_file, second_chunk_file):
        locator = SourceLocator([str(chunk_summary_file), str(second_chunk_file)])
        locator.load()
        paths = locator.get_all_source_paths()
        assert "extra/fileX.md" in paths
        assert "docs/file1.md" in paths


# ── lookup() ────────────────────────────────────────────────


class TestLookup:
    def test_lookup_by_path(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup("docs/file1.md")
        assert content is not None
        assert "第一个" in content  # 返回第一个 chunk

    def test_lookup_by_line_number(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup("docs/file1.md", line_number=25)
        assert content is not None
        assert "实现细节" in content  # line 25 在第二个 chunk (21-40)

    def test_lookup_no_line_returns_first(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup("docs/file1.md")
        assert "第一个" in content

    def test_lookup_with_leading_slash_prefix(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup("/docs/file1.md")
        assert content is not None

    def test_lookup_with_dot_slash_prefix(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup("./docs/file1.md")
        assert content is not None

    def test_lookup_backslash_normalized(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup("docs\\file1.md")
        assert content is not None

    def test_lookup_line_out_of_range_returns_last(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup("docs/file1.md", line_number=9999)
        assert content is not None
        # 行号越界返回最后一个 chunk
        assert "实现细节" in content

    def test_lookup_nonexistent_returns_none(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        assert locator.lookup("nonexistent/path.md") is None


# ── lookup_with_context() ────────────────────────────────────


class TestLookupWithContext:
    def test_center_chunk_with_context(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup_with_context("docs/file1.md", line_number=25)
        # context_extend=1，应包含中心 chunk (21-40) 及前 chunk (1-20)
        assert content is not None
        assert "第一个" in content
        assert "实现细节" in content

    def test_no_line_returns_first_chunk(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        content = locator.lookup_with_context("docs/file1.md")
        assert content is not None
        assert "第一个" in content

    def test_nonexistent_returns_none(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        assert locator.lookup_with_context("no/such/file.md") is None


# ── find_sibling_sources() ───────────────────────────────────


class TestFindSiblingSources:
    def test_same_directory(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        siblings = locator.find_sibling_sources("docs/file1.md")
        assert "docs/file2.md" in siblings
        assert "docs/file1.md" not in siblings

    def test_does_not_include_self(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        siblings = locator.find_sibling_sources("docs/file1.md")
        assert "docs/file1.md" not in siblings

    def test_max_count_respected(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        siblings = locator.find_sibling_sources("docs/file1.md", max_count=1)
        assert len(siblings) <= 1

    def test_root_level_file_returns_empty(self, tmp_path):
        """根目录文件（无父目录）应返回空列表。"""
        data = {"chunks": [{"relative_path": "rootfile.md", "line_start": 1, "line_end": 5, "content": "root"}]}
        p = tmp_path / "c.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        locator = SourceLocator([str(p)])
        siblings = locator.find_sibling_sources("rootfile.md")
        assert siblings == []


# ── search_sources_by_keywords() ─────────────────────────────


class TestSearchSourcesByKeywords:
    def test_hit(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        results = locator.search_sources_by_keywords(["file1"])
        assert len(results) >= 1
        assert any("file1" in r for r in results)

    def test_exclude_path(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        results = locator.search_sources_by_keywords(["file1"], exclude_path="docs/file1.md")
        assert all("file1" not in r for r in results)

    def test_max_results(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        results = locator.search_sources_by_keywords(["file"], max_results=1)
        assert len(results) <= 1

    def test_no_match_returns_empty(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        results = locator.search_sources_by_keywords(["zzznonexistentzzz"])
        assert results == []

    def test_case_insensitive(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        results = locator.search_sources_by_keywords(["FILE1"])
        assert len(results) >= 1


# ── 懒加载 ─────────────────────────────────────────────────


class TestLazyLoading:
    def test_first_lookup_triggers_load(self, chunk_summary_file):
        locator = SourceLocator([str(chunk_summary_file)])
        assert not locator._loaded
        locator.lookup("docs/file1.md")
        assert locator._loaded
