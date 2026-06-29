"""深度评估模块测试（从 iris2 迁移）。"""

import pytest
from iris.evaluation.deep_eval import (
    parse_references,
    extract_page_title,
    ReferenceEntry,
    SourceLocator,
    AccuracyVerifier,
    AccuracyVerdict,
    DeepEvalResult,
)


class TestParseReferences:
    """验证 Wiki 参考来源解析。"""

    def test_bracket_format_with_line(self):
        content = """## 摘要
test

## 参考来源
1. [SOURCE/path/to/doc.md:42] 该方案将准确率提升到 95%
2. [SOURCE/other.md:10] 图像采集3.0 项目启动
"""
        entries = parse_references(content)
        assert len(entries) == 2
        assert entries[0].source_path == "SOURCE/path/to/doc.md"
        assert entries[0].line_number == 42
        assert "95%" in entries[0].description
        assert entries[1].source_path == "SOURCE/other.md"
        assert entries[1].line_number == 10

    def test_bracket_format_without_line(self):
        content = """## 参考来源
1. [SOURCE/doc.md] 概述文档
"""
        entries = parse_references(content)
        assert len(entries) == 1
        assert entries[0].source_path == "SOURCE/doc.md"
        assert entries[0].line_number is None

    def test_numbered_path_with_line(self):
        content = """## 参考来源
1. SOURCE/doc.md:42（关键数据）准确率数据来源
2. SOURCE/other.md:10 项目启动记录
"""
        entries = parse_references(content)
        assert len(entries) >= 1
        assert entries[0].source_path.endswith("doc.md")

    def test_no_reference_section(self):
        content = """## 摘要
no references here

## 正文
some content
"""
        entries = parse_references(content)
        assert len(entries) == 0

    def test_empty_content(self):
        entries = parse_references("")
        assert len(entries) == 0


class TestExtractPageTitle:
    def test_from_frontmatter(self):
        content = """---
title: 图像采集3.0 项目
type: project
---
正文内容
"""
        assert extract_page_title(content, "file.md") == "图像采集3.0 项目"

    def test_fallback_to_filename(self):
        content = """# 没有 frontmatter 的内容"""
        assert extract_page_title(content, "图像采集3.0.md") == "图像采集3.0"


class TestSourceLocator:
    def test_load_and_lookup(self, tmp_path):
        """验证 chunk 索引的加载和查找。"""
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({
            "chunks": [
                {
                    "relative_path": "SOURCE/test/doc.md",
                    "line_start": 1,
                    "line_end": 10,
                    "content": "这是测试文档的第一段内容。",
                },
                {
                    "relative_path": "SOURCE/test/doc.md",
                    "line_start": 11,
                    "line_end": 20,
                    "content": "这是测试文档的第二段内容，提到了图像采集3.0项目。",
                },
                {
                    "relative_path": "SOURCE/other.md",
                    "line_start": 1,
                    "line_end": 5,
                    "content": "另一份文档。",
                },
            ],
        }, ensure_ascii=False), encoding="utf-8")

        locator = SourceLocator([str(summary_path)])
        locator.load()

        # 按路径查找
        content = locator.lookup("SOURCE/test/doc.md")
        assert "第一段" in content

        # 按行号查找
        content = locator.lookup("SOURCE/test/doc.md", line_number=15)
        assert "图像采集3.0" in content

        # 不存在的文件
        assert locator.lookup("SOURCE/nonexistent.md") is None

    def test_find_sibling_sources(self, tmp_path):
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({
            "chunks": [
                {"relative_path": "SOURCE/2025/doc_a.md", "line_start": 1, "line_end": 5,
                 "content": "文档A内容" * 50},
                {"relative_path": "SOURCE/2025/doc_b.md", "line_start": 1, "line_end": 5,
                 "content": "文档B内容" * 30},
                {"relative_path": "SOURCE/2025/doc_c.md", "line_start": 1, "line_end": 5,
                 "content": "文档C内容" * 10},
                {"relative_path": "SOURCE/other/doc_d.md", "line_start": 1, "line_end": 5,
                 "content": "文档D内容"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        locator = SourceLocator([str(summary_path)])
        locator.load()

        siblings = locator.find_sibling_sources("SOURCE/2025/doc_a.md", max_count=3)
        assert len(siblings) >= 1
        assert all("2025" in s for s in siblings)

    def test_search_by_keywords(self, tmp_path):
        import json
        summary_path = tmp_path / "chunk_summary.json"
        summary_path.write_text(json.dumps({
            "chunks": [
                {"relative_path": "SOURCE/图像采集3.0/设计文档.md", "line_start": 1, "line_end": 5,
                 "content": "图像采集3.0 设计"},
                {"relative_path": "SOURCE/其他/不相关.md", "line_start": 1, "line_end": 5,
                 "content": "无关内容"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        locator = SourceLocator([str(summary_path)])
        locator.load()

        results = locator.search_sources_by_keywords(["拍照"], max_results=3)
        assert len(results) == 1
        assert "图像采集3.0" in results[0]


class TestDeepEvalResult:
    def test_result_creation(self):
        result = DeepEvalResult(
            evaluated_at="2026-06-29T10:00:00",
            total_pages=5,
            total_references=20,
            consistent_count=15,
            inconsistent_count=3,
            unverifiable_count=1,
            source_missing_count=1,
            overall_accuracy_rate=0.833,
            pages_with_gaps=2,
            total_gaps=4,
            overall_comprehensiveness_note="测试",
        )
        assert result.overall_accuracy_rate == 0.833
        assert result.total_references == 20

    def test_json_export(self):
        from iris.evaluation import deep_eval_result_to_json

        result = DeepEvalResult(
            evaluated_at="2026-06-29",
            total_pages=1, total_references=3,
            consistent_count=2, inconsistent_count=1,
            unverifiable_count=0, source_missing_count=0,
            overall_accuracy_rate=0.667,
            pages_with_gaps=0, total_gaps=0,
            overall_comprehensiveness_note="",
        )
        d = deep_eval_result_to_json(result)
        assert d["total_pages"] == 1
        assert d["accuracy"]["overall_rate"] == 0.667
