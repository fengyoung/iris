"""ASR 子系统共享数据类型 — 无外部依赖，避免循环导入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class AsrTerm:
    """ASR 校正用术语条目。

    Attributes:
        term: 正确写法，如 "张三"、"BM25"
        category: 术语类别，person | concept | project | domain_term
        context: 简短说明，如 "算法工程师, Alpha项目"
        mis_asr: 常见 ASR 误识别列表，由 generate_misreadings() 填充
    """
    term: str
    category: str
    context: str
    mis_asr: List[str] = field(default_factory=list)


@dataclass
class AsrPromptVersion:
    """ASR 提示词版本信息。

    Attributes:
        version: 三段式版本号，如 "1.0.0"
        generated_at: ISO 8601 时间戳
        wiki_page_count: 来源 Wiki 页面总数
        term_count: 提取的术语总数
        fingerprint: Wiki 内容指纹（SHA-256 前16位 hex）
    """
    version: str
    generated_at: str
    wiki_page_count: int
    term_count: int
    fingerprint: str
    prompt_text: str = ""


# ── Phase 0 新增类型 ──────────────────────────────────────────


@dataclass
class CoverageReport:
    """ASR 热词覆盖分析报告 — 纯本地计算。

    Attributes:
        hotword_count: 当前热词总数
        max_slots: 热词配额上限（默认 500，可通过 analyze_coverage 参数覆盖）
        persons_covered: 已覆盖人物数
        persons_total: Wiki 人物页面总数
        persons_missing: 未覆盖的人名列表
        projects_covered: 已覆盖项目数
        projects_total: Wiki 项目页面总数
        projects_missing: 未覆盖的项目名列表
        concepts_covered: 已覆盖概念数
        concepts_total: Wiki 概念页面总数
        concepts_missing: 未覆盖的概念名列表
        noise_words: 疑似噪音词（通用词、超短英文、超长词）
        long_words: 超长词条（>12 汉字，ASR 热词 bias 对长词无效）
        slot_efficiency: 槽位有效利用率 = (总热词 - 噪音词) / max_slots
    """
    hotword_count: int = 0
    max_slots: int = 500
    persons_covered: int = 0
    persons_total: int = 0
    persons_missing: List[str] = field(default_factory=list)
    projects_covered: int = 0
    projects_total: int = 0
    projects_missing: List[str] = field(default_factory=list)
    concepts_covered: int = 0
    concepts_total: int = 0
    concepts_missing: List[str] = field(default_factory=list)
    noise_words: List[str] = field(default_factory=list)
    long_words: List[str] = field(default_factory=list)
    slot_efficiency: float = 0.0


@dataclass
class DictQualityReport:
    """ASR 替换词典质量检查报告 — 纯本地规则检查。

    Attributes:
        total_rules: 替换映射总条数
        format_errors: 格式异常的映射（如含括号注释 "(误为XXX)"）
        conflicting_pairs: 冲突的映射对 (term, mis1, mis2)
        category_distribution: 各类别映射数统计
    """
    total_rules: int = 0
    format_errors: List[str] = field(default_factory=list)
    conflicting_pairs: List[Tuple[str, str, str]] = field(default_factory=list)
    dangerous_mappings: List[str] = field(default_factory=list)
    category_distribution: Dict[str, int] = field(default_factory=dict)


@dataclass
class AsrCorrection:
    """单次 ASR 校正记录 — 由 iris-asr-corrector 实时写入。

    Attributes:
        timestamp: ISO 8601 时间戳
        raw_text: vocotype 原始 ASR 输出
        fast_corrected: Step 1 替换词典校正结果
        full_corrected: Step 2 LLM 校正结果
        mode: 校正模式 "fast" | "full"
        corrections_applied: 命中的替换规则列表 ["误→正", ...]
    """
    timestamp: str = ""
    raw_text: str = ""
    fast_corrected: str = ""
    full_corrected: str = ""
    mode: str = "full"
    corrections_applied: List[str] = field(default_factory=list)
    llm_time_ms: int = 0
    context_ab: Optional[Dict[str, Any]] = None
    """A/B 对比结果（--context-ab 模式）。

    None 表示未启用 A/B 对比，否则包含：
        - context_sentence_count: 上下文窗口中的句子数
        - with_context: 带上下文的 LLM 校正结果
        - without_context: 不带上下文的 LLM 校正结果
        - diff: with 和 without 之间的差异列表 [\"+添加\", \"-删除\"]
    """
