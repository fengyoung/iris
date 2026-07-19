"""ASR 文本特征检测：判断剪贴板内容是否为 vocotype 语音识别输出。"""

from __future__ import annotations

import re

# ASR 文本特征阈值
_MIN_ASR_LENGTH = 5
_MAX_ASR_LENGTH = 500
_MIN_CHINESE_RATIO = 0.3

# 非 ASR 文本特征（代码、URL、Markdown 等）
_CODE_PATTERNS = re.compile(
    r"[{};]|def\s|import\s|class\s|function\s|http[s]?://|"
    r"```|^#{1,6}\s|^\*\s|^\d+\.\s|^\-\s"
)


def _count_chinese(text: str) -> int:
    """统计中文字符数。"""
    return sum(1 for ch in text if "一" <= ch <= "鿿")


def _is_asr_text(text: str) -> bool:
    """判断文本是否像是 vocotype ASR 输出。

    检测维度：
    - 中文为主（>30%）
    - 长度在 5-500 之间
    - 无代码/URL/Markdown 特征
    """
    if not text:
        return False

    length = len(text)
    if length < _MIN_ASR_LENGTH or length > _MAX_ASR_LENGTH:
        return False

    chinese_count = _count_chinese(text)
    if chinese_count / max(length, 1) < _MIN_CHINESE_RATIO:
        return False

    if _CODE_PATTERNS.search(text):
        return False

    return True
