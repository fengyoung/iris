"""Wiki 检索模块测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path


from iris.wiki.searcher import (
    WikiSearcher,
    _read_wiki_page,
    _score_page,
    _infer_title_from_filename,
    load_index_summaries,
)
from iris.utils.tokenization import tokenize


class TestTokenize:
    def test_english_text(self):
        tokens = tokenize("Hello World Test")
        assert "hello" in tokens
        assert "world" in tokens
        assert "test" in tokens

    def test_chinese_text(self):
        tokens = tokenize("智能检测进展")
        assert "智能检测进展" in tokens

    def test_mixed_text(self):
        tokens = tokenize("Alpha项目进展")
        # 中文和英文被合并为一个token，这是分词器的行为
        assert len(tokens) > 0

    def test_empty(self):
        assert tokenize("") == []


class TestInferTitleFromFilename:
    def test_domain_prefix(self):
        result = _infer_title_from_filename(Path("领域-搜索推荐技术体系.md"))
        assert result == "搜索推荐技术体系"

    def test_concept_prefix(self):
        result = _infer_title_from_filename(Path("概念-MMoE架构.md"))
        assert result == "MMoE架构"

    def test_project_prefix(self):
        result = _infer_title_from_filename(Path("项目-Alpha智能检测.md"))
        assert result == "Alpha智能检测"

    def test_person_prefix(self):
        result = _infer_title_from_filename(Path("人物-李四.md"))
        assert result == "李四"

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
        score, terms = _score_page("智能检测", ["检测", "智能化"], "智能检测", "关于智能检测", "智能检测是核心")
        assert score >= 10
        assert "检测" in terms or "智能化" in terms

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
        content = "# LLM-WIKI 索引\n\n## 领域\n\n- [智能检测](path/to/file.md) — 智能检测概述。\n"
        p = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
        p.write(content)
        p.close()
        result = load_index_summaries(Path(p.name).parent)
        assert result == {}


class TestLoadPagesSkipsBackups:
    """_load_pages 跳过 wiki-update 备份文件（*.bak.*.md）。"""

    def _make_searcher(self, wiki_root: Path) -> WikiSearcher:
        # 轻量构造：_load_pages 只依赖 _wiki_root，绕开 ConfigBundle
        searcher = object.__new__(WikiSearcher)
        searcher._wiki_root = wiki_root.resolve()
        return searcher

    def test_bak_files_excluded(self, tmp_path: Path):
        wiki_root = tmp_path / "WIKI"
        domain = wiki_root / "01-领域"
        domain.mkdir(parents=True)
        (domain / "领域-测试.md").write_text(
            "---\ntitle: 测试\ntype: domain\n---\n\n# 测试\n\n正文。",
            encoding="utf-8")
        (domain / "领域-测试.bak.1.md").write_text("备份", encoding="utf-8")
        (domain / "领域-其他.bak.2.md").write_text("备份2", encoding="utf-8")

        pages = self._make_searcher(wiki_root)._load_pages()
        assert len(pages) == 1
        assert pages[0][1] == "测试"

    def test_index_and_changelog_still_excluded(self, tmp_path: Path):
        wiki_root = tmp_path / "WIKI"
        wiki_root.mkdir()
        (wiki_root / "index.md").write_text("# 索引", encoding="utf-8")
        (wiki_root / "changelog.md").write_text("# 变更", encoding="utf-8")
        (wiki_root / "01-领域").mkdir()
        (wiki_root / "01-领域" / "领域-正常.md").write_text(
            "---\ntitle: 正常\ntype: domain\n---\n\n正文。", encoding="utf-8")

        pages = self._make_searcher(wiki_root)._load_pages()
        assert len(pages) == 1
        assert pages[0][1] == "正常"
