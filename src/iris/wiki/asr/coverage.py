"""ASR 覆盖分析 — 纯本地，零 LLM 调用。

分析 build-asr-prompt 产出的热词列表和替换词典质量：
- 热词覆盖率：人物 / 项目 / 概念的覆盖情况
- 噪音词检测：通用词、超短英文、超长词、句子片段
- 替换词典格式检查：括号注释、冲突映射

用法:
    from iris.wiki.asr.coverage import analyze_coverage, analyze_dict_quality

    report = analyze_coverage(hotwords, wiki_pages)
    print(render_coverage_text(report))
"""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple

from ._types import AsrTerm, CoverageReport, DictQualityReport
from .._constants import get_wiki_prefix
from iris.utils.tokenization import count_chinese as _count_chinese

# ── 噪音检测规则 ──────────────────────────────────────────

# 常见的非领域通用词（在不同语境下含义不变，ASR 准确率本就很高）
_COMMON_NOISE_PATTERNS = re.compile(
    r"^(成功|联想|微调|能力圈|流水线|能力建设|盲测|"
    r"组织保障|需求梳理|工程实现|差异分析|技术路径|"
    r"持续深化|数据对齐|流量分配|模型训练|模型迭代)$"
)

# 超短纯英文（≤2 字符）— paraformer 对短英文识别率本就较高
_SHORT_ENGLISH_RE = re.compile(r"^[A-Za-z]{1,2}$")

# 句子/短语片段特征词
_SENTENCE_FRAGMENT_WORDS = {"的", "是", "在", "与", "和", "及", "或"}

# 高频中文单字——绝对不应作为误识别映射的目标（命中会导致大量误伤）
_COMMON_CHINESE_CHARS = frozenset(
    "的了一是在我不有人他这为之说来个大们上到地子中着你生时出道也自"
    "下会以可要对就学过能去都成而然把所以那从没看用此又样多但起么方"
    "如小种家后前因如年日法被当开已间将力很现理性等其点最表"
)


def is_dangerous_mapping(mis_word: str) -> bool:
    """判断误识别词是否为高危映射目标。

    - 单字高频中文：如"在""是""的"——存在于几乎所有中文文本中
    - 纯数字/标点
    此类映射会在大比例文本中产生误伤，不应加入替换词典。
    """
    return len(mis_word) == 1 and mis_word in _COMMON_CHINESE_CHARS


def _is_noise_word(word: str) -> Tuple[bool, str]:
    """判断是否为噪音热词。

    Returns:
        (is_noise, reason): 是否为噪音词及其原因
    """
    if not word or len(word) < 2:
        return True, "单字/空词"

    # 纯数字/标点
    if re.match(r"^[\d\s.%+\-／／]+$", word):
        return True, "纯数字/符号"

    # 超短英文（≤2字符，如 AI、IE、LV）
    if _SHORT_ENGLISH_RE.match(word):
        return True, "超短英文（≤2 字符，ASR 准确率高）"

    # 超长中文（>12 汉字）
    chinese_count = _count_chinese(word)
    if chinese_count > 12:
        return True, f"超长词（{chinese_count} 汉字，ASR 热词 bias 无效）"

    # 超长字符（>20 字符）
    if len(word) > 20:
        return True, f"超长词（{len(word)} 字符）"

    # 通用高频词
    if _COMMON_NOISE_PATTERNS.match(word):
        return True, "通用高频词（非领域专有）"

    # 句子片段特征
    if len(word) > 12:
        fragment_count = sum(1 for ch in word if ch in _SENTENCE_FRAGMENT_WORDS)
        if fragment_count >= 2:
            return True, "疑似句子片段"

    return False, ""


def _normalize_name(name: str) -> str:
    """规范化名称用于对比（去空格 + 小写）。"""
    return name.lower().replace(" ", "").replace(" ", "")


# ── 覆盖分析 ──────────────────────────────────────────────


