"""基于 chunk 摘要的本地检索器。"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from iris.config.loader import ConfigBundle
from iris.ingest.chunker import ChunkRecord
from iris.retrieval.planner import QueryPlan

from iris.utils.tokenization import TOKEN_RE, tokenize  # noqa: F811 — 统一分词

# BM25 参数
_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_OOV_DF = 0  # 未登录词文档频率（0 = 不假设任何文档含该词）


@dataclass(frozen=True)
class RetrievalHit:
    chunk_id: str
    score: float
    title: str
    relative_path: str
    section_path: List[str]
    content_preview: str
    line_start: int
    line_end: int
    chunk_type: str = "section"
    structural_tags: List[str] = field(default_factory=list)
    matched_terms: List[str] = field(default_factory=list)
    explanation: str = ""
    extracted_fields: Dict[str, List[str]] = field(default_factory=dict)

    def with_score(self, score: float) -> "RetrievalHit":
        return RetrievalHit(chunk_id=self.chunk_id, score=score, title=self.title,
                            relative_path=self.relative_path, section_path=self.section_path,
                            content_preview=self.content_preview, line_start=self.line_start,
                            line_end=self.line_end, chunk_type=self.chunk_type,
                            structural_tags=self.structural_tags, matched_terms=self.matched_terms,
                            explanation=self.explanation, extracted_fields=self.extracted_fields)

    def _with_explanation(self, explanation: str) -> "RetrievalHit":
        return RetrievalHit(chunk_id=self.chunk_id, score=self.score, title=self.title,
                            relative_path=self.relative_path, section_path=self.section_path,
                            content_preview=self.content_preview, line_start=self.line_start,
                            line_end=self.line_end, chunk_type=self.chunk_type,
                            structural_tags=self.structural_tags, matched_terms=self.matched_terms,
                            explanation=explanation, extracted_fields=self.extracted_fields)


@dataclass(frozen=True)
class RetrievalResult:
    total_hits: int
    hits: List[RetrievalHit]

    def to_dict(self) -> Dict[str, Any]:
        return {"total_hits": self.total_hits, "hits": [asdict(item) for item in self.hits]}


class LocalRetriever:
    def __init__(self, config: ConfigBundle):
        self._config = config
        self._chunks: List[ChunkRecord] = []
        self._loaded = False
        self._by_source: Dict[str, List[ChunkRecord]] = {}
        # 全局 BM25 统计量（_ensure_loaded 后填充）
        self._total_docs: int = 0
        self._avg_doc_len: float = 0.0
        self._df: Dict[str, int] = {}  # document frequency per term
        self._corpus_stats_computed: bool = False

    def search(self, query: str, *, top_k: int = 10, query_plan: QueryPlan | None = None) -> RetrievalResult:
        self._ensure_loaded()

        query_tokens = tokenize(query)
        scored: List[Tuple[RetrievalHit, float, str]] = []

        for chunk in self._chunks:
            score, matched = _score_chunk(query, query_tokens, chunk,
                                          total_docs=self._total_docs,
                                          avg_doc_len=self._avg_doc_len,
                                          df=self._df,
                                          query_plan=query_plan)
            if score <= 0:
                continue
            explanation = f"BM25 score={score:.2f}"
            scored.append((_chunk_to_hit(chunk, matched, explanation), score, chunk.relative_path))

        scored.sort(key=lambda item: (-item[1], item[2]))
        hits = [item[0] for item in scored[:top_k]]
        return RetrievalResult(total_hits=len(scored), hits=hits)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # 优先尝试 SQLite（FTS5 全文搜索，性能更高）
        if self._try_load_sqlite():
            self._loaded = True
            self._compute_corpus_stats()
            return
        # 回退 JSON
        data_source = self._config.data_source
        sources = data_source.get("sources", {})
        metadata_root = self._config.root / "data" / "metadata"
        for source_name in sources:
            summary_path = metadata_root / f"{source_name}_chunk_summary.json"
            if summary_path.exists():
                try:
                    payload = json.loads(summary_path.read_text(encoding="utf-8"))
                    for item in payload.get("chunks", []):
                        try:
                            chunk = ChunkRecord(**item)
                            self._chunks.append(chunk)
                            self._by_source.setdefault(source_name, []).append(chunk)
                        except (TypeError, ValueError):
                            continue
                except (json.JSONDecodeError, OSError):
                    continue
        self._loaded = True
        self._compute_corpus_stats()

    def _compute_corpus_stats(self) -> None:
        """计算全局 BM25 统计量：文档总数、平均长度、词项文档频率。

        使用 chunk.content（全文）而非 content_preview（截断预览），
        确保 TF/IDF/doc_len 统计基于完整内容而非 ~180 字符截断。
        """
        if self._corpus_stats_computed or not self._chunks:
            return
        total_len = 0
        df: Dict[str, set] = defaultdict(set)
        for i, chunk in enumerate(self._chunks):
            content = chunk.content or chunk.content_preview
            if not content:
                continue
            tokens = tokenize(content)
            total_len += len(tokens)
            for t in set(tokens):
                df[t].add(i)
        self._total_docs = len(self._chunks)
        self._avg_doc_len = total_len / max(self._total_docs, 1)
        self._df = {t: len(docs) for t, docs in df.items()}
        self._corpus_stats_computed = True

    def _try_load_sqlite(self) -> bool:
        """尝试从 SQLite ChunkStore 加载（FTS5 全文搜索加速）。"""
        try:
            from iris.core.storage import ChunkStore
            db_path = self._config.root / "data" / "chunk_store.db"
            if not db_path.exists():
                return False
            store = ChunkStore(db_path)
            chunks = store.load_all()
            for chunk in chunks:
                self._chunks.append(chunk)
                self._by_source.setdefault(chunk.source_name, []).append(chunk)
            return len(chunks) > 0
        except Exception:
            return False


def _score_chunk(query: str, query_tokens: List[str], chunk: ChunkRecord,
                 *, total_docs: int = 0, avg_doc_len: float = 0.0,
                 df: Dict[str, int] | None = None,
                 query_plan: QueryPlan | None = None) -> Tuple[float, List[str]]:
    title_lower = chunk.title.lower()
    section_lower = " ".join(chunk.section_path).lower()
    query_lower = query.lower().strip()

    if not query_lower:
        return 0.0, []

    # ── query_plan 权重调整 ──
    title_bonus = 5.0
    title_token_bonus = 3.0
    section_bonus = 3.0
    section_token_bonus = 2.0
    if query_plan is not None:
        # 高优先级 focus_areas 提升标题权重
        # 注意：当前 LLMQueryPlanner.enhance() 为占位实现，answer_focus 始终为空，
        # 此分支待 LLM 增强启用后生效。
        focus_mult = 1.0 + 0.5 * len([a for a in query_plan.answer_focus if a == "high"])
        title_bonus *= focus_mult
        title_token_bonus *= focus_mult
        # entity_weights 如果指定了特定实体权重，额外加分
        entity_mult = 1.0
        if query_plan.entities:
            entity_mult = 1.0 + 0.2 * len(query_plan.entities)
            section_bonus *= entity_mult
            section_token_bonus *= entity_mult

    score = 0.0
    matched: List[str] = []

    if query_lower in title_lower:
        score += title_bonus
    for token in query_tokens:
        if token in title_lower:
            score += title_token_bonus
            if token not in matched:
                matched.append(token)

    if query_lower in section_lower:
        score += section_bonus
    for token in query_tokens:
        if token in section_lower:
            score += section_token_bonus
            if token not in matched:
                matched.append(token)

    # 优先使用预计算的 token_freq（chunking 阶段已构建），避免每次搜索重新分词
    if chunk.token_freq:
        freq = chunk.token_freq
        doc_len = sum(freq.values())
    else:
        # 回退：兼容旧 chunk 数据（token_freq 为 None 或空 dict）
        content_tokens = tokenize(chunk.content)
        freq = defaultdict(int)
        for token in content_tokens:
            freq[token] += 1
        doc_len = len(content_tokens)
    N = max(total_docs, 1)
    avgdl = avg_doc_len if avg_doc_len > 0 else max(doc_len, 50)
    df_map = df if df is not None else {}

    for qt in query_tokens:
        tf = freq.get(qt, 0)
        if tf > 0:
            dft = df_map.get(qt, _BM25_OOV_DF)
            # 对未登录词使用平缓 IDF（等价于出现在 0 个文档中）
            idf = math.log((N - dft + 0.5) / (max(dft, 1) + 0.5) + 1.0)
            norm = 1 - _BM25_B + _BM25_B * doc_len / avgdl
            bm25 = idf * (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * norm)
            score += bm25
            if qt not in matched:
                matched.append(qt)

    return score, matched[:6]


def _chunk_to_hit(chunk: ChunkRecord, matched_terms: List[str], explanation: str) -> RetrievalHit:
    return RetrievalHit(chunk_id=chunk.chunk_id, score=0.0, title=chunk.title,
                        relative_path=chunk.relative_path, section_path=chunk.section_path,
                        content_preview=chunk.content_preview, line_start=chunk.line_start,
                        line_end=chunk.line_end, chunk_type=chunk.chunk_type,
                        structural_tags=chunk.structural_tags, matched_terms=matched_terms,
                        explanation=explanation, extracted_fields=chunk.extracted_fields)
