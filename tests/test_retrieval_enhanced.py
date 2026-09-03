"""retrieval/enhanced.py — RRF 融合、LRU 缓存、QueryRewriter 扩展同义词专项测试。"""

from __future__ import annotations

import time
import threading
from unittest.mock import MagicMock


from iris.retrieval.enhanced import (
    QueryRewriter,
    _rrf_fuse,
    _parse_ranked_ids,
    _apply_rank_order,
)
from iris.retrieval.searcher import RetrievalHit


# ── 工具函数 ──────────────────────────────────────────────────────────


def make_hit(chunk_id: str, score: float, title: str = "") -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id, score=score, title=title,
        relative_path=f"{chunk_id}.md", section_path=[],
        content_preview="", line_start=0, line_end=10,
        chunk_type="section", explanation="",
    )


# ── QueryRewriter ─────────────────────────────────────────────────────


def test_query_rewriter_default_synonyms():
    rw = QueryRewriter()
    result = rw.rewrite("周报内容")
    assert "周报" in result.tokens
    assert "双周报" in result.tokens


def test_query_rewriter_extra_synonyms_merged():
    extra = {"AI": ["人工智能", "机器学习"]}
    rw = QueryRewriter(extra_synonyms=extra)
    result = rw.rewrite("AI 进展")
    assert "人工智能" in result.tokens
    assert "机器学习" in result.tokens
    # 默认词典仍然存在
    assert "双周报" in rw._synonym_map.get("周报", [])


def test_query_rewriter_extra_extends_existing_key():
    """extra_synonyms 中的 key 与默认词典重叠时应合并而非覆盖。"""
    extra = {"周报": ["月报"]}
    rw = QueryRewriter(extra_synonyms=extra)
    synonyms = rw._synonym_map["周报"]
    assert "双周报" in synonyms   # 原默认项保留
    assert "月报" in synonyms     # 新增项追加


def test_query_rewriter_no_extra_synonyms():
    rw = QueryRewriter(extra_synonyms=None)
    assert rw._synonym_map is QueryRewriter.SYNONYM_MAP


# ── _rrf_fuse ────────────────────────────────────────────────────────


def test_rrf_fuse_basic_ordering():
    """向量分高的 chunk 应排在靠前位置。"""
    hits = [make_hit("c1", 5.0), make_hit("c2", 3.0), make_hit("c3", 1.0)]
    vector_scores = {"c1": 0.9, "c2": 0.3, "c3": 0.5}
    result = _rrf_fuse(hits, vector_scores, top_k=3)
    assert len(result) == 3
    # c1 词法+向量均高，应排第一
    assert result[0].chunk_id == "c1"


def test_rrf_fuse_pure_vector_hit_included():
    """纯向量命中（不在 lexical_hits 中）当 top_k 有余量时应被包含。"""
    hits = [make_hit("c1", 5.0)]
    vector_scores = {"c1": 0.9, "c2": 0.8}  # c2 是纯向量命中
    result = _rrf_fuse(hits, vector_scores, top_k=2)
    ids = [r.chunk_id for r in result]
    assert "c2" in ids


def test_rrf_fuse_top_k_respected():
    hits = [make_hit(f"c{i}", float(10 - i)) for i in range(10)]
    vector_scores = {f"c{i}": float(i) / 10 for i in range(10)}
    result = _rrf_fuse(hits, vector_scores, top_k=5)
    assert len(result) == 5


def test_rrf_fuse_custom_weights():
    """自定义权重下向量权重高时，向量高分 chunk 应被提升。"""
    hits = [make_hit("c1", 10.0), make_hit("c2", 1.0)]
    # c2 向量分高，但 c1 词法分高
    vector_scores = {"c1": 0.1, "c2": 0.99}
    result_default = _rrf_fuse(hits, vector_scores, top_k=2,
                               lexical_weight=0.5, vector_weight=0.5)
    result_vec_heavy = _rrf_fuse(hits, vector_scores, top_k=2,
                                 lexical_weight=0.1, vector_weight=0.9)
    # 向量权重高时 c2 排名更靠前
    default_c2_pos = next(i for i, r in enumerate(result_default) if r.chunk_id == "c2")
    vec_c2_pos = next(i for i, r in enumerate(result_vec_heavy) if r.chunk_id == "c2")
    assert vec_c2_pos <= default_c2_pos


def test_rrf_fuse_empty_vector_scores():
    hits = [make_hit("c1", 5.0), make_hit("c2", 3.0)]
    result = _rrf_fuse(hits, {}, top_k=2)
    assert len(result) == 2


