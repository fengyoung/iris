"""Wiki 知识图谱 — 实体节点、关系边、图查询与分析。

三层架构:
  第一层（节点层）: 从 Wiki frontmatter 构建实体节点，零 LLM 成本
  第二层（反向引用边）: 从 [[wikilink]] 构建 linked_to 边，零 LLM 成本
  第三层（LLM 关系边）: 委托 RelationExtractor（_relation_extractor.py）批量提取语义关系

用法:
    graph = WikiGraph(config)
    graph.refresh()                    # 增量更新（daily-start 调用）
    graph.refresh(full_llm=True)       # 全量重建 LLM 关系
    graph.neighbors("人物-张三")        # 查询邻居节点
    graph.related_entities("项目-项目Alpha")  # 按类型分组的关联实体

CLI:
    iris build-graph                   # 增量更新
    iris build-graph --full            # 全量重建
    iris build-graph --page "项目-XXX"  # 单页重建
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError
from iris.utils.shared import atomic_write_json, now_iso

from ._constants import get_display_name, get_all_types
from ._relation_extractor import RelationExtractor
from .backlink import BacklinkBuilder, BacklinkIndex
from .context_loader import WikiContextLoader, WikiPageInfo

logger = logging.getLogger(__name__)

# ── NetworkX 可选导入 ──────────────────────────────────────────
try:
    import networkx as nx
    _HAS_NETWORKX = True
except ImportError:
    nx = None  # type: ignore[assignment]
    _HAS_NETWORKX = False

# LLM 关系提取默认每批页面数（仅用于分片，目前顺序处理）
_DEFAULT_CHUNK_SIZE = 10


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


# ── 图引擎抽象（NetworkX / 纯 Python fallback）───────────────────


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
            # 纯 Python
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


# ── 核心类 ─────────────────────────────────────────────────────


class WikiGraph:
    """Wiki 知识图谱管理器。

    职责:
      - 从 Wiki 页面构建节点（frontmatter 解析）
      - 从 wikilink 反向引用构建基线边
      - 通过 LLM 提取语义关系边
      - 提供图查询（neighbors / related_entities / find_path）
      - 提供图分析（orphans / bridges / density）
      - 持久化到 data/graph/
    """

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._wiki_root = Path(config.wiki["wiki_root"]).resolve() if config.wiki else Path()
        self._data_dir = config.root / "data" / "graph"
        self._relations_dir = self._data_dir / "relations"

        # 运行时状态
        self._nodes: Dict[str, GraphNode] = {}              # id → GraphNode
        self._edges: List[GraphEdge] = []                   # 全部边
        self._engine = _GraphEngine()                       # 图计算引擎（NetworkX / 纯 Python）

    # ══════════════════════════════════════════════════════════
    # 第一层：节点构建
    # ══════════════════════════════════════════════════════════

    def build_nodes(self, *, _pages: Optional[List[WikiPageInfo]] = None) -> List[GraphNode]:
        """从 Wiki 页面 frontmatter 全量构建实体节点（零 LLM 成本）。

        Args:
            _pages: 预加载的 WikiPageInfo 列表（供 refresh() 内部复用，避免重复扫描）

        Returns:
            新构建的节点列表
        """
        if _pages is None:
            if not self._wiki_root.exists():
                return []
            loader = WikiContextLoader(self._wiki_root)
            _pages = loader.load_pages()

        nodes: Dict[str, GraphNode] = {}
        for page_info in _pages:
            title = page_info.title
            if not title:
                continue

            node_id = self._make_node_id(title, page_info.page_type)
            node = GraphNode(
                id=node_id,
                title=title,
                page_type=page_info.page_type,
                tags=self._extract_tags(page_info.body),
                summary=page_info.summary,
                wiki_path=page_info.relative_path,
            )
            nodes[node_id] = node

        self._nodes = nodes
        logger.info("构建节点完成: %d 个实体", len(nodes))
        return list(nodes.values())

    # ══════════════════════════════════════════════════════════
    # 第二层：反向引用边
    # ══════════════════════════════════════════════════════════

    def build_edges_from_backlinks(self, backlink_index: Optional[BacklinkIndex] = None) -> List[GraphEdge]:
        """从 [[wikilink]] 反向引用构建基线边（零 LLM 成本）。

        每条 wikilink 生成一条 linked_to 边。

        Args:
            backlink_index: 已有的反向引用索引，None 则自动构建

        Returns:
            新构建的 wikilink 边列表
        """
        if backlink_index is None:
            builder = BacklinkBuilder(self._wiki_root)
            backlink_index = builder.build()

        edges: List[GraphEdge] = []
        seen: Set[Tuple[str, str]] = set()

        for source_title, linked_titles in backlink_index.outbound.items():
            source_id = self._resolve_node_id(source_title)
            if source_id is None:
                continue

            for target_title in linked_titles:
                target_id = self._resolve_node_id(target_title)
                if target_id is None:
                    continue
                if source_id == target_id:
                    continue

                key = (source_id, target_id)
                if key in seen:
                    continue
                seen.add(key)

                edges.append(GraphEdge(
                    source=source_id,
                    target=target_id,
                    relation="linked_to",
                    source_type="wikilink",
                    confidence=1.0,
                    evidence_page=source_title,
                ))

        # 合并: 保留已有 LLM 边
        llm_edges = [e for e in self._edges if e.source_type == "llm"]
        self._edges = llm_edges + edges
        self._rebuild_adjacency()

        logger.info("构建 wikilink 边完成: %d 条", len(edges))
        return edges

    # ══════════════════════════════════════════════════════════
    # 第三层：LLM 关系提取
    # ══════════════════════════════════════════════════════════

    def extract_relations(
        self,
        *,
        full: bool = False,
        page_title: Optional[str] = None,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
        _pages: Optional[List[WikiPageInfo]] = None,
    ) -> List[GraphEdge]:
        """通过 LLM 提取语义关系边（委托 RelationExtractor）。

        Args:
            full: True=废弃全部缓存并重提取所有页面
            page_title: 仅重提取指定页面（优先级最高）
            chunk_size: 每批处理的页面数（仅用于日志分片）

        Returns:
            新提取的 LLM 边列表
        """
        if not self._nodes:
            self.build_nodes(_pages=_pages)

        if _pages is None:
            loader = WikiContextLoader(self._wiki_root)
            _pages = loader.load_pages() if self._wiki_root.exists() else []

        all_pages = {pi.title: pi for pi in _pages}
        extractor = RelationExtractor(
            self._config, self._nodes, self._wiki_root, self._relations_dir
        )

        if page_title:
            node_id = self._resolve_node_id(page_title)
            if node_id is None:
                logger.warning("未找到节点: %s", page_title)
                return []
            pages_to_process = [node_id]
        elif full:
            pages_to_process = list(self._nodes.keys())
        else:
            pages_to_process = extractor.find_changed_pages(_pages)

        if not pages_to_process:
            logger.info("无页面需要关系提取")
            return []

        all_new_edges = extractor.extract(
            pages_to_process, all_pages, self._edges, chunk_size=chunk_size
        )

        wikilink_edges = [e for e in self._edges if e.source_type == "wikilink"]
        self._edges = wikilink_edges + all_new_edges
        self._rebuild_adjacency()

        logger.info("LLM 关系提取完成: %d 条新边 (%d 页)",
                    len(all_new_edges), len(pages_to_process))
        return all_new_edges

    # ══════════════════════════════════════════════════════════
    # 查询方法
    # ══════════════════════════════════════════════════════════

    def neighbors(self, title: str, *, hops: int = 1) -> List[GraphNode]:
        """查询指定节点的邻居（BFS，支持多跳）。

        Args:
            title: 节点标题（如 "张三"）或完整 id（如 "人物-张三"）
            hops: 跳数（1=直接邻居, 2=邻居的邻居）

        Returns:
            邻居节点列表
        """
        node_id = self._resolve_node_id(title)
        if node_id is None:
            return []
        neighbor_ids = self._engine.neighbors(node_id, hops=hops)
        return [self._nodes[nid] for nid in neighbor_ids if nid in self._nodes]

    def related_entities(self, title: str) -> Dict[str, List[Dict[str, Any]]]:
        """查询与指定节点相关的实体，按类型分组。

        Returns:
            {"person": [{"title": "张三", "relation": "负责", "source_type": "llm"}, ...], ...}
        """
        node_id = self._resolve_node_id(title)
        if node_id is None:
            return {}

        result: Dict[str, List[Dict[str, Any]]] = {}
        for edge in self._edges:
            related_id = None
            if edge.source == node_id:
                related_id = edge.target
                relation = edge.relation
            elif edge.target == node_id:
                related_id = edge.source
                relation = f"被{edge.relation}"
            else:
                continue

            if related_id not in self._nodes:
                continue
            node = self._nodes[related_id]
            result.setdefault(node.page_type, []).append({
                "title": node.title,
                "id": node.id,
                "relation": relation,
                "source_type": edge.source_type,
                "confidence": edge.confidence,
            })

        return result

    def find_path(
        self, from_title: str, to_title: str, *, max_hops: int = 4
    ) -> Optional[List[GraphEdge]]:
        """BFS 查找两个节点之间的最短路径（边列表）。"""
        from_id = self._resolve_node_id(from_title)
        to_id = self._resolve_node_id(to_title)
        if from_id is None or to_id is None:
            return None
        if from_id == to_id:
            return []
        return self._engine.find_path(from_id, to_id, max_hops=max_hops)

    # ══════════════════════════════════════════════════════════
    # 分析方法
    # ══════════════════════════════════════════════════════════

    def find_orphans(self) -> List[str]:
        """查找零入链的孤立节点。"""
        return self._engine.orphans(set(self._nodes.keys()))

    def find_bridges(self, *, min_degree: int = 3) -> List[Dict[str, Any]]:
        """查找桥接节点——连接不同领域类型的关键节点。

        Args:
            min_degree: 最小度（连接数）阈值

        Returns:
            [{"node_id": ..., "title": ..., "connected_types": set(), "degree": n}, ...]
        """
        return self._engine.bridges(self._nodes, min_degree=min_degree)

    def density_report(self) -> Dict[str, Any]:
        """生成图谱密度报告。"""
        n_nodes = len(self._nodes)
        n_edges = len(self._edges)

        # 度分布（委托给引擎）
        stats = self._engine.degree_stats(set(self._nodes.keys()))

        # 按类型统计
        by_type: Dict[str, int] = {}
        for node in self._nodes.values():
            by_type[node.page_type] = by_type.get(node.page_type, 0) + 1

        # 按来源统计边
        wikilink_count = sum(1 for e in self._edges if e.source_type == "wikilink")
        llm_count = sum(1 for e in self._edges if e.source_type == "llm")

        # 拓扑
        orphans = self.find_orphans()
        bridges = self.find_bridges(min_degree=2)
        density = n_edges / (n_nodes * (n_nodes - 1)) if n_nodes > 1 else 0

        return {
            "nodes": n_nodes,
            "edges": n_edges,
            "edges_wikilink": wikilink_count,
            "edges_llm": llm_count,
            "density": round(density, 6),
            "avg_degree": stats["avg_degree"],
            "max_degree": stats["max_degree"],
            "max_degree_node": stats["max_degree_node"],
            "by_type": by_type,
            "orphans": len(orphans),
            "bridges": len(bridges),
        }

    # ══════════════════════════════════════════════════════════
    # 持久化
    # ══════════════════════════════════════════════════════════

    def save(self) -> None:
        """将图谱持久化到 data/graph/。"""
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # 节点
        nodes_data = {
            "nodes": {
                nid: {
                    "id": n.id,
                    "title": n.title,
                    "page_type": n.page_type,
                    "tags": n.tags,
                    "summary": n.summary,
                    "wiki_path": n.wiki_path,
                }
                for nid, n in self._nodes.items()
            },
            "updated_at": now_iso(),
        }
        atomic_write_json(self._data_dir / "nodes.json", nodes_data)

        # 边
        edges_data = {
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "relation": e.relation,
                    "source_type": e.source_type,
                    "confidence": e.confidence,
                    "evidence_page": e.evidence_page,
                }
                for e in self._edges
            ],
            "updated_at": now_iso(),
        }
        atomic_write_json(self._data_dir / "edges.json", edges_data)

        logger.info("图谱已保存: %d 节点, %d 边", len(self._nodes), len(self._edges))

    def load(self) -> bool:
        """从 data/graph/ 加载图谱。成功返回 True。"""
        nodes_path = self._data_dir / "nodes.json"
        edges_path = self._data_dir / "edges.json"

        if not nodes_path.exists() or not edges_path.exists():
            return False

        try:
            nodes_data = json.loads(nodes_path.read_text(encoding="utf-8"))
            for nid, ndata in nodes_data.get("nodes", {}).items():
                self._nodes[nid] = GraphNode(
                    id=ndata.get("id", nid),
                    title=ndata.get("title", ""),
                    page_type=ndata.get("page_type", "domain"),
                    tags=ndata.get("tags", []),
                    summary=ndata.get("summary", ""),
                    wiki_path=ndata.get("wiki_path", ""),
                )

            edges_data = json.loads(edges_path.read_text(encoding="utf-8"))
            for edata in edges_data.get("edges", []):
                self._edges.append(GraphEdge(
                    source=edata.get("source", ""),
                    target=edata.get("target", ""),
                    relation=edata.get("relation", "linked_to"),
                    source_type=edata.get("source_type", "wikilink"),
                    confidence=edata.get("confidence", 1.0),
                    evidence_page=edata.get("evidence_page", ""),
                ))

            self._rebuild_adjacency()
            logger.info("图谱已加载: %d 节点, %d 边", len(self._nodes), len(self._edges))
            return True
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("加载图谱失败: %s", exc)
            return False

    # ══════════════════════════════════════════════════════════
    # 一键刷新（供 daily-start 和 build-graph CLI 调用）
    # ══════════════════════════════════════════════════════════

    def refresh(self, *, full_llm: bool = False, page_title: Optional[str] = None) -> Dict[str, Any]:
        """一键刷新图谱：节点 → backlink 边 → LLM 边。

        Wiki 目录只扫描一次，三层都复用同一份页面列表。

        Args:
            full_llm: 是否全量重建 LLM 关系
            page_title: 仅重建指定页面的关系

        Returns:
            刷新报告 dict
        """
        report: Dict[str, Any] = {"nodes": 0, "wikilink_edges": 0, "llm_edges": 0}

        # 一次性加载所有 Wiki 页面，三层共用
        pages: List[WikiPageInfo] = []
        if self._wiki_root.exists():
            loader = WikiContextLoader(self._wiki_root)
            pages = loader.load_pages()

        # 1. 节点
        nodes = self.build_nodes(_pages=pages)
        report["nodes"] = len(nodes)

        # 2. 反向引用边（复用预加载页面，避免重复扫描）
        builder = BacklinkBuilder(self._wiki_root)
        backlink_index = builder.build_from_wiki_pages(pages)
        backlink_edges = self.build_edges_from_backlinks(backlink_index)
        report["wikilink_edges"] = len(backlink_edges)

        # 3. LLM 关系边（复用预加载页面）
        try:
            llm_edges = self.extract_relations(
                full=full_llm, page_title=page_title, _pages=pages
            )
            report["llm_edges"] = len(llm_edges)
        except LLMProviderError as exc:
            report["llm_error"] = str(exc)
            logger.warning("LLM 关系提取失败: %s", exc)

        # 4. 持久化
        self.save()

        # 附加统计
        report["density"] = self.density_report()
        report["orphan_count"] = len(self.find_orphans())

        return report

    # ══════════════════════════════════════════════════════════
    # 内部辅助方法
    # ══════════════════════════════════════════════════════════

    def _make_node_id(self, title: str, page_type: str) -> str:
        """生成节点唯一标识: "类型-标题"。"""
        display = get_display_name(page_type)
        return f"{display}-{title}"

    def _resolve_node_id(self, title_or_id: str) -> Optional[str]:
        """解析标题或 id 为节点 id。"""
        # 直接匹配
        if title_or_id in self._nodes:
            return title_or_id
        # 去掉前缀再匹配（如 "张三" → "人物-张三"）
        for node_id, node in self._nodes.items():
            if node.title == title_or_id:
                return node_id
        return None

    def _extract_tags(self, body: str) -> List[str]:
        """从页面正文中提取标签。

        匹配几种常见格式:
          - 标签: A, B, C          （独立行）
          - ...标签: A, B, C       （行内，中文句号或空格后）
          - Tags: A; B; C
        """
        import re
        tags: List[str] = []
        for line in body.splitlines()[:50]:
            line_stripped = line.strip()
            # 匹配行中任意位置的 "标签:" / "tags:" 模式
            m = re.search(r'(?:标签|tags)[:：]\s*(.+)', line_stripped, re.IGNORECASE)
            if m:
                tag_part = m.group(1).strip()
                # 支持逗号、分号、顿号分隔
                tag_part = re.sub(r'[;；、]', ',', tag_part)
                tags.extend(t.strip() for t in tag_part.split(",") if t.strip())
                break
        return tags[:10]

    def _rebuild_adjacency(self) -> None:
        """委托 _GraphEngine 从边列表重建图结构。

        NetworkX 可用时使用 DiGraph，否则回退纯 Python dict。
        """
        self._engine.build(self._edges)


# 向下兼容重导出（_safe_filename 已迁移至 _relation_extractor）
from ._relation_extractor import _safe_filename  # noqa: E402,F401

