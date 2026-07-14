"""Wiki 知识图谱 — 实体节点、关系边、LLM 关系提取、图查询与分析。

三层架构:
  第一层（节点层）: 从 Wiki frontmatter 构建实体节点，零 LLM 成本
  第二层（反向引用边）: 从 [[wikilink]] 构建 linked_to 边，零 LLM 成本
  第三层（LLM 关系边）: 批量 LLM 提取语义关系（负责/使用/属于/...），增量更新

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError, LLMService
from iris.utils.shared import atomic_write_json, now_iso

from ._constants import get_display_name, get_all_types
from .backlink import BacklinkBuilder, BacklinkIndex
from .context_loader import WikiContextLoader

logger = logging.getLogger(__name__)

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


# ── 关系提取 Prompt 模板 ──────────────────────────────────────

_RELATION_EXTRACT_PROMPT = """你是一个知识图谱关系提取专家。已知以下 Wiki 实体列表：

{{entity_list}}

请从当前页面内容中提取该页面与其他已知实体之间的关系。

当前页面：{{page_title}}（类型：{{page_type}}）
页面内容：
---
{{page_content}}
---

请输出 JSON 格式的关系三元组，每行一个，不要包含其他文字：
{{"source": "{{page_title}}", "target": "<实体id>", "relation": "<关系类型>", "confidence": <0.0-1.0>}}

关系类型包括但不限于：负责、参与、使用、属于、汇报给、协作、依赖、产出、对齐、包含、管理、指导。

