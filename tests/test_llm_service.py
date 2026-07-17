"""测试 iris.llm.service — LLM 统一入口服务。"""

import pytest
from unittest.mock import patch, MagicMock
from iris.llm.service import LLMService, GenerationResult


class TestGenerationResult:
    """GenerationResult 是不可变数据类，测试其构造和默认值。"""

    def test_construct_with_text_only(self):
        r = GenerationResult(text="Hello")
        assert r.text == "Hello"
        assert r.selected_role == ""
        assert r.provider == ""
        assert r.model == ""
        assert r.prompt_tokens == 0
        assert r.completion_tokens == 0

    def test_construct_full(self):
        r = GenerationResult(
            text="Hi", selected_role="qa", provider="deepseek",
            model="deepseek-chat", api_base_url="https://api.deepseek.com",
            matched_rule="default", prompt_tokens=100, completion_tokens=50,
        )
        assert r.text == "Hi"
        assert r.selected_role == "qa"
        assert r.provider == "deepseek"
        assert r.model == "deepseek-chat"
        assert r.api_base_url == "https://api.deepseek.com"
        assert r.matched_rule == "default"
        assert r.prompt_tokens == 100
        assert r.completion_tokens == 50

    def test_is_frozen(self):
        r = GenerationResult(text="test")
        with pytest.raises(Exception):
            r.text = "modified"  # frozen dataclass


class TestLLMServiceInit:
    """测试 LLMService 构造——需要打入 Fake provider。"""

    def test_constructor_creates_provider(self):
        """模拟 provider 创建，验证 service 不崩溃。"""
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_bundle = MagicMock()
            svc = LLMService(mock_bundle)
            assert svc._provider is mock_prov.return_value
            mock_prov.assert_called_once_with(mock_bundle)

    def test_get_provider(self):
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider"):
            svc = LLMService(MagicMock())
            assert svc.get_provider() is svc._provider

    def test_get_base_model_returns_provider(self):
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider"):
            svc = LLMService(MagicMock())
            assert svc.get_base_model() is svc._provider
            assert svc.get_adv_model() is svc._provider


class TestLLMServiceGenerate:
    """测试 generate 方法——mock provider 响应。"""

    def test_generate_returns_generation_result(self):
        from iris.core.llm_types import LLMResponse
        mock_response = LLMResponse(
            text="Response text",
            selected_role="qa", provider="deepseek",
            model="deepseek-chat", api_base_url="https://api.com",
            matched_rule="default", prompt_tokens=10, completion_tokens=5,
        )

        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate.return_value = mock_response
            svc = LLMService(MagicMock())
            result = svc.generate("Hello, world!")

            assert isinstance(result, GenerationResult)
            assert result.text == "Response text"
            assert result.selected_role == "qa"
            assert result.prompt_tokens == 10
            assert result.completion_tokens == 5

    def test_generate_with_route_context(self):
        from iris.core.llm_types import LLMResponse
        mock_response = LLMResponse(text="OK", selected_role="analysis",
                                     provider="qwen", model="qwen-max",
                                     api_base_url="https://api.qwen.com",
                                     matched_rule="analysis")

        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate.return_value = mock_response
            svc = LLMService(MagicMock())
            result = svc.generate("Analyze this", route_context={
                "input_type": "text", "task_type": "analysis", "complexity": "complex",
            })

            assert result.text == "OK"
            assert result.selected_role == "analysis"

    def test_generate_default_context_when_none(self):
        from iris.core.llm_types import LLMResponse
        mock_response = LLMResponse(
            text="T", selected_role="", provider="", model="",
            api_base_url="", matched_rule="",
        )

        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate.return_value = mock_response
            svc = LLMService(MagicMock())
            result = svc.generate("Test")

            # 验证使用了默认路由上下文
            call_args = mock_prov.return_value.generate.call_args
            request = call_args[0][0]
            assert request.route_context["input_type"] == "text"
            assert request.route_context["task_type"] == "qa"

    def test_generate_temperature0_cache_hit(self):
        """temperature=0 命中缓存时直接返回，不调用 provider。"""
        cached_payload = {
            "text": "cached answer", "selected_role": "qa",
            "provider": "deepseek", "model": "deepseek-chat",
            "api_base_url": "https://api.deepseek.com", "matched_rule": "default",
            "prompt_tokens": 5, "completion_tokens": 10,
        }
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            svc = LLMService(MagicMock())
            svc._cache = MagicMock()
            svc._cache.get.return_value = cached_payload
            result = svc.generate("cached prompt", temperature=0)
            assert result.text == "cached answer"
            assert result.prompt_tokens == 5
            mock_prov.return_value.generate.assert_not_called()

    def test_generate_temperature0_cache_miss_writes_cache(self):
        """temperature=0 缓存未命中后写入缓存。"""
        from iris.core.llm_types import LLMResponse
        mock_response = LLMResponse(
            text="fresh", selected_role="qa", provider="deepseek",
            model="deepseek-chat", api_base_url="https://api.com", matched_rule="default",
        )
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate.return_value = mock_response
            svc = LLMService(MagicMock())
            svc._cache = MagicMock()
            svc._cache.get.return_value = None  # 缓存未命中
            result = svc.generate("new prompt", temperature=0)
            assert result.text == "fresh"
            svc._cache.put.assert_called_once()

    def test_generate_provider_error_is_raised(self):
        """provider 抛出 LLMProviderError 时服务应向上传播。"""
        from iris.llm import LLMProviderError
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate.side_effect = LLMProviderError("API 超时")
            svc = LLMService(MagicMock())
            with pytest.raises(LLMProviderError):
                svc.generate("failing prompt")

    def test_get_cache_stats(self):
        """get_cache_stats 委托给 cache.stats()。"""
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider"):
            svc = LLMService(MagicMock())
            svc._cache = MagicMock()
            svc._cache.stats.return_value = {"hits": 5, "misses": 2}
            stats = svc.get_cache_stats()
            assert stats["hits"] == 5

    def test_clear_cache(self):
        """clear_cache 委托给 cache.clear()，返回删除条目数。"""
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider"):
            svc = LLMService(MagicMock())
            svc._cache = MagicMock()
            svc._cache.clear.return_value = 12
            assert svc.clear_cache() == 12


