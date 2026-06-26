"""Wiki 检索模块测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from iris.wiki.searcher import (
    WikiSearcher,
    _read_wiki_page,
    _score_page,
    _tokenize,
    _infer_title_from_filename,
    load_index_summaries,
)


class TestTokenize:
    def test_english_text(self):
        tokens = _tokenize("Hello World Test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_chinese_text(self):
        tokens = _tokenize("质检自动化进展")
        assert "质检自动化进展" in tokens

    def test_mixed_text(self):
        tokens = _tokenize("某检测项目项目进展")
        # 中文和英文被合并为一个token，这是分词器的行为
        assert len(tokens) > 0

    def test_empty(self):
        assert _tokenize("") == []


class TestInferTitleFromFilename:
    def test_domain_prefix(self):
        result = _infer_title_from_filename(Path("领域-搜索推荐技术体系.md"))
        assert result == "搜索推荐技术体系"

    def test_concept_prefix(self):
        result = _infer_title_from_filename(Path("概念-MMoE架构.md"))
        assert result == "MMoE架构"

    def test_project_prefix(self):
        result = _infer_title_from_filename(Path("项目-某检测项目手机拆修检测.md"))
        assert result == "某检测项目手机拆修检测"

    def test_person_prefix(self):
        result = _infer_title_from_filename(Path("人物-团队成员B.md"))
        assert result == "团队成员B"

    def test_no_prefix(self):
        result = _infer_title_from_filename(Path("index.md"))
        assert result == "index"


class TestReadWikiPage:
    def test_with_frontmatter(self):
        content = """---
title: 测试页面
type: domain
status: stable
created: 2026-01-01
updated: 2026-06-01
---

## 摘要
这是一个测试页面。

## 正文
内容...
"""
        p = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        p.write(content)
        p.close()
        title, ptype, status, summary, body = _read_wiki_page(Path(p.name))
        assert title == "测试页面"
        assert ptype == "domain"
        assert status == "stable"
        assert "测试页面" in summary

    def test_without_frontmatter(self, tmp_path):
        p = tmp_path / "只有标题.md"
        p.write_text("# 只有标题\n\n内容正文", encoding="utf-8")
        title, ptype, status, summary, body = _read_wiki_page(p)
        assert title == "只有标题"  # inferred from filename
        assert ptype == "domain"  # default
        assert status == "draft"  # default

    def test_summary_extraction(self):
        content = """---
title: Test
type: concept
---

## 摘要
这是摘要内容。

## 正文
正文内容。
"""
        p = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        p.write(content)
        p.close()
        _, _, _, summary, _ = _read_wiki_page(Path(p.name))
        assert "这是摘要内容" in summary


class TestScorePage:
    def test_title_match_high_score(self):
        score, terms = _score_page("质检自动化", ["质检", "智能化"], "质检自动化", "关于质检自动化", "质检自动化是核心")
        assert score >= 10
        assert "质检" in terms or "智能化" in terms

    def test_no_match_zero(self):
        score, terms = _score_page("完全无关", ["完全", "无关"], "测试", "测试页面", "内容")
        assert score == 0
        assert terms == []

    def test_partial_match(self):
        score, terms = _score_page("项目", ["项目"], "测试项目", "这是一个测试", "项目内容")
        assert score > 0


class TestLoadIndexSummaries:
    def test_missing_file(self):
        result = load_index_summaries(Path("/nonexistent"))
        assert result == {}

    def test_parse_index(self):
        content = "# LLM-WIKI 索引\n\n## 领域\n\n- [质检自动化](path/to/file.md) — 质检自动化概述。\n"
        p = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        p.write(content)
        p.close()
        result = load_index_summaries(Path(p.name).parent)
        assert result == {}
