"""本地检索黄金集评测：召回、引用完整性与过期证据检测。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iris.config.loader import ConfigBundle
from iris.core.exceptions import IrisValueError
from iris.retrieval.searcher import LocalRetriever


@dataclass(frozen=True)
class RetrievalEvalReport:
    cases: int
    recall_at_k: float
    citation_completeness: float
    stale_evidence_rate: float
    failures: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cases": self.cases,
            "recall_at_k": self.recall_at_k,
            "citation_completeness": self.citation_completeness,
            "stale_evidence_rate": self.stale_evidence_rate,
            "failures": self.failures,
        }


def evaluate_retrieval(config: ConfigBundle, cases_path: Path, *, top_k: int = 5) -> RetrievalEvalReport:
    """运行无 LLM 的稳定评测；case 需含 query 和 expected_chunk_ids。"""
    raw = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = raw.get("cases") if isinstance(raw, dict) else None
    if not isinstance(cases, list) or not cases:
        raise IrisValueError("评测文件需要非空 cases 列表")
    retriever = LocalRetriever(config)
    source_roots = [Path(item["path"]).resolve() for item in config.data_source["sources"].values()]
    recalled = cited = stale = total_hits = 0
    failures: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("query"), str):
            raise IrisValueError("每条评测需提供字符串 query")
        expected = set(case.get("expected_chunk_ids", []))
        if not expected:
            raise IrisValueError("每条评测需提供 expected_chunk_ids")
        result = retriever.search(case["query"], top_k=top_k)
        actual = {hit.chunk_id for hit in result.hits}
        recalled += bool(actual & expected)
        for hit in result.hits:
            total_hits += 1
            cited += bool(hit.relative_path and hit.line_start > 0 and hit.line_end >= hit.line_start)
            stale += not any((root / hit.relative_path).is_file() for root in source_roots)
        if not actual & expected:
            failures.append({"query": case["query"], "expected": sorted(expected), "actual": sorted(actual)})
    count = len(cases)
    return RetrievalEvalReport(
        cases=count,
        recall_at_k=round(recalled / count, 4),
        citation_completeness=round(cited / total_hits, 4) if total_hits else 0.0,
        stale_evidence_rate=round(stale / total_hits, 4) if total_hits else 0.0,
        failures=failures,
    )