class TestLLMServiceMultimodal:
    """generate_multimodal 覆盖。"""

    def test_generate_multimodal_returns_text(self):
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate_multimodal.return_value = "图片描述"
            svc = LLMService(MagicMock())
            result = svc.generate_multimodal([{"type": "text", "text": "描述这张图"}])
            assert result == "图片描述"

    def test_generate_multimodal_uses_default_context(self):
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate_multimodal.return_value = "ok"
            svc = LLMService(MagicMock())
            svc.generate_multimodal([{"type": "text", "text": "hi"}])
            _, called_ctx = mock_prov.return_value.generate_multimodal.call_args[0][:2]
            # 默认 ctx 的 input_type 应为 multimodal
            called_ctx = mock_prov.return_value.generate_multimodal.call_args[0][1]
            assert called_ctx["input_type"] == "multimodal"

    def test_generate_multimodal_provider_error_raises(self):
        from iris.llm import LLMProviderError
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate_multimodal.side_effect = LLMProviderError("no vision")
            svc = LLMService(MagicMock())
            with pytest.raises(LLMProviderError):
                svc.generate_multimodal([{"type": "text", "text": "x"}])


class TestLLMServiceAsync:
    """generate_async 覆盖。"""

    def test_generate_async_cache_hit(self):
        """temperature=0 命中缓存时 async 也应直接返回，不调用 provider。"""
        import asyncio
        cached_payload = {
            "text": "async cached", "selected_role": "qa",
            "provider": "deepseek", "model": "m", "api_base_url": "u",
            "matched_rule": "r", "prompt_tokens": 1, "completion_tokens": 2,
        }
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            svc = LLMService(MagicMock())
            svc._cache = MagicMock()
            svc._cache.get.return_value = cached_payload

            async def run():
                return await svc.generate_async("p", temperature=0)

            result = asyncio.run(run())
            assert result.text == "async cached"
            mock_prov.return_value.generate.assert_not_called()

    def test_generate_async_calls_generate(self):
        """无缓存命中时 async 走 generate() 同步路径。"""
        import asyncio
        from iris.core.llm_types import LLMResponse
        mock_response = LLMResponse(
            text="async result", selected_role="qa", provider="p",
            model="m", api_base_url="u", matched_rule="r",
        )
        with patch("iris.llm.service.EnvironmentConfiguredLLMProvider") as mock_prov:
            mock_prov.return_value.generate.return_value = mock_response
            svc = LLMService(MagicMock())
            svc._cache = MagicMock()
            svc._cache.get.return_value = None

            async def run():
                return await svc.generate_async("async prompt")

            result = asyncio.run(run())
            assert result.text == "async result"
