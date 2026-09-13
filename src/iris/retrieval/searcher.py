"""基于 chunk 摘要的本地检索器。"""

from __future__ import annotations

import functools
import json
import logging
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

from iris.config.loader import ConfigBundle
from iris.ingest.chunker import ChunkRecord
from iris.retrieval.planner import QueryPlan

from iris.utils.tokenization import tokenize  # noqa: F811 — 统一分词

logger = logging.getLogger(__name__)

# BM25 参数
_BM25_K1 = 1.5
_BM25_B = 0.75
_BM25_OOV_DF = 0  # 未登录词文档频率（0 = 不假设任何文档含该词）

# 文档日期前缀：锚定在文件名开头，避免命中 `202609` 这类 6 位月份目录名。
_DOC_DATE_PREFIX_RE = re.compile(r"^(\d{8})[-_]")


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
        # BM25 参数（可通过 app.json retrieval.bm25 配置）
        bm25_cfg = self._config.app.get("retrieval", {}).get("bm25", {}) if self._config.app else {}
        self._bm25_k1 = float(bm25_cfg.get("k1", _BM25_K1))
        self._bm25_b = float(bm25_cfg.get("b", _BM25_B))
        # 全局 BM25 统计量（_ensure_loaded 后填充）
        self._total_docs: int = 0
        self._avg_doc_len: float = 0.0
        self._df: Dict[str, int] = {}  # document frequency per term
        self._corpus_stats_computed: bool = False

    def search(self, query: str, *, top_k: int = 10, query_plan: QueryPlan | None = None) -> RetrievalResult:
        self._ensure_loaded()

        query_tokens = tokenize(query)
        scored: List[Tuple[RetrievalHit, float, str, int]] = []

        for chunk in self._chunks:
            score, matched = _score_chunk(query, query_tokens, chunk,
                                          total_docs=self._total_docs,
                                          avg_doc_len=self._avg_doc_len,
                                          df=self._df,
                                          query_plan=query_plan,
                                          bm25_k1=self._bm25_k1,
                                          bm25_b=self._bm25_b)
            if score <= 0:
                continue
            explanation = f"BM25 score={score:.2f}"
            scored.append((_chunk_to_hit(chunk, matched, explanation, score=score),
                           score, chunk.relative_path, chunk.line_start))

        # 同分兜底按文档日期降序：查询词是人名/术语时，同一批文档常完全并列
        # （如某人的各份周报得分相同），若按路径升序会稳定返回最旧的一份。
        scored.sort(key=lambda item: (-item[1], -_doc_date_ord(item[2]), item[2], item[3]))
        hits = [item[0] for item in scored[:top_k]]
        return RetrievalResult(total_hits=len(scored), hits=hits)

    def latest_documents(self, path_suffix: str, *, doc_limit: int = 4,
                         chunks_per_doc: int = 1) -> List[RetrievalHit]:
        """按路径后缀定位文档，取日期最新的 doc_limit 份，每份挑代表块。

        与 search() 的差别：search() 是词法召回，而人名在其周报正文中出现 0 次，
        任何排序策略都召回不到正文（只召回得到 frontmatter/邮件信息这类含人名的
        前言块）。此方法改用文档身份（文件名约定）定位，再按内容量选代表块。

        Args:
            path_suffix: 文档相对路径后缀，如 ``-卞凯.md``（`-` 前缀避免
                「陈鹏」误配「陈鹏飞」）
            doc_limit: 取最新的若干份文档
            chunks_per_doc: 每份文档取几个代表块

        Returns:
            RetrievalHit 列表（文档按日期降序、同日期按路径升序）；无匹配返回 []。
        """
        self._ensure_loaded()

        by_doc: Dict[str, List[ChunkRecord]] = defaultdict(list)
        for chunk in self._chunks:
            if chunk.relative_path.endswith(path_suffix):
                by_doc[chunk.relative_path].append(chunk)
        if not by_doc:
            return []

        ranked_docs = sorted(by_doc, key=lambda path: (-_doc_date_ord(path), path))[:doc_limit]
        hits: List[RetrievalHit] = []
        for path in ranked_docs:
            date_ord = _doc_date_ord(path)
            for chunk in _pick_representative_chunks(by_doc[path],
                                                     chunks_per_doc=chunks_per_doc):
                hits.append(_chunk_to_hit(chunk, [], f"文档日期={date_ord or '未知'}"))
        return hits

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # 优先尝试 SQLite（FTS5 全文搜索，性能更高）
        if self._try_load_sqlite():
            self._loaded = True
            self._compute_corpus_stats()
            return
        # 回退 JSON
        from iris.ingest import iter_chunk_items
        data_source = self._config.data_source
        sources = data_source.get("sources", {})
        metadata_root = self._config.root / "data" / "metadata"
        for item in iter_chunk_items(metadata_root, sources):
            try:
                chunk = ChunkRecord(**item)
                self._chunks.append(chunk)
                source_name = item.get("source_name", "")
                self._by_source.setdefault(source_name, []).append(chunk)
            except (TypeError, ValueError):
                continue
        self._loaded = True
        self._compute_corpus_stats()

    def _compute_corpus_stats(self) -> None:
        """计算全局 BM25 统计量：文档总数、平均长度、词项文档频率。

        使用 chunk.content（全文）而非 content_preview（截断预览），
        确保 TF/IDF/doc_len 统计基于完整内容而非 ~180 字符截断。
        统计结果缓存到磁盘，通过 chunk 索引的 mtime 判新。
        """
        if self._corpus_stats_computed or not self._chunks:
            return

        # 尝试从缓存加载
        metadata_root = self._config.root / "data" / "metadata"
        chunk_index_path = metadata_root / "chunk_hash_index.json"
        stats_cache_path = self._config.root / "data" / "cache" / "bm25_stats.json"

        if chunk_index_path.exists() and stats_cache_path.exists():
            try:
                index_mtime = chunk_index_path.stat().st_mtime
                cached = json.loads(stats_cache_path.read_text(encoding="utf-8"))
                if abs(cached.get("index_mtime", 0) - index_mtime) < 0.01:
                    self._total_docs = cached["total_docs"]
                    self._avg_doc_len = cached["avg_doc_len"]
                    self._df = cached["df"]
                    self._corpus_stats_computed = True
                    logger.info(
                        "BM25 统计从缓存加载: %d 文档, %d 词项",
                        self._total_docs, len(self._df),
                    )
                    return
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                logger.debug("BM25 缓存加载失败，重新计算: %s", exc)

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

        # 写入缓存
        if chunk_index_path.exists():
            try:
                stats_cache_path.parent.mkdir(parents=True, exist_ok=True)
                from iris.utils.shared import atomic_write_json
                atomic_write_json(stats_cache_path, {
                    "index_mtime": chunk_index_path.stat().st_mtime,
                    "total_docs": self._total_docs,
                    "avg_doc_len": self._avg_doc_len,
                    "df": self._df,
                })
            except OSError as exc:
                logger.debug("BM25 缓存写入失败: %s", exc)

    def _try_load_sqlite(self) -> bool:
        """尝试从 SQLite ChunkStore 加载（FTS5 全文搜索加速）。"""
        try:
            from iris.core.storage import ChunkStore
            db_path = self._config.root / "data" / "chunk_store.db"
            if not db_path.exists():
                return False
            with ChunkStore(db_path) as store:
                chunks = store.load_all()
            for chunk in chunks:
                self._chunks.append(chunk)
                self._by_source.setdefault(chunk.source_name, []).append(chunk)
            return len(chunks) > 0
        except Exception as exc:
            logger.warning("Chunk 索引加载失败 (%s): %s", db_path, exc)
            return False


