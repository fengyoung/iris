"""FakeLLMProvider 单元测试。"""

from __future__ import annotations

import pytest

from iris.core.fake_provider import FakeLLMProvider
from iris.core.llm_types import LLMRequest
from iris.llm import LLMProviderError


class TestFakeLLMProvider:
    """FakeLLMProvider 测试 — 覆盖所有响应模式。"""

    def test_fixed_response(self):
        """fixed_response 模式：返回固定文本。"""
        provider = FakeLLMProvider(fixed_response="Hello, world!")
        req = LLMRequest(prompt="test", route_context={"use_case": "qa"})
        result = provider.generate(req)
        assert result.text == "Hello, world!"
        assert result.provider == "fake"

    def test_response_map(self):
        """response_map 模式：按 use_case 匹配响应。"""
        provider = FakeLLMProvider(response_map={"qa": "QA response", "analysis": "Analysis response"})
        req_qa = LLMRequest(prompt="q", route_context={"use_case": "qa"})
        req_analysis = LLMRequest(prompt="a", route_context={"use_case": "analysis"})
        assert provider.generate(req_qa).text == "QA response"
        assert provider.generate(req_analysis).text == "Analysis response"

    def test_response_fn(self):
        """response_fn 模式：调用函数生成响应。"""
        def my_fn(ctx):
            return f"Response for {ctx.get('use_case', 'unknown')}"
        provider = FakeLLMProvider(response_fn=my_fn)
        req = LLMRequest(prompt="test", route_context={"use_case": "qa"})
        result = provider.generate(req)
        assert result.text == "Response for qa"

    def test_raise_on_generate(self):
        """raise_on_generate 模式：始终抛出 LLMProviderError。"""
        provider = FakeLLMProvider(raise_on_generate=True)
        req = LLMRequest(prompt="test", route_context={"use_case": "qa"})
        with pytest.raises(LLMProviderError, match="FakeLLMProvider"):
            provider.generate(req)

    def test_raise_on_generate_multimodal(self):
        """multimodal 同样受 raise_on_generate 控制。"""
        provider = FakeLLMProvider(raise_on_generate=True)
        with pytest.raises(LLMProviderError, match="FakeLLMProvider"):
            provider.generate_multimodal([{"type": "text", "text": "hello"}], {"use_case": "qa"})

    def test_generate_calls_tracking(self):
        """generate_calls 正确记录每次调用。"""
        provider = FakeLLMProvider(fixed_response="ok")
        provider.generate(LLMRequest(prompt="prompt1", route_context={"use_case": "qa"}))
        provider.generate(LLMRequest(prompt="prompt2", route_context={"use_case": "analysis"}))
        assert len(provider.generate_calls) == 2
        assert provider.generate_calls[0]["prompt"] == "prompt1"
        assert provider.generate_calls[1]["prompt"] == "prompt2"

    def test_multimodal_calls_tracking(self):
        """multimodal_calls 正确记录每次调用。"""
        provider = FakeLLMProvider(fixed_response="ok")
        provider.generate_multimodal([{"type": "text"}], {"use_case": "qa"})
        assert len(provider.multimodal_calls) == 1

    def test_reset_clears_history(self):
        """reset() 清空调用记录。"""
        provider = FakeLLMProvider(fixed_response="ok")
        provider.generate(LLMRequest(prompt="p", route_context={"use_case": "qa"}))
        assert len(provider.generate_calls) == 1
        provider.reset()
        assert len(provider.generate_calls) == 0
        assert len(provider.multimodal_calls) == 0

    def test_has_credentials_for_role(self):
        """credentials_available 控制认证状态。"""
        provider_with = FakeLLMProvider(credentials_available=True)
        provider_without = FakeLLMProvider(credentials_available=False)
        assert provider_with.has_credentials_for_role("base_model") is True
        assert provider_without.has_credentials_for_role("base_model") is False

    def test_resolve_returns_routing_decision(self):
        """resolve() 返回有效的 RoutingDecision。"""
        provider = FakeLLMProvider()
        decision = provider.resolve({"use_case": "qa"})
        assert decision.selected_role == "base_model"
        assert decision.fallback_role == "adv_model"
        assert decision.matched_rule == "__fake__"

    def test_default_fallback_response(self):
        """无 fixed_response/response_map/response_fn 时使用默认文本。"""
        provider = FakeLLMProvider()
        req = LLMRequest(prompt="test", route_context={"use_case": "unknown_case"})
        result = provider.generate(req)
        assert "Fake response" in result.text
