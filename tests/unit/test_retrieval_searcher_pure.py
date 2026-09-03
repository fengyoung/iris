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
) -> ChunkRecord:
    tokens = tokenize(content)
    freq = defaultdict(int)
    for t in tokens:
        freq[t] += 1
    return ChunkRecord(
        chunk_id="test-chunk-001",
        source_name="test_source",
        document_path="/docs/test.md",
        relative_path=relative_path,
        document_hash="abc123",
        title=title,
        section_path=section_path or [],
        level=1,
        content=content,
        content_preview=content[:180],
        line_start=1,
        line_end=20,
        word_count=len(tokens),
        token_count=len(tokens),
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
        assert hit.score == 0.0  # _chunk_to_hit 不设置 score

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
