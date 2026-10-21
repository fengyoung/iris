"""ingest/scanner.py 测试。

覆盖：markdown 标题提取、SHA256 计算、排除模式匹配、
以及基于临时目录的 scan_source_by_name 端到端扫描。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iris.ingest.chunker import MarkdownChunker
from iris.ingest.scanner import (
    SOURCE_INDEX_FILENAME,
    MarkdownScanner,
    _compute_sha256,
    _extract_markdown_title,
    _matches_any_pattern,
    is_generated_source_file,
)


# ── 纯函数 ──────────────────────────────────────────────────────

class TestExtractMarkdownTitle:
    def test_reads_first_h1(self, tmp_path):
        f = tmp_path / "doc.md"
        f.write_text("前言\n# 真正的标题\n正文", encoding="utf-8")
        assert _extract_markdown_title(f) == "真正的标题"

    def test_falls_back_to_stem_when_no_h1(self, tmp_path):
        f = tmp_path / "无标题.md"
        f.write_text("只有正文，没有一级标题", encoding="utf-8")
        assert _extract_markdown_title(f) == "无标题"


class TestComputeSha256:
    def test_stable_hash(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("hello", encoding="utf-8")
        h1 = _compute_sha256(f)
        # SHA256("hello")
        assert h1 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_differs_for_different_content(self, tmp_path):
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("x", encoding="utf-8")
        b.write_text("y", encoding="utf-8")
        assert _compute_sha256(a) != _compute_sha256(b)


class TestMatchesAnyPattern:
    def test_matches_relative_glob(self, tmp_path):
        root = tmp_path
        (tmp_path / "drafts").mkdir()
        candidate = tmp_path / "drafts" / "note.md"
        candidate.write_text("x", encoding="utf-8")
        assert _matches_any_pattern(candidate, root, ["drafts/*.md"]) is True

    def test_no_match(self, tmp_path):
        root = tmp_path
        candidate = tmp_path / "keep.md"
        candidate.write_text("x", encoding="utf-8")
        assert _matches_any_pattern(candidate, root, ["*.tmp"]) is False


# ── 端到端扫描 ──────────────────────────────────────────────────

def _make_config(source_root: Path, root: Path, *, exclude=None):
    """构造一个最小 config 替身（仅暴露 scanner 用到的属性）。"""
    return SimpleNamespace(
        root=root,
        data_source={
            "default_source": "test",
            "sources": {
                "test": {
                    "enabled": True,
                    "name": "测试源",
                    "path": str(source_root),
                    "format": "markdown",
                    "include_patterns": ["**/*.md"],
                    "exclude_patterns": exclude or [],
                }
            },
            "ingestion": {
                "max_file_size_mb": 20,
                "store_file_hash": True,
            },
        },
    )


class TestScanSourceByName:
    def test_scans_and_sorts_documents(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "b.md").write_text("# B 文档\n内容", encoding="utf-8")
        (source / "a.md").write_text("# A 文档\n内容", encoding="utf-8")

        scanner = MarkdownScanner(_make_config(source, tmp_path))
        summary = scanner.scan_source_by_name("test")

        assert summary.document_count == 2
        # 按 relative_path 排序
        assert [d.relative_path for d in summary.documents] == ["a.md", "b.md"]
        titles = {d.title for d in summary.documents}
        assert titles == {"A 文档", "B 文档"}
        # hash 已计算
        assert all(len(d.file_hash) == 64 for d in summary.documents)

    def test_respects_exclude_patterns(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "keep.md").write_text("# 保留", encoding="utf-8")
        (source / "skip.md").write_text("# 跳过", encoding="utf-8")

        scanner = MarkdownScanner(_make_config(source, tmp_path, exclude=["skip.md"]))
        summary = scanner.scan_source_by_name("test")

        rels = [d.relative_path for d in summary.documents]
        assert rels == ["keep.md"]

    def test_skips_oversized_files(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "small.md").write_text("# 小文件", encoding="utf-8")

        config = _make_config(source, tmp_path)
        config.data_source["ingestion"]["max_file_size_mb"] = 0  # 全部超限
        scanner = MarkdownScanner(config)
        summary = scanner.scan_source_by_name("test")
        assert summary.document_count == 0

    def test_skips_generated_source_index(self, tmp_path):
        """Iris 生成的 _INDEX.md 不计入文档语料（否则会被切块、嵌入成检索证据）。"""
        source = tmp_path / "src"
        source.mkdir()
        (source / "_INDEX.md").write_text("# SOURCE 文档索引\n> 自动生成", encoding="utf-8")
        (source / "doc.md").write_text("# 文档\n正文", encoding="utf-8")

        scanner = MarkdownScanner(_make_config(source, tmp_path))
        summary = scanner.scan_source_by_name("test")

        assert [d.relative_path for d in summary.documents] == ["doc.md"]
        assert summary.document_count == 1

    def test_keeps_nested_index_like_file(self, tmp_path):
        """子目录中的同名文件属于用户文档，不能被误删。"""
        source = tmp_path / "src"
        source.mkdir()
        (source / "08-参考资料").mkdir()
        (source / "08-参考资料" / "_INDEX.md").write_text("# 用户自己的索引", encoding="utf-8")

        scanner = MarkdownScanner(_make_config(source, tmp_path))
        summary = scanner.scan_source_by_name("test")

        assert [d.relative_path for d in summary.documents] == ["08-参考资料/_INDEX.md"]

    def test_scan_all_enabled_sources(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "a.md").write_text("# A", encoding="utf-8")
        scanner = MarkdownScanner(_make_config(source, tmp_path))
        summaries = scanner.scan_all_enabled_sources()
        assert len(summaries) == 1
        assert summaries[0].document_count == 1


class TestScanSummaryToDict:
    def test_round_trips_fields(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "a.md").write_text("# A", encoding="utf-8")
        scanner = MarkdownScanner(_make_config(source, tmp_path))
        summary = scanner.scan_source_by_name("test")
        d = summary.to_dict()
        assert d["source_name"] == "test"
        assert d["document_count"] == 1
        assert isinstance(d["documents"], list)
        assert d["documents"][0]["title"] == "A"


# ── 生成制品判定 ────────────────────────────────────────────────

class TestIsGeneratedSourceFile:
    def test_matches_root_source_index(self):
        assert is_generated_source_file(SOURCE_INDEX_FILENAME) is True

    def test_matches_path_object(self):
        assert is_generated_source_file(Path("_INDEX.md")) is True

    def test_nested_same_name_is_user_document(self):
        assert is_generated_source_file("08-参考资料/_INDEX.md") is False

    def test_ordinary_document_unaffected(self):
        assert is_generated_source_file("01-目标管理/20261009-Q3全面总结.md") is False


# ── 端到端：生成制品不进入检索语料 ──────────────────────────────

class TestGeneratedIndexNotChunked:
    def test_generated_index_produces_no_chunks(self, tmp_path):
        """_INDEX.md 不产生任何 chunk：检索语料里没有它。"""
        source = tmp_path / "src"
        source.mkdir()
        (source / "_INDEX.md").write_text(
            "# SOURCE 文档索引\n\n## 文档数量统计\n\n"
            "| 目录 | 文档数 |\n|------|-------:|\n| 01-目标管理 | 12 |\n",
            encoding="utf-8",
        )
        (source / "doc.md").write_text("# 文档\n正文内容", encoding="utf-8")

        chunker = MarkdownChunker(_make_config(source, tmp_path))
        summary = chunker.build_source_chunks("test")

        assert {c.relative_path for c in summary.chunks} == {"doc.md"}
        assert summary.document_count == 1
