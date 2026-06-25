"""核心抽象接口协议，用于依赖注入和单元测试。

仅保留非知识库相关的协议。
检索/Embedding/Wiki 相关协议将在步骤 2 添加。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ── LLM 层 ────────────────────────────────────────────────────────


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 调用接口。"""

    def generate(
        self,
        prompt: str,
        route_context: Dict[str, Any],
        **kwargs,
    ) -> str:
        """生成文本。"""
        ...

    def generate_multimodal(
        self,
        content_parts: List[dict],
        route_context: Dict[str, Any],
    ) -> str:
        """生成多模态内容。"""
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
