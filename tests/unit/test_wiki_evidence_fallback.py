"""Wiki 增量更新的证据兜底与「无证据」早退 — 单元测试。

两个静默失效的护栏：
1. 词法检索对整串标题要求「全部词命中」，标题里只要有一个词在语料中不存在
   （实测：`干冰机压力表点检流程` 中的「压力表」在全部 chunk 里 0 命中），
   该页永远拿不到证据 → 更新恒为 no_changes → 指纹永不推进 → 每轮判过期。
2. 无证据时仍调 LLM，模型只会把页面原样吐回，白烧一次调用且零进展。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from iris.wiki.generator import WikiGenerator


def _bare_generator():
    """绕过 __init__（不加载向量索引/LLM），只保留被测属性。"""
    gen = WikiGenerator.__new__(WikiGenerator)
    gen._config = MagicMock()
    gen._retriever = MagicMock()
    gen._enhanced_retriever = None
    gen._wiki_searcher = None
    gen._logger = MagicMock()
    return gen


class TestEvidenceFallback:
    def test_fallback_used_when_lexical_zero_hits(self):
        gen = _bare_generator()
        gen._retriever.search.return_value = MagicMock(hits=[])
        fallback_hit = MagicMock(relative_path="a.md")
        fake = MagicMock()
        fake.search.return_value = MagicMock(hits=[fallback_hit])
        with patch("iris.retrieval.EnhancedRetriever", return_value=fake):
            hits = gen._collect_evidence(query="干冰机压力表点检流程",
                                         title="干冰机压力表点检流程",
                                         page_type="concept", top_k=4)
        assert hits == [fallback_hit]
        fake.search.assert_called_once()

    def test_fallback_not_used_when_lexical_has_hits(self):
        gen = _bare_generator()
        hit = MagicMock(relative_path="b.md")
        gen._retriever.search.return_value = MagicMock(hits=[hit])
        with patch("iris.retrieval.EnhancedRetriever") as ctor:
            hits = gen._collect_evidence(query="任意标题", title="任意标题",
                                         page_type="concept", top_k=4)
        assert hits == [hit]
        ctor.assert_not_called()

    def test_fallback_failure_degrades_to_empty(self):
        gen = _bare_generator()
        gen._retriever.search.return_value = MagicMock(hits=[])
        with patch("iris.retrieval.EnhancedRetriever", side_effect=RuntimeError("boom")):
            hits = gen._collect_evidence(query="任意标题", title="任意标题",
                                         page_type="concept", top_k=4)
        assert hits == []

    def test_fallback_keeps_person_weekly_channel_first(self):
        gen = _bare_generator()
        gen._retriever.search.return_value = MagicMock(hits=[])
        weekly = MagicMock(relative_path="07-成员周报/202610/20261016-周报-w42-张三.md")
        gen._retriever.latest_documents.return_value = [weekly]
        fake = MagicMock()
        fake.search.return_value = MagicMock(hits=[MagicMock(relative_path="other.md")])
        with patch("iris.retrieval.EnhancedRetriever", return_value=fake):
            hits = gen._collect_evidence(query="张三", title="张三",
                                         page_type="person", top_k=4)
        assert hits[0] is weekly

    def test_enhanced_retriever_reused_across_calls(self):
        gen = _bare_generator()
        gen._retriever.search.return_value = MagicMock(hits=[])
        fake = MagicMock()
        fake.search.return_value = MagicMock(hits=[MagicMock(relative_path="a.md")])
        with patch("iris.retrieval.EnhancedRetriever", return_value=fake) as ctor:
            gen._collect_evidence(query="q1", title="q1", page_type="concept", top_k=4)
            gen._collect_evidence(query="q2", title="q2", page_type="concept", top_k=4)
        assert ctor.call_count == 1  # 懒建一次，避免每页重建向量索引


class TestNoEvidenceSkipsLLM:
    def _page(self, tmp_path):
        page = tmp_path / "概念-干冰机压力表点检流程.md"
        page.write_text(
            "---\ntitle: 干冰机压力表点检流程\ntype: concept\nupdated: 2026-10-11\n---\n\n正文。\n",
            encoding="utf-8",
        )
        return page

    def test_returns_no_evidence_without_llm_call(self, tmp_path):
        gen = _bare_generator()
        gen._collect_evidence = MagicMock(return_value=[])
        gen._generate_incremental_update = MagicMock()
        page = self._page(tmp_path)
        result = gen._update_page_with_content(
            title="干冰机压力表点检流程", page_type="concept", path=page,
            existing_content=page.read_text(encoding="utf-8"), top_k=4,
        )
        assert result["status"] == "no_evidence"
        gen._generate_incremental_update.assert_not_called()

    def test_evidence_present_still_calls_llm(self, tmp_path):
        # 有证据时必须照常走 LLM（不要被早退逻辑误伤）
        gen = _bare_generator()
        gen._collect_evidence = MagicMock(return_value=[MagicMock(relative_path="a.md")])
        gen._generate_incremental_update = MagicMock(return_value=None)
        page = self._page(tmp_path)
        result = gen._update_page_with_content(
            title="干冰机压力表点检流程", page_type="concept", path=page,
            existing_content=page.read_text(encoding="utf-8"), top_k=4,
        )
        assert result["status"] == "error"  # LLM 返回 None
        gen._generate_incremental_update.assert_called_once()


class TestUpdateAllPagesAggregation:
    def test_no_evidence_counted_in_summary(self, tmp_path):
        gen = _bare_generator()
        wiki = tmp_path / "wiki"
        (wiki / "02-概念").mkdir(parents=True)
        (wiki / "02-概念" / "概念-甲.md").write_text(
            "---\ntitle: 甲\ntype: concept\n---\n\n正文。\n", encoding="utf-8")
        gen._wiki_root = wiki
        gen._update_page_with_content = MagicMock(
            return_value={"status": "no_evidence", "title": "甲"})
        result = gen.update_all_pages()
        assert result["total"] == 1
        assert result["no_evidence"] == 1
        assert result["updated"] == 0
        assert result["errors"] == 0