def _adjust_weights_by_query_plan(query_plan: QueryPlan | None) -> tuple[float, float, float, float]:
    """根据 query_plan 调整标题和章节权重。

    Returns:
        (title_bonus, title_token_bonus, section_bonus, section_token_bonus)
    """
    title_bonus = 5.0
    title_token_bonus = 3.0
    section_bonus = 3.0
    section_token_bonus = 2.0

    if query_plan is None:
        return title_bonus, title_token_bonus, section_bonus, section_token_bonus

    # 高优先级 focus_areas 提升标题权重
    focus_mult = 1.0 + 0.5 * len([a for a in query_plan.answer_focus if a == "high"])
    title_bonus *= focus_mult
    title_token_bonus *= focus_mult

    # entity_weights 如果指定了特定实体权重，额外加分
    if query_plan.entities:
        entity_mult = 1.0 + 0.2 * len(query_plan.entities)
        section_bonus *= entity_mult
        section_token_bonus *= entity_mult

    return title_bonus, title_token_bonus, section_bonus, section_token_bonus


def _calculate_title_section_score(
    query_lower: str,
    query_tokens: List[str],
    title_lower: str,
    section_lower: str,
    title_bonus: float,
    title_token_bonus: float,
    section_bonus: float,
    section_token_bonus: float,
) -> tuple[float, List[str]]:
    """计算标题和章节匹配得分。

    Returns:
        (score, matched_tokens)
    """
    score = 0.0
    matched: List[str] = []

    # 标题完整匹配
    if query_lower in title_lower:
        score += title_bonus

    # 标题 token 匹配
    for token in query_tokens:
        if token in title_lower:
            score += title_token_bonus
            if token not in matched:
                matched.append(token)

    # 章节完整匹配
    if query_lower in section_lower:
        score += section_bonus

    # 章节 token 匹配
    for token in query_tokens:
        if token in section_lower:
            score += section_token_bonus
            if token not in matched:
                matched.append(token)

    return score, matched


