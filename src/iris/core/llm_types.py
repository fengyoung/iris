"""LLM 核心数据类型 — 请求/响应 dataclass。

从 llm/provider.py 迁移至 core/，消除 core→llm 依赖，
同时消除 protocols.py 中为回避循环导入而使用的 Any 类型标注。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


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
    prompt_tokens: int = 0
    completion_tokens: int = 0
