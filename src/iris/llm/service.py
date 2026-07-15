"""统一的 LLM Provider 入口。

其他模块（wiki/analysis/evaluation/trello 等）通过此服务获取 LLM 能力，
避免各自创建 EnvironmentConfiguredLLMProvider 的重复模式。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.llm import (
    EnvironmentConfiguredLLMProvider,
    LLMProviderError,
    LLMRequest,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationResult:
    """LLM 生成结果：包含文本和调用元数据。"""

    text: str
    selected_role: str = ""
    provider: str = ""
    model: str = ""
    api_base_url: str = ""
    matched_rule: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMService:
    """统一的 LLM 入口服务。

    封装 EnvironmentConfiguredLLMProvider 的创建和使用，
    提供便捷的 generate / generate_multimodal 方法。
    内置基于 prompt hash 的磁盘缓存，避免确定性调用（temperature=0）重复请求 API。
    """

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._provider = EnvironmentConfiguredLLMProvider(config)
        from iris.llm.cache import LLMResponseCache
        self._cache = LLMResponseCache(config.root / "data")

    # ── Provider 访问 ──────────────────────────────────────────────

    def get_provider(self) -> EnvironmentConfiguredLLMProvider:
        """获取完整的 provider 实例（高级用法：自定义 route_context 等）。"""
        return self._provider

    # ── 缓存访问 ────────────────────────────────────────────────────

    def get_cache_stats(self) -> "Dict[str, Any]":
        """返回 LLM 响应缓存统计信息（命中/未命中/命中率）。"""
        return self._cache.stats()

    def clear_cache(self) -> int:
        """清空 LLM 响应缓存，返回删除条目数。"""
        return self._cache.clear()

    # ── 文本生成 ───────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        route_context: Optional[Dict[str, Any]] = None,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
        force_model: Optional[str] = None,
    ) -> GenerationResult:
        """调用 LLM 生成文本。

        Args:
            prompt: 输入提示词
            route_context: 路由上下文（默认 {"input_type": "text", "task_type": "qa"}）
            temperature: 温度参数。为 0 时启用响应缓存（确定性输出）
            max_tokens: 最大输出 token
            max_retries: 重试次数
            force_model: 强制使用指定模型名称，跳过路由规则

        Returns:
            GenerationResult：包含生成文本和调用元数据
        """
        ctx = route_context or {"input_type": "text", "task_type": "qa", "complexity": "standard"}

        # 确定性调用（temperature=0）：先查缓存
        if temperature == 0:
            cached = self._cache.get(prompt, ctx, force_model)
            if cached:
                return GenerationResult(
                    text=cached["text"],
                    selected_role=cached.get("selected_role", ""),
                    provider=cached.get("provider", ""),
                    model=cached.get("model", ""),
                    api_base_url=cached.get("api_base_url", ""),
                    matched_rule=cached.get("matched_rule", ""),
                    prompt_tokens=cached.get("prompt_tokens", 0),
                    completion_tokens=cached.get("completion_tokens", 0),
                )

        request = LLMRequest(prompt=prompt, route_context=ctx)
        try:
            response = self._provider.generate(
                request,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
                force_model=force_model,
            )
            result = GenerationResult(
                text=response.text,
                selected_role=response.selected_role or "",
                provider=response.provider or "",
                model=response.model or "",
                api_base_url=response.api_base_url or "",
                matched_rule=response.matched_rule or "",
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
            )
            # 确定性调用：写入缓存
            if temperature == 0:
                self._cache.put(prompt, ctx, force_model, response)
            return result
        except LLMProviderError as exc:
            logger.error("LLM 文本生成失败: %s", exc)
            raise

    # ── 多模态生成 ─────────────────────────────────────────────────

    def generate_multimodal(
        self,
        content_parts: List[Dict[str, Any]],
        route_context: Optional[Dict[str, Any]] = None,
        *,
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        """调用多模态 LLM 生成文本。

        Args:
            content_parts: 多模态内容列表（text / image_url 等）
            route_context: 路由上下文（默认 multimodal 路由）
            temperature: 温度参数
            max_retries: 重试次数

        Returns:
            生成文本
        """
        ctx = route_context or {
            "input_type": "multimodal",
            "task_type": "image_understanding",
            "complexity": "complex",
        }
        try:
            return self._provider.generate_multimodal(
                content_parts,
                ctx,
                temperature=temperature,
                max_retries=max_retries,
            )
        except LLMProviderError as exc:
            logger.error("LLM 多模态生成失败: %s", exc)
            raise

    # ── 异步文本生成 ───────────────────────────────────────────────

    async def generate_async(
        self,
        prompt: str,
        route_context: Optional[Dict[str, Any]] = None,
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        force_model: Optional[str] = None,
    ) -> GenerationResult:
        """异步调用 LLM 生成文本（使用 httpx async 或 ThreadPoolExecutor fallback）。

        Args 与 generate() 相同。需要 `pip install httpx` 获得最佳性能，
        否则自动回退到同步调用（在线程池中运行）。
        """
        import asyncio

        ctx = route_context or {"input_type": "text", "task_type": "qa", "complexity": "standard"}

        # 确定性调用：先查缓存
        if temperature == 0:
            cached = self._cache.get(prompt, ctx, force_model)
            if cached:
                return GenerationResult(
                    text=cached["text"],
                    selected_role=cached.get("selected_role", ""),
                    provider=cached.get("provider", ""),
                    model=cached.get("model", ""),
                    api_base_url=cached.get("api_base_url", ""),
                    matched_rule=cached.get("matched_rule", ""),
                    prompt_tokens=cached.get("prompt_tokens", 0),
                    completion_tokens=cached.get("completion_tokens", 0),
                )

        # 在默认 executor 中运行同步 generate（兼容现有 provider 实现）
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.generate(
                    prompt, ctx,
                    temperature=temperature, max_tokens=max_tokens,
                    force_model=force_model,
                ),
            )
            return result
        except LLMProviderError:
            raise

    # ── 快速访问（供已有习惯的旧代码过渡） ──────────────────────

    def get_base_model(self) -> EnvironmentConfiguredLLMProvider:
        """兼容旧接口：直接返回 provider 实例（推荐改用 get_provider）。"""
        return self._provider

    def get_adv_model(self) -> EnvironmentConfiguredLLMProvider:
        """兼容旧接口：直接返回 provider 实例（推荐改用 get_provider）。"""
        return self._provider
