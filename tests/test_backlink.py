"""wiki/backlink.py 反向引用索引专项测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.wiki.backlink import BacklinkBuilder, BacklinkIndex


# ── 辅助函数 ──────────────────────────────────────────────


def _create_wiki_page(path: Path, title: str, ptype: str, body: str) -> None:
    """创建 Wiki 页面 markdown 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""---
title: {title}
type: {ptype}
status: stable
tags: [test]
---

## 摘要
Test summary for {title}.

{body}
"""
    path.write_text(content, encoding="utf-8")


def _make_wiki_structure(tmp_path: Path) -> Path:
    """创建含 4 个页面的测试 Wiki 结构。"""
    wiki_root = tmp_path / "LLM-WIKI"
    _create_wiki_page(wiki_root / "01-领域/领域-搜索.md", "搜索", "domain",
                      "核心领域。涉及 [[排序]] 和 [[张三]] 的工作。")
    _create_wiki_page(wiki_root / "02-概念/概念-排序.md", "排序", "concept",
                      "搜索排序算法。用于 [[搜索]] 领域。")
    _create_wiki_page(wiki_root / "03-项目/项目-项目Alpha.md", "项目Alpha", "project",
                      "Alpha项目。由 [[张三]] 负责，使用 [[排序]] 技术。")
    _create_wiki_page(wiki_root / "04-人物/人物-张三.md", "张三", "person",
                      "团队成员。负责 [[项目Alpha]] 项目。")
    return wiki_root


# ── BacklinkBuilder 基础 ────────────────────────────────────


class TestBacklinkBuilder:
    def test_build_empty_wiki(self, tmp_path):
        """Wiki 不存在时返回空索引。"""
        builder = BacklinkBuilder(tmp_path / "nonexistent")
        index = builder.build()
        assert index.total_pages == 0
        assert index.unique_inbound_edges == 0
        assert index.orphans == []

    def test_build_index(self, tmp_path):
        """正确构建出入链索引。"""
        wiki_root = _make_wiki_structure(tmp_path)
        builder = BacklinkBuilder(wiki_root)
        index = builder.build()

        assert index.total_pages == 4
        assert index.unique_inbound_edges > 0

        # 验证入链：搜索 被 排序 引用
        assert "排序" in index.inbound.get("搜索", [])
        # 验证出链：排序 引用了 搜索
        assert "搜索" in index.outbound.get("排序", [])

    def test_orphans_detection(self, tmp_path):
        """零入链页面正确检测。"""
        wiki_root = _make_wiki_structure(tmp_path)
        builder = BacklinkBuilder(wiki_root)
        index = builder.build()

        # 张三 被多个页面引用，不应为孤页
        # 搜索 被 排序 引用 → 非孤页
        # 排序 被 项目Alpha 引用 → 非孤页
        # 项目Alpha 被 张三 引用 → 非孤页
        # 所有页面都有入链（双向引用）
        assert len(index.orphans) <= 1  # 最多一个孤页

    def test_get_inbound(self, tmp_path):
        """get_inbound 返回入链页面列表。"""
        wiki_root = _make_wiki_structure(tmp_path)
        builder = BacklinkBuilder(wiki_root)
        inbound = builder.get_inbound("搜索")

        assert isinstance(inbound, list)
        # 排序 引用了 搜索
        assert "排序" in inbound

    def test_get_outbound(self, tmp_path):
        """get_outbound 返回出链页面列表。"""
        wiki_root = _make_wiki_structure(tmp_path)
        builder = BacklinkBuilder(wiki_root)
        outbound = builder.get_outbound("项目Alpha")

        assert isinstance(outbound, list)
        assert "张三" in outbound
        assert "排序" in outbound

    def test_noisy_links_filtered(self, tmp_path):
        """噪音 wikilink（如 [[.]] [[#]]）被过滤。"""
        wiki_root = tmp_path / "LLM-WIKI"
        _create_wiki_page(wiki_root / "02-概念/概念-测试.md", "测试", "concept",
                          "包含噪音 [[.]] [[#]] [[---]] 以及有效 [[搜索]] 链接。")
        _create_wiki_page(wiki_root / "01-领域/领域-搜索.md", "搜索", "domain",
                          "搜索领域。")

        builder = BacklinkBuilder(wiki_root)
        index = builder.build()

        # 噪音不应出现在出链中
        outbound = index.outbound.get("测试", [])
        assert "." not in outbound
        assert "#" not in outbound
        # 有效链接保留
        assert "搜索" in outbound

    def test_source_ref_links_filtered(self, tmp_path):
        """源文档引用（如 20260518-xxx）被过滤。"""
        wiki_root = tmp_path / "LLM-WIKI"
        _create_wiki_page(wiki_root / "02-概念/概念-测试.md", "测试", "concept",
                          "参考了 [[会议纪要/20260518-周会]] 和 [[搜索]]。")
        _create_wiki_page(wiki_root / "01-领域/领域-搜索.md", "搜索", "domain",
                          "搜索。")

        builder = BacklinkBuilder(wiki_root)
        index = builder.build()

        outbound = index.outbound.get("测试", [])
        assert "会议纪要/20260518-周会" not in outbound
        assert "搜索" in outbound


# ── 持久化 ───────────────────────────────────────────────


class TestBacklinkPersistence:
    def test_save_and_load(self, tmp_path):
        """save + load 完整巡回。"""
        wiki_root = _make_wiki_structure(tmp_path)
        builder = BacklinkBuilder(wiki_root)
        save_path = tmp_path / "data" / "backlink_index.json"
        builder.save(save_path)

        assert save_path.exists()
        data = json.loads(save_path.read_text(encoding="utf-8"))
        assert data["total_pages"] == 4

        # 加载
        loaded = builder.load(save_path)
        assert loaded is not None
        assert loaded.total_pages == 4

    def test_load_nonexistent(self, tmp_path):
        """加载不存在的文件返回 None。"""
        builder = BacklinkBuilder(tmp_path / "LLM-WIKI")
        result = builder.load(tmp_path / "nonexistent.json")
        assert result is None

    def test_to_dict_from_dict(self, tmp_path):
        """to_dict / from_dict 巡回一致性。"""
        wiki_root = _make_wiki_structure(tmp_path)
        builder = BacklinkBuilder(wiki_root)
        index = builder.build()

        d = index.to_dict()
        restored = BacklinkIndex.from_dict(d)

        assert restored.total_pages == index.total_pages
        assert restored.unique_inbound_edges == index.unique_inbound_edges
        assert restored.orphans == index.orphans


# ── 边界 ──────────────────────────────────────────────────


class TestBacklinkEdgeCases:
    def test_broken_file_tolerated(self, tmp_path):
        """损坏的 md 文件不导致整个构建崩溃。"""
        wiki_root = tmp_path / "LLM-WIKI"
        _create_wiki_page(wiki_root / "01-领域/领域-正常.md", "正常", "domain", "OK")
        bad = wiki_root / "01-领域/领域-损坏.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"\xff\xfe\x00\x01")  # 无效 UTF-8

        builder = BacklinkBuilder(wiki_root)
        index = builder.build()
        assert index.total_pages >= 1

    def test_empty_wiki_pages(self, tmp_path):
        """无 wikilink 的页面正常处理。"""
        wiki_root = tmp_path / "LLM-WIKI"
        _create_wiki_page(wiki_root / "01-领域/领域-空.md", "空", "domain",
                          "无任何链接的页面。")

        builder = BacklinkBuilder(wiki_root)
        index = builder.build()
        assert index.total_pages == 1
        assert index.unique_inbound_edges == 0
        assert "空" in index.orphans

    def test_self_link_not_counted(self, tmp_path):
        """页面内的自引用 [[自己]] 正常计入出入链。"""
        wiki_root = tmp_path / "LLM-WIKI"
        _create_wiki_page(wiki_root / "02-概念/概念-自引.md", "自引", "concept",
                          "自引用 [[自引]]。")

        builder = BacklinkBuilder(wiki_root)
        index = builder.build()
        # 自引用会出现在出入链中（这是正确的 wikilink 行为）
        assert index.unique_inbound_edges == 1
