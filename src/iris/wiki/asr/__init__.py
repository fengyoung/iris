"""ASR 提示词生成子系统 — 术语提取、热词生成、Prompt 优化、版本管理。

子模块:
  _types.py           — AsrTerm, AsrPromptVersion（零依赖数据类型）
  extractor.py        — TermExtractor（术语提取 + LLM 误识别生成）
  hotwords.py         — LLMHotwordExtractor（热词表生成）
  formatter.py        — render_asr_prompt, format_hotwords_file
  prompt_optimizer.py — LLMPromptOptimizer
  version.py          — 版本管理（load/save/bump/determine_new_version）
"""

from ._types import AsrTerm, AsrPromptVersion
from .extractor import TermExtractor
from .hotwords import LLMHotwordExtractor, hotwords_to_terms
from .formatter import render_asr_prompt, format_hotwords_file, format_replace_dict
from .prompt_optimizer import LLMPromptOptimizer
from .version import (
    load_version,
    save_version,
    bump_version,
    determine_new_version,
    compute_fingerprint,
)

__all__ = [
    # 数据类型
    "AsrTerm",
    "AsrPromptVersion",
    # 核心类
    "TermExtractor",
    "LLMHotwordExtractor",
    "LLMPromptOptimizer",
    # 格式化
    "render_asr_prompt",
    "format_hotwords_file",
    "format_replace_dict",
    # 热词
    "hotwords_to_terms",
    # 版本管理
    "determine_new_version",
    "load_version",
    "save_version",
    "bump_version",
    "compute_fingerprint",
]
