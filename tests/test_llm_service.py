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