要求：
1. 只提取页面内容中明确提到的关系，不要虚构
2. target 必须来自已知实体列表
3. confidence 表示你对这个关系的确定程度
4. 无关系时输出空数组 []
5. 每行一个完整的 JSON 对象"""


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
        self._nodes: Dict[str, GraphNode] = {}     # id → GraphNode
        self._edges: List[GraphEdge] = []           # 全部边
        self._adjacency: Dict[str, List[str]] = {}  # id → [neighbor_ids]

    # ══════════════════════════════════════════════════════════
    # 第一层：节点构建
    # ══════════════════════════════════════════════════════════

    def build_nodes(self) -> List[GraphNode]:
        """从 Wiki 页面 frontmatter 全量构建实体节点（零 LLM 成本）。

        Returns:
            新构建的节点列表
        """
        if not self._wiki_root.exists():
            return []

        loader = WikiContextLoader(self._wiki_root)
        nodes: Dict[str, GraphNode] = {}

        for page_info in loader.load_pages():
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
    ) -> List[GraphEdge]:
        """通过 LLM 提取语义关系边。

        对每个页面调用 LLM 提取 (source, target, relation) 三元组。
        结果写入 relations/ 缓存目录，增量模式下仅重提取 mtime 变更的页面。

        Args:
            full: True=废弃全部缓存并重提取所有页面
            page_title: 仅重提取指定页面（优先级最高）
            chunk_size: 每批处理的页面数（仅用于日志分片，非并发）

        Returns:
            新提取的 LLM 边列表
        """
        if not self._nodes:
            self.build_nodes()

        # 确定要处理的页面列表
        if page_title:
            node_id = self._resolve_node_id(page_title)
            if node_id is None:
                logger.warning("未找到节点: %s", page_title)
                return []
            pages_to_process = [node_id]
        elif full:
            pages_to_process = list(self._nodes.keys())
        else:
            pages_to_process = self._find_changed_pages()

        if not pages_to_process:
            logger.info("无页面需要关系提取")
            return []

        # 获取 LLM provider
        llm_service = LLMService(self._config)
        provider = llm_service.get_provider()

        # 构建实体列表（供 LLM prompt 使用）
        entity_list = self._format_entity_list()

        # 分批处理
        loader = WikiContextLoader(self._wiki_root)
        all_pages = {pi.title: pi for pi in loader.load_pages()}

        all_new_edges: List[GraphEdge] = []
        seen_edge_keys: Set[Tuple[str, str, str, str]] = {
            (e.source, e.target, e.relation, e.source_type) for e in self._edges
        }
        self._relations_dir.mkdir(parents=True, exist_ok=True)

        for chunk_start in range(0, len(pages_to_process), chunk_size):
            chunk = pages_to_process[chunk_start:chunk_start + chunk_size]
            for node_id in chunk:
                node = self._nodes.get(node_id)
                if node is None:
                    continue

                page_info = all_pages.get(node.title)
                if page_info is None:
                    continue

                edges = self._extract_page_relations(
                    provider, node, page_info.body, entity_list
                )
                # 去重：跳过已存在的 (source, target, relation, source_type)
                for edge in edges:
                    key = (edge.source, edge.target, edge.relation, edge.source_type)
                    if key not in seen_edge_keys:
                        seen_edge_keys.add(key)
                        all_new_edges.append(edge)

                # 缓存单页结果（去重后的边）
                self._save_page_relations(node.title, edges)

        # 合并边：保留 wikilink 边 + 新 LLM 边
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
        if node_id is None or node_id not in self._adjacency:
            return []

        # BFS 按层展开，hops 层后停止
        visited: Set[str] = {node_id}
        current_level: Set[str] = {node_id}

        for level in range(hops):
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
        return [self._nodes[nid] for nid in visited if nid in self._nodes]

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
        """BFS 查找两个节点之间的最短路径（边列表）。

        使用邻接表索引，O(V+E) 而非 O(V×E)。
        """
        from_id = self._resolve_node_id(from_title)
        to_id = self._resolve_node_id(to_title)
        if from_id is None or to_id is None:
            return None

        if from_id == to_id:
            return []

        # 构建按 source 索引的边列表（一次遍历）
        out_edges: Dict[str, List[GraphEdge]] = {}
        for edge in self._edges:
            out_edges.setdefault(edge.source, []).append(edge)
            # wikilink 边允许反向遍历
            if edge.source_type == "wikilink":
                out_edges.setdefault(edge.target, []).append(edge)

        queue = deque([(from_id, [])])
        visited = {from_id}

        while queue:
            current, path = queue.popleft()
            if len(path) >= max_hops:
                continue

            for edge in out_edges.get(current, []):
                # 确定遍历方向
                if edge.source == current:
                    next_id = edge.target
                elif edge.target == current:
                    next_id = edge.source
                else:
                    continue

                if next_id in visited:
                    continue

                new_path = path + [edge]
                if next_id == to_id:
                    return new_path

                visited.add(next_id)
                queue.append((next_id, new_path))

        return None

    # ══════════════════════════════════════════════════════════
    # 分析方法
    # ══════════════════════════════════════════════════════════

    def find_orphans(self) -> List[str]:
        """查找零入链的孤立节点。"""
        referenced: Set[str] = set()
        for edge in self._edges:
            referenced.add(edge.target)
        return sorted([
            node_id for node_id in self._nodes
            if node_id not in referenced
        ])

    def find_bridges(self, *, min_degree: int = 3) -> List[Dict[str, Any]]:
        """查找桥接节点——连接不同领域类型的关键节点。

        Args:
            min_degree: 最小度（连接数）阈值

        Returns:
            [{"node_id": ..., "title": ..., "connected_types": set(), "degree": n}, ...]
        """
        bridges: List[Dict[str, Any]] = []
        for node_id, node in self._nodes.items():
            neighbor_ids = set(self._adjacency.get(node_id, []))
            if len(neighbor_ids) < min_degree:
                continue

            # 统计邻居的类型分布
            neighbor_types: Set[str] = set()
            for nid in neighbor_ids:
                if nid in self._nodes:
                    neighbor_types.add(self._nodes[nid].page_type)

            if len(neighbor_types) >= 2:  # 跨越至少 2 种类型
                bridges.append({
                    "node_id": node_id,
                    "title": node.title,
                    "page_type": node.page_type,
                    "connected_types": sorted(neighbor_types),
                    "degree": len(neighbor_ids),
                })

        bridges.sort(key=lambda b: b["degree"], reverse=True)
        return bridges

    def density_report(self) -> Dict[str, Any]:
        """生成图谱密度报告。"""
        n_nodes = len(self._nodes)
        n_edges = len(self._edges)

        # 度分布
        degrees = {nid: len(self._adjacency.get(nid, [])) for nid in self._nodes}
        if degrees:
            avg_degree = sum(degrees.values()) / len(degrees)
            max_degree_node = max(degrees, key=degrees.get)
            max_degree = degrees[max_degree_node]
        else:
            avg_degree = 0
            max_degree_node = ""
            max_degree = 0

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
            "avg_degree": round(avg_degree, 2),
            "max_degree": max_degree,
            "max_degree_node": max_degree_node,
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

        Args:
            full_llm: 是否全量重建 LLM 关系
            page_title: 仅重建指定页面的关系

        Returns:
            刷新报告 dict
        """
        report: Dict[str, Any] = {"nodes": 0, "wikilink_edges": 0, "llm_edges": 0}

        # 1. 节点
        nodes = self.build_nodes()
        report["nodes"] = len(nodes)

        # 2. 反向引用边
        backlink_edges = self.build_edges_from_backlinks()
        report["wikilink_edges"] = len(backlink_edges)

        # 3. LLM 关系边
        try:
            llm_edges = self.extract_relations(full=full_llm, page_title=page_title)
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

    def _format_entity_list(self) -> str:
        """格式化实体列表为 LLM prompt 上下文。"""
        lines: List[str] = []
        for node_id, node in sorted(self._nodes.items()):
            type_name = get_display_name(node.page_type)
            lines.append(f"{node_id}（{type_name}）: {node.summary[:80] if node.summary else node.title}")
        return "\n".join(lines)

    def _rebuild_adjacency(self) -> None:
        """从边列表重建邻接表。"""
        self._adjacency.clear()
        for edge in self._edges:
            self._adjacency.setdefault(edge.source, []).append(edge.target)
            if edge.source_type == "wikilink":
                # wikilink 边双向
                self._adjacency.setdefault(edge.target, []).append(edge.source)

    def _find_changed_pages(self) -> List[str]:
        """找出内容 mtime 晚于缓存的页面（增量更新用）。"""
        changed: List[str] = []
        loader = WikiContextLoader(self._wiki_root)

        for page_info in loader.load_pages():
            title = page_info.title
            if not title:
                continue

            cache_file = self._relations_dir / f"{_safe_filename(title)}.json"
            if not cache_file.exists():
                changed.append(self._make_node_id(title, page_info.page_type))
                continue

            try:
                cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_at = cache_data.get("extracted_at", "")
                if not cached_at:
                    changed.append(self._make_node_id(title, page_info.page_type))
                    continue

                page_mtime = page_info.path.stat().st_mtime
                cached_time = datetime.fromisoformat(cached_at).timestamp()
                if page_mtime > cached_time:
                    changed.append(self._make_node_id(title, page_info.page_type))
            except (json.JSONDecodeError, OSError, ValueError):
                changed.append(self._make_node_id(title, page_info.page_type))

        return changed

    def _extract_page_relations(
        self,
        provider,
        node: GraphNode,
        body: str,
        entity_list: str,
    ) -> List[GraphEdge]:
        """对单个页面调用 LLM 提取关系三元组。"""
        prompt = _RELATION_EXTRACT_PROMPT.replace("{{entity_list}}", entity_list)
        prompt = prompt.replace("{{page_title}}", node.id)
        prompt = prompt.replace("{{page_type}}", get_display_name(node.page_type))
        # 截断过长内容
        body_trimmed = body[:4000] if len(body) > 4000 else body
        prompt = prompt.replace("{{page_content}}", body_trimmed)

        edges: List[GraphEdge] = []
        try:
            from iris.llm import LLMRequest
            response = provider.generate(
                LLMRequest(prompt=prompt, route_context={
                    "input_type": "text",
                    "task_type": "wiki_generate",
                    "complexity": "standard",
                }),
                temperature=0.1,
                max_tokens=2000,
            )
            edges = self._parse_triples(response.text, node.id)
        except (LLMProviderError, Exception) as exc:
            logger.warning("关系提取失败 [%s]: %s", node.title, exc)

        return edges

    def _parse_triples(self, text: str, source_id: str) -> List[GraphEdge]:
        """从 LLM 输出解析 JSON 行格式的三元组。"""
        edges: List[GraphEdge] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line == "[]":
                continue
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    continue
                target = obj.get("target", "")
                relation = obj.get("relation", "")
                if not target or not relation:
                    continue
                if target not in self._nodes:
                    continue
                confidence = float(obj.get("confidence", 0.5))
                edges.append(GraphEdge(
                    source=source_id,
                    target=target,
                    relation=relation,
                    source_type="llm",
                    confidence=min(max(confidence, 0.0), 1.0),
                    evidence_page=source_id,
                ))
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return edges

    def _save_page_relations(self, title: str, edges: List[GraphEdge]) -> None:
        """缓存单页关系提取结果。"""
        cache_file = self._relations_dir / f"{_safe_filename(title)}.json"
        data = {
            "page_title": title,
            "extracted_at": now_iso(),
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "relation": e.relation,
                    "source_type": e.source_type,
                    "confidence": e.confidence,
                    "evidence_page": e.evidence_page,
                }
                for e in edges
            ],
        }
        atomic_write_json(cache_file, data)


# ── 工具函数 ──────────────────────────────────────────────────


def _safe_filename(title: str) -> str:
    """将页面标题转换为安全的文件名。"""
    safe = title.replace("/", "-").replace(":", "-").replace("\\", "-")
    safe = "".join(c for c in safe if c.isalnum() or c in ".-_ ()（）")
    return safe.strip()[:120]