# ── LRU 缓存线程安全 ─────────────────────────────────────────────────


def test_cache_concurrent_set_get(config_bundle):
    """多线程并发读写缓存不崩溃，最终状态一致。"""
    from iris.retrieval.enhanced import EnhancedRetrievalResult
    from iris.retrieval.enhanced import EnhancedRetriever
    from unittest.mock import patch

    # 构造最小化 EnhancedRetriever（不需要真实 LLM / 索引）
    with patch("iris.retrieval.enhanced.LocalRetriever"), \
         patch("iris.retrieval.enhanced.LLMService"), \
         patch("iris.retrieval.enhanced.WikiSearcher"), \
         patch("iris.retrieval.enhanced._init_embedder", return_value=None):
        retriever = EnhancedRetriever(config_bundle)

    dummy = MagicMock(spec=EnhancedRetrievalResult)
    errors = []

    def writer(key_suffix):
        try:
            for i in range(50):
                key = f"query{key_suffix}::t5::mlocal"
                retriever._cache_set(key, dummy)
        except Exception as e:
            errors.append(e)

    def reader(key_suffix):
        try:
            for i in range(50):
                key = f"query{key_suffix}::t5::mlocal"
                retriever._cache_get(key)
        except Exception as e:
            errors.append(e)

    threads = []
    for j in range(6):
        threads.append(threading.Thread(target=writer, args=(j % 3,)))
        threads.append(threading.Thread(target=reader, args=(j % 3,)))

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"并发操作产生异常: {errors}"


def test_cache_ttl_expiry(config_bundle):
    """缓存 TTL 过期后返回 None。"""
    from iris.retrieval.enhanced import EnhancedRetriever
    from unittest.mock import patch

    with patch("iris.retrieval.enhanced.LocalRetriever"), \
         patch("iris.retrieval.enhanced.LLMService"), \
         patch("iris.retrieval.enhanced.WikiSearcher"), \
         patch("iris.retrieval.enhanced._init_embedder", return_value=None):
        retriever = EnhancedRetriever(config_bundle)

    retriever._CACHE_TTL = 0.01  # 10ms TTL，用于测试
    key = "testquery::t5::mlocal"
    dummy = MagicMock()
    retriever._cache_set(key, dummy)
    assert retriever._cache_get(key) is not None  # 刚写入应命中

    time.sleep(0.05)  # 等待过期
    assert retriever._cache_get(key) is None      # 过期后应返回 None


def test_cache_maxsize_eviction(config_bundle):
    """缓存超过 MAXSIZE 时，旧条目应被驱逐。"""
    from iris.retrieval.enhanced import EnhancedRetriever
    from unittest.mock import patch

    with patch("iris.retrieval.enhanced.LocalRetriever"), \
         patch("iris.retrieval.enhanced.LLMService"), \
         patch("iris.retrieval.enhanced.WikiSearcher"), \
         patch("iris.retrieval.enhanced._init_embedder", return_value=None):
        retriever = EnhancedRetriever(config_bundle)

    retriever._CACHE_MAXSIZE = 3
    dummy = MagicMock()
    for i in range(5):
        retriever._cache_set(f"key{i}::t5::mlocal", dummy)

    assert len(retriever._cache) <= 3


# ── _parse_ranked_ids / _apply_rank_order ────────────────────────────


def test_parse_ranked_ids_json():
    assert _parse_ranked_ids("[3, 1, 2]") == [3, 1, 2]


def test_parse_ranked_ids_plain_text():
    assert _parse_ranked_ids("排序：3, 1, 2") == [3, 1, 2]


def test_apply_rank_order_reorders():
    hits = [make_hit("c1", 1.0), make_hit("c2", 2.0), make_hit("c3", 3.0)]
    ranked = _apply_rank_order(hits, [3, 1, 2], top_k=3)
    assert [r.chunk_id for r in ranked] == ["c3", "c1", "c2"]


def test_apply_rank_order_fills_missing():
    """ranked_ids 不足 top_k 时，补充未排序的剩余 hits。"""
    hits = [make_hit(f"c{i}", float(i)) for i in range(5)]
    # ranked_ids=[3] → 1-indexed，对应 hits[2]=c2
    ranked = _apply_rank_order(hits, [3], top_k=3)
    assert len(ranked) == 3
    assert ranked[0].chunk_id == "c2"   # 1-indexed id=3 → hits[2]=c2
    # 其余位置用未被选中的 hits 补充
    remaining_ids = {r.chunk_id for r in ranked[1:]}
    assert len(remaining_ids) == 2
    assert "c2" not in remaining_ids    # 不重复选 c2
