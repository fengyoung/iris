"""理解层：LLM 逐段分析（要点/风险/问题/决策点/建议提问），结构化 JSON 输出 + 容错降级。"""

from __future__ import annotations

import sys
import time
from typing import Any, Dict, List, Optional

from iris.feed._topic_detector import _parse_json_safe

from .models import SegmentAnalysis

_ANALYSIS_DEADLINE_SEC = 15.0  # 单段分析总时间预算（实时场景，超时降级）
_SUMMARY_DEADLINE_SEC = 10.0   # 会议总结时间预算（退出场景，失败跳过）
_MAX_ITEMS = 10                # 每字段最多保留条数
_MAX_ITEM_CHARS = 120          # 每条最多字符数
_TRANSCRIPT_MAX_CHARS = 4000   # 总结 Prompt 的逐段转写上限


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
    ) -> Optional[SegmentAnalysis]:
        prompt = self._loader.render(
            "meeting_live_analyze.md",
            {
                "segment_text": segment_text,
                "retrieval_context": retrieval_context,
                "meeting_summary": meeting_summary,
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
            return SegmentAnalysis(**self._normalize(data))
        except Exception as e:
            print(f"[Iris] ⚠ 会议段分析失败: {e}", file=sys.stderr)
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
            transcript = transcript[: _TRANSCRIPT_MAX_CHARS] + "…"
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
            print(f"[Iris] ⚠ 会议总结失败（跳过）: {e}", file=sys.stderr)
            return None

    @staticmethod
    def _normalize(data: Any) -> Dict[str, List[str]]:
        """容错归一化：非 dict → 空；字段缺失/非 list → 空列表；元素非 str → str()。

        每条截断 _MAX_ITEM_CHARS，每字段最多 _MAX_ITEMS 条。
        """
        fields = (
            "key_points",
            "risks",
            "questions",
            "decisions",
            "suggested_questions",
        )
        result: Dict[str, List[str]] = {f: [] for f in fields}
        if not isinstance(data, dict):
            return result
        for field in fields:
            value = data.get(field)
            if not isinstance(value, list):
                continue
            items = []
            for item in value:
                if len(items) >= _MAX_ITEMS:
                    break
                if item is None:
                    continue  # None 跳过（str(None)="None" 是噪音）
                text = item.strip() if isinstance(item, str) else str(item).strip()
                if text:
                    items.append(text[:_MAX_ITEM_CHARS])
            result[field] = items
        return result