def analyze_coverage(
    hotwords: List[str],
    wiki_pages: List,  # List[WikiPageInfo] — 避免循环导入
    max_slots: int = 500,
) -> CoverageReport:
    """对比热词列表与 Wiki 页面，计算覆盖率并检测噪音。

    Args:
        hotwords: 热词字符串列表（来自 build-asr-prompt Stage 1）
        wiki_pages: WikiPageInfo 列表（按 person/concept/project/domain 分类）
        max_slots: 热词槽位上限（vocotype 默认 500，可从 asr_profiles.json 读取后传入）

    Returns:
        CoverageReport: 覆盖分析报告
    """
    report = CoverageReport(
        hotword_count=len(hotwords),
        max_slots=max_slots,
    )

    # 分类 Wiki 页面
    person_names: Set[str] = set()
    project_names: Set[str] = set()
    concept_names: Set[str] = set()

    for p in wiki_pages:
        name = (p.title or "").strip()
        if not name:
            continue
        ptype = p.page_type
        if ptype == "person":
            # 人物名取文件名去前缀
            stem = p.path.stem
            prefix = get_wiki_prefix(ptype)
            if stem.startswith(prefix):
                name = stem[len(prefix):]
            person_names.add(name)
        elif ptype == "project":
            project_names.add(name)
        elif ptype == "concept":
            concept_names.add(name)

    report.persons_total = len(person_names)
    report.projects_total = len(project_names)
    report.concepts_total = len(concept_names)

    # 规范化热词
    hotword_normalized: Dict[str, str] = {}
    for hw in hotwords:
        key = _normalize_name(hw)
        if key not in hotword_normalized:
            hotword_normalized[key] = hw

    # 检查覆盖
    for name in person_names:
        if _normalize_name(name) in hotword_normalized:
            report.persons_covered += 1
        else:
            report.persons_missing.append(name)

    for name in project_names:
        if _normalize_name(name) in hotword_normalized:
            report.projects_covered += 1
        else:
            report.projects_missing.append(name)

    for name in concept_names:
        if _normalize_name(name) in hotword_normalized:
            report.concepts_covered += 1
        else:
            report.concepts_missing.append(name)

    # 噪音检测
    for hw in hotwords:
        is_noise, reason = _is_noise_word(hw)
        if is_noise:
            report.noise_words.append(f"{hw}（{reason}）")

    # 超长词
    for hw in hotwords:
        if _count_chinese(hw) > 12:
            report.long_words.append(hw)

    # 槽位效率（对噪音词去重，防止同一热词因多次出现导致 effective 为负）
    unique_noise = {w.split("（")[0] for w in report.noise_words}
    effective = max(0, report.hotword_count - len(unique_noise))
    report.slot_efficiency = effective / report.max_slots if report.max_slots > 0 else 0.0

    return report


# ── 替换词典质量 ──────────────────────────────────────────


def analyze_dict_quality(terms: List[AsrTerm]) -> DictQualityReport:
    """纯规则检查替换词典的格式和冲突。

    Args:
        terms: 已填充 mis_asr 的 AsrTerm 列表

    Returns:
        DictQualityReport: 质量检查报告
    """
    report = DictQualityReport()

    # 计算总映射数
    total = 0
    for t in terms:
        total += len(t.mis_asr)
    report.total_rules = total

    # 格式检查：含括号注释的误识别
    bracket_pattern = re.compile(r"[（(].*?[）)]")
    for t in terms:
        for mis in t.mis_asr:
            if bracket_pattern.search(mis):
                report.format_errors.append(
                    f"含括号注释: {t.term}={t.category} → '{mis}'"
                )

    # 冲突检测：不同正确词映射到同一误识别
    mis_to_terms: Dict[str, List[str]] = {}
    for t in terms:
        for mis in t.mis_asr:
            key = _normalize_name(mis)
            if key not in mis_to_terms:
                mis_to_terms[key] = []
            mis_to_terms[key].append(t.term)

    for mis_key, term_list in mis_to_terms.items():
        unique_terms = list(set(term_list))
        if len(unique_terms) > 1:
            # 取第一个 mapped mis_asr 原文
            example_mis = mis_key
            for t in terms:
                for m in t.mis_asr:
                    if _normalize_name(m) == mis_key:
                        example_mis = m
                        break
            report.conflicting_pairs.append(
                (example_mis, unique_terms[0], unique_terms[1])
            )

    # 高危映射检测：误识别词为通用高频单字
    for t in terms:
        for mis in t.mis_asr:
            if is_dangerous_mapping(mis):
                report.dangerous_mappings.append(
                    f"{mis}→{t.term} [{t.category}]"
                )

    # 类别分布
    cat_counts: Dict[str, int] = {}
    for t in terms:
        cat = t.category
        cat_counts[cat] = cat_counts.get(cat, 0) + len(t.mis_asr)
    report.category_distribution = cat_counts

    return report


