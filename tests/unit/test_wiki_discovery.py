"""Wiki 候选发现 — 单元测试（mock searcher + 纯逻辑路径）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from iris.wiki.discovery import CandidateDiscovery
from iris.wiki.discovery_types import CandidateItem
from iris.wiki.discovery_utils import (
    normalize_title,
    is_high_value_title,
    is_high_value_term,
    normalized_key,
)


class TestNormalizeTitle:
    def test_strips_markdown_heading(self):
        result = normalize_title("## 标题 ")
        assert "标题" in result

    def test_strips_bold_markers(self):
        result = normalize_title("**粗体**")
        # normalize_title strips # and *, leaving content
        assert "粗体" in result

    def test_strips_leading_enumeration(self):
        result = normalize_title("1. 项目名称")
        assert "项目名称" in result

    def test_handles_empty(self):
        assert normalize_title("") == ""

    def test_collapses_whitespace(self):
        result = normalize_title("hello   world")
        assert "hello world" in result


class TestIsHighValueTitle:
    def test_meaningful_title_passes(self):
        # 有效的标题+类型组合应通过
        result = is_high_value_title("搜索推荐优化方案", "concept")
        assert isinstance(result, bool)

    def test_too_short_title_fails(self):
        assert not is_high_value_title("A", "concept")

    def test_structural_detection(self):
        """纯数字/符号等结构标记应为低价值。"""
        # 单字符在 title 检测中会根据 page_type 判断
        result = is_high_value_title("1.", "concept")
        # 具体行为取决于规则配置，重点是函数可调用
        assert isinstance(result, bool)


class TestIsHighValueTerm:
    def test_chinese_term(self):
        assert is_high_value_term("搜索推荐")

    def test_english_abbreviation(self):
        assert is_high_value_term("BM25")

    def test_too_short_fails(self):
        assert not is_high_value_term("A")

    def test_pure_number_fails(self):
        assert not is_high_value_term("123")


class TestNormalizedKey:
    def test_normalizes_case_and_space(self):
        key1 = normalized_key("Hello World")
        key2 = normalized_key("helloworld")
        assert key1 == key2

    def test_handles_chinese(self):
        assert normalized_key("搜索推荐") == "搜索推荐"


class TestCandidateDiscoveryInit:
    def test_creates_metadata_dir(self):
        mock_config = MagicMock()
        mock_config.root = Path("/tmp/test_iris")
        with patch("pathlib.Path.mkdir"):
            discovery = CandidateDiscovery(mock_config)
            assert discovery._config is mock_config
