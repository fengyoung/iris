"""查询规划：识别问题类型、实体、时间范围与检索偏好。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("iris.retrieval.planner")

# 动态生成年份范围（当前年及之前 6 年）
_current_year = datetime.now().year
_YEAR_RANGE = "|".join(str(y) for y in range(_current_year - 6, _current_year + 1))

TIME_SCOPE_PATTERNS = [
    (re.compile(r"最近|近期|当前|现在|本周|本月|最新"), "recent"),
    (re.compile(rf"去年|今年|(?:{_YEAR_RANGE})|Q[1-4]|季度|时间线|演进"), "historical"),
]
TERM_RE = re.compile(r"[A-Z]{2,}[A-Za-z0-9\-]*")
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-一-鿿]+")
STOPWORDS = {"什么", "多少", "一个", "我们", "你们", "当前", "最近", "一下", "帮我", "一下子"}


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    normalized_query: str
    query_intent: str
    question_type: str
    time_scope: str
    entities: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    preferred_sources: List[str] = field(default_factory=list)
    answer_focus: List[str] = field(default_factory=list)
    explain: List[str] = field(default_factory=list)
    llm_enhanced: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QueryPlanner:
    def build(self, query: str) -> QueryPlan:
        normalized = " ".join(query.split())
        query_intent = _infer_query_intent(normalized)
        question_type = _infer_question_type(normalized)
        time_scope = _infer_time_scope(normalized)
        entities = _extract_entities(normalized)
        keywords = _extract_keywords(normalized)
        explain = [f"识别为{question_type}问题", f"意图={query_intent}", f"时间范围={time_scope}"]
        if entities:
            explain.append("实体=" + " / ".join(entities[:4]))
        return QueryPlan(original_query=query, normalized_query=normalized, query_intent=query_intent,
                         question_type=question_type, time_scope=time_scope, entities=entities,
                         keywords=keywords, explain=explain)


def _infer_query_intent(query: str) -> str:
    if any(keyword in query for keyword in ("是什么", "含义", "定义", "术语", "缩写")):
        return "definition"
    if any(keyword in query for keyword in ("对比", "比较", "区别", "差异", "优劣")):
        return "comparison"
    if any(keyword in query for keyword in ("进展", "里程碑", "阶段", "时间线", "演进")):
        return "timeline"
    if any(keyword in query for keyword in ("风险", "问题", "阻塞", "挑战")):
        return "risk"
    if any(keyword in query for keyword in ("为什么", "原因", "背景")):
        return "reason"
    return "general"


def _infer_question_type(query: str) -> str:
    if any(keyword in query for keyword in ("项目", "里程碑", "目标", "进展", "结论")):
        return "project"
    if any(keyword in query for keyword in ("是什么", "含义", "术语", "缩写", "定义")) or TERM_RE.search(query.strip()):
        return "term"
    return "topic"


def _infer_time_scope(query: str) -> str:
    for pattern, scope in TIME_SCOPE_PATTERNS:
        if pattern.search(query):
            return scope
    return "unspecified"


def _extract_entities(query: str) -> List[str]:
    entities: List[str] = []
    for match in TERM_RE.finditer(query):
        entity = match.group(0)
        if entity not in entities:
            entities.append(entity)
    for token in re.findall(r"[A-Za-z0-9_\-一-鿿]{2,}", query):
        if token in STOPWORDS:
            continue
        if any(marker in token for marker in ("项目", "机制", "技术", "系统", "平台", "方案")) and token not in entities:
            entities.append(token)
    return entities[:6]


class LLMQueryPlanner:
    """低置信度时调用 LLM 增强查询规划。

    当前为占位实现（步骤 2.3 简化阶段）：直接返回规则规划结果。
    未来将在此处接入 LLM 对低置信度查询的语义增强和实体识别。
    """

    def __init__(self, llm_provider, prompt_loader):
        self._llm_provider = llm_provider
        self._prompt_loader = prompt_loader

    def enhance(self, rule_plan: QueryPlan) -> QueryPlan:
        """[占位] 返回规则规划结果，LLM 增强尚未启用。"""
        return rule_plan


def _extract_keywords(query: str) -> List[str]:
    found = []
    for match in TOKEN_RE.finditer(query):
        token = match.group(0).strip()
        if len(token) < 2 or token in STOPWORDS:
            continue
        if token not in found:
            found.append(token)
    return found[:10]
