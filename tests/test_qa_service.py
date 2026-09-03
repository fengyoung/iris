"""qa/service.py 单元测试。"""

from __future__ import annotations

from unittest.mock import patch


class TestQAServiceLocal:
    """QAService.ask local 模式。"""

    def test_ask_local_returns_response(self, config_bundle):
        from iris.qa.service import QAService
        from iris.retrieval.searcher import RetrievalResult, RetrievalHit

        # mock LocalRetriever (via EnhancedRetriever)
        mock_hit = RetrievalHit(
            chunk_id="c1", score=0.9, title="测试文档",
            relative_path="docs/test.md", section_path=["第一节"],
            content_preview="内容预览", line_start=1, line_end=10,
            chunk_type="section", explanation="",
        )
        mock_result = RetrievalResult(total_hits=1, hits=[mock_hit])

        svc = QAService(config_bundle)
        with patch.object(svc._retriever._local, "search", return_value=mock_result):
            response = svc.ask("测试问题", mode="local", top_k=3)
        assert response is not None
        assert hasattr(response, "answer") or hasattr(response, "to_dict")

    def test_ask_returns_dict(self, config_bundle):
        from iris.qa.service import QAService
        from iris.retrieval.searcher import RetrievalResult

        svc = QAService(config_bundle)
        mock_result = RetrievalResult(total_hits=0, hits=[])
        with patch.object(svc._retriever._local, "search", return_value=mock_result):
            response = svc.ask("问题", mode="local")
        d = response.to_dict()
        assert isinstance(d, dict)


class TestQAServiceLLMFallback:
    """QAService.ask llm 模式降级到 local。"""

    def test_llm_mode_falls_back_on_error(self, config_bundle):
        from iris.qa.service import QAService
        from iris.retrieval.searcher import RetrievalResult
        from iris.llm import LLMProviderError

        svc = QAService(config_bundle)
        mock_result = RetrievalResult(total_hits=0, hits=[])

        with patch.object(svc._retriever._local, "search", return_value=mock_result):
            with patch.object(svc._llm, "generate", side_effect=LLMProviderError("offline")):
                # 应降级到 local 而非 crash
                response = svc.ask("测试", mode="llm")
        assert response is not None
