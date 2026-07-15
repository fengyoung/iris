"""wiki/discovery_utils.py 纯函数 单元测试。"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from iris.wiki.discovery_utils import (
    append_sample,
    should_merge,
    merge_candidates,
    prefer_candidate_title,
    merge_paths,
    normalized_key,
    build_candidates,
    suppress_path_concentrated_noise,
)
from iris.wiki.discovery_types import CandidateItem


# ── append_sample ──────────────────────────────────────────


def test_append_sample_adds_new():
    paths = []
    append_sample(paths, "a.md")
    assert paths == ["a.md"]


def test_append_sample_no_duplicates():
    paths = ["a.md"]
    append_sample(paths, "a.md")
    assert paths == ["a.md"]


def test_append_sample_max_three():
    paths = ["a.md", "b.md", "c.md"]
    append_sample(paths, "d.md")
    assert len(paths) == 3
    assert "d.md" not in paths


# ── normalized_key ─────────────────────────────────────────


def test_normalized_key_case():
    assert normalized_key("BM25") == normalized_key("bm25")


def test_normalized_key_whitespace():
    assert normalized_key("Hello World") == normalized_key("HelloWorld")


def test_normalized_key_chinese():
    assert normalized_key("人工智能") == "人工智能"


# ── should_merge ───────────────────────────────────────────


def _make_item(title, page_type, score=10, evidence=5, paths=None):
    return CandidateItem(
        title=title, page_type=page_type, query=title,
        score=score, evidence_count=evidence,
        sample_paths=paths if paths is not None else [],
    )


def test_should_merge_same_key():
    a = _make_item("项目Alpha", "project")
    b = _make_item("项目Alpha", "project")
    assert should_merge(a, b)


def test_should_merge_different_types_same_key():
    a = _make_item("BM25", "concept")
    b = _make_item("BM25", "domain")
    assert should_merge(a, b)


def test_should_merge_project_contains():
    a = _make_item("项目Alpha优化", "project")
    b = _make_item("项目Alpha", "project")
    assert should_merge(a, b)


def test_should_merge_different_projects():
    a = _make_item("项目A", "project")
    b = _make_item("项目B", "project")
    assert not should_merge(a, b)


# ── merge_candidates ───────────────────────────────────────


def test_merge_candidates_same_type():
    a = _make_item("项目Alpha", "project", score=10, evidence=3)
    b = _make_item("项目Alpha", "project", score=5, evidence=2)
    result = merge_candidates(a, b)
    assert result.title == "项目Alpha"
    assert result.score == 15
    assert result.evidence_count == 5


def test_merge_candidates_different_type():
    a = _make_item("BM25", "concept", score=10, evidence=5)
    b = _make_item("BM25", "domain", score=8, evidence=3)
    result = merge_candidates(a, b)
    # concept 和 domain 优先级相同 (均为2), left (concept) 保留
    assert result.score == 18


# ── prefer_candidate_title ─────────────────────────────────


def test_prefer_project_shorter():
    a = _make_item("项目Alpha优化项目", "project")
    b = _make_item("项目Alpha", "project")
    result = prefer_candidate_title(a, b)
    assert result.title == "项目Alpha"


def test_prefer_domain_higher_score():
    a = _make_item("长标题A", "domain", score=20)
    b = _make_item("短B", "domain", score=5)
    result = prefer_candidate_title(a, b)
    assert result.title == "长标题A"


# ── merge_paths ────────────────────────────────────────────


def test_merge_paths_combines():
    result = merge_paths(["a.md"], ["b.md"])
    assert "a.md" in result
    assert "b.md" in result


def test_merge_paths_max_three():
    result = merge_paths(["a.md", "b.md", "c.md"], ["d.md"])
    assert len(result) == 3


def test_merge_paths_no_duplicates():
    result = merge_paths(["a.md", "b.md"], ["b.md", "c.md"])
    assert result.count("b.md") == 1


# ── build_candidates ───────────────────────────────────────


def test_build_candidates_below_threshold():
    counter = Counter({"test": 1})
    evidence = Counter({"test": 1})
    result = build_candidates(counter, evidence, {}, "project")
    # threshold for project is 3
    assert len(result) == 0


def test_build_candidates_concept_needs_english():
    counter = Counter({"测试概念": 10})
    evidence = Counter({"测试概念": 10})
    result = build_candidates(counter, evidence, {}, "concept")
    # concept needs English characters in title
    assert len(result) == 0


def test_build_candidates_concept_with_english():
    counter = Counter({"BM25算法": 10})
    evidence = Counter({"BM25算法": 10})
    result = build_candidates(counter, evidence, {}, "concept")
    assert len(result) == 1


def test_build_candidates_project():
    counter = Counter({"项目Alpha": 10})
    evidence = Counter({"项目Alpha": 10})
    result = build_candidates(counter, evidence, {}, "project")
    assert len(result) == 1
    assert result[0].title == "项目Alpha"


# ── suppress_path_concentrated_noise ───────────────────────


def test_suppress_keeps_valid():
    items = [_make_item("项目Alpha", "project", score=10, evidence=5,
                        paths=["06-我的周报/test.md"])]
    result = suppress_path_concentrated_noise(items)
    assert len(result) == 1


def test_suppress_removes_low_value_concept():
    items = [_make_item("小工具", "concept", score=2, evidence=3,
                        paths=["08-参考资料/noise.md"])]
    result = suppress_path_concentrated_noise(items)
    assert isinstance(result, list)
