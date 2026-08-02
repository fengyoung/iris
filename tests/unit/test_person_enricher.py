"""人物信息丰富器 — 单元测试（mock FeishuClient）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest
from iris.wiki.person_enricher import (
    PersonEnricher,
    EnrichResult,
    EnrichSummary,
    _BATCH_SIZE,
)


class TestEnrichResult:
    def test_default_values(self):
        r = EnrichResult(name="张三", status="updated")
        assert r.name == "张三"
        assert r.department == ""
        assert r.email == ""

    def test_full_fields(self):
        r = EnrichResult(
            name="张三", status="updated",
            department="数据部门", email="zhangsan@example.com",
        )
        assert r.department == "数据部门"


class TestEnrichSummary:
    def test_default_counts_are_zero(self):
        s = EnrichSummary()
        assert s.total == 0
        assert s.updated == 0

    def test_accumulates_details(self):
        s = EnrichSummary(total=2, updated=1, not_found=1)
        s.details.append(EnrichResult(name="张三", status="updated"))
        s.details.append(EnrichResult(name="李四", status="not_found"))
        assert len(s.details) == 2
        assert s.updated == 1


class TestPersonEnricherPure:
    """测试 PersonEnricher 中不依赖飞书 API 的纯逻辑。"""

    @patch("iris.wiki.person_enricher.FeishuClient")
    def test_init_creates_client(self, mock_feishu):
        mock_config = MagicMock()
        mock_config.wiki = {"wiki_root": str(Path("/tmp/test_wiki"))}
        enricher = PersonEnricher(mock_config)
        assert enricher._person_dir.name == "04-人物"

    @patch("iris.wiki.person_enricher.FeishuClient")
    def test_enrich_skips_when_person_dir_missing(self, mock_feishu):
        mock_config = MagicMock()
        mock_config.wiki = {"wiki_root": str(Path("/tmp/nonexistent"))}
        enricher = PersonEnricher(mock_config)
        with patch("pathlib.Path.exists", return_value=False):
            with patch("pathlib.Path.is_dir", return_value=False):
                result = enricher.enrich(dry_run=True)
                assert result.total == 0

    @patch("iris.wiki.person_enricher.FeishuClient")
    def test_batch_size_constant(self, mock_feishu):
        assert _BATCH_SIZE == 10


class TestFrontmatterParsing:
    """测试前置元数据解析（通过 person_enricher 依赖的 searcher.parse_frontmatter）。"""

    def test_parse_basic_frontmatter(self):
        from iris.wiki.searcher import parse_frontmatter
        content = "---\ntitle: 张三\ndepartment: 数据部门\n---\n正文内容"
        fm, body = parse_frontmatter(content)
        assert fm["title"] == "张三"
        assert fm["department"] == "数据部门"
        assert "正文内容" in body

    def test_parse_empty_frontmatter(self):
        from iris.wiki.searcher import parse_frontmatter
        content = "没有前置元数据的纯文本"
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_parse_empty_frontmatter_separators(self):
        from iris.wiki.searcher import parse_frontmatter
        content = "---\n---\n正文"
        fm, body = parse_frontmatter(content)
        assert "正文" in body
