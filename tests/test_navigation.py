"""测试 iris.wiki.navigation — Wiki 导航构建与辅助函数。"""

import pytest
from pathlib import Path
from iris.wiki.navigation import (
    NavBuildResult,
    _char_sequence_match,
    _parse_updated_from_content,
    _is_wiki_broken_link,
)


class TestNavBuildResult:
    def test_full_construction(self):
        r = NavBuildResult(
            nav_path="/wiki/index.md",
            pages_written=15,
            errors=["page not found"],
        )
        assert r.nav_path == "/wiki/index.md"
        assert r.pages_written == 15
        assert r.errors == ["page not found"]

    def test_empty_errors(self):
        r = NavBuildResult(nav_path="", pages_written=0, errors=[])
        assert r.errors == []


class TestCharSequenceMatch:
    def test_exact_match(self):
        assert _char_sequence_match("hello", "hello world") is True

    def test_substring_match(self):
        assert _char_sequence_match("abc", "xabcx") is True

    def test_no_match(self):
        assert _char_sequence_match("xyz", "abcdef") is False

    def test_short_is_longer_than_long(self):
        assert _char_sequence_match("abcdef", "abc") is False

    def test_empty_short(self):
        assert _char_sequence_match("", "anything") is True

    def test_empty_long(self):
        assert _char_sequence_match("something", "") is False

    def test_chinese_chars(self):
        assert _char_sequence_match("数据智能", "ExampleOrg技术研发部") is True
        assert _char_sequence_match("测试", "人工智能") is False


class TestParseUpdatedFromContent:
    def test_extracts_date_from_frontmatter(self):
        content = """---
title: Test
updated: 2026-07-15
---
Body text."""
        assert _parse_updated_from_content(content) == "2026-07-15"

    def test_extracts_updated_quoted(self):
        content = """---
updated: "2026-06-01"
---
Body."""
        assert _parse_updated_from_content(content) == "2026-06-01"

    def test_no_updated_field(self):
        content = """---
title: Test
---
Body."""
        assert _parse_updated_from_content(content) == ""

    def test_no_frontmatter(self):
        assert _parse_updated_from_content("Just content.") == ""

    def test_empty_content(self):
        assert _parse_updated_from_content("") == ""


class TestIsWikiBrokenLink:
    def test_exact_title_match(self):
        titles = {"张三": True}
        result = _is_wiki_broken_link("张三", titles)
        assert result is None  # 不是死链

    def test_substring_match(self):
        titles = {"数据平台建设": True}
        result = _is_wiki_broken_link("数据平台", titles)
        assert result is None  # 子串匹配

    def test_broken_link(self):
        titles = {"张三": True}
        result = _is_wiki_broken_link("李四李四李四李四", titles)
        assert result == "broken"

    def test_known_tech_term_ignored(self):
        """知名技术术语（如 BERT、XGBoost）不应视为死链。"""
        titles = {}
        result = _is_wiki_broken_link("BERT", titles)
        assert result is None

    def test_noise_pattern_ignored(self):
        """噪音模式（如单个点、破折号）忽略。"""
        titles = {}
        result = _is_wiki_broken_link("---", titles)
        assert result is None