def _calculate_bm25_score(
    query_tokens: List[str],
    chunk: ChunkRecord,
    total_docs: int,
    avg_doc_len: float,
    df: Dict[str, int] | None,
    bm25_k1: float,
    bm25_b: float,
) -> tuple[float, List[str]]:
    """计算 BM25 得分。

    Returns:
        (bm25_score, matched_tokens)
    """
    # 优先使用预计算的 token_freq
    if chunk.token_freq:
        freq = chunk.token_freq
        doc_len = sum(freq.values())
    else:
        # 回退：兼容旧 chunk 数据
        content_tokens = tokenize(chunk.content)
        freq = defaultdict(int)
        for token in content_tokens:
            freq[token] += 1
        doc_len = len(content_tokens)

    N = max(total_docs, 1)
    avgdl = avg_doc_len if avg_doc_len > 0 else max(doc_len, 50)
    df_map = df if df is not None else {}

    score = 0.0
    matched: List[str] = []

    for qt in query_tokens:
        tf = freq.get(qt, 0)
        if tf > 0:
            dft = df_map.get(qt, _BM25_OOV_DF)
            # 对未登录词使用平缓 IDF
            idf = math.log((N - dft + 0.5) / (max(dft, 1) + 0.5) + 1.0)
            norm = 1 - bm25_b + bm25_b * doc_len / avgdl
            bm25 = idf * (tf * (bm25_k1 + 1)) / (tf + bm25_k1 * norm)
            score += bm25
            if qt not in matched:
                matched.append(qt)

    return score, matched


