"""深度评估数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ReferenceEntry:
    """从 Wiki 页面 ## 参考来源 解析出的单条引用。"""
    raw: str
    source_path: str
    line_number: Optional[int]
    description: str
    resolved_chunk: Optional[str] = None
    resolved_context: Optional[str] = None


@dataclass
class AccuracyVerdict:
    """单条引用的准确性判定结果。"""
    reference: ReferenceEntry
    verdict: str               # consistent / inconsistent / unverifiable / source_missing
    detail: str = ""


@dataclass
class CoverageGap:
    """全面性评估中发现的遗漏项。"""
    source_path: str
    missing_topic: str
    detail: str = ""


@dataclass
class PageDeepResult:
    """单个 Wiki 页面的深度评估结果。"""
    title: str
    page_type: str
    path: str

    accuracy_verdicts: List[AccuracyVerdict] = field(default_factory=list)
    accuracy_rate: Optional[float] = None

    coverage_gaps: List[CoverageGap] = field(default_factory=list)
    comprehensiveness_note: str = ""


@dataclass
class DeepEvalResult:
    """深度评估的完整输出。"""
    evaluated_at: str
    total_pages: int

    total_references: int
    consistent_count: int
    inconsistent_count: int
    unverifiable_count: int
    source_missing_count: int
    overall_accuracy_rate: Optional[float]

    pages_with_gaps: int
    total_gaps: int
    overall_comprehensiveness_note: str

    page_results: List[PageDeepResult] = field(default_factory=list)
    top_inconsistent_pages: List[dict] = field(default_factory=list)
    recommendations: List[dict] = field(default_factory=list)
