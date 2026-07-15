"""iris.wiki.discovery_utils 纯函数扩展单元测试（补充已有测试未覆盖路径）。"""

from __future__ import annotations

import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from iris.wiki.discovery_utils import (
    normalize_title,
    find_parent_title,
    canonicalize_title,
    infer_page_type,
    extract_terms,
    extract_persons,
    is_high_value_title,
    is_high_value_term,
    path_weight,
    normalized_key,
    merge_paths,
    should_merge,
    merge_candidates,
    is_wiki_stale,
    parse_wiki_generated_at,
)
from iris.wiki.discovery_types import CandidateItem


def _make_item(title: str, page_type: str, score: int = 10, evidence: int = 5,
               paths=None) -> CandidateItem:
    return CandidateItem(
        title=title, page_type=page_type, query=title,
        score=score, evidence_count=evidence,
        sample_paths=paths if paths is not None else [],
    )


# ─────────────────────────────────────────────────────────────
# normalize_title
# ─────────────────────────────────────────────────────────────

class TestNormalizeTitle:
    def test_strips_spaces(self):
        assert normalize_title("  搜索引擎  ") == "搜索引擎"

    def test_strips_heading_markers(self):
        assert normalize_title("## 搜索引擎") == "搜索引擎"

    def test_strips_asterisks(self):
        assert normalize_title("**重点项目**") == "重点项目"

    def test_strips_leading_enum(self):
        result = normalize_title("1. 搜索优化")
        assert "搜索优化" in result
        assert result.startswith("搜索")


# ─────────────────────────────────────────────────────────────
# find_parent_title
# ─────────────────────────────────────────────────────────────

class TestFindParentTitle:
    def test_empty_list_returns_none(self):
        assert find_parent_title([]) is None

    def test_all_empty_titles_returns_none(self):
        assert find_parent_title(["", " ", "##"]) is None


# ─────────────────────────────────────────────────────────────
# canonicalize_title
# ─────────────────────────────────────────────────────────────

class TestCanonicalizeTitle:
    def test_person_type_long_name(self):
        # 9字符姓名，不会被ONLY_SECTION_RE(<=8字符)匹配
        name = "张小明华伟志强国超"
        result = canonicalize_title(name, page_type="person")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_project_type_with_english_chars(self):
        # 含英文字符，不会被ONLY_SECTION_RE匹配
        title = "AI推荐引擎平台架构"
        result = canonicalize_title(title, page_type="project")
        assert isinstance(result, str)

    def test_structural_title_fallback_to_parent(self):
        # "总结" 是结构性标题，在无父标题时返回空字符串；
        # 有父标题时递归用父标题
        result = canonicalize_title("总结", page_type="domain",
                                    parent_title="AI推荐引擎平台架构")
        # 结果应不含"总结"（已被替换），或返回父标题内容
        assert "总结" not in result or result == ""

    def test_structural_title_no_parent_returns_empty(self):
        # 纯结构标题无父标题时应返回空
        result = canonicalize_title("背景", page_type="domain", parent_title=None)
        assert result == ""


# ─────────────────────────────────────────────────────────────
# infer_page_type
# ─────────────────────────────────────────────────────────────

class TestInferPageType:
    def test_returns_string(self):
        result = infer_page_type("负责人张三")
        assert isinstance(result, str)

    def test_unknown_content_returns_domain(self):
        result = infer_page_type("未知内容")
        assert result == "domain"

    def test_project_keyword(self):
        result = infer_page_type("项目进展汇报")
        assert result == "project"


# ─────────────────────────────────────────────────────────────
# extract_terms
# ─────────────────────────────────────────────────────────────

class TestExtractTerms:
    def test_returns_list(self):
        result = extract_terms("搜索引擎优化技术")
        assert isinstance(result, list)

    def test_nonempty_for_valid_text(self):
        result = extract_terms("BM25召回率优化")
        assert len(result) > 0


# ─────────────────────────────────────────────────────────────
# extract_persons
# ─────────────────────────────────────────────────────────────

class TestExtractPersons:
    def test_org_suffix_filtered(self):
        # "搜索团队" 以"团队"结尾，不应出现在人物列表中
        result = extract_persons("参会人：搜索团队")
        assert "搜索团队" not in result

    def test_returns_list(self):
        result = extract_persons("负责人：张三")
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────
# is_high_value_title
# ─────────────────────────────────────────────────────────────

class TestIsHighValueTitle:
    def test_empty_string_false(self):
        assert is_high_value_title("", "domain") is False

    def test_single_char_false(self):
        assert is_high_value_title("搜", "domain") is False

    def test_long_domain_title_true(self):
        # 9字符标题，超过domain的5字符门槛
        assert is_high_value_title("搜索引擎优化架构系统", "domain") is True

    def test_project_long_title_true(self):
        assert is_high_value_title("搜索质量提升大项目", "project") is True

    def test_project_short_title_false(self):
        # 2字符，project需要>=4
        assert is_high_value_title("搜索", "project") is False

    def test_structural_title_false(self):
        # "结论" 在 STRUCTURAL_TITLES 中
        assert is_high_value_title("结论", "domain") is False


# ─────────────────────────────────────────────────────────────
# is_high_value_term
# ─────────────────────────────────────────────────────────────

class TestIsHighValueTerm:
    def test_empty_false(self):
        assert is_high_value_term("") is False

    def test_single_char_false(self):
        assert is_high_value_term("A") is False

    def test_valid_term_true(self):
        assert is_high_value_term("检索召回率") is True