# ── 渲染 ──────────────────────────────────────────────────


def render_coverage_text(report: CoverageReport) -> str:
    """将覆盖分析报告渲染为人类可读文本。"""
    lines = [
        "=" * 50,
        "ASR 覆盖分析",
        "=" * 50,
        f"热词槽位: {report.hotword_count}/{report.max_slots} "
        f"({report.slot_efficiency:.1%})",
        "",
        f"人物覆盖: {report.persons_covered}/{report.persons_total} "
        f"({_pct(report.persons_covered, report.persons_total)})",
    ]
    if report.persons_missing:
        lines.append(f"  未覆盖: {', '.join(report.persons_missing[:20])}")
        if len(report.persons_missing) > 20:
            lines.append(f"          ... 及 {len(report.persons_missing) - 20} 个")

    lines.append("")
    lines.append(
        f"项目覆盖: {report.projects_covered}/{report.projects_total} "
        f"({_pct(report.projects_covered, report.projects_total)})"
    )
    if report.projects_missing:
        lines.append(f"  未覆盖: {', '.join(report.projects_missing[:10])}")

    lines.append("")
    lines.append(
        f"概念覆盖: {report.concepts_covered}/{report.concepts_total} "
        f"({_pct(report.concepts_covered, report.concepts_total)})"
    )
    if report.concepts_missing:
        lines.append(f"  未覆盖: {', '.join(report.concepts_missing[:10])}")

    if report.noise_words:
        lines.append("")
        lines.append(f"噪音词: {len(report.noise_words)} 个")
        for nw in report.noise_words[:15]:
            lines.append(f"  · {nw}")
        if len(report.noise_words) > 15:
            lines.append(f"  ... 及 {len(report.noise_words) - 15} 个")

    if report.long_words:
        lines.append("")
        lines.append(f"超长词 (>12 汉字): {len(report.long_words)} 个")
        for lw in report.long_words[:10]:
            lines.append(f"  · {lw}")

    lines.append("")
    lines.append(f"槽位效率: {report.slot_efficiency:.1%}")
    return "\n".join(lines)


def render_dict_quality_text(report: DictQualityReport) -> str:
    """将替换词典质量报告渲染为人类可读文本。"""
    lines = [
        "=" * 50,
        "ASR 替换词典质量检查",
        "=" * 50,
        f"总映射数: {report.total_rules}",
        f"格式错误: {len(report.format_errors)} 条",
    ]
    if report.format_errors:
        for fe in report.format_errors[:10]:
            lines.append(f"  · {fe}")

    lines.append(f"冲突映射: {len(report.conflicting_pairs)} 组")
    for mis, t1, t2 in report.conflicting_pairs[:10]:
        lines.append(f"  · '{mis}' → '{t1}' vs '{t2}'")

    if report.dangerous_mappings:
        lines.append(f"高危映射: {len(report.dangerous_mappings)} 条")
        for dm in report.dangerous_mappings[:15]:
            lines.append(f"  · {dm}")

    if report.category_distribution:
        lines.append("")
        lines.append("类别分布:")
        for cat, count in sorted(report.category_distribution.items()):
            lines.append(f"  {cat}: {count}")

    return "\n".join(lines)


def _pct(covered: int, total: int) -> str:
    """安全百分比。"""
    if total == 0:
        return "N/A"
    return f"{covered / total:.1%}"
