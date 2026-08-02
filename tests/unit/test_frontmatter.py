"""iris.core.frontmatter 模块单元测试。"""

from __future__ import annotations

import pytest
from iris.core.frontmatter import (
    DOC_TYPES,
    build_frontmatter,
    get_frontmatter_field,
    has_frontmatter,
    inject_frontmatter,
    parse_frontmatter,
)


class TestBuildFrontmatter:
    """build_frontmatter 函数测试。"""

    def test_basic_fields(self):
        """基本字段渲染为 YAML frontmatter 块。"""
        result = build_frontmatter({
            "title": "测试文档",
            "date": "2026-07-30",
            "type": "会议纪要",
        })
        assert result.startswith("---\n")
        assert result.endswith("\n---\n")
        assert "title: 测试文档" in result
        assert "date: 2026-07-30" in result
        assert "type: 会议纪要" in result

    def test_empty_fields_return_empty_string(self):
        """所有字段为空时返回空字符串。"""
        assert build_frontmatter({}) == ""
        assert build_frontmatter({"title": "", "date": None}) == ""  # type: ignore[arg-type]

    def test_skip_none_and_empty_values(self):
        """None 值和空字符串被自动跳过。"""
        result = build_frontmatter({
            "title": "有效标题",
            "author": None,  # type: ignore[dict-item]
            "notes": "",
            "type": "讨论思考",
        })
        assert "title: 有效标题" in result
        assert "author" not in result
        assert "notes" not in result
        assert "type: 讨论思考" in result

    def test_skip_empty_list(self):
        """空列表被跳过。"""
        result = build_frontmatter({
            "title": "测试",
            "tags": [],
        })
        assert "tags" not in result

    def test_list_rendering(self):
        """列表渲染为 YAML 序列。"""
        result = build_frontmatter({
            "title": "测试",
            "tags": ["a", "b", "c"],
        })
        assert "tags:" in result
        assert "  - a" in result
        assert "  - b" in result
        assert "  - c" in result

    def test_boolean_rendering(self):
        """布尔值渲染为 true/false。"""
        result = build_frontmatter({
            "title": "测试",
            "ai_processed": True,
            "draft": False,
        })
        assert "ai_processed: true" in result
        assert "draft: false" in result

    def test_integer_rendering(self):
        """整数值直接渲染。"""
        result = build_frontmatter({
            "title": "测试",
            "count": 42,
        })
        assert "count: 42" in result

    def test_quoting_colon_in_value(self):
        """含冒号空格的值加引号包裹。"""
        result = build_frontmatter({
            "title": "测试: 副标题",
        })
        # 应该被引号包裹
        assert '"测试: 副标题"' in result or "'测试: 副标题'" in result

    def test_quoting_special_chars(self):
        """含特殊字符的值加引号包裹。"""
        result = build_frontmatter({
            "title": "测试 [v1]",
        })
        assert '"测试 [v1]"' in result or "'测试 [v1]'" in result


class TestInjectFrontmatter:
    """inject_frontmatter 函数测试。"""

    def test_inject_before_content(self):
        """在正文前注入 frontmatter。"""
        content = "# Hello World\n\nSome text."
        result = inject_frontmatter(content, {"title": "Test", "date": "2026-07-30"})
        assert result.startswith("---\n")
        assert "# Hello World" in result

    def test_idempotent_existing_frontmatter(self):
        """已有 frontmatter 时跳过注入，返回原内容。"""
        content = "---\ntitle: Existing\n---\n\n# Body"
        result = inject_frontmatter(content, {"title": "New"})
        assert result == content

    def test_empty_fields_no_injection(self):
        """全部字段为空时返回原内容。"""
        content = "# Just content"
        result = inject_frontmatter(content, {})
        assert result == content

    def test_partial_fields_still_inject(self):
        """部分字段有效时仍注入。"""
        content = "# Content"
        result = inject_frontmatter(content, {"title": "Test", "notes": ""})
        assert result.startswith("---\n")
        assert "title: Test" in result
        assert "notes" not in result


