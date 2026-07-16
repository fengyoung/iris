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
        assert _char_sequence_match("技术研发", "ExampleOrg技术研发部") is True
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


class TestContentQuality:
    def test_compute_content_quality(self, tmp_path):
        from iris.wiki.navigation import _compute_content_quality

        wiki_root = tmp_path / "LLM-WIKI"
        for d in ["01-领域", "02-概念"]:
            (wiki_root / d).mkdir(parents=True)

        (wiki_root / "01-领域" / "领域-搜索.md").write_text(
            "---\ntitle: 搜索\ntype: domain\nstatus: stable\n---\n## 摘要\n搜索领域。[[排序]]", encoding="utf-8")
        (wiki_root / "02-概念" / "概念-排序.md").write_text(
            "---\ntitle: 排序\ntype: concept\nstatus: stable\n---\n## 摘要\n排序算法。[[搜索]]", encoding="utf-8")

        titles = {
            "领域-搜索": wiki_root / "01-领域" / "领域-搜索.md",
            "概念-排序": wiki_root / "02-概念" / "概念-排序.md",
        }
        result = _compute_content_quality(titles, wiki_root)
        assert "info_density" in result
        assert "duplicates" in result


class TestCleanNoiseLinks:
    def test_removes_noise_patterns(self):
        from iris.wiki.navigation import _clean_noise_links

        content = "参考 [[---]] 和 [[...]] 以及 [[正常链接]]"
        cleaned = _clean_noise_links(content)
        assert "正常链接" in cleaned

    def test_preserves_valid_links(self):
        from iris.wiki.navigation import _clean_noise_links

        content = "见 [[项目Alpha]] 和 [[概念-排序]]"
        cleaned = _clean_noise_links(content)
        assert "项目Alpha" in cleaned
        assert "概念-排序" in cleaned


class TestWikiNavigationBuilder:
    def test_build_on_empty_dir(self, tmp_path):
        from iris.wiki.navigation import WikiNavigationBuilder
        from iris.config.models import ConfigBundleV2

        wiki_root = tmp_path / "LLM-WIKI"
        wiki_root.mkdir()

        bundle = ConfigBundleV2.from_dicts(
            root=tmp_path, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t", "sources": {"t": {"path": str(tmp_path)}}},
            llm_dict={},
            wiki_dict={"wiki_root": str(wiki_root)},
        )
        builder = WikiNavigationBuilder(bundle)
        result = builder.build()
        assert result.pages_written == 0
        assert result.nav_path

    def test_build_creates_index(self, tmp_path):
        from iris.wiki.navigation import WikiNavigationBuilder
        from iris.config.models import ConfigBundleV2

        wiki_root = tmp_path / "LLM-WIKI"
        for d in ["01-领域", "02-概念"]:
            (wiki_root / d).mkdir(parents=True)

        (wiki_root / "01-领域" / "领域-搜索.md").write_text(
            "---\ntitle: 搜索\ntype: domain\nstatus: stable\n---\n## 摘要\n搜索领域概述。", encoding="utf-8")
        (wiki_root / "02-概念" / "概念-排序.md").write_text(
            "---\ntitle: 排序\ntype: concept\nstatus: stable\n---\n## 摘要\n排序算法。", encoding="utf-8")

        bundle = ConfigBundleV2.from_dicts(
            root=tmp_path, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "t", "sources": {"t": {"path": str(tmp_path)}}},
            llm_dict={},
            wiki_dict={"wiki_root": str(wiki_root)},
        )
        builder = WikiNavigationBuilder(bundle)
        result = builder.build(write=True)
        assert result.pages_written == 2

        index_path = wiki_root / "index.md"
        assert index_path.exists()
        content = index_path.read_text(encoding="utf-8")
        assert "搜索" in content
        assert "排序" in content
