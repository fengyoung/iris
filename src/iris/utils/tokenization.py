"""Tokenization 工具：估算 token 数、文本分块等。"""

from __future__ import annotations

import re

# 中英文混合场景的粗略 token 估算系数
# 英文约 1 token / 4 chars，中文约 1 token / 1.5 chars
_CHINESE_RE = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（非精确，适用于上下文预算控制）。"""
    chinese_chars = len(_CHINESE_RE.findall(text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4) + 1


def truncate_by_tokens(text: str, max_tokens: int) -> str:
    """按 token 预算截断文本，保留完整句子。"""
    if estimate_tokens(text) <= max_tokens:
        return text

    # 按句号/换行分段，逐段追加直到超预算
    segments = re.split(r"(?<=[。\n])", text)
    result = []
    budget = max_tokens

    for seg in segments:
        cost = estimate_tokens(seg)
        if cost >= budget:
            remaining_chars = int(budget * 1.5)
            if remaining_chars > 10:
                result.append(seg[:remaining_chars] + "\n\n...（截断）")
            break
        result.append(seg)
        budget -= cost

    return "".join(result)
