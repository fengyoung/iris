"""ASR 输出格式化 — 热词文件、替换词典、校正 Prompt 的渲染输出。

提供 standard / compact 两种输出格式。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from ._types import AsrTerm, AsrPromptVersion


from iris.utils.tokenization import (  # noqa: E402 — 向后兼容别名
    count_chinese as _count_chinese,
    exceeds_char_limit as _exceeds_char_limit,
)



def format_hotwords_file(hotwords: List[str], output_path: str) -> str:
    """将热词列表写入 txt 文件（每行一个，自动去重）。

    Args:
        hotwords: 热词列表
        output_path: 输出文件路径

    Returns:
        写入的文件路径
    """
    # 防御性去重，保持顺序
    seen = set()
    unique = []
    for w in hotwords:
        key = w.lower().replace(" ", "")
        if key not in seen:
            seen.add(key)
            unique.append(w)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(unique) + "\n", encoding="utf-8")
    return str(path)


def format_replace_dict(
    terms: List[AsrTerm],
    output_path: str,
    max_mappings: int = 2000,
    max_chars: int = 20,
) -> str:
    """将术语+误识别映射输出为替换词典 JSON。

    replace_map 格式：{{"误识别": "正确写法", ...}}

    过滤规则：
    - 错误词和正确词均不超过 20 字符或 10 个中文字
    - 误识别词不能是通用高频字（如"在""是"），避免大面积误伤

    Args:
        terms: 已填充 mis_asr 的术语列表
        output_path: 输出文件路径
        max_mappings: 最多映射条数（默认 2000，可通过 profile 配置覆盖）
        max_chars: 误识别和正确词的最大字符数

    Returns:
        写入的文件路径
    """
    from iris.wiki.asr.coverage import is_dangerous_mapping

    replace_map = {}
    added = set()
    dangerous_skipped = 0
    for t in terms:
        if _exceeds_char_limit(t.term, max_total=max_chars, max_chinese=10):
            continue
        for mis in t.mis_asr:
            if not mis:
                continue
            if is_dangerous_mapping(mis):
                dangerous_skipped += 1
                continue
            # 错误詞也检查长度
            if _exceeds_char_limit(mis, max_total=max_chars, max_chinese=10):
                continue
            if mis not in added and mis != t.term:
                replace_map[mis] = t.term
                added.add(mis)
                if len(replace_map) >= max_mappings:
                    break
        if len(replace_map) >= max_mappings:
            break
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"replace_map": replace_map}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


# ═══════════════════════════════════════════════════════════════════
# Phase 3: LLM Prompt 优化器
# ═══════════════════════════════════════════════════════════════════


def render_asr_prompt(
    terms: List[AsrTerm],
    version: AsrPromptVersion,
    output_format: str = "standard",
) -> str:
    """将术语列表渲染为 ASR 校正系统提示词。

    Args:
        terms: 已填充 mis_asr 的术语列表
        version: 版本信息
        output_format: "standard" (Markdown 表格) 或 "compact" (纯文本)

    Returns:
        完整 prompt 字符串，可直接复制到 vocotype LLM 校正配置中
    """
    if output_format in ("compact",):
        return _render_compact(terms, version)
    return _render_standard(terms, version)


def _render_standard(terms: List[AsrTerm], version: AsrPromptVersion) -> str:
    """标准格式：Markdown 表格，便于人类阅读和微调。"""
    # 按类别分组
    persons = [t for t in terms if t.category == "person"]
    concepts = [t for t in terms if t.category == "concept"]
    projects = [t for t in terms if t.category == "project"]
    domain_terms = [t for t in terms if t.category == "domain_term"]

    lines = [
        "你是 ASR 语音转写后处理校正助手。你的任务是将语音转写文本校正为准确、流畅的书面中文。",
        "严格遵守以下校正规则。仅输出校正后的文本，不要添加任何解释、说明或前缀。",
        "",
    ]

    # 人名词典
    if persons:
        lines.append("## 人名词典")
        lines.append("以下为工作场景中的人名，语音转写中可能被误识别为同音/近音字：")
        lines.append("")
        lines.append("| 正确写法 | 说明 | 常见 ASR 误识别 |")
        lines.append("|---------|------|----------------|")
        for t in persons:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 术语词典
    if concepts:
        lines.append("## 术语词典")
        lines.append("以下为工作领域的专业术语，语音转写中可能被误识别：")
        lines.append("")
        lines.append("| 正确写法 | 说明 | 常见 ASR 误识别 |")
        lines.append("|---------|------|----------------|")
        for t in concepts:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 项目名词典
    if projects:
        lines.append("## 项目名词典")
        lines.append("以下为工作场景中的项目名称：")
        lines.append("")
        lines.append("| 正确写法 | 说明 | 常见 ASR 误识别 |")
        lines.append("|---------|------|----------------|")
        for t in projects:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 领域专有名词
    if domain_terms:
        lines.append("## 领域专有名词")
        lines.append("")
        lines.append("| 正确写法 | 来源领域 | 常见 ASR 误识别 |")
        lines.append("|---------|---------|----------------|")
        for t in domain_terms:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 通用校正规则
    lines.extend([
        "## 通用校正规则",
        "- 技术英文术语保持原写法，不要翻译成中文",
        "- 中文数字与阿拉伯数字：根据上下文判断（技术语境中\"二十五\"→25）",
        "- 中英文混排时，英文前后保留空格",
        "- 代码、命令、文件名等保持原样，不翻译",
        "- 标点符号使用中文全角标点",
        "- 保持原意不变，仅修正转写错误和语序问题",
        "",
        "---",
        f"ASR Prompt v{version.version} | 生成: {version.generated_at} | 来源: LLM-WIKI（{version.wiki_page_count}页, {version.term_count}术语）",
    ])

    return "\n".join(lines)


def _render_compact(terms: List[AsrTerm], version: AsrPromptVersion) -> str:
    """紧凑格式：纯文本分号分隔，最小 token 消耗。"""
    persons = [t for t in terms if t.category == "person"]
    concepts = [t for t in terms if t.category == "concept"]
    projects = [t for t in terms if t.category == "project"]
    domain_terms = [t for t in terms if t.category == "domain_term"]

    blocks = ["你是 ASR 校正助手。校正规则如下，仅输出校正后文本："]

    def _term_str(t: AsrTerm) -> str:
        ctx = f",{t.context}" if t.context else ""
        mis = "/".join(t.mis_asr) if t.mis_asr else ""
        if mis:
            return f"{t.term}={t.category}{ctx}|勿误:{mis}"
        return f"{t.term}={t.category}{ctx}"

    if persons:
        blocks.append("【人名】" + ";".join(_term_str(t) for t in persons))
    if concepts:
        blocks.append("【术语】" + ";".join(_term_str(t) for t in concepts))
    if projects:
        blocks.append("【项目】" + ";".join(_term_str(t) for t in projects))
    if domain_terms:
        blocks.append("【领域】" + ";".join(_term_str(t) for t in domain_terms))

    blocks.append("【规则】英文术语保持原写;代码/命令/文件名不翻译;中英文间加空格;中文全角标点;保持原意仅修正转写错误")

    blocks.append(
        f"---\n"
        f"v{version.version}|{version.generated_at}|{version.wiki_page_count}页{version.term_count}术语"
    )

    return "\n".join(blocks)


# ═══════════════════════════════════════════════════════════════════
# 版本管理由 asr_version.py 统一提供


