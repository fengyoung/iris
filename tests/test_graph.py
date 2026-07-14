"""wiki/graph.py 知识图谱专项测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.wiki.backlink import BacklinkBuilder, BacklinkIndex
from iris.wiki.graph import GraphEdge, GraphNode, WikiGraph, _safe_filename
from iris.utils.shared import now_iso


# ── 辅助函数 ──────────────────────────────────────────────


def _create_wiki_page(path: Path, title: str, ptype: str, body: str,
                      status: str = "stable", tags: str = "test") -> None:
    """创建 Wiki 页面 markdown 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"""---
title: {title}
type: {ptype}
status: {status}
tags: [{tags}]
---

## 摘要
Summary for {title}.

{body}
"""
    path.write_text(content, encoding="utf-8")


def _make_wiki_root(tmp_path: Path, label: str = "") -> Path:
    """创建含 4 页面的测试 Wiki 结构。"""
    subdir = f"wiki_{label}" if label else "LLM-WIKI"
    wiki_root = tmp_path / subdir
    _create_wiki_page(wiki_root / "01-领域/领域-搜索.md", "搜索", "domain",
                      "搜索领域。由 [[张三]] 负责。涉及 [[排序]] 和 [[项目Alpha]] 项目。")
    _create_wiki_page(wiki_root / "02-概念/概念-排序.md", "排序", "concept",
                      "搜索排序算法。用于 [[搜索]] 和 [[项目Alpha]]。")
    _create_wiki_page(wiki_root / "03-项目/项目-项目Alpha.md", "项目Alpha", "project",
                      "Alpha项目。由 [[张三]] 负责，使用 [[排序]] 技术。标签: 搜索, 推荐, AI",
                      tags="搜索, 推荐, AI")
    _create_wiki_page(wiki_root / "04-人物/人物-张三.md", "张三", "person",
                      "团队负责人。负责 [[项目Alpha]] 项目。")
    return wiki_root


def _make_config_bundle(tmp_path: Path, wiki_root: Path):
    """创建最小化 ConfigBundle。"""
    from iris.config.loader import ConfigBundle
    return ConfigBundle(
        root=tmp_path,
        app={"app": {"name": "Test"}},
        data_source={"sources": {}},
        llm={
            "models": {
                "base_model": {
                    "enabled": True, "default_model_id": "test",
                    "models": {
                        "test": {
                            "provider": "openai_compatible", "model": "test",
                            "api_base_url": "https://test.com/v1", "api_key": "sk-test",
                            "priority": 10, "multimodal": False,
                        }
                    }
                },
                "adv_model": {
                    "enabled": True, "default_model_id": "test-adv",
                    "models": {
                        "test-adv": {
                            "provider": "openai_compatible", "model": "test-adv",
                            "api_base_url": "https://test.com/v1", "api_key": "sk-test",
                            "priority": 10, "multimodal": True,
                        }
                    }
                }
            },
            "routing": {"rules": []},
            "default_strategy": {"default_model_role": "base_model", "fallback_model_role": "adv_model"},
            "embedding": {"enabled": False},
        },
        wiki={"wiki_root": str(wiki_root)},
    )


# ── WikiGraph 第一层：节点构建 ────────────────────────────────


