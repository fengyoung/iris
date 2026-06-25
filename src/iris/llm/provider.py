"""LLM provider 抽象与配置读取。"""

from __future__ import annotations

import json
import logging
import random
import socket
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib import error, request

logger = logging.getLogger(__name__)

from iris.config.loader import ConfigBundle
from iris.llm.model_manager import ModelManager
from iris.llm.router import ModelRouter, RoutingDecision


class LLMProviderError(RuntimeError):
    """LLM provider 相关错误。"""


@dataclass(frozen=True)
class LLMRequest:
    """LLM 请求上下文。"""

    prompt: str
    route_context: Dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """LLM 返回结果。"""

    text: str
    selected_role: str
    provider: str
    model: str
    api_base_url: str
    matched_rule: str


class BaseLLMProvider:
    """LLM provider 接口。"""

    def generate(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    def generate_multimodal(
        self, content_parts: list[dict], route_context: dict
    ) -> str:
        raise NotImplementedError


class EnvironmentConfiguredLLMProvider(BaseLLMProvider):
    """根据 llm.json 决定是否可调用真实 LLM。"""

    OPENAI_COMPATIBLE_PROVIDERS = {"openai", "deepseek", "openai_compatible", "qwen", "bailian", "custom-algo-platform"}

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._router = ModelRouter(config)
        self._model_manager = ModelManager(config.llm["models"], config.root / "data")

    def get_active_model_config(self, role: str) -> Dict[str, Any]:
        """获取指定角色的当前活跃模型完整配置。"""
        return self._model_manager.get_active_model_config(role)

    def get_model_manager(self) -> ModelManager:
        """返回内部的 ModelManager 实例，供外部查询/切换模型。"""
        return self._model_manager

    def generate(
        self,
        request_data: LLMRequest,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        decision = self._router.route(request_data.route_context)

        # 构建降级链：同角色 priority 降序 → 跨角色 fallback
        fallback_chain = self._build_fallback_chain(decision)
        tried_models: List[str] = []
        last_error: Optional[Exception] = None

        for role, model_id, model_config in fallback_chain:
            model_key = f"{role}/{model_id}"
            if model_key in tried_models:
                continue
            tried_models.append(model_key)

            provider_name = str(model_config["provider"]).lower()
            api_key = model_config.get("api_key", "")
            api_base_url = model_config["api_base_url"]

            if not api_key:
                last_error = LLMProviderError(
                    f"llm.json 中 {role}.{model_id}.api_key 为空"
                )
                continue

            try:
                if provider_name in self.OPENAI_COMPATIBLE_PROVIDERS:
                    timeout = model_config.get("timeout_seconds", 60)
                    max_retries = model_config.get("max_retries", 0)
                    text = self._call_openai_compatible(
                        api_base_url, api_key, model_config["model"], request_data.prompt,
                        temperature=temperature, max_tokens=max_tokens,
                        timeout=timeout, max_retries=max_retries,
                    )
                elif provider_name == "anthropic":
                    text = self._call_anthropic(
                        api_base_url, api_key, model_config["model"],
                        request_data.prompt,
                    )
                else:
                    last_error = LLMProviderError(f"暂不支持的 provider: {provider_name}")
                    continue

                # 成功：如果是降级模型，记录日志
                if role != decision.selected_role or model_id != self._model_manager.get_active_model_id(role):
                    logger.warning("模型降级: %s/%s → %s/%s", decision.selected_role, self._model_manager.get_active_model_id(decision.selected_role), role, model_id)

                return LLMResponse(
                    text=text,
                    selected_role=role,
                    provider=provider_name,
                    model=model_config["model"],
                    api_base_url=api_base_url,
                    matched_rule=decision.matched_rule,
                )

            except LLMProviderError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = LLMProviderError(f"模型 {model_key} 调用异常: {exc}")
                continue

        raise LLMProviderError(
            f"LLM 调用失败，已尝试全部降级链 ({', '.join(tried_models)}): {last_error}"
        )

    def _build_fallback_chain(
        self, decision: RoutingDecision
    ) -> List[tuple]:
        """构建模型降级链：(role, model_id, model_config) 列表。

        1. 同角色内按 priority 降序（活跃模型优先）
        2. 跨角色 fallback 按 priority 降序
        """
        chain: List[tuple] = []
        seen_roles: set = set()
        primary_role = decision.selected_role
        fallback_role = decision.fallback_role

        for model_id, cfg in self._model_manager.get_models_by_priority(primary_role):
            chain.append((primary_role, model_id, cfg))
        seen_roles.add(primary_role)

        if fallback_role and fallback_role not in seen_roles:
            for model_id, cfg in self._model_manager.get_models_by_priority(fallback_role):
                chain.append((fallback_role, model_id, cfg))
            seen_roles.add(fallback_role)

        return chain

    def generate_multimodal(
        self,
        content_parts: list[dict],
        route_context: Dict[str, Any],
        *,
        temperature: Optional[float] = None,
    ) -> str:
        decision = self._router.route(route_context)

        fallback_chain = self._build_fallback_chain(decision)
        tried_models: List[str] = []
        last_error: Optional[Exception] = None

        for role, model_id, model_config in fallback_chain:
            if not model_config.get("multimodal", False):
                continue

            model_key = f"{role}/{model_id}"
            if model_key in tried_models:
                continue
            tried_models.append(model_key)

            provider_name = str(model_config["provider"]).lower()
            api_key = model_config.get("api_key", "")
            api_base_url = model_config["api_base_url"]

            if not api_key:
                last_error = LLMProviderError(f"llm.json 中 {role}.{model_id}.api_key 为空")
                continue
            if provider_name not in self.OPENAI_COMPATIBLE_PROVIDERS:
                last_error = LLMProviderError(f"多模态暂不支持 provider: {provider_name}")
                continue

            try:
                timeout = model_config.get("timeout_seconds", 60)
                max_retries = model_config.get("max_retries", 0)
                text = self._call_openai_compatible_multimodal(
                    api_base_url, api_key, model_config["model"], content_parts,
                    temperature=temperature, timeout=timeout, max_retries=max_retries,
                )
                if role != decision.selected_role or model_id != self._model_manager.get_active_model_id(role):
                    logger.warning("模型降级: %s/%s → %s/%s", decision.selected_role, self._model_manager.get_active_model_id(decision.selected_role), role, model_id)
                return text

            except LLMProviderError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = LLMProviderError(f"模型 {model_key} 调用异常: {exc}")
                continue

        raise LLMProviderError(
            f"多模态 LLM 调用失败，已尝试全部降级链 ({', '.join(tried_models)}): {last_error}"
        )

    def resolve(self, route_context: Dict[str, Any]) -> RoutingDecision:
        return self._router.route(route_context)

    def has_credentials_for_role(self, role: str) -> bool:
        try:
            model_config = self.get_active_model_config(role)
            return bool(model_config.get("api_key", ""))
        except (KeyError, ModelManagerError):
            return False

    def _call_openai_compatible(
        self, api_base_url: str, api_key: str, model: str, prompt: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 60,
        max_retries: int = 0,
    ) -> str:
        endpoint = _join_url(api_base_url, "/chat/completions")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "temperature": temperature if temperature is not None else 0.2,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        data = self._post_json(
            endpoint,
            payload,
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            timeout=timeout,
            max_retries=max_retries,
        )
        return _extract_chat_completions_text(data)

    def _call_openai_compatible_multimodal(
        self, api_base_url: str, api_key: str, model: str, content_parts: list[dict],
        *,
        temperature: Optional[float] = None,
        timeout: int = 60,
        max_retries: int = 0,
    ) -> str:
        endpoint = _join_url(api_base_url, "/chat/completions")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": content_parts,
                }
            ],
            "temperature": temperature if temperature is not None else 0.2,
        }
        data = self._post_json(
            endpoint,
            payload,
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            timeout=timeout,
            max_retries=max_retries,
        )
        return _extract_chat_completions_text(data)

    def _call_anthropic(self, api_base_url: str, api_key: str, model: str, prompt: str) -> str:
        endpoint = _join_url(api_base_url, "/messages")
        payload = {
            "model": model,
            "max_tokens": 1200,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }
        data = self._post_json(
            endpoint,
            payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        return _extract_anthropic_text(data)

    def _post_json(self, url: str, payload: Dict[str, Any], headers: Dict[str, str], *,
                   timeout: int = 60, max_retries: int = 0) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url=url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in headers.items():
            req.add_header(key, value)

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            if attempt > 0:
                backoff = 2 ** attempt + random.uniform(0, 1)
                time.sleep(backoff)

            try:
                with request.urlopen(req, timeout=timeout) as response:
                    raw = response.read().decode("utf-8")
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_exc = LLMProviderError(f"LLM 请求失败: HTTP {exc.code} -> {detail}")
                if exc.code != 429 and exc.code < 500:
                    raise last_exc
                continue
            except error.URLError as exc:
                last_exc = LLMProviderError(f"LLM 网络请求失败: {exc}")
                continue
            except socket.timeout as exc:
                last_exc = LLMProviderError("LLM 请求超时")
                continue

            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                last_exc = LLMProviderError("LLM 返回了无法解析的 JSON")
                continue

        raise last_exc or LLMProviderError("LLM 请求失败，已达最大重试次数")


class NullLLMProvider(BaseLLMProvider):
    """不调用真实 LLM 的空实现。"""

    def generate(self, request: LLMRequest) -> LLMResponse:
        raise LLMProviderError("当前 provider 为 NullLLMProvider，未启用真实 LLM")

    def generate_multimodal(
        self, content_parts: list[dict], route_context: dict
    ) -> str:
        raise LLMProviderError("当前 provider 为 NullLLMProvider，未启用真实 LLM")


def _join_url(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + suffix


def _extract_chat_completions_text(payload: Dict[str, Any]) -> str:
    choices = payload.get("choices", [])
    if not choices:
        raise LLMProviderError("聊天补全响应中未找到 choices")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        texts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"} and item.get("text"):
                texts.append(str(item["text"]).strip())
        text = "\n".join(part for part in texts if part).strip()
        if text:
            return text
    reasoning = message.get("reasoning_content", "")
    if isinstance(reasoning, str) and reasoning.strip() and not content:
        return reasoning.strip()
    finish_reason = choices[0].get("finish_reason", "")
    raise LLMProviderError(
        f"聊天补全响应中未找到可用文本输出 (finish_reason={finish_reason})"
    )


def _extract_anthropic_text(payload: Dict[str, Any]) -> str:
    content = payload.get("content", [])
    texts = [item.get("text", "").strip() for item in content if item.get("type") == "text"]
    text = "\n".join(part for part in texts if part).strip()
    if text:
        return text
    raise LLMProviderError("Anthropic 响应中未找到可用文本输出")
