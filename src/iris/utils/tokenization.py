"""Tokenization 工具：分词、估算 token 数、文本分块等。"""

from __future__ import annotations

import re
from typing import List

# ── 共享分词定义 ────────────────────────────────────────────
# searcher / wiki/searcher / chunker / planner 统一引用，
# 避免跨模块重复定义。

# 中英混合分词正则（字母数字 + CJK 统一表意文字）
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-一-鿿]+")

# 中英文混合场景的粗略 token 估算系数
# 英文约 1 token / 4 chars，中文约 1 token / 1.5 chars
_CHINESE_RE = re.compile(r"[一-鿿]")


def tokenize(text: str) -> List[str]:
    """对文本分词，返回 token 列表（小写化）。"""
    return TOKEN_RE.findall(text.lower())


def count_chinese(text: str) -> int:
    """统计文本中的中文字符数（含 CJK + CJK Extension A）。"""
    return sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')


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
