"""ASR 子系统 — 术语提取、热词生成、校正引擎、覆盖分析、反馈模型。

子模块:
  _types.py           — AsrTerm, AsrPromptVersion, CoverageReport, DictQualityReport, AsrCorrection
  extractor.py        — TermExtractor（术语提取 + LLM 误识别生成）
  hotwords.py         — LLMHotwordExtractor（热词表生成）
  formatter.py        — render_asr_prompt, format_hotwords_file, format_replace_dict
  prompt_optimizer.py — LLMPromptOptimizer
  corrector.py        — AsrCorrector（实时校正引擎）
  coverage.py         — 覆盖分析 + 噪音检测
  feedback.py         — 反馈数据模型 + JSONL 读写
  version.py          — 版本管理
"""

from ._types import (
    AsrTerm, AsrPromptVersion,
    CoverageReport, DictQualityReport, AsrCorrection,
)
from .extractor import TermExtractor
from .hotwords import LLMHotwordExtractor, hotwords_to_terms
from .formatter import render_asr_prompt, format_hotwords_file, format_replace_dict
from .prompt_optimizer import LLMPromptOptimizer
from .corrector import AsrCorrector, correct_text_static
from .coverage import (
    analyze_coverage, analyze_dict_quality,
    render_coverage_text, render_dict_quality_text,
)
from .feedback import (
    save_correction, load_corrections,
    extract_mappings_from_corrections, compute_hit_frequency,
    apply_feedback_to_dict,
)
from .version import (
    load_version, save_version, bump_version,
    determine_new_version, compute_fingerprint,
)

__all__ = [
    # 数据类型
    "AsrTerm", "AsrPromptVersion",
    "CoverageReport", "DictQualityReport", "AsrCorrection",
    # 核心类
    "TermExtractor", "LLMHotwordExtractor", "LLMPromptOptimizer", "AsrCorrector",
    # 格式化
    "render_asr_prompt", "format_hotwords_file", "format_replace_dict",
    # 热词
    "hotwords_to_terms",
    # 覆盖分析
    "analyze_coverage", "analyze_dict_quality",
    "render_coverage_text", "render_dict_quality_text",
    # 反馈
    "save_correction", "load_corrections",
    "extract_mappings_from_corrections", "compute_hit_frequency",
    "apply_feedback_to_dict",
    # 校正
    "correct_text_static",
    # 版本管理
    "determine_new_version", "load_version", "save_version",
    "bump_version", "compute_fingerprint",
]
