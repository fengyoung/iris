"""deep_eval 纯函数与数据类单元测试（不调用 LLM）。"""

from __future__ import annotations


from iris.evaluation.deep_eval import (
    parse_references,
    extract_page_title,
    _get_accuracy_prompt,
    _get_page_accuracy_prompt,
    _get_comprehensiveness_prompt,
)
from iris.evaluation._types import (
    ReferenceEntry,
    AccuracyVerdict,
    CoverageGap,
)


# ─────────────────────────────────────────────────────────────
# parse_references
# ─────────────────────────────────────────────────────────────

class TestParseReferences:
    def test_empty_content(self):
        assert parse_references("") == []

    def test_no_section(self):
        content = "## 概述\n一些文字\n"
        assert parse_references(content) == []

    def test_format1_with_line_number(self):
        content = "## 参考来源\n[docs/file.md:10] 这是描述\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "docs/file.md"
        assert refs[0].line_number == 10
        assert refs[0].description == "这是描述"

    def test_format1_no_line_number(self):
        content = "## 参考来源\n[docs/file.md] 描述内容\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "docs/file.md"
        assert refs[0].line_number is None
        assert "描述内容" in refs[0].description

    def test_format2_numbered_with_line(self):
        content = "## 参考来源\n1. docs/file.md:50 这是一段描述\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "docs/file.md"
        assert refs[0].line_number == 50

    def test_format2_with_chapter_note(self):
        content = "## 参考来源\n1. docs/report.md:50（第二章）关键结论\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "docs/report.md"
        assert refs[0].line_number == 50
        assert "关键结论" in refs[0].description

    def test_format3_no_line_number(self):
        content = "## 参考来源\n1. docs/summary.md 整体概述\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "docs/summary.md"
        assert refs[0].line_number is None

    def test_format4_line_range(self):
        # 修复后 RANGE_PATTERN 使用非贪婪匹配，能正确捕获完整路径
        content = "## 参考来源\ndocs/doc.md:109-116\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].line_number == 109
        assert refs[0].source_path == "docs/doc.md"

    def test_format4_nested_path_regression(self):
        """回归测试：深层路径不能被贪婪 .* 截断。"""
        content = "## 参考来源\nSOURCE/05-会议纪要/2024/report.md:109-116\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "SOURCE/05-会议纪要/2024/report.md"
        assert refs[0].line_number == 109

    def test_format4_with_leading_space(self):
        """格式4 现用 search()，能匹配行中任意位置的路径。"""
        content = "## 参考来源\n详见 SOURCE/dir/file.md:10-20 了解详情\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "SOURCE/dir/file.md"
        assert refs[0].line_number == 10

    def test_inline_format_skipped(self):
        content = "## 参考来源\n1. 1. 使用语境：某某项目\n"
        refs = parse_references(content)
        assert refs == []

    def test_stops_at_next_heading(self):
        content = "## 参考来源\n1. docs/a.md:1 描述A\n## 其他章节\n1. docs/b.md:2 描述B\n"
        refs = parse_references(content)
        assert len(refs) == 1
        assert refs[0].source_path == "docs/a.md"

    def test_empty_lines_skipped(self):
        content = "## 参考来源\n\n1. docs/file.md:5 有效引用\n\n"
        refs = parse_references(content)
        assert len(refs) == 1

    def test_multiple_references(self):
        content = (
            "## 参考来源\n"
            "[docs/a.md:1] 引用A\n"
            "1. docs/b.md:20 引用B\n"
            "1. docs/c.md 无行号引用\n"
        )
        refs = parse_references(content)
        assert len(refs) == 3
        paths = [r.source_path for r in refs]
        assert "docs/a.md" in paths
        assert "docs/b.md" in paths
        assert "docs/c.md" in paths


# ─────────────────────────────────────────────────────────────
# extract_page_title
# ─────────────────────────────────────────────────────────────

class TestExtractPageTitle:
    def test_from_frontmatter(self):
        content = "---\ntitle: 搜索质量评估\ntype: domain\n---\n正文"
        assert extract_page_title(content, "搜索质量评估.md") == "搜索质量评估"

    def test_fallback_to_filename(self):
        content = "正文内容，没有 frontmatter"
        assert extract_page_title(content, "领域-搜索引擎.md") == "领域-搜索引擎"

    def test_filename_strip_extension(self):
        content = ""
        result = extract_page_title(content, "人物-张三.md")
        assert result == "人物-张三"


# ─────────────────────────────────────────────────────────────
# 提示词函数
# ─────────────────────────────────────────────────────────────

class TestPromptFunctions:
    def test_get_accuracy_prompt_nonempty(self):
        prompt = _get_accuracy_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 10

    def test_get_page_accuracy_prompt_nonempty(self):
        prompt = _get_page_accuracy_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 10

    def test_get_comprehensiveness_prompt_nonempty(self):
        prompt = _get_comprehensiveness_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 10


# ─────────────────────────────────────────────────────────────
# 数据类默认字段
# ─────────────────────────────────────────────────────────────

class TestDataclassDefaults:
    def test_reference_entry_defaults(self):
        entry = ReferenceEntry(
            raw="[docs/a.md:1] 描述",
            source_path="docs/a.md",
            line_number=1,
            description="描述",
        )
        assert entry.resolved_chunk is None
        assert entry.resolved_context is None

    def test_accuracy_verdict_default_detail(self):
        entry = ReferenceEntry(raw="raw", source_path="a.md", line_number=None, description="")
        verdict = AccuracyVerdict(reference=entry, verdict="consistent")
        assert verdict.detail == ""

    def test_coverage_gap_default_detail(self):
        gap = CoverageGap(source_path="docs/a.md", missing_topic="缺少关键信息")
        assert gap.detail == ""
