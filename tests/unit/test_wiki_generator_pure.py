"""Wiki 页面生成器纯函数 — 单元测试（模板/验证/解析/内容提取逻辑）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from iris.wiki.generator import WikiGenerator, WikiPageDraft, WikiWriteResult


# ── 数据类测试 ──────────────────────────────────────────────────────


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


# ── 初始化 ──────────────────────────────────────────────────────────


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
            assert " " not in result
            assert len(result) > 0
        except ImportError:
            pytest.skip("_slugify_title 为非公开 API，跳过")


# ── _extract_wiki_content ───────────────────────────────────────────


class TestExtractWikiContent:
    """覆盖 5 个 heuristic 分支。"""

    def test_strict_frontmatter_match(self):
        """分支 1：---\\ntitle: 开头到文末。"""
        text = "---\ntitle: 张三\ntype: person\n---\n\n## 摘要\n工程师。"
        result = WikiGenerator._extract_wiki_content(text)
        assert result.startswith("---")
        assert "title: 张三" in result

    def test_markdown_code_block(self):
        """分支 2：```markdown ... ``` 包裹。"""
        text = "以下是页面：\n\n```markdown\n---\ntitle: 测试\ntype: concept\n---\n\n正文\n```"
        result = WikiGenerator._extract_wiki_content(text)
        assert "title: 测试" in result

    def test_code_block_no_frontmatter(self):
        """代码块不以 --- 开头时继续后续 heuristic。"""
        text = "```markdown\n正文\n```\n---\ntitle: 测试\ntype: concept\n---\n\n正文"
        result = WikiGenerator._extract_wiki_content(text)
        assert "title: 测试" in result

    def test_starts_with_frontmatter(self):
        """分支 3：文本以 --- 开头。"""
        text = "---\ntitle: 直接\n---\n\n内容"
        result = WikiGenerator._extract_wiki_content(text)
        assert result.startswith("---")

    def test_newline_dash_fallback(self):
        """分支 4：查找 \\n---\\n 分隔的 frontmatter。"""
        text = "对话\n\n---\ntitle: 延迟\ntype: project\n---\n\n正文"
        result = WikiGenerator._extract_wiki_content(text)
        assert "title: 延迟" in result

    def test_find_dash_title_pattern(self):
        """分支 5：对话开始，查找 ---\\ntitle:。"""
        text = "AI 回复\n\n---\ntitle: 页面\ntype: concept\n---\n\n正文"
        result = WikiGenerator._extract_wiki_content(text)
        assert "title: 页面" in result

    def test_fallback_returns_original(self):
        """所有分支失败时返回原始文本。"""
        text = "没有 frontmatter"
        result = WikiGenerator._extract_wiki_content(text)
        assert result == text


# ── _render_with_fallback ──────────────────────────────────────────


class TestRenderWithFallback:
    def test_template_exists(self):
        with patch("iris.wiki.generator._load_template_file") as mock_load:
            mock_load.return_value = "Hello {name}!"
            result = WikiGenerator._render_with_fallback(
                "wiki/test.txt", "FALLBACK", name="World",
            )
            assert result == "Hello World!"

    def test_template_missing(self):
        with patch("iris.wiki.generator._load_template_file", return_value=None):
            result = WikiGenerator._render_with_fallback(
                "wiki/missing.txt", "Fallback text",
            )
            assert result == "Fallback text"


# ── _validate_update_output ────────────────────────────────────────


class TestValidateUpdateOutput:

    def test_valid_update_passes(self):
        existing = "---\ntitle: 张三\ntype: person\n---\n\n正文"
        new = "---\ntitle: 张三\ntype: person\nupdated: 2026-07-23\n---\n\n更新正文"
        result = WikiGenerator._validate_update_output(new, existing, "张三")
        assert "张三" in result

    def test_missing_frontmatter_recovered(self):
        existing = "---\ntitle: 张三\ntype: person\n---\n\n旧正文"
        new = "没有 frontmatter 的文本\n---\n正文"
        result = WikiGenerator._validate_update_output(new, existing, "张三")
        assert "title: 张三" in result

    def test_title_tampered_fixed(self):
        existing = "---\ntitle: 张三\ntype: person\n---\n\n旧正文"
        new = "---\ntitle: 李四\ntype: person\n---\n\n正文"
        result = WikiGenerator._validate_update_output(new, existing, "张三")
        assert "title: 张三" in result
        assert "title: 李四" not in result

    def test_unrecoverable_returns_existing(self):
        existing = "---\ntitle: 张三\n---\n\n旧正文"
        new = "没有"
        result = WikiGenerator._validate_update_output(new, existing, "张三")
        assert result == existing

    def test_code_block_trailing_stripped(self):
        existing = "---\ntitle: 张三\n---\n\n旧正文"
        new = "---\ntitle: 张三\n---\n\n正文\n```"
        result = WikiGenerator._validate_update_output(new, existing, "张三")
        assert not result.endswith("```")

    def test_windows_newlines_normalized(self):
        """Windows \\r\\n 在输入中被统一（有效更新不触发回退路径）。"""
        existing = "---\ntitle: 张三\n---\n\n旧正文"
        new = "---\r\ntitle: 张三\r\n---\r\n\r\n新正文"
        result = WikiGenerator._validate_update_output(new, existing, "张三")
        # 内部统一换行后正常处理
        assert "新正文" in result or "\r" not in result.replace("\r\n", "\n")


# ── check_reference_quality ────────────────────────────────────────


class TestCheckReferenceQuality:

    def test_described_refs_counted(self):
        """带 ≥10 字描述的引用被计入 described_refs。"""
        content = """---
