"""校正前后文本的词级差异对比（用于反馈日志与终端展示）。"""

from __future__ import annotations

import difflib
from typing import List

# 最多展示的修改处数
_MAX_CHANGES = 8


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _scan_run(text: str, start: int, pred) -> int:
    """从 start 起向后扫描满足 pred 的连续字符，返回终止下标。"""
    j = start
    while j < len(text) and pred(text[j]):
        j += 1
    return j


def _tokenize(text: str) -> List[str]:
    """中文逐字、英文按词、空白连续、其余（标点/数字）单字成 token。"""
    tokens: List[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if _is_cjk(ch):
            tokens.append(ch)
            i += 1
        elif ch.isalpha():
            j = _scan_run(text, i, str.isalpha)
            tokens.append(text[i:j])
            i = j
        elif ch.isspace():
            j = _scan_run(text, i, str.isspace)
            tokens.append(text[i:j])
            i = j
        else:
            tokens.append(ch)
            i += 1
    return tokens


def _describe_opcode(tag: str, old_str: str, new_str: str) -> str:
    """把一个 difflib opcode 渲染为「旧→新」或「⊕新」，无可展示内容返回空串。"""
    if tag == "replace":
        if old_str and new_str:
            return f"{old_str}→{new_str}"
        if new_str:
            return f"⊕{new_str}"
    elif tag == "insert" and new_str:
        return f"⊕{new_str}"
    return ""


def _diff_changes(before: str, after: str) -> List[str]:
    """对比校正前后的文本差异，词级比较。"""
    tokens_before = _tokenize(before)
    tokens_after = _tokenize(after)

    changes: List[str] = []
    matcher = difflib.SequenceMatcher(None, tokens_before, tokens_after)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_str = "".join(tokens_before[i1:i2]).strip()
        new_str = "".join(tokens_after[j1:j2]).strip()
        desc = _describe_opcode(tag, old_str, new_str)
        if desc:
            changes.append(desc)

    return changes[:_MAX_CHANGES]
