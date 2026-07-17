"""Wiki 知识图谱 — LLM 关系提取器。

从 graph.py 拆分：封装所有 LLM 驱动的三元组提取逻辑，WikiGraph 通过 RelationExtractor
委托关系层操作，自身聚焦节点构建、图查询与持久化。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError, LLMService
from iris.utils.shared import atomic_write_json, now_iso

from ._constants import get_display_name

logger = logging.getLogger(__name__)

# ── Prompt 模板 ───────────────────────────────────────────────

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


def _safe_filename(title: str) -> str:
    """将页面标题转换为安全的文件名。"""
    safe = title.replace("/", "-").replace(":", "-").replace("\\", "-")
    safe = "".join(c for c in safe if c.isalnum() or c in ".-_ ()（）")
    return safe.strip()[:120]


# ── 数据类型（避免循环导入：直接引用 graph 中定义的 dataclass）──────
# 调用方 graph.py 负责传入 GraphNode / GraphEdge，本模块仅做类型注解
# 使用 TYPE_CHECKING 避免运行时循环导入
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .graph import GraphEdge, GraphNode


class RelationExtractor:
    """LLM 关系提取器 — 封装第三层关系边的全部逻辑。

    设计原则：无副作用地操作外部传入的 nodes 引用（只读），
    所有修改（新增边、缓存写入）通过返回值或直接写磁盘完成。
    """

    def __init__(
        self,
        config: ConfigBundle,
        nodes: Dict[str, "GraphNode"],
        wiki_root: Path,
        relations_dir: Path,
    ) -> None:
        self._config = config
        self._nodes = nodes          # 只读引用，WikiGraph 负责更新节点后再传入
        self._wiki_root = wiki_root
        self._relations_dir = relations_dir

    # ── 公共接口 ──────────────────────────────────────────────

    def extract(
        self,
        pages_to_process: List[str],
        all_pages: Dict[str, Any],
        existing_edges: List["GraphEdge"],
        *,
        chunk_size: int = 10,
    ) -> List["GraphEdge"]:
        """对指定页面列表批量提取 LLM 关系边。

        Args:
            pages_to_process: 待提取的节点 id 列表
            all_pages: title → WikiPageInfo 映射
            existing_edges: 已有边列表（用于去重）
            chunk_size: 分片日志粒度

        Returns:
            新提取（去重后）的边列表
        """
        llm_service = LLMService(self._config)
        entity_list = self.format_entity_list()
        self._relations_dir.mkdir(parents=True, exist_ok=True)

        seen_edge_keys: Set[Tuple[str, str, str, str]] = {
            (e.source, e.target, e.relation, e.source_type) for e in existing_edges
        }
        all_new_edges: List["GraphEdge"] = []

        for chunk_start in range(0, len(pages_to_process), chunk_size):
            chunk = pages_to_process[chunk_start:chunk_start + chunk_size]
            for node_id in chunk:
                node = self._nodes.get(node_id)
                if node is None:
                    continue
                page_info = all_pages.get(node.title)
                if page_info is None:
                    continue

                edges = self._extract_page_relations(llm_service, node, page_info.body, entity_list)
                for edge in edges:
                    key = (edge.source, edge.target, edge.relation, edge.source_type)
                    if key not in seen_edge_keys:
                        seen_edge_keys.add(key)
                        all_new_edges.append(edge)
                self._save_page_relations(node.title, edges)

        return all_new_edges

    def find_changed_pages(self, pages: List[Any]) -> List[str]:
        """找出内容 mtime 晚于缓存的页面（增量更新）。

        Returns:
            需要重提取的节点 id 列表
        """
        from datetime import datetime

        changed: List[str] = []
        for page_info in pages:
            title = page_info.title
            if not title:
                continue

            cache_file = self._relations_dir / f"{_safe_filename(title)}.json"
            node_id = f"{get_display_name(page_info.page_type)}-{title}"

            if not cache_file.exists():
                changed.append(node_id)
                continue

            try:
                cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
                cached_at = cache_data.get("extracted_at", "")
                if not cached_at:
                    changed.append(node_id)
                    continue
                page_mtime = page_info.path.stat().st_mtime
                cached_time = datetime.fromisoformat(cached_at).timestamp()
                if page_mtime > cached_time:
                    changed.append(node_id)
            except (json.JSONDecodeError, OSError, ValueError):
                changed.append(node_id)

        return changed

    def format_entity_list(self) -> str:
        """格式化实体列表供 LLM prompt 使用。"""
        lines: List[str] = []
        for node_id, node in sorted(self._nodes.items()):
            type_name = get_display_name(node.page_type)
            lines.append(f"{node_id}（{type_name}）: {node.summary[:80] if node.summary else node.title}")
        return "\n".join(lines)

    # ── 私有实现 ──────────────────────────────────────────────

    def _extract_page_relations(
        self,
        llm_service: LLMService,
        node: "GraphNode",
        body: str,
        entity_list: str,
    ) -> List["GraphEdge"]:
        """对单个页面调用 LLM 提取关系三元组。"""
        prompt = _RELATION_EXTRACT_PROMPT.replace("{{entity_list}}", entity_list)
        prompt = prompt.replace("{{page_title}}", node.id)
        prompt = prompt.replace("{{page_type}}", get_display_name(node.page_type))
        body_trimmed = body[:4000] if len(body) > 4000 else body
        prompt = prompt.replace("{{page_content}}", body_trimmed)

        edges: List["GraphEdge"] = []
        try:
            result = llm_service.generate(
                prompt,
                route_context={
                    "input_type": "text",
                    "task_type": "wiki_generate",
                    "complexity": "standard",
                },
                temperature=0,
                max_tokens=2000,
            )
            edges = self._parse_triples(result.text, node.id)
        except LLMProviderError as exc:
            logger.warning("关系提取失败（LLM 错误）[%s]: %s", node.title, exc)
        except Exception as exc:
            logger.error("关系提取意外失败 [%s]: %s", node.title, exc, exc_info=True)

        return edges

    def _parse_triples(self, text: str, source_id: str) -> List["GraphEdge"]:
        """从 LLM 输出解析三元组，兼容逐行 JSON 对象和 JSON 数组两种格式。"""
        edges: List["GraphEdge"] = []

        stripped = text.strip()
        if stripped.startswith("[") or stripped.startswith("{"):
            try:
                parsed = json.loads(stripped)
                candidates = parsed if isinstance(parsed, list) else [parsed]
                for obj in candidates:
                    edge = self._triple_obj_to_edge(obj, source_id)
                    if edge:
                        edges.append(edge)
                if edges:
                    return edges
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        for line in text.splitlines():
            line = line.strip()
            if not line or line in ("[]", "[", "]"):
                continue
            line = line.rstrip(",")
            try:
                obj = json.loads(line)
                edge = self._triple_obj_to_edge(obj, source_id)
                if edge:
                    edges.append(edge)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return edges

    def _triple_obj_to_edge(self, obj: Any, source_id: str) -> Optional["GraphEdge"]:
        """将单个三元组 dict 转换为 GraphEdge，校验失败返回 None。"""
        from .graph import GraphEdge

        if not isinstance(obj, dict):
            return None
        target = obj.get("target", "")
        relation = obj.get("relation", "")
        if not target or not relation:
            return None
        if target not in self._nodes:
            return None
        confidence = min(max(float(obj.get("confidence", 0.5)), 0.0), 1.0)
        return GraphEdge(
            source=source_id,
            target=target,
            relation=relation,
            source_type="llm",
            confidence=confidence,
            evidence_page=source_id,
        )

    def _save_page_relations(self, title: str, edges: List["GraphEdge"]) -> None:
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
