"""iris.retrieval.searcher 纯函数与数据类单元测试。"""

from __future__ import annotations

from collections import defaultdict

import pytest

from iris.ingest.chunker import ChunkRecord
from iris.retrieval.searcher import (
    RetrievalHit,
    RetrievalResult,
    _chunk_to_hit,
    _score_chunk,
)
from iris.utils.tokenization import tokenize


# ── helper ─────────────────────────────────────────────────


def make_chunk(
    title: str = "测试标题",
    content: str = "这是测试内容",
    section_path=None,
    relative_path: str = "docs/test.md",
    token_freq=None,
    chunk_type: str = "section",
    chunk_id: str = "test-chunk-001",
    line_start: int = 1,
    token_count=None,
) -> ChunkRecord:
    tokens = tokenize(content)
    freq = defaultdict(int)
    for t in tokens:
        freq[t] += 1
    return ChunkRecord(
        chunk_id=chunk_id,
        source_name="test_source",
        document_path="/docs/test.md",
        relative_path=relative_path,
        document_hash="abc123",
        title=title,
        section_path=section_path or [],
        level=1,
        content=content,
        content_preview=content[:180],
        line_start=line_start,
        line_end=line_start + 19,
        word_count=len(tokens),
        token_count=token_count if token_count is not None else len(tokens),
        chunk_type=chunk_type,
        token_freq=token_freq if token_freq is not None else dict(freq),
    )


# ── _score_chunk ────────────────────────────────────────────


class TestScoreChunk:
    def test_empty_query_returns_zero(self):
        chunk = make_chunk(title="项目Alpha", content="内容")
        score, matched = _score_chunk("", [], chunk)
        assert score == 0.0
        assert matched == []

    def test_title_exact_match_gives_high_score(self):
        chunk = make_chunk(title="项目Alpha", content="其他不相关内容")
        query = "项目Alpha"
        tokens = tokenize(query)
        score, matched = _score_chunk(query, tokens, chunk)
        assert score >= 5.0  # title_bonus=5.0

    def test_token_match_returns_matched(self):
        chunk = make_chunk(title="搜索排序", content="搜索排序算法")
        query = "搜索"
        tokens = tokenize(query)
        score, matched = _score_chunk(query, tokens, chunk)
        assert score > 0
        assert len(matched) >= 1

    def test_section_path_match(self):
        chunk = make_chunk(
            title="其他标题",
            content="内容",
            section_path=["召回率", "评估指标"],
        )
        query = "召回率"
        tokens = tokenize(query)
        score, matched = _score_chunk(query, tokens, chunk)
        assert score > 0

    def test_bm25_content_score_with_token_freq(self):
        """当 token_freq 存在时使用预计算词频计算 BM25。"""
        chunk = make_chunk(
            title="无关标题",
            content="项目 Alpha 功能 上线 里程碑",
            token_freq={"项目": 3, "alpha": 2, "功能": 1},
        )
        query = "项目"
        tokens = tokenize(query)
        score, matched = _score_chunk(query, tokens, chunk,
                                      total_docs=100, avg_doc_len=50.0)
        assert score > 0
        assert "项目" in matched

    def test_no_token_freq_fallback_tokenization(self):
        """token_freq 为空 dict 时，fallback 到实时分词，命中标题或内容。"""
        # title 直接包含查询词，即使 token_freq={} 也可得分（通过标题匹配）
        chunk = make_chunk(
            title="召回率优化",
            content="召回率优化方案详细描述",
            token_freq={},
        )
        query = "召回率"
        tokens = tokenize(query)
        score, matched = _score_chunk(query, tokens, chunk,
                                      total_docs=10, avg_doc_len=20.0)
        # 标题包含"召回率"，title_bonus 应触发
        assert score > 0

    def test_matched_terms_max_6(self):
        """matched 列表最多 6 个词。"""
        # 构建含多个不同词的内容
        content = "A B C D E F G H I J K L M"
        token_freq = {t.lower(): 2 for t in content.split()}
        chunk = make_chunk(title="T", content=content, token_freq=token_freq)
        query = "A B C D E F G H"
        tokens = tokenize(query)
        score, matched = _score_chunk(query, tokens, chunk,
                                      total_docs=1, avg_doc_len=10.0)
        assert len(matched) <= 6