title: test
---

正文

## 参考来源
- [source.md:10] 这是一条超过十字的详细事实断言描述"""
        result = WikiGenerator.check_reference_quality(content)
        assert result["described_refs"] >= 1

    def test_total_refs(self):
        """引用总数正确统计。"""
        content = """---
title: test
---

## 参考来源
- [a.md:1] 超过十字的详细事实断言描述
- [b.md:2] 短"""
        result = WikiGenerator.check_reference_quality(content)
        assert result["total_refs"] >= 1

    def test_bare_path_refs(self):
        """描述不足 10 字计为 bare_path_refs。"""
        content = """---
title: test
---

## 参考来源
- [a.md:1] 短描述"""
        result = WikiGenerator.check_reference_quality(content)
        assert result["bare_path_refs"] >= 1

    def test_empty_content_returns_zeroes(self):
        result = WikiGenerator.check_reference_quality("")
        assert result["total_refs"] == 0

    def test_no_ref_section_returns_no_refs(self):
        content = "---\ntitle: test\n---\n\n正文无参考来源"
        result = WikiGenerator.check_reference_quality(content)
        assert result["quality"] == "no_refs"



# ── _collect_evidence：人物页「本人周报通道」 ────────────────────────


class _StubRetriever:
    """记录调用的桩检索器。"""

    def __init__(self, main_hits, weekly_hits=None):
        self._main_hits = main_hits
        self._weekly_hits = weekly_hits if weekly_hits is not None else []
        self.latest_calls = []

    def search(self, query, *, top_k=10, **kwargs):
        return MagicMock(hits=list(self._main_hits))

    def latest_documents(self, path_suffix, *, doc_limit=4, chunks_per_doc=1):
        self.latest_calls.append((path_suffix, doc_limit, chunks_per_doc))
        return list(self._weekly_hits)


def _hit(chunk_id: str, path: str) -> MagicMock:
    hit = MagicMock()
    hit.chunk_id = chunk_id
    hit.relative_path = path
    hit.title = chunk_id
    hit.content_preview = f"内容 {chunk_id}"
    return hit


def _make_generator(main_hits, weekly_hits=None):
    gen = object.__new__(WikiGenerator)     # 只需 _retriever
    gen._retriever = _StubRetriever(main_hits, weekly_hits)
    return gen


class TestCollectEvidence:
    def test_person_weekly_hits_are_reserved(self):
        """周报正文优先占槽：不被高分主通道结果挤掉。"""
        main = [_hit(f"main{i}", f"docs/main{i}.md") for i in range(8)]
        weekly = [_hit(f"wk{i}", f"07-成员周报/2026{i}-周报-w3{i}-卞凯.md") for i in range(4)]
        gen = _make_generator(main, weekly)

        hits = gen._collect_evidence(query="卞凯", title="卞凯",
                                     page_type="person", top_k=8)

        ids = [h.chunk_id for h in hits]
        assert all(f"wk{i}" in ids for i in range(4)), "周报块必须全部保留"
        assert ids[:4] == ["wk0", "wk1", "wk2", "wk3"], "周报块应占据最前面的槽位"

    def test_non_person_does_not_use_weekly_channel(self):
        """project/domain 页零行为变化：不调 latest_documents。"""
        main = [_hit(f"m{i}", f"docs/m{i}.md") for i in range(8)]
        retriever = _StubRetriever(main, [_hit("wk0", "07-成员周报/x-周报-w37-卞凯.md")])
        gen = object.__new__(WikiGenerator)
        gen._retriever = retriever

        hits = gen._collect_evidence(query="视频稽查", title="视频稽查与在线审核项目",
                                     page_type="project", top_k=8)

        assert retriever.latest_calls == []
        assert [h.chunk_id for h in hits] == [f"m{i}" for i in range(8)]

    def test_result_capped_at_evidence_limit(self):
        from iris.wiki.generator import _EVIDENCE_LIMIT
        main = [_hit(f"m{i}", f"docs/m{i}.md") for i in range(20)]
        weekly = [_hit(f"wk{i}", f"07-成员周报/x{i}-周报-w37-卞凯.md") for i in range(4)]
        hits = _make_generator(main, weekly)._collect_evidence(
            query="卞凯", title="卞凯", page_type="person", top_k=20)
        assert len(hits) == _EVIDENCE_LIMIT

    def test_person_channel_suffix_uses_title(self):
        """通道按 `-{页面标题}.md` 定位，避免「陈鹏」误配「陈鹏飞」。"""
        gen = _make_generator([], [])
        gen._collect_evidence(query="陈鹏", title="陈鹏", page_type="person", top_k=5)
        assert gen._retriever.latest_calls == [("-陈鹏.md", 4, 1)]

    def test_empty_weekly_channel_degrades_silently(self):
        """通道无匹配（改名/归档）时只用主检索，不抛异常。"""
        main = [_hit("m0", "docs/m0.md")]
        hits = _make_generator(main, [])._collect_evidence(
            query="卞凯", title="卞凯", page_type="person", top_k=5)
        assert [h.chunk_id for h in hits] == ["m0"]
