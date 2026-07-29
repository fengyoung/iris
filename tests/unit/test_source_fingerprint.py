"""Wiki source_fingerprint 源文档指纹机制单元测试。"""

from __future__ import annotations

from datetime import datetime, timedelta

from iris.wiki.discovery_utils import (
    inject_source_fingerprint,
    is_wiki_stale,
    parse_wiki_source_fingerprint,
    render_source_fingerprint,
    strip_source_fingerprint,
)


_PAGE_TEMPLATE = """---
title: 项目-测试
type: project
status: published
created: 2026-07-01
updated: {updated}
---

# 项目-测试

正文内容。

## 参考来源

- [文档](03-方案报告/2026-07/方案.md)
"""


def _make_page(tmp_path, *, updated=None, fingerprint=None):
    updated = updated or datetime.now().strftime("%Y-%m-%d")
    content = _PAGE_TEMPLATE.format(updated=updated)
    if fingerprint:
        content = inject_source_fingerprint(content, fingerprint)
    page = tmp_path / "项目-测试.md"
    page.write_text(content, encoding="utf-8")
    return page


# ─────────────────────────────────────────────────────────────
# render / strip / inject / parse
# ─────────────────────────────────────────────────────────────

class TestRenderSourceFingerprint:
    def test_empty_returns_empty(self):
        assert render_source_fingerprint({}) == ""

    def test_sorted_and_quoted(self):
        fp = {"b/2.md": "hash2", "a/1.md": "hash1"}
        rendered = render_source_fingerprint(fp)
        lines = rendered.splitlines()
        assert lines[0] == "source_fingerprint:"
        assert lines[1] == '  - "a/1.md@hash1"'
        assert lines[2] == '  - "b/2.md@hash2"'


class TestInjectAndParse:
    def test_inject_into_frontmatter(self, tmp_path):
        page = _make_page(tmp_path, fingerprint={"03-方案报告/方案.md": "abc123def456"})
        content = page.read_text(encoding="utf-8")
        # 指纹在 frontmatter 内（第二个 --- 之前）
        front = content.split("---")[1]
        assert "source_fingerprint:" in front
        assert '03-方案报告/方案.md@abc123def456' in front

    def test_inject_idempotent(self):
        content = _PAGE_TEMPLATE.format(updated="2026-07-29")
        once = inject_source_fingerprint(content, {"a.md": "h1"})
        twice = inject_source_fingerprint(once, {"a.md": "h2"})
        assert twice.count("source_fingerprint:") == 1
        assert "a.md@h2" in twice
        assert "a.md@h1" not in twice

    def test_inject_no_frontmatter_unchanged(self):
        content = "# 无 frontmatter 页面\n\n正文。\n"
        assert inject_source_fingerprint(content, {"a.md": "h1"}) == content

    def test_inject_empty_fingerprint_unchanged(self):
        content = _PAGE_TEMPLATE.format(updated="2026-07-29")
        assert inject_source_fingerprint(content, {}) == content

    def test_parse_roundtrip(self, tmp_path):
        fp = {"05-会议纪要/会议.md": "1a2b3c4d5e6f", "06-我的周报/周报.md": "f6e5d4c3b2a1"}
        page = _make_page(tmp_path, fingerprint=fp)
        assert parse_wiki_source_fingerprint(str(page)) == fp

    def test_parse_missing_file_returns_empty(self):
        assert parse_wiki_source_fingerprint("/nonexistent/page.md") == {}

    def test_parse_page_without_fingerprint(self, tmp_path):
        page = _make_page(tmp_path)
        assert parse_wiki_source_fingerprint(str(page)) == {}

    def test_strip_removes_block(self):
        content = _PAGE_TEMPLATE.format(updated="2026-07-29")
        injected = inject_source_fingerprint(content, {"a.md": "h1", "b.md": "h2"})
        stripped = strip_source_fingerprint(injected)
        assert "source_fingerprint" not in stripped
        assert "title: 项目-测试" in stripped


# ─────────────────────────────────────────────────────────────
# is_wiki_stale 指纹判定
# ─────────────────────────────────────────────────────────────

class TestIsWikiStaleFingerprint:
    def test_unchanged_sources_fresh(self, tmp_path):
        # 源文档 hash 未变 → 新鲜（即使 updated 很旧，不再按天数误判）
        old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        page = _make_page(tmp_path, updated=old_date, fingerprint={"a.md": "abc123def456"})
        hash_index = {"a.md": {"hash": "abc123def456789", "modified_at": "2026-07-01"}}
        assert is_wiki_stale(page, hash_index=hash_index) is False

    def test_changed_source_stale(self, tmp_path):
        page = _make_page(tmp_path, fingerprint={"a.md": "abc123def456"})
        hash_index = {"a.md": {"hash": "ffffffffffff000", "modified_at": "2026-07-29"}}
        assert is_wiki_stale(page, hash_index=hash_index) is True

    def test_deleted_source_stale(self, tmp_path):
        # 源文档从索引中消失（被删除）→ 过时
        page = _make_page(tmp_path, fingerprint={"gone.md": "abc123def456"})
        assert is_wiki_stale(page, hash_index={"other.md": {"hash": "x"}}) is True

    def test_any_of_multiple_changed_stale(self, tmp_path):
        page = _make_page(tmp_path, fingerprint={"a.md": "aaaa", "b.md": "bbbb"})
        hash_index = {
            "a.md": {"hash": "aaaa1111"},
            "b.md": {"hash": "changed"},
        }
        assert is_wiki_stale(page, hash_index=hash_index) is True

    def test_no_fingerprint_falls_back_to_days_old(self, tmp_path):
        # 旧页面无指纹：按天数兜底（90 天前 → 过时）
        old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        page = _make_page(tmp_path, updated=old_date)
        hash_index = {"a.md": {"hash": "abc"}}
        assert is_wiki_stale(page, hash_index=hash_index) is True

    def test_no_fingerprint_recent_fresh(self, tmp_path):
        page = _make_page(tmp_path)
        hash_index = {"a.md": {"hash": "abc"}}
        assert is_wiki_stale(page, hash_index=hash_index) is False

    def test_no_hash_index_falls_back_to_days(self, tmp_path):
        # 不提供 hash_index（旧调用方式）：行为与旧版一致
        old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        page = _make_page(tmp_path, updated=old_date, fingerprint={"a.md": "abc"})
        assert is_wiki_stale(page) is True