# ── _chunk_to_hit ─────────────────────────────────────────────


class TestChunkToHit:
    def test_returns_retrieval_hit(self):
        chunk = make_chunk(title="测试标题", content="内容")
        hit = _chunk_to_hit(chunk, ["词A"], "BM25 score=1.5")
        assert isinstance(hit, RetrievalHit)
        assert hit.title == "测试标题"
        assert hit.matched_terms == ["词A"]
        assert hit.explanation == "BM25 score=1.5"
        assert hit.score == 0.0  # 未传 score 时保持 0.0（向后兼容默认值）

    def test_score_propagated_from_caller(self):
        """真实 BM25 分必须落到 hit.score：下游排序与 RRF 都读该字段。"""
        chunk = make_chunk(title="测试标题", content="内容")
        hit = _chunk_to_hit(chunk, ["词A"], "BM25 score=1.5", score=1.5)
        assert hit.score == 1.5

    def test_all_fields_populated(self):
        chunk = make_chunk(
            title="完整标题",
            content="完整内容",
            section_path=["章节A"],
            relative_path="path/to/doc.md",
            chunk_type="title",
        )
        hit = _chunk_to_hit(chunk, [], "")
        assert hit.relative_path == "path/to/doc.md"
        assert hit.section_path == ["章节A"]
        assert hit.chunk_type == "title"
        assert hit.chunk_id == "test-chunk-001"


# ── RetrievalHit ──────────────────────────────────────────────


class TestRetrievalHit:
    def _make_hit(self, score=1.0):
        return RetrievalHit(
            chunk_id="c1",
            score=score,
            title="标题A",
            relative_path="file.md",
            section_path=["章节"],
            content_preview="预览内容",
            line_start=1,
            line_end=10,
        )

    def test_with_score(self):
        hit = self._make_hit(score=1.0)
        new_hit = hit.with_score(9.9)
        assert new_hit.score == 9.9
        assert new_hit.chunk_id == hit.chunk_id
        assert new_hit.title == hit.title

    def test_with_score_returns_new_instance(self):
        hit = self._make_hit(score=1.0)
        new_hit = hit.with_score(2.0)
        assert new_hit is not hit

    def test_with_explanation(self):
        hit = self._make_hit()
        new_hit = hit._with_explanation("new explanation")
        assert new_hit.explanation == "new explanation"
        assert new_hit.score == hit.score

    def test_frozen_dataclass(self):
        hit = self._make_hit()
        with pytest.raises(Exception):
            hit.score = 999.0  # type: ignore


# ── RetrievalResult.to_dict() ─────────────────────────────────


class TestRetrievalResult:
    def test_to_dict_basic(self):
        hit = RetrievalHit(
            chunk_id="c1",
            score=0.8,
            title="标题",
            relative_path="f.md",
            section_path=[],
            content_preview="预览",
            line_start=1,
            line_end=5,
        )
        result = RetrievalResult(total_hits=1, hits=[hit])
        d = result.to_dict()
        assert d["total_hits"] == 1
        assert len(d["hits"]) == 1
        assert d["hits"][0]["title"] == "标题"
        assert d["hits"][0]["score"] == 0.8

    def test_to_dict_empty_hits(self):
        result = RetrievalResult(total_hits=0, hits=[])
        d = result.to_dict()
        assert d["total_hits"] == 0
        assert d["hits"] == []

    def test_to_dict_contains_all_hit_fields(self):
        hit = RetrievalHit(
            chunk_id="c2",
            score=0.5,
            title="T",
            relative_path="r.md",
            section_path=["S1"],
            content_preview="p",
            line_start=10,
            line_end=20,
            chunk_type="section",
            matched_terms=["term1"],
            explanation="explain",
        )
        result = RetrievalResult(total_hits=1, hits=[hit])
        d = result.to_dict()
        hit_dict = d["hits"][0]
        assert "chunk_id" in hit_dict
        assert "matched_terms" in hit_dict
        assert "explanation" in hit_dict
        assert hit_dict["matched_terms"] == ["term1"]


# ── _doc_date_ord ─────────────────────────────────────────────


