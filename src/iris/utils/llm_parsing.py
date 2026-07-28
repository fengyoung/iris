"""LLM 响应解析工具 — 统一处理代码块剥离和 JSON 提取。

消除 term_extractor / generator / analysis 等模块中的重复解析逻辑。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


# ```json / ```markdown / ``` 包裹剥离
_FENCE_PATTERN = re.compile(r"```(?:json|markdown|md)?\s*\n?", re.IGNORECASE)


def strip_code_fence(text: str) -> str:
    """剥离 LLM 响应前后的 ``` 代码块标记，返回纯净内容。

    >>> strip_code_fence("```json\\n{\"key\": 1}\\n```")
    '{"key": 1}'
    """
    cleaned = text.strip()
    # 去除开头的 ```json / ```markdown / ```
    cleaned = _FENCE_PATTERN.sub("", cleaned, count=1)
    # 去除末尾的 ```
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()


def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """尝试将 LLM 响应解析为 JSON dict，失败返回 None。

    自动剥离代码块标记并处理常见截断情况。
    """
    cleaned = strip_code_fence(text)
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict):
            return result
        return None
    except json.JSONDecodeError:
        # 尝试修复截断的 JSON：补全末尾 }]
        for suffix in ["}]", "}", "]"]:
            try:
                result = json.loads(cleaned + suffix)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue
        return None


def extract_json_object(text: str) -> Optional[str]:
    """括号计数提取最外层 JSON 对象（用于 LLM 响应中混有非 JSON 文本的情况）。

    返回从第一个 '{' 到匹配 '}' 的子字符串，失败返回 None。
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def extract_json_from_text(text: str, key: str) -> Optional[Dict[str, Any]]:
    """从 LLM 文本响应中按正则提取 JSON 对象（包含指定 key）。

    用于 LLM 在 JSON 前后混入解释文字的场景。
    """
    # 尝试匹配 {"key": ... } 或 [{ ... }]
    pattern = re.compile(
        r'\{\s*"' + re.escape(key) + r'"\s*:\s*[\["].*?\}',
        re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        return try_parse_json(match.group(0))
    return None
