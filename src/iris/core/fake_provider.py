"""Fake LLM Provider：用于测试的 LLM 提供者，返回固定响应。"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from iris.core.llm_types import LLMRequest, LLMResponse
from iris.llm import LLMProviderError
from iris.llm.provider import BaseLLMProvider


class FakeLLMProvider(BaseLLMProvider):
    def __init__(self, fixed_response: str = "", response_map: Optional[Dict[str, str]] = None,
                 response_fn: Optional[Callable[[Dict[str, Any]], str]] = None,
                 raise_on_generate: bool = False, credentials_available: bool = True):
        self._fixed_response = fixed_response
        self._response_map = response_map or {}
        self._response_fn = response_fn
        self._raise_on_generate = raise_on_generate
        self._credentials_available = credentials_available
        self._generate_calls: List[Dict[str, Any]] = []
        self._multimodal_calls: List[Dict[str, Any]] = []

    def generate(self, request: LLMRequest, *, temperature: Optional[float] = None,
                 max_tokens: Optional[int] = None, max_retries: Optional[int] = None) -> LLMResponse:
        call_record = {"prompt": request.prompt[:200], "route_context": request.route_context,
                       "temperature": temperature, "max_tokens": max_tokens}
        self._generate_calls.append(call_record)
        if self._raise_on_generate:
            raise LLMProviderError("FakeLLMProvider 配置为抛出异常")
        text = self._resolve_response(request.route_context)
        use_case = request.route_context.get("use_case", "unknown")
        return LLMResponse(text=text, selected_role="base_model", provider="fake", model="fake-model",
                           api_base_url="http://fake.local", matched_rule=f"__fake_{use_case}__")

    def generate_multimodal(self, content_parts: list[dict], route_context: Dict[str, Any],
                            *, temperature: Optional[float] = None, max_retries: Optional[int] = None) -> str:
        self._multimodal_calls.append({"content_parts": content_parts, "route_context": route_context})
        if self._raise_on_generate:
            raise LLMProviderError("FakeLLMProvider 配置为抛出异常")
        return self._resolve_response(route_context)

    def _resolve_response(self, route_context: Dict[str, Any]) -> str:
        if self._response_fn:
            return self._response_fn(route_context)
        use_case = route_context.get("use_case", "")
        if use_case in self._response_map:
            return self._response_map[use_case]
        if self._fixed_response:
            return self._fixed_response
        return f"Fake response for {use_case}"

    @property
    def generate_calls(self) -> List[Dict[str, Any]]:
        return list(self._generate_calls)

    @property
    def multimodal_calls(self) -> List[Dict[str, Any]]:
        return list(self._multimodal_calls)

    def reset(self) -> None:
        self._generate_calls.clear()
        self._multimodal_calls.clear()

    def has_credentials_for_role(self, role: str) -> bool:
        return self._credentials_available

    def resolve(self, route_context: Dict[str, Any]) -> Any:
        from iris.llm.router import RoutingDecision
        return RoutingDecision(selected_role="base_model", fallback_role="adv_model", matched_rule="__fake__")
