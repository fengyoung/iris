"""增强检索：查询改写、查询规划、Wiki 联动与可选 LLM 重排。"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError, LLMService
from iris.retrieval.embedder import EmbedderError, TextEmbedder, build_embedder_from_config
from iris.retrieval.planner import LLMQueryPlanner, QueryPlan, QueryPlanner
from iris.retrieval.searcher import LocalRetriever, RetrievalHit
from iris.retrieval.vector_index import VectorIndex
from iris.utils.prompting import PromptTemplateLoader
from iris.wiki.searcher import WikiSearcher

logger = logging.getLogger("iris.retrieval.enhanced")
SPLIT_RE = re.compile(r"[\s,，。；;、]+")
_MIN_LOCAL_CANDIDATES = 12


@dataclass(frozen=True)
class RewrittenQuery:
    original: str
    rewritten: str
    tokens: List[str]


@dataclass(frozen=True)
class EnhancedRetrievalResult:
    query: str
    query_intent: str
    rewritten_query: str
    total_hits: int
    hits: List[RetrievalHit]
    rerank_mode: str
    wiki_hits: List[Dict[str, Any]]
    query_plan: Dict[str, Any]
    explanations: List[str]
    llm: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {"query": self.query, "query_intent": self.query_intent,
                   "rewritten_query": self.rewritten_query, "total_hits": self.total_hits,
                   "hits": [asdict(item) for item in self.hits], "rerank_mode": self.rerank_mode,
                   "wiki_hits": self.wiki_hits, "query_plan": self.query_plan,
                   "explanations": self.explanations}
        if self.llm is not None:
            payload["llm"] = self.llm
        return payload


class QueryRewriter:
    SYNONYM_MAP = {"周报": ["周报", "双周报", "工作总结"], "会议": ["会议", "纪要", "讨论"],
                   "机制": ["机制", "流程", "规则"], "方案": ["方案", "行动方案", "规划"],
                   "项目": ["项目", "专项", "方向"], "决议": ["决议", "结论", "决定"],
                   "进展": ["进展", "当前", "阶段", "里程碑"]}

    def rewrite(self, query: str, plan: QueryPlan | None = None) -> RewrittenQuery:
        parts = [part.strip() for part in SPLIT_RE.split(query) if part.strip()]
        expanded: List[str] = []
        for part in parts:
            expanded.append(part)
            for key, synonyms in self.SYNONYM_MAP.items():
                if key in part or part in synonyms:
                    for synonym in synonyms:
                        if synonym not in expanded:
                            expanded.append(synonym)
        if plan:
            for token in plan.entities + plan.keywords:
                if token not in expanded:
                    expanded.append(token)
        rewritten = " ".join(expanded) if expanded else query
        return RewrittenQuery(original=query, rewritten=rewritten, tokens=expanded or [query])


class EnhancedRetriever:
    _CACHE_MAXSIZE = 32
    _CACHE_TTL = 60

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._local = LocalRetriever(config)
        self._planner = QueryPlanner()
        self._rewriter = QueryRewriter()
        self._llm = LLMService(config)
        self._wiki_searcher = WikiSearcher(config) if config.wiki else None
        self._prompt_loader = PromptTemplateLoader(config)
        self._embedder: Optional[TextEmbedder] = _init_embedder(config)
        self._vector_indexes: Dict[str, VectorIndex] = {}
        if self._embedder:
            self._vector_indexes = _load_vector_indexes(config)
        self._llm_planner = LLMQueryPlanner(self._llm.get_provider(), self._prompt_loader)
        self._cache: OrderedDict = OrderedDict()

    def _cache_key(self, query: str, top_k: int, mode: str) -> str:
        return f"{query}::t{top_k}::m{mode}"

    def _cache_get(self, key: str) -> Optional[EnhancedRetrievalResult]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        result, cached_at = entry
        if time.monotonic() - cached_at > self._CACHE_TTL:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return result

    def _cache_set(self, key: str, result: EnhancedRetrievalResult) -> None:
        self._cache[key] = (result, time.monotonic())
        while len(self._cache) > self._CACHE_MAXSIZE:
            self._cache.popitem(last=False)

    def search(self, query: str, *, top_k: int = 5, mode: str = "local",
               query_plan: Optional[QueryPlan] = None) -> EnhancedRetrievalResult:
        cache_key = self._cache_key(query, top_k, mode)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        rule_plan = query_plan or self._planner.build(query)
        effective_plan = self._llm_planner.enhance(rule_plan)
        rewritten = self._rewriter.rewrite(query, effective_plan)
        base_result = self._local.search(rewritten.rewritten, top_k=max(top_k * 4, _MIN_LOCAL_CANDIDATES), query_plan=effective_plan)
        hits = self._boost_hits_for_answerability(base_result.hits, query_plan=effective_plan)

        vector_hit_ids: List[str] = []
        vector_enabled = False
        if self._embedder and self._vector_indexes:
            try:
                query_vec = self._embedder.embed_one(query)
                vector_candidates: Dict[str, float] = {}
                for idx in self._vector_indexes.values():
                    for cid, score in idx.search(query_vec, top_k=max(top_k * 4, _MIN_LOCAL_CANDIDATES)):
                        if score > vector_candidates.get(cid, -1):
                            vector_candidates[cid] = score
                if vector_candidates:
                    hits = _rrf_fuse(hits, vector_candidates, top_k=max(top_k * 4, _MIN_LOCAL_CANDIDATES))
                    vector_hit_ids = list(vector_candidates.keys())
                    vector_enabled = True
            except EmbedderError:
                logger.warning("向量检索降级，回退到纯词法检索")

        wiki_hits_raw = self._wiki_searcher.search(rewritten.rewritten, top_k=4) if self._wiki_searcher else []
        wiki_hits = [{"title": h.title, "relative_path": h.relative_path, "page_type": h.page_type,
                       "summary": h.summary, "score": h.score, "matched_terms": h.matched_terms}
                      for h in wiki_hits_raw]
        explanations = list(effective_plan.explain)
        explanations.append(f"本地召回 {base_result.total_hits} 条 chunk")
        if vector_enabled:
            explanations.append(f"向量融合 {len(vector_hit_ids)} 条候选")
        if wiki_hits:
            explanations.append(f"Wiki 命中 {len(wiki_hits)} 条")

        if mode == "llm":
            reranked_hits, llm_meta, rerank_mode = self._rerank_with_llm(query, hits, top_k=top_k)
        else:
            reranked_hits = hits[:top_k]
            llm_meta = None
            rerank_mode = "local"

        result = EnhancedRetrievalResult(query=query, query_intent=effective_plan.query_intent,
                                          rewritten_query=rewritten.rewritten, total_hits=base_result.total_hits,
                                          hits=reranked_hits, rerank_mode=rerank_mode, wiki_hits=wiki_hits,
                                          query_plan=effective_plan.to_dict(), explanations=explanations, llm=llm_meta)
        self._cache_set(cache_key, result)
        return result

    def _boost_hits_for_answerability(self, hits: Sequence[RetrievalHit], *, query_plan: QueryPlan) -> List[RetrievalHit]:
        boosted = []
        for hit in hits:
            score = hit.score
            joined = (hit.title + " " + " > ".join(hit.section_path) + " " + hit.content_preview).lower()
            if query_plan.query_intent == "definition" and any(w in joined for w in ("定义", "术语", "含义", "缩写")):
                score += 2.0
            if query_plan.query_intent == "timeline" and any(w in joined for w in ("进展", "里程碑", "阶段", "计划", "当前")):
                score += 1.8
            if query_plan.question_type == "project" and any(w in joined for w in ("目标", "进展", "结论", "下一步")):
                score += 1.2
            boosted.append(hit.with_score(score))
        boosted.sort(key=lambda item: (-item.score, item.relative_path, item.line_start))
        return boosted

    def _rerank_with_llm(self, query: str, hits: Sequence[RetrievalHit], *, top_k: int) -> tuple:
        if not hits:
            return [], {"fallback_used": False, "reason": "no_hits"}, "llm"
        prompt = self._build_rerank_prompt(query, hits[:min(len(hits), 12)])
        route_context = {"input_type": "text", "task_type": "analysis", "complexity": "complex", "use_case": "retrieval_rerank"}
        try:
            result = self._llm.generate(prompt, route_context=route_context)
            ranked_ids = _parse_ranked_ids(result.text)
            reranked = _apply_rank_order(hits, ranked_ids, top_k=top_k)
            return reranked, {"selected_role": result.selected_role, "provider": result.provider,
                              "model": result.model, "api_base_url": result.api_base_url,
                              "matched_rule": result.matched_rule, "fallback_used": False}, "llm"
        except LLMProviderError as exc:
            logger.warning("LLM 重排降级：%s，回退到本地排序", exc)
            return list(hits[:top_k]), {"fallback_used": True, "reason": str(exc)}, "local_fallback"

    def _build_rerank_prompt(self, query: str, hits: Sequence[RetrievalHit]) -> str:
        lines = [f"{i}. 标题：{h.title}；路径：{h.relative_path}；标签：{','.join(h.structural_tags) or '无'}；内容：{h.content_preview}"
                 for i, h in enumerate(hits, start=1)]
        return self._prompt_loader.render("retrieval_rerank.md", {"query": query, "candidate_lines": "\n".join(lines)})


def _parse_ranked_ids(text: str) -> List[int]:
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [int(item) for item in data if str(item).isdigit() or isinstance(item, int)]
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return [int(item) for item in re.findall(r"\d+", text)]


def _apply_rank_order(hits, ranked_ids, *, top_k: int) -> List[RetrievalHit]:
    ranked = []
    seen = set()
    for item in ranked_ids:
        index = item - 1
        if 0 <= index < len(hits) and index not in seen:
            ranked.append(hits[index])
            seen.add(index)
        if len(ranked) >= top_k:
            break
    if len(ranked) < top_k:
        for index, hit in enumerate(hits):
            if index in seen:
                continue
            ranked.append(hit)
            if len(ranked) >= top_k:
                break
    return ranked


def _rrf_fuse(lexical_hits, vector_scores, *, top_k: int, k: int = 60,
              lexical_weight: float = 0.5, vector_weight: float = 0.5) -> List[RetrievalHit]:
    """RRF + 向量语义融合。

    RRF 得分量级 ~[0, 0.02]，归一化 BM25 得分量级 ~[0, 1]。
    各占一半权重，避免 BM25 原始分（5-20+）完全支配排序。
    同时包含纯向量命中（超出 lexical_hits 范围的新发现）。
    """
    lexical_rrf = {}
    for rank, hit in enumerate(lexical_hits, start=1):
        lexical_rrf[hit.chunk_id] = lexical_weight * (1.0 / (k + rank))
    max_vec = max(vector_scores.values()) if vector_scores else 1.0
    vector_rrf = {cid: vector_weight * (score / max(max_vec, 1e-9)) for cid, score in vector_scores.items()}
    all_ids = set(lexical_rrf) | set(vector_rrf)
    combined = {cid: lexical_rrf.get(cid, 0.0) + vector_rrf.get(cid, 0.0) for cid in all_ids}
    hit_by_id = {h.chunk_id: h for h in lexical_hits}
    ranked_ids = sorted(combined, key=lambda cid: -combined[cid])

    # 归一化 BM25 得分，量级对齐 RRF 得分
    max_lexical = max((h.score for h in lexical_hits if h.score > 0), default=1.0)
    bm25_bonus = 0.02  # BM25 归一化后对融合得分的贡献系数

    result = []
    for cid in ranked_ids:
        if cid in hit_by_id:
            orig = hit_by_id[cid]
            normalized_bm25 = (orig.score / max(max_lexical, 1e-9)) * bm25_bonus
            blended = combined[cid] + normalized_bm25
            result.append(orig.with_score(blended)._with_explanation(
                orig.explanation + " [vector-fused]"))
        # 包含纯向量命中
        elif len(hit_by_id) < top_k:
            result.append(RetrievalHit(
                chunk_id=cid, score=combined[cid], title="",
                relative_path="", section_path=[], content_preview="",
                line_start=0, line_end=0, chunk_type="vector",
                explanation=f"向量相似度={combined[cid]:.4f}",
            ))
        if len(result) >= top_k:
            break
    return result


def _init_embedder(config: ConfigBundle):
    llm_cfg = config.llm
    emb_cfg = llm_cfg.get("embedding", {})
    if not emb_cfg.get("enabled", False):
        return None
    return build_embedder_from_config(llm_cfg)


def _load_vector_indexes(config: ConfigBundle) -> Dict[str, VectorIndex]:
    metadata_root = config.root / "data" / "metadata"
    sources = config.data_source.get("sources", {})
    indexes = {}
    for source_name, cfg in sources.items():
        if not cfg.get("enabled", True):
            continue
        index_path = metadata_root / f"{source_name}_vector_index"
        idx = VectorIndex(index_path)
        if idx.load():
            indexes[source_name] = idx
    return indexes
