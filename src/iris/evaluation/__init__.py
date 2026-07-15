"""Wiki 深度评估 — 内容准确性 + 全面性校验。

从 iris2 迁移，提供两个核心能力：
1. 内容准确性校验：逐条核对 Wiki 引用描述与源文档 chunk 是否一致
2. 内容全面性校验：通过路径相似度发现同主题下未引用的源文件
"""

from .deep_eval import (
    DeepEvalResult,
    DeepEvaluator,
    PageDeepResult,
    AccuracyVerdict,
    CoverageGap,
    ReferenceEntry,
    SourceLocator,
    deep_eval_result_to_json,
    print_deep_eval_pretty,
)

__all__ = [
    "DeepEvalResult",
    "DeepEvaluator",
    "PageDeepResult",
    "AccuracyVerdict",
    "CoverageGap",
    "ReferenceEntry",
    "deep_eval_result_to_json",
    "print_deep_eval_pretty",
]
