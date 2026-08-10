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
from iris.utils.tokenization import TOKEN_RE

TERM_RE = re.compile(r"[A-Z]{2,}[A-Za-z0-9\-]*")
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
    """LLM 增强查询规划：低置信度时调用 LLM 做语义增强和实体识别。

    使用 LLM 对规则规划结果进行二次分析：校验意图分类、补全实体、推断时间范围。
    仅在规则规划置信度低（关键词匹配弱、实体稀疏）时触发，避免不必要的 LLM 调用。
    """

    _LOW_CONFIDENCE_INTENTS = {"general", "topic"}
    _MIN_ENTITIES_FOR_SKIP = 2

    def __init__(self, llm_provider, prompt_loader):
        self._llm = llm_provider
        self._prompts = prompt_loader

    def enhance(self, rule_plan: QueryPlan, *, _deadline: Optional[float] = None) -> QueryPlan:
        """LLM 增强：低置信度时调用模型二次分析，否则返回原规划。

        _deadline: 内部参数，LLM 调用的降级链总超时（Unix 时间戳）。
        实时场景（meeting-live-assistant 检索）必须传入——此调用是全链路唯一
        默认无 deadline 的 LLM 点，provider 挂起会占死线程池。
        """
        if not self._should_enhance(rule_plan):
            return rule_plan
        try:
            enhanced = self._call_llm_enhance(rule_plan, _deadline=_deadline)
            if enhanced:
                return enhanced
        except Exception:
            logger.warning("LLM 查询增强失败，回退规则规划", exc_info=True)
        return rule_plan

    def _should_enhance(self, plan: QueryPlan) -> bool:
        """判断是否需要 LLM 增强：意图 generic / 实体不足。"""
        if plan.query_intent not in self._LOW_CONFIDENCE_INTENTS:
            return len(plan.entities) < self._MIN_ENTITIES_FOR_SKIP
        return True

    def _call_llm_enhance(self, plan: QueryPlan, *, _deadline: Optional[float] = None) -> Optional[QueryPlan]:
        """调用 LLM 做语义增强，成功返回新 QueryPlan，失败返回 None。"""
        from iris.llm import LLMRequest

        prompt = (
            f"分析以下查询，提取关键信息并以 JSON 返回：\n"
            f"查询：{plan.original_query}\n"
            f"规则预判：意图={plan.query_intent}，类型={plan.question_type}，时间={plan.time_scope}\n"
            f'返回格式：{{"intent":"definition|comparison|timeline|risk|reason|general",'
            f'"question_type":"project|term|topic","time_scope":"recent|historical|unspecified",'
            f'"entities":["实体1","实体2"],"keywords":["关键词1"]}}'
        )

        request = LLMRequest(
            prompt=prompt,
            route_context={"input_type": "text", "task_type": "query_enhancement", "use_case": "retrieval"},
        )
        response = self._llm.generate(request, temperature=0.0, _deadline=_deadline)
        if not response or not response.text:
            return None

        data = json.loads(response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        return QueryPlan(
            original_query=plan.original_query,
            normalized_query=plan.normalized_query,
            query_intent=str(data.get("intent", plan.query_intent)),
            question_type=str(data.get("question_type", plan.question_type)),
            time_scope=str(data.get("time_scope", plan.time_scope)),
            entities=list(data.get("entities", plan.entities)),
            keywords=list(data.get("keywords", plan.keywords)),
            preferred_sources=list(plan.preferred_sources),
            answer_focus=list(plan.answer_focus),
            explain=plan.explain + ["LLM 增强已应用"],
            llm_enhanced=True,
        )


def _extract_keywords(query: str) -> List[str]:
    found = []
    for match in TOKEN_RE.finditer(query):
        token = match.group(0).strip()
        if len(token) < 2 or token in STOPWORDS:
            continue
        if token not in found:
            found.append(token)
    return found[:10]
