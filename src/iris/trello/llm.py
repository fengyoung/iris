"""Trello LLM 辅助：总结、排序、自然语言解析。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.llm import LLMService
from iris.trello.models import TrelloCard

_TRELLO_DATETIME_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


class TrelloLLM:
    def __init__(self, config_bundle: ConfigBundle, model: Optional[str] = None):
        self._config = config_bundle
        self._llm = LLMService(config_bundle)
        self._model = model

    def summarize(self, cards: List[TrelloCard]) -> str:
        if not cards:
            return "当前无未完成待办。"
        context = self._build_cards_context(cards)
        prompt = _SUMMARIZE_PROMPT.format(context=context, now=datetime.now().strftime("%Y-%m-%d %H:%M"))
        response = self._call_llm(prompt, input_type="text")
        return response

    def prioritize(self, cards: List[TrelloCard]) -> List[Dict[str, Any]]:
        if not cards:
            return []
        context = self._build_cards_context(cards)
        prompt = _PRIORITIZE_PROMPT.format(context=context, now=datetime.now().strftime("%Y-%m-%d %H:%M"))
        response = self._call_llm(prompt, input_type="text")
        try:
            result = json.loads(_extract_json_block(response))
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, ValueError):
            return [{"id": c.id, "name": c.name, "priority_reason": "(解析失败)"} for c in cards]

    def parse_natural_language(self, text: str) -> Dict[str, Any]:
        prompt = _NL_PARSE_PROMPT.format(user_input=text)
        response = self._call_llm(prompt, input_type="text")
        try:
            result = json.loads(_extract_json_block(response))
            return result if isinstance(result, dict) else {"action": "unknown", "reason": "LLM 返回格式异常"}
        except (json.JSONDecodeError, ValueError):
            return {"action": "unknown", "reason": "解析失败"}

    def suggest_breakdown(self, title: str, desc: str = "") -> List[str]:
        prompt = _BREAKDOWN_PROMPT.format(title=title, desc=desc or "(无详细描述)")
        response = self._call_llm(prompt, input_type="text")
        try:
            result = json.loads(_extract_json_block(response))
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, ValueError):
            return []

    def discover_todos(self, conversation_text: str, existing_titles: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        if not conversation_text.strip():
            return []
        from iris.utils.prompting import PromptTemplateLoader
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            loader = PromptTemplateLoader(self._config)
            prompt = loader.render("trello_discover.md", {"conversation_text": conversation_text, "now": now})
        except (OSError, ValueError, KeyError):
            prompt = _DISCOVER_PROMPT.format(context=conversation_text, now=now)
        if existing_titles:
            prompt += "\n\n以下为 Trello 看板中已有的待办事项，请勿重复输出语义相同的项：\n" + "\n".join(f"- {t}" for t in existing_titles)
        response = self._call_llm(prompt, input_type="text")
        try:
            result = json.loads(_extract_json_block(response))
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, ValueError):
            return []

    def _call_llm(self, prompt: str, input_type: str = "text") -> str:
        route_context: Dict[str, Any] = {"input_type": input_type}
        if self._model:
            route_context["user_selected_role"] = f"{self._model}_model"
        if input_type == "multimodal":
            route_context["task_type"] = "analysis"
            route_context["complexity"] = "complex"
        return self._llm.generate(prompt, route_context=route_context).text

    def _build_cards_context(self, cards: List[TrelloCard]) -> str:
        lines = []
        for idx, card in enumerate(cards, 1):
            labels = ", ".join(lb.name or lb.color or "" for lb in card.labels)
            due_str = card.due or "无截止时间"
            lines.append(f"{idx}. [{card.list_name}] {card.name} | 标签:{labels} | 截止:{due_str}")
        return "\n".join(lines)


_SUMMARIZE_PROMPT = """你是项目管理助手。请根据以下待办列表，生成一段简洁的中文待办摘要（不超过 300 字），按紧急度/重要性分组说明。

当前时间：{now}

待办列表：
{context}

请输出纯文本摘要，不需要 JSON 格式。"""

_PRIORITIZE_PROMPT = """你是项目管理助手。请根据以下待办列表，按紧急度和重要性排序，并输出 JSON 数组。

当前时间：{now}

待办列表：
{context}

输出格式：
[{{"id": "", "name": "原标题", "suggested_order": 1, "priority_reason": "优先级理由"}}]

请只输出 JSON 数组，不要包含其他内容。"""

_NL_PARSE_PROMPT = """你是任务管理助手。根据用户的自然语言输入，解析意图并输出 JSON。

用户输入：{user_input}

请解析为 JSON 对象，包含以下字段：
- action: 动作类型。可选值: create/list/update/complete/summarize/prioritize/search/status
- title: 待办标题（create/update/search 时提取）
- desc: 描述（可选）
- due: 截止时间 YYYY-MM-DD（可选）
- category: work 或 life（可选，默认 work）
- list_name: 列表名（可选，默认 TODO）
- query: 搜索关键词（search 时使用）
- card_id: 待办 ID（update/complete 时使用）
- filter_today/filter_weekly: true/false

请只输出 JSON 对象。"""

_BREAKDOWN_PROMPT = """你是项目管理助手。请将以下任务拆解为 3-5 个子任务，输出 JSON 字符串数组。

任务标题：{title}
任务描述：{desc}

输出格式：["子任务1", "子任务2", ...]"""

_DISCOVER_PROMPT = """你是任务管理助手。从对话文本中检测潜在的待办事项。

当前时间：{now}

对话文本：
{context}

请检测文本中提及的待办事项。注意：
- 排除已完成的事项
- 排除假设性太弱的事项
- 排除纯信息陈述
- 相对时间转为绝对值

每个候选包含：title（简洁动作描述）, desc（可选）, due（YYYY-MM-DD）, category（work/life）, confidence（0.0-1.0）, context（原文关键句）

如果没有发现任何待办，返回空数组。

请只输出 JSON 数组。"""


def _extract_json_block(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        if lines and lines[0].strip() in ("json", "javascript", "python", "yaml", ""):
            lines = lines[1:]
        text = "\n".join(lines).strip()
    return text