class TestDocDateOrd:
    def test_reads_filename_prefix(self):
        from iris.retrieval.searcher import _doc_date_ord
        assert _doc_date_ord("07-成员周报/202609/20260911-周报-w37-卞凯.md") == 20260911
        assert _doc_date_ord("01-目标管理/2026/20260111-OP规划.md") == 20260111

    def test_ignores_month_directory(self):
        """6 位月份目录名（202609）不得被当成日期。"""
        from iris.retrieval.searcher import _doc_date_ord
        assert _doc_date_ord("07-成员周报/202609/x.md") == 0

    def test_rejects_impossible_month_or_day(self):
        from iris.retrieval.searcher import _doc_date_ord
        assert _doc_date_ord("20261332-bad.md") == 0
        assert _doc_date_ord("20260012-bad.md") == 0

    def test_returns_zero_without_date(self):
        from iris.retrieval.searcher import _doc_date_ord
        assert _doc_date_ord("docs/readme.md") == 0
        assert _doc_date_ord("") == 0


# ── frontmatter 块识别 + 代表块选取 ────────────────────────────


class TestPickRepresentativeChunks:
    def test_skips_frontmatter_and_takes_longest_body(self):
        from iris.retrieval.searcher import _pick_representative_chunks
        fm = make_chunk(content="---\ntype: 成员周报\ntitle: 周报 - 卞凯\n---",
                        token_count=99, chunk_id="c1", line_start=1)
        body = make_chunk(content="### 1. 本周工作总结\n实质内容",
                          token_count=10, chunk_id="c2", line_start=30)
        picked = _pick_representative_chunks([fm, body], chunks_per_doc=1)
        assert [c.chunk_id for c in picked] == ["c2"]

    def test_longest_body_wins_even_if_short_preamble_exists(self):
        from iris.retrieval.searcher import _pick_representative_chunks
        preamble = make_chunk(content="## 邮件信息\n发件人：卞凯", token_count=23,
                              chunk_id="c2", line_start=5)
        body = make_chunk(content="## 周报内容\n详细正文", token_count=73,
                          chunk_id="c3", line_start=20)
        picked = _pick_representative_chunks([preamble, body], chunks_per_doc=1)
        assert [c.chunk_id for c in picked] == ["c3"]

    def test_relaxes_when_all_frontmatter(self):
        """全部为 frontmatter 块时放宽回退，不返回空。"""
        from iris.retrieval.searcher import _pick_representative_chunks
        fm1 = make_chunk(content="---\na: 1\n---", token_count=5, chunk_id="c1")
        fm2 = make_chunk(content="---\nb: 2\n---", token_count=9, chunk_id="c2")
        picked = _pick_representative_chunks([fm1, fm2], chunks_per_doc=1)
        assert [c.chunk_id for c in picked] == ["c2"]

    def test_respects_chunks_per_doc(self):
        from iris.retrieval.searcher import _pick_representative_chunks
        chunks = [make_chunk(content=f"正文{i}", token_count=i * 10, chunk_id=f"c{i}")
                  for i in range(1, 5)]
        assert len(_pick_representative_chunks(chunks, chunks_per_doc=2)) == 2


# ── LocalRetriever：同分兜底 + latest_documents ────────────────


def _make_retriever(chunks):
    """轻量 harness：直接塞入 chunks 与 BM25 统计量，绕过配置与磁盘加载。"""
    from iris.retrieval.searcher import LocalRetriever, _BM25_B, _BM25_K1
    retriever = object.__new__(LocalRetriever)
    retriever._chunks = list(chunks)
    retriever._loaded = True
    retriever._by_source = {}
    retriever._bm25_k1 = _BM25_K1
    retriever._bm25_b = _BM25_B
    retriever._total_docs = max(len(chunks), 1)
    retriever._avg_doc_len = max(
        1.0, sum(max(c.token_count, 1) for c in chunks) / max(len(chunks), 1))
    df = defaultdict(int)
    for chunk in chunks:
        for term in set(chunk.token_freq):
            df[term] += 1
    retriever._df = dict(df)
    retriever._corpus_stats_computed = True
    return retriever


def _tied_chunk(path: str, chunk_id: str, line_start: int = 1,
                title: str = "测试标题", content: str = "这是测试内容") -> ChunkRecord:
    """同 title/content → BM25 分完全并列，只有路径/行号不同。"""
    return make_chunk(title=title, content=content, relative_path=path,
                      chunk_id=chunk_id, line_start=line_start)