class TestParseFrontmatter:
    """parse_frontmatter 函数测试。"""

    def test_parse_basic(self):
        """解析基本 frontmatter 并返回字段字典和正文。"""
        text = "---\ntitle: 测试文档\ndate: 2026-07-30\n---\n\n# 正文内容"
        fields, body = parse_frontmatter(text)
        assert fields == {"title": "测试文档", "date": "2026-07-30"}
        assert "# 正文内容" in body

    def test_parse_no_frontmatter(self):
        """无 frontmatter 时返回空字典和原文本。"""
        text = "# Just a heading\n\nSome content."
        fields, body = parse_frontmatter(text)
        assert fields == {}
        assert body == text

    def test_parse_strips_quotes(self):
        """引号包裹的值自动去除引号。"""
        text = '---\ntitle: "带引号的值"\nauthor: \'另一个值\'\n---\n\nBody'
        fields, body = parse_frontmatter(text)
        assert fields["title"] == "带引号的值"
        assert fields["author"] == "另一个值"

    def test_parse_crlf_line_endings(self):
        """处理 Windows 风格的 \\r\\n 换行符。"""
        text = "---\r\ntitle: 测试\r\ndate: 2026-07-30\r\n---\r\n\r\n# Body"
        fields, body = parse_frontmatter(text)
        assert fields == {"title": "测试", "date": "2026-07-30"}

    def test_parse_multiline_frontmatter(self):
        """解析多行 frontmatter。"""
        text = "---\ntitle: 测试\nstatus: active\ncreated: 2026-07-30\ntags:\n  - a\n  - b\n---\n\n# Body"
        fields, body = parse_frontmatter(text)
        assert fields["title"] == "测试"
        assert fields["status"] == "active"
        # 嵌套列表行不含 ":" → 不被解析为独立字段
        assert fields["tags"] == ""  # 空值行


class TestHasFrontmatter:
    """has_frontmatter 函数测试。"""

    def test_has_frontmatter_true(self):
        """以 frontmatter 开头的文本返回 True。"""
        assert has_frontmatter("---\ntitle: Test\n---\n\nBody") is True

    def test_has_frontmatter_false(self):
        """无 frontmatter 的文本返回 False。"""
        assert has_frontmatter("# Heading") is False
        assert has_frontmatter("Plain text") is False

    def test_has_frontmatter_bom(self):
        """BOM 前缀后跟 frontmatter 返回 True。"""
        assert has_frontmatter("﻿---\ntitle: Test\n---\n\nBody") is True


class TestGetFrontmatterField:
    """get_frontmatter_field 函数测试。"""

    def test_get_existing_field(self):
        """获取存在的字段值。"""
        text = "---\ntitle: 测试文档\ndate: 2026-07-30\n---\n\n# Body"
        assert get_frontmatter_field(text, "title") == "测试文档"
        assert get_frontmatter_field(text, "date") == "2026-07-30"

    def test_get_missing_field(self):
        """不存在的字段返回空字符串。"""
        text = "---\ntitle: 测试\n---\n\n# Body"
        assert get_frontmatter_field(text, "author") == ""

    def test_get_no_frontmatter(self):
        """无 frontmatter 时返回空字符串。"""
        assert get_frontmatter_field("# No fm", "title") == ""


class TestNeedsQuotingNumbers:
    """_needs_quoting 数字形式字符串测试。"""

    def _needs_q(self, s: str) -> bool:
        from iris.core.frontmatter import _needs_quoting
        return _needs_quoting(s)

    def test_integer_string_quoted(self):
        """纯数字字符串需要引号。"""
        assert self._needs_q("123") is True
        assert self._needs_q("0") is True
        assert self._needs_q("-5") is True
        assert self._needs_q("+3") is True

    def test_float_string_quoted(self):
        """浮点数字字符串需要引号。"""
        assert self._needs_q("3.14") is True
        assert self._needs_q(".5") is True
        assert self._needs_q("-5.2") is True

    def test_sci_notation_quoted(self):
        """科学计数法需要引号。"""
        assert self._needs_q("1e6") is True
        assert self._needs_q("1.5e-3") is True

    def test_hex_octal_binary_quoted(self):
        """十六进制/八进制/二进制需要引号。"""
        assert self._needs_q("0x1A") is True
        assert self._needs_q("0o77") is True
        assert self._needs_q("0b1010") is True

    def test_date_not_quoted(self):
        """日期格式（非纯数字）不需要引号。"""
        assert self._needs_q("2026-07-30") is False
        assert self._needs_q("v3.19.26") is False

    def test_normal_text_not_quoted(self):
        """普通文本不需要引号。"""
        assert self._needs_q("hello") is False
        assert self._needs_q("张三") is False


class TestDocTypes:
    """DOC_TYPES 常量测试。"""

    def test_all_types_covered(self):
        """确保核心文档类型都已定义。"""
        essential = [
            "meeting_minutes", "weekly_report", "chat_digest",
            "feishu_doc", "discussion", "proposal",
            "reference", "okr", "dept_mgmt",
            "work_briefing", "my_weekly",
        ]
        for key in essential:
            assert key in DOC_TYPES
            assert isinstance(DOC_TYPES[key], str)
            assert len(DOC_TYPES[key]) > 0
