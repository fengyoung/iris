"""iris.wiki.context_loader.WikiContextLoader 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris.wiki.context_loader import WikiContextLoader

# ── fixtures ────────────────────────────────────────────────

DOMAIN_PAGE_CONTENT = """\
---
title: 搜索
type: domain
status: active
summary: 搜索领域概述
---

这是搜索领域的正文内容，介绍搜索技术栈和核心概念。
"""

CONCEPT_PAGE_CONTENT = """\
---
title: 召回率
type: concept
status: active
summary: 召回率是评估搜索质量的指标
---

召回率定义：在所有相关文档中，系统检索出的比例。
"""

PERSON_PAGE_CONTENT = """\
---
title: 张三
type: person
status: active
summary: 技术研发部成员
---

张三是技术研发部的工程师，负责搜索排序模块开发。
"""

BAK_PAGE_CONTENT = """\
---
title: 旧页面
type: domain
status: draft
summary: 备份页面
---

这是一个备份文件，不应被加载。
"""


@pytest.fixture
def wiki_root(tmp_path: Path) -> Path:
    """创建临时 Wiki 目录结构。"""
    # 创建标准目录
    (tmp_path / "01-领域").mkdir()
    (tmp_path / "02-概念").mkdir()
    (tmp_path / "03-项目").mkdir()
    (tmp_path / "04-人物").mkdir()

    # 写入测试页面
    (tmp_path / "01-领域" / "领域-搜索.md").write_text(DOMAIN_PAGE_CONTENT, encoding="utf-8")
    (tmp_path / "02-概念" / "概念-召回率.md").write_text(CONCEPT_PAGE_CONTENT, encoding="utf-8")
    (tmp_path / "04-人物" / "人物-张三.md").write_text(PERSON_PAGE_CONTENT, encoding="utf-8")

    # 写入 bak 文件（应被跳过）
    (tmp_path / "01-领域" / "领域-搜索.bak.1.md").write_text(BAK_PAGE_CONTENT, encoding="utf-8")

    return tmp_path


# ── __init__ ────────────────────────────────────────────────


class TestInit:
    def test_accepts_path_object(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        assert loader.root == wiki_root.resolve()

    def test_accepts_string(self, wiki_root):
        loader = WikiContextLoader(str(wiki_root))
        assert loader.root == wiki_root.resolve()


# ── load_pages() ─────────────────────────────────────────────


class TestLoadPages:
    def test_loads_all_pages(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        pages = loader.load_pages()
        titles = [p.title for p in pages]
        assert "搜索" in titles
        assert "召回率" in titles
        assert "张三" in titles

    def test_filter_by_type_domain(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        pages = loader.load_pages(page_types=["domain"])
        assert all(p.page_type == "domain" for p in pages)
        assert len(pages) >= 1

    def test_filter_by_type_person(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        pages = loader.load_pages(page_types=["person"])
        titles = [p.title for p in pages]
        assert "张三" in titles
        assert "搜索" not in titles

    def test_bak_files_skipped(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        pages = loader.load_pages()
        titles = [p.title for p in pages]
        assert "旧页面" not in titles

    def test_nonexistent_type_dir_skipped(self, wiki_root):
        """不存在的类型目录应静默跳过。"""
        loader = WikiContextLoader(wiki_root)
        # project 目录存在但为空
        pages = loader.load_pages(page_types=["project"])
        assert pages == []

    def test_sort_order_respected(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        pages = loader.load_pages(
            page_types=["domain", "person"],
            sort_order=["person", "domain"],
        )
        if len(pages) >= 2:
            # person 类型应排在前面
            person_indices = [i for i, p in enumerate(pages) if p.page_type == "person"]
            domain_indices = [i for i, p in enumerate(pages) if p.page_type == "domain"]
            if person_indices and domain_indices:
                assert min(person_indices) < min(domain_indices)


# ── load_context() ────────────────────────────────────────────


class TestLoadContext:
    def test_returns_string(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        ctx = loader.load_context()
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_content_truncation_with_marker(self, wiki_root):
        """max_chars_per_page 很小时，正文应被截断并含截断标记。"""
        loader = WikiContextLoader(wiki_root)
        ctx = loader.load_context(max_chars_per_page=5)
        assert "截断" in ctx

    def test_max_pages_limits_output(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        ctx_limited = loader.load_context(max_pages=1)
        ctx_all = loader.load_context()
        assert len(ctx_limited) < len(ctx_all)

    def test_label_prefix_present(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        ctx = loader.load_context(label_prefix=True)
        # label_prefix=True 时应含 "## 领域：搜索" 格式
        assert "##" in ctx

    def test_label_prefix_false(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        ctx = loader.load_context(label_prefix=False)
        # label_prefix=False 时用文件 stem
        assert "领域-搜索" in ctx or "领域" in ctx

    def test_empty_result_for_unknown_type(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        ctx = loader.load_context(page_types=["project"])  # 空目录
        assert ctx == ""

    def test_filtered_by_type(self, wiki_root):
        loader = WikiContextLoader(wiki_root)
        ctx = loader.load_context(page_types=["domain"])
        assert "搜索" in ctx
        assert "召回率" not in ctx