class TestSearchTieBreak:
    def test_exact_tie_prefers_newer_document(self):
        """同分时按文档日期降序：修复前恒按路径升序 → 返回最旧的一份。"""
        old = _tied_chunk("07-成员周报/202506/20250606-周报-w23-卞凯.md", "old")
        new = _tied_chunk("07-成员周报/202609/20260911-周报-w37-卞凯.md", "new")
        hits = _make_retriever([old, new]).search("测试标题", top_k=2).hits
        assert [h.chunk_id for h in hits] == ["new", "old"]

    def test_undated_document_does_not_jump_ahead(self):
        """无日期文档不得因缺失日期插到有日期文档前面。"""
        dated = _tied_chunk("07-成员周报/202506/20250606-x.md", "dated")
        undated = _tied_chunk("misc/no-date.md", "undated")
        hits = _make_retriever([undated, dated]).search("测试标题", top_k=2).hits
        assert [h.chunk_id for h in hits] == ["dated", "undated"]

    def test_score_outranks_date(self):
        """分数优先于日期：高分旧文档仍在前。"""
        high = _tied_chunk("07-成员周报/202506/20250606-x.md", "high",
                           title="测试标题", content="无关内容")
        low = _tied_chunk("07-成员周报/202609/20260911-y.md", "low",
                          title="无关标题", content="这是测试内容")
        hits = _make_retriever([high, low]).search("测试标题", top_k=2).hits
        assert hits[0].chunk_id == "high"

    def test_same_date_falls_back_to_path(self):
        """同分同日期 → 路径升序，保证结果确定。"""
        a = _tied_chunk("07-成员周报/202609/20260911-a.md", "a")
        b = _tied_chunk("07-成员周报/202609/20260911-b.md", "b")
        hits = _make_retriever([b, a]).search("测试标题", top_k=2).hits
        assert [h.chunk_id for h in hits] == ["a", "b"]


class TestLatestDocuments:
    def _weekly(self, name: str, date: str, week: str) -> list:
        base = f"07-成员周报/{date[:6]}/{date}-周报-{week}-{name}.md"
        return [
            make_chunk(content="---\ntype: 成员周报\n---", relative_path=base,
                       chunk_id=f"{base}::chunk-1", line_start=1, token_count=6),
            make_chunk(content="## 邮件信息\n发件人", relative_path=base,
                       chunk_id=f"{base}::chunk-2", line_start=12, token_count=20),
            make_chunk(content="### 1. 本周工作总结\n实际正文内容", relative_path=base,
                       chunk_id=f"{base}::chunk-3", line_start=30, token_count=80),
        ]

    def test_returns_newest_documents_first(self):
        chunks = (self._weekly("卞凯", "20250606", "w23")
                  + self._weekly("卞凯", "20260911", "w37"))
        hits = _make_retriever(chunks).latest_documents("-卞凯.md", doc_limit=2)
        assert "20260911" in hits[0].relative_path
        assert "20250606" in hits[1].relative_path

    def test_picks_body_chunk_not_frontmatter_or_preamble(self):
        chunks = self._weekly("卞凯", "20260911", "w37")
        hits = _make_retriever(chunks).latest_documents("-卞凯.md", doc_limit=1)
        assert len(hits) == 1
        assert hits[0].chunk_id.endswith("chunk-3")

    def test_suffix_filter_is_exact(self):
        chunks = (self._weekly("卞凯", "20260911", "w37")
                  + [make_chunk(relative_path="04-讨论思考/202605/讨论(with卞凯).md",
                                chunk_id="noise-1", content="### 1. 正文"),
                     make_chunk(relative_path="01-目标管理/2026/2026年Q3-OKR.md",
                                chunk_id="noise-2", content="### 1. 正文")])
        hits = _make_retriever(chunks).latest_documents("-卞凯.md", doc_limit=5)
        assert {h.relative_path for h in hits} == {"07-成员周报/202609/20260911-周报-w37-卞凯.md"}

    def test_no_match_returns_empty(self):
        hits = _make_retriever(self._weekly("卞凯", "20260911", "w37")).latest_documents(
            "-不存在的人.md", doc_limit=5)
        assert hits == []
