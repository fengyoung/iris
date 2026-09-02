"""wiki/searcher.py 纯函数单元测试。"""

from __future__ import annotations



from iris.wiki.searcher import (
    parse_frontmatter,
    get_frontmatter_field,
    _infer_title_from_filename,
    _read_wiki_page,
    _score_page,
    WikiHit,
)
from iris.utils.tokenization import tokenize


# ─────────────────────────────────────────────────────────────
# parse_frontmatter
# ─────────────────────────────────────────────────────────────

class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        text = "---\ntitle: 搜索引擎\ntype: domain\nstatus: published\n---\n正文"
        fields, body = parse_frontmatter(text)
        assert fields["title"] == "搜索引擎"
        assert fields["type"] == "domain"
        assert fields["status"] == "published"
        assert "正文" in body

    def test_no_frontmatter(self):
        text = "# 标题\n正文内容"
        fields, body = parse_frontmatter(text)
        assert fields == {}
        assert "正文内容" in body

    def test_empty_frontmatter(self):
        text = "---\n---\n正文"
        fields, body = parse_frontmatter(text)
        assert fields == {}
        assert "正文" in body

    def test_crlf_line_endings(self):
        text = "---\r\ntitle: 标题\r\ntype: domain\r\n---\r\n正文"
        fields, body = parse_frontmatter(text)
        assert fields.get("title") == "标题"
        assert fields.get("type") == "domain"

    def test_double_quoted_value(self):
        text = '---\ntitle: "带引号标题"\ntype: domain\n---\n正文'
        fields, _ = parse_frontmatter(text)
        assert fields["title"] == "带引号标题"

    def test_single_quoted_value(self):
        text = "---\ntitle: '单引号标题'\ntype: domain\n---\n正文"
        fields, _ = parse_frontmatter(text)
        assert fields["title"] == "单引号标题"


# ─────────────────────────────────────────────────────────────
# get_frontmatter_field
# ─────────────────────────────────────────────────────────────

class TestGetFrontmatterField:
    def test_existing_field(self):
        text = "---\ntitle: 测试标题\ntype: domain\n---\n内容"
        assert get_frontmatter_field(text, "title") == "测试标题"

    def test_missing_field_returns_empty(self):
        text = "---\ntitle: 测试\n---\n内容"
        assert get_frontmatter_field(text, "nonexistent") == ""


# ─────────────────────────────────────────────────────────────
# _infer_title_from_filename
# ─────────────────────────────────────────────────────────────

class TestInferTitleFromFilename:
    def test_person_prefix(self, tmp_path):
        p = tmp_path / "人物-张三.md"
        p.touch()
        assert _infer_title_from_filename(p) == "张三"

    def test_domain_prefix(self, tmp_path):
        p = tmp_path / "领域-搜索引擎.md"
        p.touch()
        assert _infer_title_from_filename(p) == "搜索引擎"

    def test_no_prefix(self, tmp_path):
        p = tmp_path / "未知文件名.md"
        p.touch()
        assert _infer_title_from_filename(p) == "未知文件名"


# ─────────────────────────────────────────────────────────────
# _read_wiki_page
# ─────────────────────────────────────────────────────────────

class TestReadWikiPage:
    def test_valid_page_with_summary_section(self, tmp_path):
        p = tmp_path / "领域-搜索.md"
        p.write_text(
            "---\ntitle: 搜索\ntype: domain\nstatus: published\n---\n"
            "## 摘要\n这是摘要内容。\n## 详情\n详细描述。",
            encoding="utf-8"
        )
        title, ptype, status, summary, body = _read_wiki_page(p)
        assert title == "搜索"
        assert ptype == "domain"
        assert status == "published"
        assert summary == "这是摘要内容。"

    def test_no_frontmatter_uses_defaults(self, tmp_path):
        p = tmp_path / "人物-李四.md"
        p.write_text("# 李四\n这是关于李四的介绍。", encoding="utf-8")
        title, ptype, status, summary, body = _read_wiki_page(p)
        assert title == "李四"   # 从文件名推断
        assert ptype == "domain"  # 默认
        assert status == "draft"  # 默认

    def test_first_non_heading_line_as_summary(self, tmp_path):
        p = tmp_path / "概念-算法.md"
        p.write_text(
            "---\ntitle: 算法\ntype: concept\nstatus: draft\n---\n"
            "这是第一段非标题文字，应作为摘要。",
            encoding="utf-8"
        )
        title, ptype, status, summary, body = _read_wiki_page(p)
        assert "第一段非标题文字" in summary

    def test_unreadable_file_returns_safe_defaults(self, tmp_path):
        p = tmp_path / "人物-王五.md"
        # 写入非 UTF-8 字节，使 UTF-8 读取失败
        p.write_bytes(b"\xff\xfe\x00\x01invalid utf-8 bytes")
        title, ptype, status, summary, body = _read_wiki_page(p)
        # 应返回安全默认值，不应抛异常
        assert isinstance(title, str)
        assert ptype == "domain"
        assert status == "draft"
        assert summary == ""
        assert body == ""


# ─────────────────────────────────────────────────────────────
# _score_page
# ─────────────────────────────────────────────────────────────

class TestScorePage:
    def test_empty_query_returns_zero(self):
        score, matched = _score_page("", [], "标题", "摘要", "正文")
        assert score == 0.0
        assert matched == []

    def test_exact_title_match_high_score(self):
        query = "搜索引擎"
        tokens = tokenize(query)
        score, matched = _score_page(query, tokens, "搜索引擎优化", "摘要", "正文")
        assert score > 5.0

    def test_summary_match(self):
        query = "召回率"
        tokens = tokenize(query)
        score, matched = _score_page(query, tokens, "不相关标题", "提高召回率是目标", "")
        assert score > 0

    def test_body_match(self):
        query = "增量构建"
        tokens = tokenize(query)
        score, matched = _score_page(query, tokens, "无关标题", "无关摘要", "增量构建技术细节说明")
        assert score > 0

    def test_no_match_returns_zero(self):
        query = "完全不存在的词汇xyz"
        tokens = tokenize(query)
        score, matched = _score_page(query, tokens, "标题", "摘要", "正文")
        assert score == 0.0

    def test_matched_terms_contains_hits(self):
        query = "搜索 召回"
        tokens = tokenize(query)
        _, matched = _score_page(query, tokens, "搜索系统召回", "摘要", "")
        assert len(matched) > 0


# ─────────────────────────────────────────────────────────────
# WikiHit 数据类
# ─────────────────────────────────────────────────────────────

class TestWikiHit:
    def test_default_source_and_status(self):
        hit = WikiHit(
            title="搜索引擎",
            relative_path="01-领域/领域-搜索引擎.md",
            page_type="domain",
            summary="搜索引擎技术",
            score=8.5,
        )
        assert hit.source == "wiki"
        assert hit.status == "draft"
        assert hit.matched_terms is None

    def test_custom_status(self):
        hit = WikiHit(
            title="测试",
            relative_path="test.md",
            page_type="concept",
            summary="",
            score=1.0,
            status="published",
        )
        assert hit.status == "published"