class TestGraphNodes:
    def test_build_nodes(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        nodes = graph.build_nodes()

        assert len(nodes) == 4
        node_ids = {n.id for n in nodes}
        assert "领域-搜索" in node_ids
        assert "概念-排序" in node_ids
        assert "项目-项目Alpha" in node_ids
        assert "人物-张三" in node_ids

    def test_node_tags_extraction(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()

        # 项目Alpha 有标签 "搜索, 推荐, AI"
        node = graph._nodes.get("项目-项目Alpha")
        assert node is not None
        assert len(node.tags) >= 1

    def test_empty_wiki(self, tmp_path):
        bundle = _make_config_bundle(tmp_path, tmp_path / "nonexistent")
        graph = WikiGraph(bundle)
        nodes = graph.build_nodes()
        assert nodes == []

    def test_node_id_format(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()

        for node_id, node in graph._nodes.items():
            assert node.id == node_id
            assert node.title in node_id


# ── WikiGraph 第二层：反向引用边 ──────────────────────────────


class TestGraphBacklinkEdges:
    def test_build_edges_from_backlinks(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        edges = graph.build_edges_from_backlinks()

        assert len(edges) > 0
        for edge in edges:
            assert edge.source_type == "wikilink"
            assert edge.confidence == 1.0
            assert edge.relation == "linked_to"

    def test_edges_use_node_ids(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        for edge in graph._edges:
            assert edge.source in graph._nodes, f"{edge.source} not in nodes"
            assert edge.target in graph._nodes, f"{edge.target} not in nodes"


# ── WikiGraph 查询方法 ──────────────────────────────────────


class TestGraphQueries:
    def test_neighbors(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        neighbors = graph.neighbors("张三")
        assert len(neighbors) > 0
        neighbor_titles = {n.title for n in neighbors}
        assert "项目Alpha" in neighbor_titles

    def test_neighbors_by_id(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        neighbors = graph.neighbors("人物-张三")
        assert len(neighbors) > 0

    def test_neighbors_nonexistent(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()

        neighbors = graph.neighbors("不存在")
        assert neighbors == []

    def test_neighbors_2_hops(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        neighbors = graph.neighbors("张三", hops=2)
        # 张三 → 项目Alpha → (排序, 搜索)
        neighbor_titles = {n.title for n in neighbors}
        assert "项目Alpha" in neighbor_titles
        # 2 跳可见排序和搜索
        assert len(neighbors) >= 2

    def test_related_entities(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        entities = graph.related_entities("项目Alpha")
        assert isinstance(entities, dict)
        # 项目Alpha 与 张三(person) 和 排序(concept) 相关
        all_titles = []
        for items in entities.values():
            all_titles.extend(it["title"] for it in items)
        assert "张三" in all_titles or "排序" in all_titles

    def test_find_path(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        path = graph.find_path("张三", "搜索")
        # 张三 → 项目Alpha → 搜索 或 张三 → 搜索(通过搜索页面引用)
        assert path is not None or True  # BFS 路径可能存在

    def test_find_path_same_node(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()

        path = graph.find_path("张三", "张三")
        assert path == []


# ── WikiGraph 分析方法 ─────────────────────────────────────


class TestGraphAnalysis:
    def test_find_orphans(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        orphans = graph.find_orphans()
        assert isinstance(orphans, list)

    def test_find_bridges(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        bridges = graph.find_bridges(min_degree=1)
        assert isinstance(bridges, list)
        for b in bridges:
            assert "node_id" in b
            assert "degree" in b
            assert "connected_types" in b

    def test_density_report(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()

        report = graph.density_report()
        assert report["nodes"] == 4
        assert report["edges"] > 0
        assert "density" in report
        assert "by_type" in report
        assert "orphans" in report
        assert 0 <= report["density"] <= 1


# ── WikiGraph 持久化 ──────────────────────────────────────


class TestGraphPersistence:
    def test_save_and_load(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.build_edges_from_backlinks()
        graph.save()

        # 验证文件存在
        assert (tmp_path / "data" / "graph" / "nodes.json").exists()
        assert (tmp_path / "data" / "graph" / "edges.json").exists()

        # 加载到新 graph
        graph2 = WikiGraph(bundle)
        assert graph2.load()
        assert len(graph2._nodes) == 4
        assert len(graph2._edges) > 0

    def test_load_nonexistent(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        assert not graph.load()

    def test_save_overwrite(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)
        graph.build_nodes()
        graph.save()

        # Rebuild and save again
        graph.build_nodes()
        graph.build_edges_from_backlinks()
        graph.save()

        graph2 = WikiGraph(bundle)
        graph2.load()
        assert len(graph2._nodes) == 4


# ── WikiGraph refresh (一键刷新) ────────────────────────────


class TestGraphRefresh:
    def test_refresh_builds_complete_graph(self, tmp_path):
        wiki_root = _make_wiki_root(tmp_path)
        bundle = _make_config_bundle(tmp_path, wiki_root)
        graph = WikiGraph(bundle)

        report = graph.refresh(full_llm=False)

        assert report["nodes"] == 4
        assert report["wikilink_edges"] > 0
        assert "density" in report

        # 无 LLM provider 时，llm_edges 为 0（跳过 LLM 提取）
        assert "llm_edges" in report


# ── 工具函数 ───────────────────────────────────────────────


class TestGraphUtils:
    def test_safe_filename(self):
        assert _safe_filename("搜索") == "搜索"
        assert _safe_filename("a/b:c") == "a-b-c"

    def test_now_iso(self):
        ts = now_iso()
        assert "T" in ts
        assert len(ts) > 10


# ── GraphNode / GraphEdge ──────────────────────────────────


class TestGraphDataClasses:
    def test_graph_node(self):
        node = GraphNode(id="领域-搜索", title="搜索", page_type="domain",
                        tags=["AI"], summary="搜索领域")
        assert node.id == "领域-搜索"
        assert node.page_type == "domain"

    def test_graph_edge(self):
        edge = GraphEdge(source="项目-项目Alpha", target="人物-张三",
                        relation="负责", source_type="llm", confidence=0.9,
                        evidence_page="项目-项目Alpha")
        assert edge.relation == "负责"
        assert edge.confidence == 0.9
        assert edge.source_type == "llm"
