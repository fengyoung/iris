"""ASR 术语清洗工具 — 正则常量、噪音过滤、Markdown 清理。

从 term_extractor.py 中抽出的纯函数工具集，供 term_extractor 和
asr_hotwords 复用，零外部依赖。
"""

from __future__ import annotations

import re

# ── 正则常量 ──────────────────────────────────────────────

_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")

# 排除的章节标题（无 ASR 价值）
_SKIP_HEADINGS = frozenset({
    "摘要", "正文", "概述", "总结", "背景", "目标", "结论",
    "参考来源", "关联页面", "当前结论", "相关依据",
})

# 领域术语噪音模式：纯数字、百分比、数值+单位、单字母等
_NOISE_TERM_RE = re.compile(
    r"^\d+[分%]?$|"              # "100", "100分"
    r"^\d+\.\d+[%+]?$|"         # "0.38", "48.14%", "55%+"
    r"^\d+\s*张图片|"            # "2000 张图片"
    r"^\d+\s*台手机|"            # "2000 台手机"
    r"^\d+\s*(台|个|张|款|套|分|%|款)\s*$|"  # "19 台", "1个"
    r"\d+\s*[/%]\s*\d+[+]?$|"   # "22/33", "55%+"
    r"^[①②③④⑤⑥⑦⑧⑨⑩]|"       # 列表编号开头
    r"^[A-Za-z0-9]{,2}$|"       # "V21", "h1" 等
    r"^V?\d+(?:\.\d+)+$|"       # "1.0.1", "V2.1"
    r"^\d+年\d+月\d+日$|"       # "2026年6月2日" — 日期
    r"^\d+个(百分)?点$|"         # "5个百分点", "1个百分点", "3百分点"
    r"是\s*.*的\s*|"            # "脏污是...的" — 判断句型
    r"^\d+/\d+[-–]\d+/\d+$|"    # "6/24-6/27"
    r"^\d+/\d+[（(]\w+[）)]$|"  # "6/26（周五）"
    r"^\d+/\d+\s*前$|"          # "6/30 前"
    r"^\d+[%+]\+$|"             # "55%+"（百分比+加号）
    r"^[A-Za-z]+[,，]\s*\d+$"   # "Week, 10" 类
)


def is_noise_term(term: str) -> bool:
    """判断术语是否为噪音（数字/百分比/短字母等）。"""
    return bool(_NOISE_TERM_RE.match(term.strip()))


def clean_markup(text: str) -> str:
    """清理文本中的 Markdown / Wiki 语法标记。

    - **粗体** → 粗体
    - [[链接]] → 链接
    - [[链接|显示名]] → 显示名
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\[\[([^\[\]]+?)\|([^\[\]]+?)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\[\]]+?)\]\]", r"\1", text)
    return text


def truncate_context(text: str, max_chars: int = 60) -> str:
    """截断 context 到指定长度（优先在句号处断句）。

    先清理 Markdown/Wiki 标记再截断，确保长度按实际文字计算。
    """
    if not text:
        return ""
    clean = clean_markup(text.strip())
    if len(clean) <= max_chars:
        return clean
    for sep in ("。", "；"):
        idx = clean.find(sep)
        if 0 < idx <= max_chars:
            return clean[:idx + 1]
    return clean[:max_chars].rstrip() + "…"
