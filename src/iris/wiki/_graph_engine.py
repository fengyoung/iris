"""图计算引擎 — 封装 NetworkX / 纯 Python 底层图算法。

从 graph.py 提取的 _GraphEngine 类，负责所有图遍历与查询操作。
同时定义 GraphNode / GraphEdge 数据结构，避免 graph.py ↔ _graph_engine.py 循环导入。
WikiGraph 通过本引擎间接访问图数据结构，无需感知底层实现。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# ── 数据结构 ──────────────────────────────────────────────────


@dataclass
class GraphNode:
    """知识图谱节点（Wiki 实体）。"""

    id: str                         # "人物-张三"
    title: str                      # "张三"
    page_type: str                  # domain / concept / project / person
    tags: List[str] = field(default_factory=list)
    summary: str = ""
    wiki_path: str = ""


@dataclass(frozen=True)
class GraphEdge:
    """知识图谱边（实体间关系）。"""

    source: str                     # 源节点 id
    target: str                     # 目标节点 id
    relation: str                   # "负责" / "使用" / "linked_to" / ...
    source_type: str = "wikilink"   # "wikilink" | "llm"
    confidence: float = 1.0         # wikilink=1.0, llm=0.0~1.0
    evidence_page: str = ""         # 关系来源的 Wiki 页面标题


# ── NetworkX 可选导入 ──────────────────────────────────────────
try:
    import networkx as nx
    _HAS_NETWORKX = True
except ImportError:
    nx = None  # type: ignore[assignment]
    _HAS_NETWORKX = False


class _GraphEngine:
    """图计算引擎，优先使用 NetworkX，不可用时回退纯 Python。

    封装所有图算法操作，WikiGraph 无需感知底层实现。
    """

    def __init__(self):
        self._nx: Any = None  # networkx.DiGraph (if available)
        self._adjacency: Dict[str, List[str]] = {}
        self._out_edges: Dict[str, List[GraphEdge]] = {}

    def build(self, edges: List[GraphEdge]) -> None:
        """从边列表构建图结构。"""
        if _HAS_NETWORKX:
            self._nx = nx.DiGraph()
            for edge in edges:
                self._nx.add_edge(edge.source, edge.target,
                                  relation=edge.relation,
                                  source_type=edge.source_type,
                                  confidence=edge.confidence)
                # wikilink 边反向也加入
                if edge.source_type == "wikilink":
                    self._nx.add_edge(edge.target, edge.source,
                                      relation="linked_to",
                                      source_type="wikilink",
                                      confidence=1.0)
        else:
            self._adjacency.clear()
            self._out_edges.clear()
            for edge in edges:
                self._adjacency.setdefault(edge.source, []).append(edge.target)
                self._adjacency.setdefault(edge.target, []).append(edge.source)
                self._out_edges.setdefault(edge.source, []).append(edge)
                if edge.source_type == "wikilink":
                    self._out_edges.setdefault(edge.target, []).append(edge)

    def neighbors(self, node_id: str, hops: int = 1) -> Set[str]:
        """获取指定节点 hops 跳内的邻居。"""
        if self._nx is not None:
            result: Set[str] = set()
            frontier = {node_id}
            for _ in range(hops):
                next_frontier: Set[str] = set()
                for n in frontier:
                    for _, neighbor in self._nx.edges(n):
                        if neighbor not in result and neighbor != node_id:
                            next_frontier.add(neighbor)
                result.update(next_frontier)
                frontier = next_frontier
                if not frontier:
                    break
            return result
        else:
            visited: Set[str] = {node_id}
            current_level: Set[str] = {node_id}
            for _ in range(hops):
                next_level: Set[str] = set()
                for nid in current_level:
                    for neighbor in self._adjacency.get(nid, []):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            next_level.add(neighbor)
                current_level = next_level
                if not current_level:
                    break
            visited.discard(node_id)
            return visited

    def find_path(self, from_id: str, to_id: str, max_hops: int = 4) -> Optional[List[GraphEdge]]:
        """查找两个节点间的最短路径（边列表）。"""
        if self._nx is not None:
            try:
                path_nodes = nx.shortest_path(self._nx, from_id, to_id)
                if len(path_nodes) - 1 > max_hops:
                    return None
                edges: List[GraphEdge] = []
                for i in range(len(path_nodes) - 1):
                    edge_data = self._nx.get_edge_data(path_nodes[i], path_nodes[i + 1])
                    if edge_data:
                        edges.append(GraphEdge(
                            source=path_nodes[i], target=path_nodes[i + 1],
                            relation=edge_data.get("relation", "linked_to"),
                            source_type=edge_data.get("source_type", "wikilink"),
                            confidence=edge_data.get("confidence", 1.0),
                        ))
                return edges if edges else None
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                return None
        else:
            # 纯 Python BFS
            queue = deque([(from_id, [])])
            visited = {from_id}
            while queue:
                current, path = queue.popleft()
                if len(path) >= max_hops:
                    continue
                for edge in self._out_edges.get(current, []):
                    next_id = edge.target if edge.source == current else edge.source
                    if next_id in visited:
                        continue
                    new_path = path + [edge]
                    if next_id == to_id:
                        return new_path
                    visited.add(next_id)
                    queue.append((next_id, new_path))
            return None

    def orphans(self, all_node_ids: Set[str]) -> List[str]:
        """查找零入链的孤立节点。"""
        if self._nx is not None:
            in_degrees = dict(self._nx.in_degree())
            return sorted([n for n in all_node_ids if n in in_degrees and in_degrees[n] == 0])
        else:
            referenced: Set[str] = set()
            for targets in self._adjacency.values():
                referenced.update(targets)
            return sorted([n for n in all_node_ids if n not in referenced])

    def bridges(self, nodes: Dict[str, Any], min_degree: int = 3) -> List[Dict[str, Any]]:
        """查找桥接节点。"""
        if self._nx is not None:
            bridges_list: List[Dict[str, Any]] = []
            for node_id, node in nodes.items():
                if node_id not in self._nx:
                    continue
                degree = self._nx.degree(node_id)
                if degree < min_degree:
                    continue
                neighbor_types: Set[str] = set()
                for neighbor in self._nx.neighbors(node_id):
                    if neighbor in nodes:
                        neighbor_types.add(nodes[neighbor].page_type)
                if len(neighbor_types) >= 2:
                    bridges_list.append({
                        "node_id": node_id, "title": node.title,
                        "page_type": node.page_type,
                        "connected_types": sorted(neighbor_types),
                        "degree": degree,
                    })
            bridges_list.sort(key=lambda b: b["degree"], reverse=True)
            return bridges_list
        else:
            bridges_list: List[Dict[str, Any]] = []
            for node_id, node in nodes.items():
                neighbor_ids = set(self._adjacency.get(node_id, []))
                if len(neighbor_ids) < min_degree:
                    continue
                neighbor_types: Set[str] = set()
                for nid in neighbor_ids:
                    if nid in nodes:
                        neighbor_types.add(nodes[nid].page_type)
                if len(neighbor_types) >= 2:
                    bridges_list.append({
                        "node_id": node_id, "title": node.title,
                        "page_type": node.page_type,
                        "connected_types": sorted(neighbor_types),
                        "degree": len(neighbor_ids),
                    })
            bridges_list.sort(key=lambda b: b["degree"], reverse=True)
            return bridges_list

    def degree_stats(self, node_ids: Set[str]) -> Dict[str, Any]:
        """计算度分布统计。"""
        if self._nx is not None and node_ids:
            degrees = {n: self._nx.degree(n) for n in node_ids if n in self._nx}
        else:
            degrees = {nid: len(self._adjacency.get(nid, [])) for nid in node_ids}
        if degrees:
            avg = sum(degrees.values()) / len(degrees)
            max_node = max(degrees, key=degrees.get)
            return {"avg_degree": round(avg, 2), "max_degree": degrees[max_node],
                    "max_degree_node": max_node}
        return {"avg_degree": 0, "max_degree": 0, "max_degree_node": ""}
