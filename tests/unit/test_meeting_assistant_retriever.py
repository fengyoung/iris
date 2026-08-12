"""检索适配器单元测试：初始化降级 / 搜索异常容错 / 上下文格式化。

依赖 EnhancedRetriever 在构造时被 mock，只测试适配器层逻辑。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from iris.retrieval import RetrievalHit


# ── 工具 ──────────────────────────────────────────────────────

def _make_hit(title="测试文档", content="这是文档内容预览",
              section_path=None, relative_path="test.md"):
    """构造 RetrievalHit。"""
    return RetrievalHit(
        chunk_id=f"chunk_{title}",
        title=title,
        content_preview=content,
        section_path=section_path or [],
        relative_path=relative_path,
        score=0.85,
        line_start=0,
        line_end=1,
    )


# ── TestRetrieverAdapter ──────────────────────────────────────

class TestRetrieverAdapter:
    """检索适配器容错。"""

    def test_init_failure_sets_none(self):
        """EnhancedRetriever 构造失败时 _retriever=None。"""
        with patch("iris.assistant._retriever.EnhancedRetriever",
                   side_effect=RuntimeError("索引损坏")):
            from iris.assistant._retriever import RetrieverAdapter
            adapter = RetrieverAdapter(SimpleNamespace(app={}))
            assert adapter._retriever is None

    def test_search_returns_empty_on_none(self):
        """_retriever=None 时 search() 返回 []。"""
        from iris.assistant._retriever import RetrieverAdapter
        adapter = RetrieverAdapter.__new__(RetrieverAdapter)
        adapter._retriever = None
        assert adapter.search("测试查询") == []

    def test_search_returns_empty_on_exception(self):
        """检索异常时返回 []。"""
        from iris.assistant._retriever import RetrieverAdapter
        adapter = RetrieverAdapter.__new__(RetrieverAdapter)
        mock_retriever = SimpleNamespace()
        mock_retriever.search = lambda **kw: (_ for _ in ()).throw(
            RuntimeError("检索超时"))
        adapter._retriever = mock_retriever
        assert adapter.search("测试查询") == []

    def test_search_deadline_passed(self):
        """_deadline 参数正确传递给 EnhancedRetriever.search()。"""
        import time
        from iris.assistant._retriever import RetrieverAdapter, _RETRIEVER_DEADLINE_SEC
        adapter = RetrieverAdapter.__new__(RetrieverAdapter)
        calls = {}
        mock_retriever = SimpleNamespace()
        def _fake_search(text="", top_k=5, mode="local", _deadline=None):
            calls["deadline"] = _deadline
            return SimpleNamespace(hits=[])
        mock_retriever.search = _fake_search
        adapter._retriever = mock_retriever
        before = time.monotonic()
        adapter.search("测试查询", top_k=3)
        assert "deadline" in calls
        # deadline = 调用时刻 + 8s
        assert calls["deadline"] == pytest.approx(before + _RETRIEVER_DEADLINE_SEC, abs=0.2)


# ── TestFormatContext ─────────────────────────────────────────

class TestFormatContext:
    """命中列表 → 分析 Prompt 上下文块。"""

    def test_empty_hits_returns_empty_string(self):
        from iris.assistant._retriever import RetrieverAdapter
        assert RetrieverAdapter.format_context([]) == ""

    def test_formats_title_and_preview(self):
        from iris.assistant._retriever import RetrieverAdapter
        hits = [_make_hit(title="图像识别方案", content="使用 Paraformer 模型")]
        result = RetrieverAdapter.format_context(hits)
        assert "图像识别方案" in result
        assert "Paraformer" in result
        assert result.startswith("- ")

    def test_truncates_to_max_chars(self):
        from iris.assistant._retriever import RetrieverAdapter
        hits = [_make_hit(title="长文档", content="A" * 2000)]
        result = RetrieverAdapter.format_context(hits, max_chars=100)
        assert len(result) <= 103  # 100 + "…"
        assert result.endswith("…")

    def test_section_path_included(self):
        from iris.assistant._retriever import RetrieverAdapter
        hits = [_make_hit(title="文档", section_path=["Chapter 1", "Section A"],
                          content="内容")]
        result = RetrieverAdapter.format_context(hits)
        assert "Chapter 1 > Section A" in result

    def test_falls_back_to_relative_path(self):
        from iris.assistant._retriever import RetrieverAdapter
        hits = [_make_hit(title="", content="内容", relative_path="docs/test.md")]
        result = RetrieverAdapter.format_context(hits)
        assert "docs/test.md" in result
