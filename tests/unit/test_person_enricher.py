"""人物信息丰富器 — 单元测试（mock FeishuClient）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

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


class TestUpdatePagePreservesManualFields:
    """v3.28.1 回归：_update_page 新值为空时不得清空已有字段/覆盖首次备份。

    历史 bug：① 页面缺 department 但已有手工 email 时，飞书返回空 email
    会把已有值替换为空串（email 是人工排歧过的关键字段）；
    ② 二次 enrich 用当前内容覆盖首次备份，最原始的人工版本永久丢失。
    """

    def _make_enricher(self):
        enricher = object.__new__(PersonEnricher)
        return enricher

    def _make_page(self, tmp_path, fm_lines):
        page = tmp_path / "人物-张三.md"
        page.write_text("---\n" + "\n".join(fm_lines) + "\n---\n\n# 张三\n正文。\n",
                        encoding="utf-8")
        return page

    def test_empty_email_keeps_existing(self, tmp_path):
        enricher = self._make_enricher()
        page = self._make_page(tmp_path, [
            "title: 张三", "email: zhangsan@example.com",
        ])

        # 飞书只返回 department，email 为空
        enricher._update_page(page, "张三", "数据部门", "")

        text = page.read_text(encoding="utf-8")
        assert "email: zhangsan@example.com" in text, "已有 email 不得被清空（回归核心断言）"
        assert "department: 数据部门" in text

    def test_empty_department_keeps_existing(self, tmp_path):
        enricher = self._make_enricher()
        page = self._make_page(tmp_path, [
            "title: 张三", "department: 老部门", "email: old@example.com",
        ])

        enricher._update_page(page, "张三", "", "new@example.com")

        text = page.read_text(encoding="utf-8")
        assert "department: 老部门" in text
        assert "email: new@example.com" in text

    def test_backup_not_overwritten_on_second_run(self, tmp_path):
        enricher = self._make_enricher()
        page = self._make_page(tmp_path, ["title: 张三", "email: manual@example.com"])
        original = page.read_text(encoding="utf-8")

        enricher._update_page(page, "张三", "部门A", "a@example.com")
        bak = tmp_path / "人物-张三.bak.enrich"
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == original, "首次备份 = 原始版本"

        # 二次 enrich：备份不得被第一次 enrich 后的内容覆盖
        enricher._update_page(page, "张三", "部门B", "b@example.com")
        assert bak.read_text(encoding="utf-8") == original, \
            "二次运行不得覆盖首次备份（回归核心断言）"
        assert "department: 部门B" in page.read_text(encoding="utf-8")
