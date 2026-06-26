"""分析报告生成服务。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from iris.llm import EnvironmentConfiguredLLMProvider, LLMProviderError, LLMRequest
from iris.qa import QAService
from iris.utils.logging import IrisLogger
from iris.utils.prompting import PromptTemplateLoader

from ._helpers import render_evidence_blocks, render_structured_evidence


@dataclass(frozen=True)
class ReportResponse:
    query: str
    mode: str
    markdown: str
    blocks: List[Dict[str, Any]]
    structured: Dict[str, Any] | None = None
    llm: Dict[str, Any] | None = None
    review: Dict[str, Any] | None = None
    revised: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"query": self.query, "mode": self.mode, "markdown": self.markdown,
                "blocks": self.blocks, "structured": self.structured,
                "llm": self.llm, "review": self.review, "revised": self.revised}


class AnalysisReportService:
    def __init__(self, config: ConfigBundle):
        self._config = config
        self._qa = QAService(config)
        self._llm_provider = EnvironmentConfiguredLLMProvider(config)
        self._prompt_loader = PromptTemplateLoader(config)
        self._logger = IrisLogger(config)

    def build_report(self, query: str, *, top_k: int = 6, mode: str = "llm", two_stage: bool = False) -> ReportResponse:
        qa_response = self._qa.ask(query, top_k=top_k, mode=mode)
        blocks = [{"title": b.title, "summary": b.summary, "relative_path": b.citation.relative_path,
                    "line_start": b.citation.line_start, "section_path": b.citation.section_path,
                    "score": b.score, "evidence_type": b.evidence_type, "tags": b.tags}
                  for b in qa_response.blocks]
        structured = qa_response.structured or {}

        if mode == "llm":
            try:
                prompt = self._prompt_loader.render("analysis_report.md",
                    {"query": query, "answer": qa_response.answer, "blocks": render_evidence_blocks(blocks),
                     "structured_context": render_structured_evidence(structured)})
                llm_response = self._llm_provider.generate(LLMRequest(prompt=prompt,
                    route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "use_case": "analysis_basic"}))
                markdown = llm_response.text.strip()
                llm_payload = {"provider": llm_response.provider, "model": llm_response.model,
                               "selected_role": llm_response.selected_role, "matched_rule": llm_response.matched_rule, "fallback_used": False}
                review_result = None
                revised = False
                if two_stage:
                    markdown, review_result, revised = self._review_and_revise(query=query, draft=markdown, structured=structured, llm_payload=llm_payload)
                result = ReportResponse(query=query, mode="llm", markdown=markdown, blocks=blocks, structured=structured, llm=llm_payload, review=review_result, revised=revised)
                self._logger.log("analysis_report", result.to_dict())
                return result
            except LLMProviderError as exc:
                self._logger.log("analysis_llm_fallback", {"query": query, "reason": str(exc)})
                sections = _load_report_sections(self._config.app)
                markdown = _build_local_report(query, qa_response.answer, blocks, structured, sections=sections)
                result = ReportResponse(query=query, mode="local_fallback", markdown=markdown, blocks=blocks, structured=structured, llm={"fallback_used": True, "reason": str(exc)})
                self._logger.log("analysis_report", result.to_dict())
                return result

        sections = _load_report_sections(self._config.app)
        markdown = _build_local_report(query, qa_response.answer, blocks, structured, sections=sections)
        result = ReportResponse(query=query, mode="local", markdown=markdown, blocks=blocks, structured=structured, llm={"fallback_used": False})
        self._logger.log("analysis_report", result.to_dict())
        return result

    def build_biweekly_report(self, *, query: str = "", top_k: int = 8, mode: str = "llm") -> ReportResponse:
        """生成双周报——汇总近两周进展。"""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        two_weeks_ago = (now - timedelta(days=14)).strftime("%Y-%m-%d")
        period = f"{two_weeks_ago} ~ {now.strftime('%Y-%m-%d')}"

        if not query:
            query = f"近两周({period})工作进展"

        # 利用通用报告服务生成
        result = self.build_report(query, top_k=top_k, mode=mode)

        # 包装为双周报格式
        header = f"# 双周报 ({period})\n\n"
        # 尝试在 markdown 开头插入周期信息
        if not result.markdown.startswith("# 双周报"):
            revised = header + result.markdown
            result = ReportResponse(
                query=result.query, mode=result.mode, markdown=revised,
                blocks=result.blocks, structured=result.structured,
                llm=result.llm, review=result.review, revised=result.revised,
            )
        return result

    def _review_and_revise(self, query, draft, structured, llm_payload) -> Tuple[str, Optional[Dict], bool]:
        structured_ctx = render_structured_evidence(structured)
        try:
            review_prompt = self._prompt_loader.render("report_review.md", {"query": query, "draft": draft, "structured_context": structured_ctx})
            review_resp = self._llm_provider.generate(LLMRequest(prompt=review_prompt, route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "user_selected_role": "adv_model"}))
            review_data = _parse_review_json(review_resp.text)
            if not review_data or review_data.get("quality_score", 5) >= 4:
                return draft, review_data, False
            issues_text = "\n".join(f"- {i}" for i in review_data.get("issues", []))
            suggestions_text = "\n".join(f"- {s}" for s in review_data.get("suggestions", []))
            revise_prompt = self._prompt_loader.render("report_revise.md", {"query": query, "issues": issues_text or "无", "suggestions": suggestions_text or "无", "draft": draft, "structured_context": structured_ctx})
            revise_resp = self._llm_provider.generate(LLMRequest(prompt=revise_prompt, route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "use_case": "analysis_basic"}))
            return revise_resp.text.strip(), review_data, True
        except LLMProviderError:
            return draft, None, False


def _parse_review_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and "quality_score" in data:
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    match = re.search(r'\{[^{}]*"quality_score"[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


DEFAULT_REPORT_SECTIONS = [
    ("背景概览", "overview"), ("目标", "goal"), ("当前进展", "progress"),
    ("关键结论", "decision"), ("风险与问题", "risk"), ("建议动作", "next_steps"), ("参考来源", "sources"),
]


def _load_report_sections(config: Dict[str, Any]) -> List[tuple]:
    report_cfg = config.get("report", {})
    custom = report_cfg.get("sections", [])
    if not custom:
        return DEFAULT_REPORT_SECTIONS
    result = [(s.get("title", ""), s.get("group", "")) for s in custom if s.get("title") and s.get("group")]
    return result or DEFAULT_REPORT_SECTIONS


def _build_local_report(query, answer, blocks, structured, *, sections=None) -> str:
    if sections is None:
        sections = DEFAULT_REPORT_SECTIONS
    overview = structured.get("overview") or (blocks[0]["summary"] if blocks else "暂无")
    lines = [f"# {query} 分析报告", ""]
    for title, group in sections:
        content = _resolve_section_content(group, structured, blocks, answer, overview)
        lines.append(f"## {title}")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def _resolve_section_content(group_name, structured, blocks, answer, overview) -> str:
    if group_name == "sources":
        return "\n".join(f"- {b['relative_path']}:{b['line_start']}" for b in blocks[:5]) or "- 暂无"
    if group_name == "next_steps":
        ns = structured.get("recommended_next_steps", [])
        return "\n".join(f"- {item}" for item in ns) or "- 建议继续补充最新证据"
    if group_name == "overview":
        return overview
    if group_name == "goal":
        return _pick_group_line(structured, "goal") or overview
    if group_name == "progress":
        return _pick_group_line(structured, "progress") or (blocks[1]["summary"] if len(blocks) > 1 else overview)
    if group_name == "decision":
        return _render_group_lines(structured, "decision", fallback=answer)
    if group_name == "risk":
        return _render_group_lines(structured, "risk", fallback="- 暂无显式风险记录")
    items = structured.get("groups", {}).get(group_name, [])
    return "\n".join(f"- {item['summary']}" for item in items[:3]) if items else "- 暂无"


def _pick_group_line(structured, name):
    items = structured.get("groups", {}).get(name, [])
    return items[0]["summary"] if items else ""


def _render_group_lines(structured, name, *, fallback):
    items = structured.get("groups", {}).get(name, [])
    return "\n".join(f"- {item['summary']}" for item in items[:3]) if items else fallback