def _score_chunk(query: str, query_tokens: List[str], chunk: ChunkRecord,
                 *, total_docs: int = 0, avg_doc_len: float = 0.0,
                 df: Dict[str, int] | None = None,
                 query_plan: QueryPlan | None = None,
                 bm25_k1: float = _BM25_K1, bm25_b: float = _BM25_B) -> Tuple[float, List[str]]:
    """对单个 chunk 进行评分（标题/章节匹配 + BM25）。"""
    title_lower = chunk.title.lower()
    section_lower = " ".join(chunk.section_path).lower()
    query_lower = query.lower().strip()

    if not query_lower:
        return 0.0, []

    # 阶段 1：根据 query_plan 调整权重
    title_bonus, title_token_bonus, section_bonus, section_token_bonus = \
        _adjust_weights_by_query_plan(query_plan)

    # 阶段 2：计算标题和章节得分
    title_section_score, matched_title_section = _calculate_title_section_score(
        query_lower, query_tokens, title_lower, section_lower,
        title_bonus, title_token_bonus, section_bonus, section_token_bonus
    )

    # 阶段 3：计算 BM25 得分
    bm25_score, matched_bm25 = _calculate_bm25_score(
        query_tokens, chunk, total_docs, avg_doc_len, df, bm25_k1, bm25_b
    )

    # 合并得分和匹配词
    total_score = title_section_score + bm25_score
    all_matched = matched_title_section + [t for t in matched_bm25 if t not in matched_title_section]

    return total_score, all_matched[:6]


@functools.lru_cache(maxsize=16384)
def _doc_date_ord(relative_path: str) -> int:
    """从 relative_path 的文件名前缀 YYYYMMDD 派生可排序日期；无法解析 → 0。

    用于检索同分时的兜底排序：`-date` 作降序键时，0 最小 → 无日期文档稳定排在
    所有有日期文档之后，不会因缺日期反而插队；同分组内仍按路径确定性排序。

    缓存键是路径字符串（本库约 1.7k 个不同路径），避免每次查询重复正则。
    """
    name = relative_path.rsplit("/", 1)[-1]
    match = _DOC_DATE_PREFIX_RE.match(name)
    if not match:
        return 0
    text = match.group(1)
    month, day = int(text[4:6]), int(text[6:8])
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return 0
    return int(text)


def _is_frontmatter_chunk(text: str) -> bool:
    """chunk 文本是否为 YAML frontmatter 元数据块。

    与 evaluation/_source_locator.py 的 skip_frontmatter 判断一致。不用
    core.frontmatter 的 FRONTMATTER_RE：它要求闭合 `---` 后有换行，而 chunk
    的 content 被 strip 过，会静默判否。
    """
    return (text or "").lstrip().startswith("---")


def _pick_representative_chunks(chunks: Iterable[ChunkRecord], *,
                                chunks_per_doc: int) -> List[ChunkRecord]:
    """从一份文档的 chunks 中挑代表块：优先正文（剔除 frontmatter），取最长者。

    周报这类文档里，人名词只出现在 frontmatter/邮件信息等前言块中，正文块与
    查询词零词面重叠，因此不能按检索分选块，只能按"内容量"选。全部为前言块时
    放宽回退（对齐 _source_locator 的放宽兜底）。
    """
    items = list(chunks)
    body = [c for c in items if not _is_frontmatter_chunk(c.content)]
    pool = body or items
    return sorted(pool, key=lambda c: (-c.token_count, c.line_start))[:chunks_per_doc]


def _chunk_to_hit(chunk: ChunkRecord, matched_terms: List[str], explanation: str,
                  *, score: float = 0.0) -> RetrievalHit:
    """构造检索命中。score 需由调用方传入真实 BM25 分：下游（QA 证据块排序、
    RRF 融合的 bm25_bonus）都读 hit.score，缺失会退化为按 bonus 常量或路径字母序。"""
    return RetrievalHit(chunk_id=chunk.chunk_id, score=score, title=chunk.title,
                        relative_path=chunk.relative_path, section_path=chunk.section_path,
                        content_preview=chunk.content_preview, line_start=chunk.line_start,
                        line_end=chunk.line_end, chunk_type=chunk.chunk_type,
                        structural_tags=chunk.structural_tags, matched_terms=matched_terms,
                        explanation=explanation, extracted_fields=chunk.extracted_fields)
