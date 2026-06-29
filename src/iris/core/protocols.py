"""核心抽象接口协议，用于依赖注入和单元测试。

LLMProvider 是项目中 LLM 调用的唯一抽象接口，
BaseLLMProvider、FakeLLMProvider、NullLLMProvider 均需满足此协议。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ── LLM 层 ────────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 调用接口 —— 项目中所有 LLM Provider 的唯一抽象契约。

    方法签名与 BaseLLMProvider 完全对齐：
      - generate(request, *, temperature, max_tokens, max_retries) -> response
      - generate_multimodal(content_parts, route_context, *, temperature, max_retries) -> str
    """

    def generate(
        self,
        request: Any,  # LLMRequest（避免循环导入，此处用 Any）
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> Any:  # LLMResponse
        """生成文本。"""
        ...

    def generate_multimodal(
        self,
        content_parts: List[dict],
        route_context: Dict[str, Any],
        *,
        temperature: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> str:
        """生成多模态内容。"""
        ...

    def has_credentials_for_role(self, role: str) -> bool:
        """检查指定角色是否有可用凭证。"""
        ...

    def resolve(self, route_context: Dict[str, Any]) -> Any:  # RoutingDecision
        """解析路由决策（不实际调用 LLM）。"""
        ...


# ── 记忆层 ────────────────────────────────────────────────────────


@runtime_checkable
class MemoryStore(Protocol):
    """记忆存储接口。"""

    def load(self) -> Dict[str, Any]:
        """加载记忆。"""
        ...

    def save(self, payload: Dict[str, Any]) -> None:
        """保存记忆。"""
        ...


# ── 提示模板层 ────────────────────────────────────────────────────


@runtime_checkable
class PromptLoader(Protocol):
    """提示模板加载接口。"""

    def render(self, template_name: str, variables: Dict[str, Any]) -> str:
        ...
