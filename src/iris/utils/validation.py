"""轻量输入验证工具 —— 统一 CLI 输入的安全处理。

提供安全的类型转换、JSON 解析和必填字段校验，
避免各 handler 中分散的 try/except 和静默失败。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class ValidationError(ValueError):
    """输入验证失败。"""


def safe_int(value: Any, default: int, *, min_val: Optional[int] = None,
             max_val: Optional[int] = None) -> int:
    """安全转换整数，失败时返回默认值，可选范围检查。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None and result < min_val:
        return min_val
    if max_val is not None and result > max_val:
        return max_val
    return result


def safe_float(value: Any, default: float, *, min_val: Optional[float] = None,
               max_val: Optional[float] = None) -> float:
    """安全转换浮点数，失败时返回默认值。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if min_val is not None and result < min_val:
        return min_val
    if max_val is not None and result > max_val:
        return max_val
    return result


def safe_parse_json(text: str, *, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """安全解析 JSON 字符串，失败时返回 fallback（默认 {}）。"""
    if fallback is None:
        fallback = {}
    try:
        result = json.loads(text)
        if not isinstance(result, dict):
            return fallback
        return result
    except (json.JSONDecodeError, TypeError):
        return fallback


def validate_required_keys(data: Dict[str, Any], required: List[str],
                           *, label: str = "data") -> None:
    """校验字典包含所有必填键，缺少时抛出 ValidationError。"""
    missing = [k for k in required if k not in data or data[k] is None]
    if missing:
        raise ValidationError(f"{label} 缺少必填字段: {', '.join(missing)}")


def safe_get_str(data: Dict[str, Any], key: str, default: str = "") -> str:
    """安全获取字符串值。"""
    val = data.get(key, default)
    if val is None:
        return default
    return str(val).strip()


def safe_get_list(data: Dict[str, Any], key: str) -> List[Any]:
    """安全获取列表值。"""
    val = data.get(key, [])
    if isinstance(val, list):
        return val
    return []
