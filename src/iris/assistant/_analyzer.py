"""理解层：LLM 逐段分析（要点/风险/问题/决策点/建议提问），结构化 JSON 输出 + 容错降级。"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from iris.feed._topic_detector import _parse_json_safe

from .models import SegmentAnalysis, SpeakerLabel

_logger = logging.getLogger(__name__)

_ANALYSIS_DEADLINE_SEC = 15.0  # 单段分析总时间预算（实时场景，超时降级）
_SUMMARY_DEADLINE_SEC = 10.0   # 会议总结时间预算（退出场景，失败跳过）
_MAX_ITEMS = 10                # 每字段最多保留条数
_MAX_ITEM_CHARS = 120          # 每条最多字符数
_TRANSCRIPT_MAX_CHARS = 4000   # 总结 Prompt 的逐段转写上限
_HEAD_CHARS = 1000             # 截断时保留头部（会议背景/开场）
_TAIL_CHARS = 3000             # 截断时保留尾部（结论/行动项）


class SegmentAnalyzer:
    """LLM 结构化分析；llm_service 为鸭子类型（.generate(...) -> .text）。

    失败降级：任何异常/超时/非 JSON 输出 → 返回 None（面板/文档显示「分析不可用」），
    会议流程不中断。
    """

    def __init__(self, llm_service: object, template_loader: object, *, model: str = ""):
        self._llm = llm_service
        self._loader = template_loader
        self._model = model

    def analyze(
        self,
        segment_text: str,
        retrieval_context: str,
        meeting_summary: str,
        *,
        open_questions: str = "",
        adjacent_context: str = "",
        agenda: str = "",
    ) -> Optional[SegmentAnalysis]:
        prompt = self._loader.render(
            "meeting_live_analyze.md",
            {
                "segment_text": segment_text,
                "retrieval_context": retrieval_context,
                "meeting_summary": meeting_summary,
                "open_questions": open_questions or "（暂无）",
                "adjacent_context": adjacent_context or "",
                "agenda": agenda or "",
            },
        )
        try:
            result = self._llm.generate(
                prompt,
                route_context={
                    "task_type": "meeting_analysis",
                    "input_type": "text",
                    "use_case": "meeting_analysis",
                },
                temperature=0.1,
                max_tokens=1200,
                max_retries=0,  # 实时场景不重试，超时直接降级
                extra_body={"thinking": {"type": "disabled"}},
                force_model=self._model or None,
                _deadline=time.monotonic() + _ANALYSIS_DEADLINE_SEC,
            )
            data = _parse_json_safe(result.text, "会议段分析")
            if not data:
                return None
            norm = self._normalize(data)
            return SegmentAnalysis(
                key_points=norm["key_points"],
                risks=norm["risks"],
                questions=norm["questions"],
                decisions=norm["decisions"],
                suggested_questions=norm["suggested_questions"],
                resolved_questions=norm["resolved_questions"],
                topic=norm["topic"],
                topic_change=norm["topic_change"],
                topic_summary=norm["topic_summary"],
                todos=norm["todos"],
                speaker=norm["speaker"] or SpeakerLabel(),
            )
        except Exception as e:
            _logger.warning("会议段分析失败: %s", e)
            return None

    def summarize(self, state: Any) -> Optional[str]:
        """会议结束总结：全段转写 + 会议累计 → Markdown 总结文本。

        失败降级返回 None（退出流程不阻塞），成功返回纯 Markdown 文本。
        """
        transcript = "\n".join(
            f"- 段{s.seq}（{s.started_at:%H:%M}）：{s.corrected_text or s.raw_text}"
            for s in state.segments
        )
        if len(transcript) > _TRANSCRIPT_MAX_CHARS:
            # 头+尾策略：保留开场背景与最新结论，避免纯头部截断丢失会议后半程内容
            head = transcript[:_HEAD_CHARS]
            # 在换行边界截断 head（避免断在句子中间）
            if "\n" in head:
                head = head[: head.rfind("\n")]
            tail_start = max(_HEAD_CHARS, len(transcript) - _TAIL_CHARS)
            # tail 也在换行边界开始
            if "\n" in transcript[tail_start:]:
                tail_start += transcript[tail_start:].index("\n")
            transcript = head + "\n…\n" + transcript[tail_start:]
        parts = []
        if state.key_points:
            parts.append("要点: " + "；".join(state.key_points))
        if state.decisions:
            parts.append("决策: " + "；".join(state.decisions))
        if state.risks:
            parts.append("风险: " + "；".join(state.risks))
        if state.open_questions:
            parts.append("待解决: " + "；".join(state.open_questions))
        try:
            prompt = self._loader.render(
                "meeting_live_summary.md",
                {
                    "meeting_summary": "\n".join(parts) or "（暂无累计要点）",
                    "transcript": transcript or "（无语音段）",
                },
            )
            result = self._llm.generate(
                prompt,
                route_context={
                    "task_type": "meeting_summary",
                    "input_type": "text",
                    "use_case": "meeting_analysis",
                },
                temperature=0.2,
                max_tokens=1000,
                max_retries=0,  # 退出场景不重试，失败直接跳过
                extra_body={"thinking": {"type": "disabled"}},
                force_model=self._model or None,
                _deadline=time.monotonic() + _SUMMARY_DEADLINE_SEC,
            )
            text = (result.text or "").strip()
            return text or None
        except Exception as e:
            _logger.warning("会议总结失败（跳过）: %s", e)
            return None

    def suggest_questions(
        self,
        analysis: "SegmentAnalysis",
        meeting_summary: str,
        retrieval_context: str,
        deadline: float,
    ) -> list[str]:
        """独立生成建议提问（仅采样段调用，temperature=0.5 提升尖锐度）。

        deadline：总时间预算（time.monotonic() 绝对值），超时不等待直接降级返回 []。
        """
        remaining = deadline - time.monotonic()
        if remaining < 2.0:
            return []  # 时间不足，跳过

        prompt = self._loader.render(
            "meeting_live_suggest.md",
            {
                "key_points": "；".join(analysis.key_points) or "（无）",
                "risks": "；".join(analysis.risks) or "（无）",
                "questions": "；".join(analysis.questions) or "（无）",
                "decisions": "；".join(analysis.decisions) or "（无）",
                "meeting_summary": meeting_summary,
                "retrieval_context": retrieval_context,
            },
        )
        try:
            result = self._llm.generate(
                prompt,
                route_context={
                    "task_type": "meeting_suggest",
                    "input_type": "text",
                    "use_case": "meeting_analysis",
                },
                temperature=0.5,
                max_tokens=300,
                max_retries=0,
                extra_body={"thinking": {"type": "disabled"}},
                force_model=self._model or None,
                _deadline=deadline,
            )
            data = _parse_json_safe(result.text, "建议提问")
            if isinstance(data, list):
                return [s.strip() for s in data if isinstance(s, str) and s.strip()][:3]
            return []
        except Exception:
            return []  # 失败静默降级，保留主分析的 suggested_questions

    @staticmethod
    def _normalize(data: Any) -> Dict[str, Any]:
        """容错归一化：非 dict → 空；字段缺失/非 list → 空列表；元素非 str → str()。

        每条截断 _MAX_ITEM_CHARS，每字段最多 _MAX_ITEMS 条。
        decisions 支持新旧两种格式：["str"] 或 [{"text":"str","confidence":"confirmed"}]
        """
        list_fields = (
            "key_points",
            "risks",
            "questions",
            "suggested_questions",
            "resolved_questions",
        )
        result: Dict[str, Any] = {f: [] for f in list_fields}
        result["decisions"] = []  # List[DecisionItem]
        result["todos"] = []  # List[TodoItem]
        result["topic"] = ""
        result["topic_change"] = False
        result["topic_summary"] = ""
        result["speaker"] = None  # SpeakerLabel or None
        if not isinstance(data, dict):
            return result
        # 标量字段
        for f in ("topic", "topic_summary"):
            v = data.get(f, "")
            if isinstance(v, str):
                result[f] = v.strip()[:200]
        result["topic_change"] = bool(data.get("topic_change", False))
        # 列表字段
        for field in list_fields:
            value = data.get(field)
            if not isinstance(value, list):
                continue
            items = []
            for item in value:
                if len(items) >= _MAX_ITEMS:
                    break
                if item is None:
                    continue
                text = item.strip() if isinstance(item, str) else str(item).strip()
                if text:
                    items.append(text[:_MAX_ITEM_CHARS])
            result[field] = items
        # decisions 字段：支持 {"text":"...","confidence":"confirmed"} 或纯字符串
        decisions_data = data.get("decisions")
        if isinstance(decisions_data, list):
            from iris.assistant.models import DecisionItem
            decisions = []
            for item in decisions_data:
                if len(decisions) >= _MAX_ITEMS:
                    break
                if isinstance(item, dict):
                    text = str(item.get("text", "")).strip()
                    conf = str(item.get("confidence", "proposed")).strip()
                    if text and conf in ("confirmed", "proposed", "tentative"):
                        decisions.append(DecisionItem(text=text[:_MAX_ITEM_CHARS], confidence=conf))
                elif isinstance(item, str):
                    decisions.append(DecisionItem(text=item.strip()[:_MAX_ITEM_CHARS]))
            result["decisions"] = decisions
        # todos 字段：支持 {"text":"...","assignee":"...","deadline":"..."} 或纯字符串
        todos_data = data.get("todos")
        if isinstance(todos_data, list):
            from iris.assistant.models import TodoItem
            todos = []
            for item in todos_data:
                if len(todos) >= _MAX_ITEMS:
                    break
                if isinstance(item, dict):
                    text = str(item.get("text", "")).strip()
                    if text:
                        todos.append(TodoItem(
                            text=text[:_MAX_ITEM_CHARS],
                            assignee=str(item.get("assignee", "")).strip()[:50],
                            deadline=str(item.get("deadline", "")).strip()[:50],
                        ))
                elif isinstance(item, str):
                    todos.append(TodoItem(text=item.strip()[:_MAX_ITEM_CHARS]))
            result["todos"] = todos
        # speaker 字段
        speaker_data = data.get("speaker")
        if isinstance(speaker_data, dict):
            from iris.assistant.models import SpeakerLabel
            result["speaker"] = SpeakerLabel(
                speaker_id=str(speaker_data.get("speaker_id", "")).strip()[:20],
                role_hint=str(speaker_data.get("role_hint", "")).strip()[:20],
                turn_index=int(speaker_data.get("turn_index", 0)) if speaker_data.get("turn_index") else 0,
                is_turn_change=bool(speaker_data.get("is_turn_change", False)),
            )
        return result
