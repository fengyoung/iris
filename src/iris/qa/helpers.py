"""QA 模块纯函数工具库。"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from iris.retrieval import RetrievalHit

from .models import AnswerBlock

TERM_QUERY_RE = re.compile(r"^[A-Z]{2,}[A-Za-z0-9\-]*$")
EXPLICIT_MEMORY_RE = re.compile(r"^\s*(请)?(记住|牢记|纠正|更正|正确的是|请注意|更新记忆|以后|我的偏好|我喜欢|我不喜欢|我希望|我不希望|偏好|习惯)")


def infer_evidence_type(hit: RetrievalHit) -> str:
    for field in ("decision", "goal", "progress", "risk", "definition", "timeline"):
        if field in hit.extracted_fields or field in hit.structural_tags:
            return field
    return "general"


def intent_title(intent: str) -> str:
    return {"definition": "定义与解释", "comparison": "对比要点", "timeline": "时间线要点",
            "risk": "风险与问题", "reason": "背景与原因"}.get(intent, "依据")


def group_title(group_name: str) -> str:
    return {"goal": "目标", "progress": "进展", "decision": "结论/决策", "risk": "风险",
            "definition": "定义", "timeline": "时间线", "supporting": "补充证据"}.get(group_name, group_name)


def is_memory_only_instruction(question: str) -> bool:
    text = question.strip()
    if len(text) > 120:
        return False
    if any(token in text for token in ("？", "?", "请问", "进展", "分析", "总结", "为什么", "如何")):
        return False
    return bool(EXPLICIT_MEMORY_RE.search(text))


def infer_question_type(question: str, wiki_hits: List[Dict[str, Any]]) -> str:
    question_lower = question.lower()
    if wiki_hits and wiki_hits[0].get("page_type") in {"project", "domain", "concept", "person"}:
        return {"project": "project", "domain": "topic", "concept": "term", "person": "topic"}.get(wiki_hits[0]["page_type"], "topic")
    if any(keyword in question for keyword in ("项目", "进展", "里程碑", "目标", "当前结论")):
        return "project"
    if any(keyword in question for keyword in ("是什么", "含义", "缩写", "术语", "定义")) or TERM_QUERY_RE.match(question.strip()):
        return "term"
    if any(keyword in question_lower for keyword in ("机制", "流程", "主题", "演进", "背景", "为什么")):
        return "topic"
    return "topic"


def _merge_updates(quick: list, llm: list) -> list:
    """合并两轮记忆更新，保持顺序并去重。"""
    seen = set()
    merged = []
    for item in (quick or []) + (llm or []):
        if item not in seen:
            seen.add(item)
            merged.append(item)
    return merged


def block_bonus(block: AnswerBlock, question_type: str) -> float:
    joined = (block.title + " " + " > ".join(block.citation.section_path) + " " + block.summary).lower()
    bonus = 0.0
    if question_type == "project":
        for keyword in ("目标", "进展", "结论", "里程碑", "状态", "下一步"):
            if keyword in joined:
                bonus += 1.2
    elif question_type == "term":
        for keyword in ("定义", "说明", "术语", "缩写", "使用"):
            if keyword in joined:
                bonus += 1.0
    else:
        for keyword in ("机制", "流程", "演进", "方案", "讨论"):
            if keyword in joined:
                bonus += 0.8
    if any(noise in joined for noise in ("邮件信息", "会议信息", "模板")):
        bonus -= 1.5
    if block.evidence_type in {"decision", "progress", "goal", "definition"}:
        bonus += 0.6
    return bonus
