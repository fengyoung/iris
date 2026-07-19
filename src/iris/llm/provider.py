"""LLM provider 抽象与配置读取。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 默认参数
_DEFAULT_TEMPERATURE = 0.2

from iris.config.loader import ConfigBundle
from iris.core.llm_types import LLMRequest, LLMResponse  # 从 core/ 迁移（消除循环依赖）
from iris.llm.model_manager import ModelManager, ModelManagerError
from iris.llm.router import ModelRouter, RoutingDecision


class LLMProviderError(RuntimeError):
    """LLM provider 相关错误。"""


class BaseLLMProvider:
    """LLM provider 抽象基类 — 所有 LLM 提供者必须实现此接口。

    使用 NotImplementedError 而非 abc.ABC，保持简洁。
    FakeLLMProvider、NullLLMProvider 均继承自此基类。
    """

    def generate(
        self,
        request: LLMRequest,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> LLMResponse:
        raise NotImplementedError

    def generate_multimodal(
        self,
        content_parts: list[dict],
        route_context: dict,
        *,
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        raise NotImplementedError

    def has_credentials_for_role(self, role: str) -> bool:
        """子类可覆盖。"""
        return False

    def resolve(self, route_context: Dict[str, Any]) -> Any:
        """子类可覆盖。"""
        raise NotImplementedError


class EnvironmentConfiguredLLMProvider(BaseLLMProvider):
    """根据 llm.json 决定是否可调用真实 LLM。"""

    OPENAI_COMPATIBLE_PROVIDERS = {"openai", "deepseek", "openai_compatible", "qwen", "bailian", "custom-algo-platform"}

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._router = ModelRouter(config)
        self._model_manager = ModelManager(config.llm["models"], config.root / "data")
        from iris.llm.usage_tracker import UsageTracker
        self._tracker = UsageTracker(config.root / "data")

    def get_active_model_config(self, role: str) -> Dict[str, Any]:
        """获取指定角色的当前活跃模型配置（不含 api_key）。"""
        return self._model_manager.get_active_model_config(role, sensitive=False)

    def _get_active_model_config_sensitive(self, role: str) -> Dict[str, Any]:
        """[内部] 获取活跃模型完整配置（含 api_key），仅供 provider 内部 API 调用使用。"""
        return self._model_manager.get_active_model_config(role, sensitive=True)

    def get_model_manager(self) -> ModelManager:
        """返回内部的 ModelManager 实例，供外部查询/切换模型。"""
        return self._model_manager

    def _find_model_by_name(self, model_name: str) -> Optional[Dict[str, Any]]:
        """在所有角色中按 model 字段或 model_id 查找模型配置（含 api_key）。"""
        return self._model_manager.find_model_by_name(model_name)

    def _dispatch_provider_call(
        self,
        api_base: str,
        api_key: str,
        model_name: str,
        prompt: str,
        provider_name: str,
        cfg: Dict[str, Any],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, int, int]:
        """按 provider 类型分发 API 调用，返回 (text, prompt_tokens, completion_tokens)。"""
        timeout = cfg.get("timeout_seconds", 60)
        effective_retries = max_retries if max_retries is not None else cfg.get("max_retries", 0)
        if provider_name in self.OPENAI_COMPATIBLE_PROVIDERS:
            return self._call_openai_compatible(
                api_base, api_key, model_name, prompt,
                temperature=temperature, max_tokens=max_tokens,
                timeout=timeout, max_retries=effective_retries,
                extra_body=extra_body,
            )
        if provider_name == "anthropic":
            return self._call_anthropic(
                api_base, api_key, model_name, prompt,
                max_tokens=max_tokens, max_retries=effective_retries,
            )
        raise LLMProviderError(f"暂不支持的 provider: {provider_name}")

    def generate(
        self,
        request_data: LLMRequest,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
        force_model: Optional[str] = None,
    ) -> LLMResponse:
        # force_model：跳过路由，直接使用指定模型
        if force_model:
            model_cfg = self._find_model_by_name(force_model)
            if model_cfg is None:
                raise LLMProviderError(f"未找到模型: {force_model}")
            api_base = model_cfg["api_base_url"]
            api_key = model_cfg.get("api_key", "")
            model_name = model_cfg["model"]
            provider_name = str(model_cfg["provider"]).lower()
            if max_tokens is None:
                max_tokens = model_cfg.get("max_tokens")
            text, pt, ct = self._dispatch_provider_call(
                api_base, api_key, model_name, request_data.prompt, provider_name, model_cfg,
                temperature=temperature, max_tokens=max_tokens, max_retries=max_retries,
                extra_body=request_data.extra_body,
            )
            self._tracker.record(
                model=model_name, provider=provider_name,
                route_role="forced", matched_rule="force_model",
                prompt_tokens=pt, completion_tokens=ct,
            )
            return LLMResponse(
                text=text, selected_role="forced", provider=provider_name,
                model=model_name, api_base_url=api_base,
                matched_rule="force_model",
                prompt_tokens=pt, completion_tokens=ct,
            )

        decision = self._router.route(request_data.route_context)

        if max_tokens is None:
            try:
                default_cfg = self._model_manager.get_active_model_config(
                    decision.selected_role, sensitive=False
                )
                max_tokens = default_cfg.get("max_tokens")
            except ModelManagerError:
                pass

        def _try_call(api_base: str, api_key: str, model_name: str, cfg: Dict[str, Any]) -> Tuple[str, int, int]:
            """文本 API 调用闭包，捕获 prompt / temperature / max_tokens。"""
            return self._dispatch_provider_call(
                api_base, api_key, model_name, request_data.prompt,
                str(cfg["provider"]).lower(), cfg,
                temperature=temperature, max_tokens=max_tokens, max_retries=max_retries,
            )

        text, role, provider_name, model_name, api_base, pt, ct = self._fallback_loop(
            decision, _try_call,
        )
        self._tracker.record(
            model=model_name, provider=provider_name,
            route_role=role, matched_rule=decision.matched_rule,
            prompt_tokens=pt, completion_tokens=ct,
        )
        return LLMResponse(
            text=text, selected_role=role, provider=provider_name,
            model=model_name, api_base_url=api_base,
            matched_rule=decision.matched_rule,
            prompt_tokens=pt, completion_tokens=ct,
        )

    def _build_fallback_chain(
        self, decision: RoutingDecision
    ) -> List[tuple]:
        """构建模型降级链：(role, model_id, model_config) 列表。

        1. 同角色内按 priority 降序（allow_auto_upgrade=false 时仅活跃模型）
        2. 跨角色 fallback 按 priority 降序（allow_auto_downgrade=false 时跳过）
        """
        chain: List[tuple] = []
        seen_roles: set = set()
        primary_role = decision.selected_role
        fallback_role = decision.fallback_role

        if decision.allow_auto_upgrade:
            for model_id, cfg in self._model_manager.get_models_by_priority(primary_role):
                chain.append((primary_role, model_id, cfg))
        else:
            # 仅使用当前活跃模型，不尝试同角色其他模型
            try:
                active_cfg = self._model_manager.get_active_model_config(primary_role, sensitive=True)
                active_id = self._model_manager.get_active_model_id(primary_role)
                chain.append((primary_role, active_id, active_cfg))
            except ModelManagerError as exc:
                raise LLMProviderError(f"角色 {primary_role} 配置错误: {exc}") from exc
        seen_roles.add(primary_role)

        if fallback_role and fallback_role not in seen_roles and decision.allow_auto_downgrade:
            for model_id, cfg in self._model_manager.get_models_by_priority(fallback_role):
                chain.append((fallback_role, model_id, cfg))
            seen_roles.add(fallback_role)

        return chain

    def _fallback_loop(
        self,
        decision: RoutingDecision,
        call_fn,
        *,
        model_filter=None,
        error_label: str = "LLM",
    ):
        """共享降级循环：路由 → 遍历降级链 → 调用 call_fn → 成功返回。

        被 generate() 和 generate_multimodal() 共享，消除 ~85% 重复代码。

        Args:
            decision: 路由决策（含 selected_role / fallback_role）
            call_fn: (api_base, api_key, model_name, config) -> str
            model_filter: 可选 (config) -> bool，多模态调用传入以跳过纯文本模型
            error_label: 错误消息中的调用类型前缀

        Returns:
            (text, role, provider_name, model_name, api_base_url, prompt_tokens, completion_tokens)
        """
        fallback_chain = self._build_fallback_chain(decision)
        tried_models: List[str] = []
        last_error: Optional[Exception] = None

        for role, model_id, model_config in fallback_chain:
            if model_filter and not model_filter(model_config):
                continue

            model_key = f"{role}/{model_id}"
            if model_key in tried_models:
                continue
            tried_models.append(model_key)

            api_key = model_config.get("api_key", "")
            if not api_key:
                last_error = LLMProviderError(
                    f"llm.json 中 {role}.{model_id}.api_key 为空"
                )
                continue

            api_base_url = model_config["api_base_url"]
            try:
                text, pt, ct = call_fn(api_base_url, api_key, model_config["model"], model_config)

                if role != decision.selected_role or model_id != self._model_manager.get_active_model_id(role):
                    logger.warning("模型降级: %s/%s → %s/%s",
                                   decision.selected_role,
                                   self._model_manager.get_active_model_id(decision.selected_role),
                                   role, model_id)

                return text, role, str(model_config["provider"]).lower(), model_config["model"], api_base_url, pt, ct

            except LLMProviderError as exc:
                last_error = exc
                continue
            except Exception as exc:
                last_error = LLMProviderError(f"模型 {model_key} 调用异常: {exc}")
                continue

        raise LLMProviderError(
            f"{error_label} 调用失败，已尝试全部降级链 ({', '.join(tried_models)}): {last_error}"
        )

    def generate_multimodal(
        self,
        content_parts: list[dict],
        route_context: Dict[str, Any],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        decision = self._router.route(route_context)

        def _try_multimodal(api_base: str, api_key: str, model_name: str, cfg: Dict[str, Any]) -> Tuple[str, int, int]:
            """多模态 API 调用闭包，捕获 content_parts。"""
            provider_name = str(cfg["provider"]).lower()
            if provider_name not in self.OPENAI_COMPATIBLE_PROVIDERS:
                raise LLMProviderError(f"多模态暂不支持 provider: {provider_name}")
            timeout = cfg.get("timeout_seconds", 60)
            effective_retries = max_retries if max_retries is not None else cfg.get("max_retries", 0)
            effective_max_tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens")
            return self._call_openai_compatible_multimodal(
                api_base, api_key, model_name, content_parts,
                temperature=temperature, timeout=timeout,
                max_retries=effective_retries, max_tokens=effective_max_tokens,
            )

        text, _role, _provider, _model, _api_base, pt, ct = self._fallback_loop(
            decision, _try_multimodal,
            model_filter=lambda cfg: cfg.get("multimodal", False),
            error_label="多模态 LLM",
        )
        self._tracker.record(
            model=_model, provider=_provider,
            route_role=_role, matched_rule=decision.matched_rule,
            prompt_tokens=pt, completion_tokens=ct,
            is_multimodal=True,
        )
        return text

    def resolve(self, route_context: Dict[str, Any]) -> RoutingDecision:
        return self._router.route(route_context)

    def has_credentials_for_role(self, role: str) -> bool:
        """检查指定角色下是否有任何模型配置了有效 api_key。

        扫描全部模型而非仅活跃模型，避免误报角色不可用。
        """
        try:
            for _model_id, cfg in self._model_manager.get_models_by_priority(role):
                if cfg.get("api_key", "").strip():
                    return True
            return False
        except (KeyError, ModelManagerError):
            return False

    def _call_openai_chat(
        self, api_base_url: str, api_key: str, model: str, content,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 60,
        max_retries: int = 0,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, int, int]:
        """统一的 OpenAI 兼容 Chat Completions 调用。

        content 可以是 str（纯文本）或 list[dict]（多模态 content_parts）。
        extra_body 可包含要合并到请求体中的额外字段。
        返回 (text, prompt_tokens, completion_tokens)。
        """
        endpoint = _join_url(api_base_url, "/chat/completions")
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature if temperature is not None else _DEFAULT_TEMPERATURE,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if extra_body:
            payload.update(extra_body)
        data = self._post_json(
            endpoint,
            payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            max_retries=max_retries,
        )
        usage = data.get("usage", {})
        pt = int(usage.get("prompt_tokens", 0))
        ct = int(usage.get("completion_tokens", 0))
        return _extract_chat_completions_text(data), pt, ct

    # 向后兼容：保留旧方法名，内部委托给统一方法
    def _call_openai_compatible(
        self, api_base_url: str, api_key: str, model: str, prompt: str,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 60,
        max_retries: int = 0,
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, int, int]:
        return self._call_openai_chat(
            api_base_url, api_key, model, prompt,
            temperature=temperature, max_tokens=max_tokens,
            timeout=timeout, max_retries=max_retries,
            extra_body=extra_body,
        )

    def _call_openai_compatible_multimodal(
        self, api_base_url: str, api_key: str, model: str, content_parts: list[dict],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 60,
        max_retries: int = 0,
    ) -> Tuple[str, int, int]:
        return self._call_openai_chat(
            api_base_url, api_key, model, content_parts,
            temperature=temperature, max_tokens=max_tokens,
            timeout=timeout, max_retries=max_retries,
        )

    def _call_anthropic(self, api_base_url: str, api_key: str, model: str, prompt: str,
                        max_tokens: Optional[int] = None,
                        max_retries: int = 0) -> Tuple[str, int, int]:
        endpoint = _join_url(api_base_url, "/messages")
        payload = {
            "model": model,
            "max_tokens": max_tokens or 4096,
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
            max_retries=max_retries,
        )
        # Anthropic API 用 input_tokens / output_tokens
        usage = data.get("usage", {})
        pt = int(usage.get("input_tokens", 0))
        ct = int(usage.get("output_tokens", 0))
        return _extract_anthropic_text(data), pt, ct

    def _post_json(self, url: str, payload: Dict[str, Any], headers: Dict[str, str], *,
                   timeout: int = 60, max_retries: int = 0) -> Dict[str, Any]:
        from iris.core.http_client import http_post_json
        return http_post_json(url, payload, headers, timeout=timeout, max_retries=max_retries,
                             error_factory=lambda msg: LLMProviderError(f"LLM 请求失败: {msg}"))


class NullLLMProvider(BaseLLMProvider):
    """不调用真实 LLM 的空实现。"""

    def generate(self, request: LLMRequest, *, temperature=None, max_tokens=None,
                 max_retries=None) -> LLMResponse:
        raise LLMProviderError("当前 provider 为 NullLLMProvider，未启用真实 LLM")

    def generate_multimodal(self, content_parts: list[dict], route_context: dict,
                            *, temperature=None, max_retries=None) -> str:
        raise LLMProviderError("当前 provider 为 NullLLMProvider，未启用真实 LLM")

    def has_credentials_for_role(self, role: str) -> bool:
        return False

    def resolve(self, route_context):
        from iris.llm.router import RoutingDecision
        return RoutingDecision(selected_role="base_model", fallback_role=None, matched_rule="__null__")


def _join_url(base_url: str, suffix: str) -> str:
    return base_url.rstrip("/") + suffix


def _is_deepseek_thinking_model(model: str) -> bool:
    """DeepSeek v4 系列模型默认开启 thinking（CoT 推理），需显式关闭。"""
    return "deepseek-v4" in model.lower()


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
    if isinstance(reasoning, str) and reasoning.strip():
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
