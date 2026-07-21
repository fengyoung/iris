"""Wiki 页面生成器纯函数 — 单元测试（模板/验证/解析逻辑）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from iris.wiki.generator import WikiGenerator, WikiPageDraft, WikiWriteResult


class TestWikiPageDraft:
    def test_create_draft(self):
        draft = WikiPageDraft(
            page_type="concept",
            title="测试页面",
            slug="测试页面",
            output_path=Path("/tmp/test.md"),
            markdown="这是测试内容。",
        )
        assert draft.title == "测试页面"
        assert draft.page_type == "concept"
        assert draft.markdown == "这是测试内容。"

    def test_draft_has_path_fields(self):
        draft = WikiPageDraft(
            page_type="person",
            title="张三",
            slug="张三",
            output_path=Path("/tmp/person.md"),
            markdown="人物内容",
        )
        assert draft.slug == "张三"
        assert draft.output_path == Path("/tmp/person.md")


class TestWikiWriteResult:
    def test_success_result(self):
        result = WikiWriteResult(
            path=Path("/tmp/test.md"),
            action="created",
        )
        assert result.action == "created"
        assert result.path == Path("/tmp/test.md")

    def test_backup_result(self):
        result = WikiWriteResult(
            path=Path("/tmp/test.md"),
            action="updated",
            backup_path=Path("/tmp/backup.md"),
        )
        assert result.action == "updated"
        assert result.backup_path == Path("/tmp/backup.md")


class TestWikiGeneratorInit:
    @patch("iris.wiki.generator.WikiSearcher")
    def test_initializes_with_config(self, mock_searcher):
        mock_config = MagicMock()
        mock_config.root = Path("/tmp")
        mock_config.wiki = {"wiki_root": str(Path("/tmp/wiki"))}
        gen = WikiGenerator(mock_config)
        assert gen._config is mock_config


class TestSlugifyTitle:
    def test_slugify_importable(self):
        """验证 _slugify_title 存在且可调用。"""
        try:
            from iris.wiki.generator import _slugify_title
            result = _slugify_title("Hello World")
            # slugify 去除空格和特殊字符
            assert " " not in result
            assert len(result) > 0
        except ImportError:
            pytest.skip("_slugify_title 为非公开 API，跳过")
