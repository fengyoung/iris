"""_graph_engine.py 纯 Python 回退路径测试。

当 NetworkX 不可用时，_GraphEngine 应使用纯 Python BFS / 邻接表实现，
功能与 NetworkX 路径等价。本文件专门测试该回退路径。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from iris.wiki._graph_engine import GraphEdge, GraphNode, _GraphEngine


def _make_edge(src: str, tgt: str, relation: str = "linked_to",
               source_type: str = "wikilink", confidence: float = 1.0) -> GraphEdge:
    return GraphEdge(source=src, target=tgt, relation=relation,
                     source_type=source_type, confidence=confidence)


def _make_node(node_id: str, page_type: str = "concept") -> GraphNode:
    return GraphNode(id=node_id, title=node_id, page_type=page_type)


# ── 纯 Python 回退：模块级 patch _HAS_NETWORKX ──────────────────


@pytest.fixture
def engine_no_nx():
    """创建使用纯 Python 回退路径的 _GraphEngine。"""
    with patch("iris.wiki._graph_engine._HAS_NETWORKX", False):
        engine = _GraphEngine()
        engine._nx = None  # 确保不通过 NetworkX
        yield engine


class TestBuildFallback:
    """纯 Python build() 回退路径。"""

    def test_empty_edges_no_error(self, engine_no_nx):
        engine_no_nx.build([])

    def test_single_edge_adjacency(self, engine_no_nx):
        engine_no_nx.build([_make_edge("A", "B", source_type="llm")])
        assert "B" in engine_no_nx._adjacency.get("A", [])
        # LLM 单向边：A 应有出边，B 不应有出边
        assert "A" in engine_no_nx._out_edges
        assert "B" not in engine_no_nx._out_edges

    def test_wikilink_bidirectional(self, engine_no_nx):
        engine_no_nx.build([_make_edge("A", "B", source_type="wikilink")])
        assert "B" in engine_no_nx._adjacency.get("A", [])
        assert "A" in engine_no_nx._adjacency.get("B", [])
        # wikilink 边 target 也应有出边
        assert "B" in engine_no_nx._out_edges
        assert "A" in engine_no_nx._out_edges


class TestNeighborsFallback:
    """纯 Python neighbors() 回退路径。"""

    def test_hops_1_direct(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("A", "C"),
        ])
        nb = engine_no_nx.neighbors("A", hops=1)
        assert nb == {"B", "C"}

    def test_hops_2_two_hops(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("B", "C"),
        ])
        nb = engine_no_nx.neighbors("A", hops=2)
        assert "C" in nb
        assert "A" not in nb  # 自身不在结果中

    def test_unknown_node_returns_empty(self, engine_no_nx):
        engine_no_nx.build([_make_edge("A", "B")])
        assert engine_no_nx.neighbors("Z", hops=1) == set()

    def test_isolated_node(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("C", "D"),
        ])
        assert engine_no_nx.neighbors("A", hops=1) == {"B"}


class TestFindPathFallback:
    """纯 Python find_path() 回退路径（BFS 最短路径）。"""

    def test_direct_connection(self, engine_no_nx):
        engine_no_nx.build([_make_edge("A", "B")])
        path = engine_no_nx.find_path("A", "B")
        assert path is not None
        assert len(path) == 1
        assert path[0].source == "A"
        assert path[0].target == "B"

    def test_two_hop_path(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("B", "C"),
        ])
        path = engine_no_nx.find_path("A", "C", max_hops=3)
        assert path is not None
        assert len(path) == 2

    def test_no_path_returns_none(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("C", "D"),
        ])
        assert engine_no_nx.find_path("A", "D") is None

    def test_path_exceeds_max_hops(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("B", "C"),
            _make_edge("C", "D"),
        ])
        # max_hops=1，跳数超限
        assert engine_no_nx.find_path("A", "D", max_hops=1) is None

    def test_same_node(self, engine_no_nx):
        """起止点相同应返回空路径（不做自环检测）。"""
        engine_no_nx.build([_make_edge("A", "B")])
        path = engine_no_nx.find_path("A", "A")
        # BFS 从 A 出发搜索，A 已 visited，返回 None 是正确的
        assert path is None


class TestOrphansFallback:
    """纯 Python orphans() 回退路径。"""

    def test_no_orphans_when_all_linked(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("B", "C"),
        ])
        # A 有出边到 B，B 被 A 引用且有出边到 C，C 被 B 引用
        orphans = engine_no_nx.orphans({"A", "B", "C"})
        assert orphans == []

    def test_zero_indegree_node_is_orphan(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
        ])
        orphans = engine_no_nx.orphans({"A", "B", "C"})
        assert "C" in orphans

    def test_empty_graph(self, engine_no_nx):
        engine_no_nx.build([])
        orphans = engine_no_nx.orphans({"A", "B"})
        assert sorted(orphans) == ["A", "B"]

    def test_result_sorted(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
        ])
        orphans = engine_no_nx.orphans({"Z", "A", "B", "M"})
        assert orphans == sorted(orphans)


class TestBridgesFallback:
    """纯 Python bridges() 回退路径。"""

    def test_no_bridges_when_low_degree(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("A", "C"),
        ])
        nodes = {
            "A": _make_node("A", "concept"),
            "B": _make_node("B", "person"),
            "C": _make_node("C", "project"),
        }
        # A 的度数为 2（< min_degree=3），不应成为桥接节点
        bridges = engine_no_nx.bridges(nodes, min_degree=3)
        assert bridges == []

    def test_bridge_node_found(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("A", "C"),
            _make_edge("A", "D"),
        ])
        nodes = {
            "A": _make_node("A", "person"),
            "B": _make_node("B", "concept"),
            "C": _make_node("C", "project"),
            "D": _make_node("D", "concept"),
        }
        bridges = engine_no_nx.bridges(nodes, min_degree=3)
        assert len(bridges) >= 1
        assert bridges[0]["node_id"] == "A"
        assert bridges[0]["degree"] >= 3
        assert len(bridges[0]["connected_types"]) >= 2

    def test_not_bridge_when_single_type(self, engine_no_nx):
        """所有邻居同类型的节点不应被识别为桥接节点。"""
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("A", "C"),
            _make_edge("A", "D"),
        ])
        nodes = {
            "A": _make_node("A", "person"),
            "B": _make_node("B", "concept"),
            "C": _make_node("C", "concept"),
            "D": _make_node("D", "concept"),
        }
        # 所有邻居都是 concept 类型，不应为 bridge
        bridges = engine_no_nx.bridges(nodes, min_degree=3)
        assert bridges == []


class TestDegreeStatsFallback:
    """纯 Python degree_stats() 回退路径。"""

    def test_avg_and_max_degree(self, engine_no_nx):
        engine_no_nx.build([
            _make_edge("A", "B"),
            _make_edge("A", "C"),
        ])
        stats = engine_no_nx.degree_stats({"A", "B"})
        assert stats["max_degree"] >= 1

    def test_empty_node_set(self, engine_no_nx):
        engine_no_nx.build([_make_edge("A", "B")])
        stats = engine_no_nx.degree_stats(set())
        assert stats["avg_degree"] == 0
        assert stats["max_degree"] == 0
        assert stats["max_degree_node"] == ""
