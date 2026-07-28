"""测试 ASR 热词提取 — wiki/asr_hotwords.py 纯函数。"""

from __future__ import annotations

import json
import pytest

from iris.wiki.context_loader import WikiPageInfo
from iris.wiki.asr.hotwords import (
    _build_page_batches,
    _clean_text_term,
    _is_valid_hotword,
    _parse_hotwords_response,
    hotwords_to_terms,
    _HOTWORD_BATCH_SIZE,
)
from iris.wiki.asr import AsrTerm


def _make_page(title, page_type, body="", summary=""):
    return WikiPageInfo(
        path=None, title=title, page_type=page_type,
        status="stable", summary=summary, body=body, relative_path="",
    )


class TestBuildPageBatches:
    def test_empty_pages(self):
        assert _build_page_batches([]) == []

    def test_single_batch(self):
        pages = [_make_page(f"p{i}", "concept") for i in range(5)]
        batches = _build_page_batches(pages)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_multiple_batches(self):
        pages = [_make_page(f"p{i}", "concept") for i in range(_HOTWORD_BATCH_SIZE + 5)]
        batches = _build_page_batches(pages)
        assert len(batches) == 2

    def test_sorted_by_type_person_first(self):
        pages = [
            _make_page("d1", "domain"), _make_page("p1", "person"),
            _make_page("c1", "concept"), _make_page("p2", "person"),
        ]
        batches = _build_page_batches(pages)
        types = [p.page_type for b in batches for p in b]
        expected = ["person", "person", "concept", "domain"]
        assert types == expected


class TestCleanTextTerm:
    def test_strips_whitespace(self):
        assert _clean_text_term("  测试  ") == "测试"

    def test_strips_chinese_punctuation(self):
        assert _clean_text_term("「测试」") == "测试"

    def test_normal_term_unchanged(self):
        assert _clean_text_term("人工智能") == "人工智能"


class TestIsValidHotword:
    def test_too_short(self):
        assert _is_valid_hotword("A") is False
        assert _is_valid_hotword("") is False

    def test_valid_term(self):
        assert _is_valid_hotword("人工智能") is True

    def test_pure_numbers_invalid(self):
        assert _is_valid_hotword("12345") is False

    def test_unmatched_brackets(self):
        assert _is_valid_hotword("测试（未完") is False

    def test_long_sentence_fragment(self):
        assert _is_valid_hotword("这是一个很长的测试句子在系统中运行") is False

    def test_valid_mixed_term(self):
        assert _is_valid_hotword("BM25算法") is True


class TestParseHotwordsResponse:
    def test_json_array(self):
        resp = json.dumps([{"term": "人工智能"}, {"term": "机器学习"}])
        result = _parse_hotwords_response(resp, set())
        assert result == ["人工智能", "机器学习"]

    def test_json_in_markdown_block(self):
        resp = "```json\n" + json.dumps([{"term": "深度学习"}]) + "\n```"
        result = _parse_hotwords_response(resp, set())
        assert result == ["深度学习"]

    def test_dedup_with_existing(self):
        resp = json.dumps([{"term": "已有"}, {"term": "新词"}])
        result = _parse_hotwords_response(resp, {"已有"})
        assert result == ["新词"]

    def test_invalid_json_parses_substring(self):
        result = _parse_hotwords_response("前面文字" + json.dumps([{"term": "热词"}]) + "后面", set())
        assert result == ["热词"]

    def test_empty_response(self):
        assert _parse_hotwords_response("", set()) == []

    def test_plain_text_no_json(self):
        assert _parse_hotwords_response("not json at all", set()) == []


class TestHotwordsToTerms:
    def test_adds_new_words(self):
        existing = [AsrTerm(term="已有词", category="term", context="test")]
        result = hotwords_to_terms(["新热词", "已有词"], existing)
        names = [t.term for t in result]
        assert "新热词" in names
        assert "已有词" in names

    def test_empty_hotwords(self):
        assert hotwords_to_terms([], []) == []