# ─────────────────────────────────────────────────────────────
# path_weight
# ─────────────────────────────────────────────────────────────

class TestPathWeight:
    def test_unknown_path_returns_1(self):
        assert path_weight("unknown/file.md") == 1

    def test_known_path_higher_weight(self):
        # 讨论思考 = 4, 会议纪要 = 3
        assert path_weight("04-讨论思考/some.md") > 1
        assert path_weight("05-会议纪要/meeting.md") > 1


# ─────────────────────────────────────────────────────────────
# normalized_key
# ─────────────────────────────────────────────────────────────

class TestNormalizedKey:
    def test_brackets_removed(self):
        key = normalized_key("搜索（引擎）")
        assert "（" not in key
        assert "）" not in key

    def test_english_lowercased(self):
        assert normalized_key("SearchEngine") == "searchengine"

    def test_chinese_preserved(self):
        assert "搜索" in normalized_key("搜索引擎")


# ─────────────────────────────────────────────────────────────
# merge_paths
# ─────────────────────────────────────────────────────────────

class TestMergePaths:
    def test_no_duplicates(self):
        result = merge_paths(["a.md", "b.md"], ["b.md", "c.md"])
        assert result.count("b.md") == 1

    def test_max_three(self):
        result = merge_paths(["a.md", "b.md", "c.md"], ["d.md", "e.md"])
        assert len(result) <= 3

    def test_order_preserved(self):
        result = merge_paths(["a.md"], ["b.md", "c.md"])
        assert result[0] == "a.md"


# ─────────────────────────────────────────────────────────────
# should_merge
# ─────────────────────────────────────────────────────────────

class TestShouldMerge:
    def test_same_key_different_types(self):
        a = _make_item("BM25", "domain")
        b = _make_item("BM25", "person")
        assert should_merge(a, b) is True

    def test_same_type_same_key(self):
        a = _make_item("搜索引擎", "domain")
        b = _make_item("搜索引擎", "domain")
        assert should_merge(a, b) is True

    def test_different_type_different_key(self):
        a = _make_item("搜索引擎", "domain")
        b = _make_item("召回算法", "person")
        assert should_merge(a, b) is False

    def test_project_prefix_contains(self):
        a = _make_item("搜索项目", "project")
        b = _make_item("搜索项目改进", "project")
        assert should_merge(a, b) is True


# ─────────────────────────────────────────────────────────────
# merge_candidates
# ─────────────────────────────────────────────────────────────

class TestMergeCandidates:
    def test_scores_added(self):
        a = _make_item("BM25", "domain", score=5)
        b = _make_item("BM25", "person", score=3)
        merged = merge_candidates(a, b)
        assert merged.score == 8

    def test_different_types_higher_priority_wins(self):
        # domain priority=2 > person priority=1
        domain = _make_item("BM25", "domain", score=5, evidence=3)
        person = _make_item("BM25", "person", score=3, evidence=2)
        merged = merge_candidates(domain, person)
        assert merged.page_type == "domain"

    def test_same_type_merge_scores(self):
        a = _make_item("搜索引擎", "domain", score=6, evidence=4)
        b = _make_item("搜索引擎", "domain", score=4, evidence=3)
        merged = merge_candidates(a, b)
        assert merged.score == 10
        assert merged.evidence_count == 7


# ─────────────────────────────────────────────────────────────
# is_wiki_stale
# ─────────────────────────────────────────────────────────────

class TestIsWikiStale:
    def test_nonexistent_file_is_stale(self):
        assert is_wiki_stale(Path("/nonexistent/file.md")) is True

    def test_old_page_is_stale(self, tmp_path):
        # 页面无 updated 字段 -> stale
        p = tmp_path / "old.md"
        p.write_text("---\ntitle: 旧页面\n---\n内容", encoding="utf-8")
        assert is_wiki_stale(p) is True

    def test_recent_page_not_stale(self, tmp_path):
        # 昨天生成的页面不陈腐
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        p = tmp_path / "recent.md"
        p.write_text(
            f"---\ntitle: 最新页面\nupdated: {yesterday}\n---\n内容",
            encoding="utf-8"
        )
        assert is_wiki_stale(p) is False


# ─────────────────────────────────────────────────────────────
# parse_wiki_generated_at
# ─────────────────────────────────────────────────────────────

class TestParseWikiGeneratedAt:
    def test_valid_datetime(self, tmp_path):
        p = tmp_path / "page.md"
        p.write_text(
            "---\ntitle: 测试\nupdated: 2026-01-15T10:30:00\n---\n内容",
            encoding="utf-8"
        )
        result = parse_wiki_generated_at(str(p))
        assert result is not None
        assert isinstance(result, datetime)
        assert result.year == 2026

    def test_invalid_datetime_returns_none(self, tmp_path):
        p = tmp_path / "bad_date.md"
        p.write_text("---\ntitle: 测试\nupdated: not-a-date\n---\n内容", encoding="utf-8")
        assert parse_wiki_generated_at(str(p)) is None

    def test_no_updated_field_returns_none(self, tmp_path):
        p = tmp_path / "no_date.md"
        p.write_text("---\ntitle: 测试\n---\n内容", encoding="utf-8")
        assert parse_wiki_generated_at(str(p)) is None

    def test_nonexistent_file_returns_none(self):
        assert parse_wiki_generated_at("/nonexistent/path.md") is None
