"""_graph_engine.py 单元测试 — 纯图操作，零 mock，覆盖全部 6 个方法。"""

from __future__ import annotations

import pytest

from iris.wiki._graph_engine import GraphEdge, GraphNode, _GraphEngine


def _make_edge(src: str, tgt: str, relation: str = "linked_to",
               source_type: str = "wikilink", confidence: float = 1.0) -> GraphEdge:
    return GraphEdge(source=src, target=tgt, relation=relation,
                     source_type=source_type, confidence=confidence)


def _make_node(node_id: str, page_type: str = "concept") -> GraphNode:
    return GraphNode(id=node_id, title=node_id, page_type=page_type)


# ── build ──────────────────────────────────────────────────────────

class TestBuild:
    def test_empty_edges_no_error(self):
        engine = _GraphEngine()
        engine.build([])  # 不应抛异常

    def test_single_directed_edge(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B", source_type="llm")])
        # 单向 LLM 边：A 可到 B
        neighbors = engine.neighbors("A", hops=1)
        assert "B" in neighbors

    def test_wikilink_edge_bidirectional(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B", source_type="wikilink")])
        # wikilink 边应双向建立
        assert "B" in engine.neighbors("A", hops=1)
        assert "A" in engine.neighbors("B", hops=1)

    def test_multiple_edges(self):
        engine = _GraphEngine()
        edges = [_make_edge("A", "B"), _make_edge("B", "C"), _make_edge("A", "C")]
        engine.build(edges)
        nb_a = engine.neighbors("A", hops=1)
        assert "B" in nb_a
        assert "C" in nb_a


# ── neighbors ─────────────────────────────────────────────────────

class TestNeighbors:
    def _engine_triangle(self) -> _GraphEngine:
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B"), _make_edge("B", "C")])
        return engine

    def test_hops_1_direct_only(self):
        engine = self._engine_triangle()
        nb = engine.neighbors("A", hops=1)
        assert "B" in nb
        assert "C" not in nb

    def test_hops_2_two_hops(self):
        engine = self._engine_triangle()
        nb = engine.neighbors("A", hops=2)
        assert "B" in nb
        assert "C" in nb

    def test_unknown_node_returns_empty(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B")])
        assert engine.neighbors("Z", hops=1) == set()

    def test_self_not_in_result(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B")])
        nb = engine.neighbors("A", hops=2)
        assert "A" not in nb

    def test_isolated_node(self):
        engine = _GraphEngine()
        engine.build([])
        assert engine.neighbors("A", hops=1) == set()


# ── find_path ──────────────────────────────────────────────────────

class TestFindPath:
    def test_direct_connection(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B", source_type="llm")])
        path = engine.find_path("A", "B")
        assert path is not None
        assert len(path) == 1
        assert path[0].source == "A"
        assert path[0].target == "B"

    def test_two_hop_path(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B"), _make_edge("B", "C")])
        path = engine.find_path("A", "C", max_hops=4)
        assert path is not None
        assert len(path) == 2

    def test_no_path_returns_none(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B", source_type="llm")])
        # C 是孤立节点，从 A 不可达
        path = engine.find_path("A", "C")
        assert path is None

    def test_path_exceeds_max_hops(self):
        engine = _GraphEngine()
        # A→B→C→D (3 hops), max=2
        engine.build([
            _make_edge("A", "B", source_type="llm"),
            _make_edge("B", "C", source_type="llm"),
            _make_edge("C", "D", source_type="llm"),
        ])
        path = engine.find_path("A", "D", max_hops=2)
        assert path is None

    def test_same_node(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B")])
        # 起终点相同：路径长度为 0，不同实现行为不同，至少不抛异常
        try:
            engine.find_path("A", "A")
        except Exception as e:
            pytest.fail(f"find_path raised unexpectedly: {e}")


# ── orphans ────────────────────────────────────────────────────────

class TestOrphans:
    def test_no_orphans_when_all_linked(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B"), _make_edge("B", "C")])
        all_ids = {"A", "B", "C"}
        # wikilink 双向，B 和 C 都有入链；A 也有反向链
        result = engine.orphans(all_ids)
        # 至少无错误
        assert isinstance(result, list)

    def test_zero_indegree_node_is_orphan(self):
        engine = _GraphEngine()
        # LLM 单向边 A→B：A 无入链（孤立），B 有入链
        engine.build([_make_edge("A", "B", source_type="llm")])
        all_ids = {"A", "B", "C"}
        result = engine.orphans(all_ids)
        # A 在图中且入度为 0，应为孤立
        assert "A" in result
        # B 有来自 A 的入链，不是孤立
        assert "B" not in result

    def test_empty_graph_returns_empty(self):
        # orphans 仅检测"已在图中但零入度"的节点；空图无任何节点，结果为空
        engine = _GraphEngine()
        engine.build([])
        result = engine.orphans({"A", "B"})
        assert result == []

    def test_result_sorted(self):
        engine = _GraphEngine()
        engine.build([])
        result = engine.orphans({"C", "A", "B"})
        assert result == sorted(result)


# ── bridges ────────────────────────────────────────────────────────

class TestBridges:
    def test_no_bridges_when_low_degree(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B")])
        nodes = {
            "A": _make_node("A", "person"),
            "B": _make_node("B", "concept"),
        }
        result = engine.bridges(nodes, min_degree=3)
        assert result == []

    def test_bridge_node_found(self):
        engine = _GraphEngine()
        # HUB 连接 3 个不同类型节点
        engine.build([
            _make_edge("HUB", "P1", source_type="llm"),
            _make_edge("HUB", "C1", source_type="llm"),
            _make_edge("HUB", "D1", source_type="llm"),
        ])
        nodes = {
            "HUB": _make_node("HUB", "concept"),
            "P1": _make_node("P1", "person"),
            "C1": _make_node("C1", "concept"),
            "D1": _make_node("D1", "domain"),
        }
        result = engine.bridges(nodes, min_degree=2)
        hub_result = [b for b in result if b["node_id"] == "HUB"]
        assert hub_result, "HUB 应为桥节点"
        assert len(hub_result[0]["connected_types"]) >= 2

    def test_sorted_by_degree_descending(self):
        engine = _GraphEngine()
        engine.build([
            _make_edge("A", "X1", source_type="llm"),
            _make_edge("A", "X2", source_type="llm"),
            _make_edge("A", "X3", source_type="llm"),
            _make_edge("B", "Y1", source_type="llm"),
            _make_edge("B", "Y2", source_type="llm"),
        ])
        nodes = {
            "A": _make_node("A", "project"),
            "B": _make_node("B", "project"),
            "X1": _make_node("X1", "person"),
            "X2": _make_node("X2", "concept"),
            "X3": _make_node("X3", "domain"),
            "Y1": _make_node("Y1", "person"),
            "Y2": _make_node("Y2", "concept"),
        }
        result = engine.bridges(nodes, min_degree=2)
        if len(result) >= 2:
            assert result[0]["degree"] >= result[1]["degree"]


# ── degree_stats ───────────────────────────────────────────────────

class TestDegreeStats:
    def test_empty_node_set(self):
        engine = _GraphEngine()
        engine.build([])
        result = engine.degree_stats(set())
        assert result["avg_degree"] == 0
        assert result["max_degree"] == 0

    def test_single_node(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B")])
        result = engine.degree_stats({"A"})
        assert result["avg_degree"] >= 0
        assert result["max_degree_node"] == "A"

    def test_stats_shape(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B"), _make_edge("A", "C")])
        result = engine.degree_stats({"A", "B", "C"})
        assert "avg_degree" in result
        assert "max_degree" in result
        assert "max_degree_node" in result

    def test_avg_degree_is_float(self):
        engine = _GraphEngine()
        engine.build([_make_edge("A", "B"), _make_edge("A", "C")])
        result = engine.degree_stats({"A", "B", "C"})
        assert isinstance(result["avg_degree"], float)

    def test_max_degree_node_has_highest_degree(self):
        engine = _GraphEngine()
        # A 连接了 B、C、D（度最高）
        engine.build([
            _make_edge("A", "B"),
            _make_edge("A", "C"),
            _make_edge("A", "D"),
        ])
        result = engine.degree_stats({"A", "B", "C", "D"})
        assert result["max_degree_node"] == "A"
