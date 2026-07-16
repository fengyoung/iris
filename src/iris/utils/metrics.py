"""知识库指标导出 — 按周导出 Wiki/图谱/数据源关键指标为结构化 JSON。

用法:
    from iris.utils.metrics import MetricsExporter
    exporter = MetricsExporter(config)
    snapshot = exporter.snapshot()   # 即时快照
    exporter.export(snapshot)        # 写入 data/metrics/{week_key}.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle

logger = logging.getLogger(__name__)


def _week_key(dt: Optional[datetime] = None) -> str:
    """生成周标识符: 2026-W28。"""
    dt = dt or datetime.now(tz=timezone.utc)
    return dt.strftime("%Y-W%W")


class MetricsExporter:
    """知识库指标快照与导出。

    收集维度：
      - Wiki: 页面总数、按类型分布、stale 页面数
      - Graph: 节点数、边数、wikilink/LLM 边分布、密度、孤立节点数
      - Source: 文档数、Chunk 数、最近扫描时间
      - LLM: 累计调用次数、token 用量（从 UsageTracker 读取）
    """

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._metrics_dir = config.root / "data" / "metrics"
        self._wiki_root = Path(config.wiki["wiki_root"]) if config.wiki else None

    def snapshot(self) -> Dict[str, Any]:
        """生成当前知识库的即时指标快照。"""
        return {
            "exported_at": datetime.now(tz=timezone.utc).isoformat(),
            "week": _week_key(),
            "wiki": self._wiki_metrics(),
            "graph": self._graph_metrics(),
            "source": self._source_metrics(),
            "llm": self._llm_metrics(),
        }

    def export(self, snapshot: Optional[Dict[str, Any]] = None) -> Path:
        """将指标快照写入 data/metrics/{week_key}.json。"""
        data = snapshot or self.snapshot()
        self._metrics_dir.mkdir(parents=True, exist_ok=True)
        week = data.get("week", _week_key())
        output_path = self._metrics_dir / f"{week}.json"
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("指标快照已导出: %s", output_path)
        return output_path

    def list_snapshots(self) -> List[Path]:
        """列出所有已保存的指标快照文件。"""
        if not self._metrics_dir.exists():
            return []
        return sorted(
            [p for p in self._metrics_dir.iterdir() if p.suffix == ".json" and p.stem.startswith("202")],
            key=lambda p: p.name,
        )

    def trend(self, weeks: int = 4) -> Dict[str, Any]:
        """读取最近 N 周的指标趋势数据。

        Returns:
            {"weeks": [...], "wiki_pages": [...], "graph_nodes": [...], "graph_edges": [...]}
        """
        snapshots = self.list_snapshots()
        if not snapshots:
            return {"weeks": [], "wiki_pages": [], "graph_nodes": [], "graph_edges": []}

        recent = snapshots[-weeks:]
        trend_data: Dict[str, List[Any]] = {
            "weeks": [], "wiki_pages": [], "graph_nodes": [], "graph_edges": [],
        }
        for sp in recent:
            try:
                data = json.loads(sp.read_text(encoding="utf-8"))
                trend_data["weeks"].append(data.get("week", sp.stem))
                trend_data["wiki_pages"].append(data.get("wiki", {}).get("total_pages", 0))
                trend_data["graph_nodes"].append(data.get("graph", {}).get("nodes", 0))
                trend_data["graph_edges"].append(data.get("graph", {}).get("edges", 0))
            except (json.JSONDecodeError, OSError):
                continue
        return trend_data

    # ── 各维度采集 ──────────────────────────────────────

    def _wiki_metrics(self) -> Dict[str, Any]:
        if not self._wiki_root or not self._wiki_root.exists():
            return {"total_pages": 0, "by_type": {}, "stale_pages": 0}

        from iris.wiki.context_loader import WikiContextLoader
        from iris.wiki.discovery_utils import is_wiki_stale

        loader = WikiContextLoader(self._wiki_root)
        pages = loader.load_pages()
        by_type: Dict[str, int] = {}
        stale_count = 0
        for p in pages:
            by_type[p.page_type] = by_type.get(p.page_type, 0) + 1
            try:
                if is_wiki_stale(p.path):
                    stale_count += 1
            except Exception as exc:
                logger.warning("Wiki 过期检查失败: %s (%s)", p.path, exc)
                pass

        return {
            "total_pages": len(pages),
            "by_type": by_type,
            "stale_pages": stale_count,
        }

    def _graph_metrics(self) -> Dict[str, Any]:
        try:
            from iris.wiki.graph import WikiGraph
            graph = WikiGraph(self._config)
            if not graph.load():
                return {"nodes": 0, "edges": 0, "loaded": False}
            report = graph.density_report()
            return {"loaded": True, **report}
        except Exception as exc:
            logger.warning("图谱指标收集失败: %s", exc)
            return {"nodes": 0, "edges": 0, "loaded": False}

    def _source_metrics(self) -> Dict[str, Any]:
        metadata_dir = self._config.root / "data" / "metadata"
        sources: Dict[str, Dict[str, Any]] = {}
        total_docs = 0
        total_chunks = 0

        for summary_file in sorted(metadata_dir.glob("*_chunk_summary.json")):
            source_name = summary_file.stem.replace("_chunk_summary", "")
            try:
                data = json.loads(summary_file.read_text(encoding="utf-8"))
                docs = data.get("document_count", 0)
                chunks = data.get("chunk_count", 0)
                sources[source_name] = {"documents": docs, "chunks": chunks, "scanned_at": data.get("scanned_at", "")}
                total_docs += docs
                total_chunks += chunks
            except (json.JSONDecodeError, OSError):
                continue

        return {"total_documents": total_docs, "total_chunks": total_chunks, "by_source": sources}

    def _llm_metrics(self) -> Dict[str, Any]:
        try:
            from iris.llm.usage_tracker import UsageTracker
            tracker = UsageTracker(self._config.root / "data")
            total = tracker.total_records()
            if total == 0:
                return {"total_calls": 0, "total_tokens": 0}
            stats = tracker.stats(by="month")
            total_tokens = sum(s.get("total_tokens", 0) for s in stats) if stats else 0
            return {
                "total_calls": total,
                "total_tokens": total_tokens,
                "monthly_stats": stats,
            }
        except Exception as exc:
            logger.warning("LLM 用量统计收集失败: %s", exc)
            return {"total_calls": 0, "total_tokens": 0}
