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

    def build_biweekly_report(self, *, query: str = "", top_k: int = 12, mode: str = "llm") -> ReportResponse:
        """生成双周报——汇总近两周进展，按项目方向编写。

        与 build_report 不同：此方法独立实现完整 pipeline，
        加载 Wiki 上下文 + 检索最近证据 → LLM 按方向编写结构化周报。
        """
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        two_weeks_ago = now - timedelta(days=14)
        period = f"{two_weeks_ago.strftime('%Y.%m.%d')}～{now.strftime('%Y.%m.%d')}"

        if not query:
            query = f"近两周({period})工作进展"

        # 1. 加载 Wiki 上下文作为背景知识
        wiki_context = self._load_wiki_for_report()

        # 2. 检索近两周的证据
        evidence_blocks = self._retrieve_recent_evidence(query, top_k=top_k)

        if mode == "local":
            markdown = self._build_local_biweekly(period, evidence_blocks, wiki_context)
            return ReportResponse(query=query, mode="local", markdown=markdown, blocks=[],
                                  structured={}, llm={"fallback_used": False})

        # 3. LLM 模式：用模板生成
        try:
            cfg = self._config.app.get("biweekly_report", {})
            author_info = cfg.get("author_info", "你是一个技术团队的写作助手。")
            prompt = self._prompt_loader.render("biweekly_report.md", {
                "period": period,
                "author_info": author_info,
                "wiki_context": wiki_context,
                "evidence": evidence_blocks,
            })
            # 使用 base_model（避免 reasoning 模型输出 CoT）
            response = self._llm_provider.generate(
                LLMRequest(prompt=prompt, route_context={
                    "input_type": "text", "task_type": "analysis",
                    "complexity": "standard", "use_case": "biweekly_report",
                })
            )
            markdown = response.text.strip()
            # 清理代码块包裹
            from iris.wiki.generator import WikiGenerator
            markdown = WikiGenerator._strip_code_fence(markdown)

            # 添加时间周期头
            if not markdown.startswith("*时间周期"):
                markdown = f"*时间周期：{period}*\n\n{markdown}"

            llm_payload = {
                "provider": response.provider, "model": response.model,
                "selected_role": response.selected_role,
                "matched_rule": response.matched_rule, "fallback_used": False,
            }
            result = ReportResponse(query=query, mode="llm", markdown=markdown,
                                     blocks=[], structured={}, llm=llm_payload)
            self._logger.log("biweekly_report", result.to_dict())
            return result

        except LLMProviderError as exc:
            self._logger.log("biweekly_llm_fallback", {"query": query, "reason": str(exc)})
            markdown = self._build_local_biweekly(period, evidence_blocks, wiki_context)
            return ReportResponse(query=query, mode="local_fallback", markdown=markdown,
                                  blocks=[], structured={},
                                  llm={"fallback_used": True, "reason": str(exc)})

    def _load_wiki_for_report(self) -> str:
        """加载 Wiki 页面上下文 + OP 规划文档，用于双周报背景知识。"""
        from iris.wiki.context_loader import WikiContextLoader
        fragments = []

        # 0. 优先加载 OP 规划作为主框架
        op_text = self._load_op_document()
        if op_text:
            fragments.append(f"## OP规划（双周报必须对齐此框架）\n{op_text}")

        # 1. Wiki 页面（跳过概念，仅领域/项目/人物）
        if self._config.wiki and self._config.wiki.get("wiki_root"):
            wiki_root = Path(self._config.wiki["wiki_root"]).resolve()
            if wiki_root.exists():
                loader = WikiContextLoader(wiki_root)
                ctx = loader.load_context(
                    page_types=["domain", "project", "person"],
                    max_chars_per_page=1500,
                )
                if ctx:
                    fragments.append(ctx)

        return "\n\n".join(fragments) if fragments else "（无背景知识）"

    def _load_op_document(self) -> str:
        """加载 SOURCE 中的 OP 规划文档。"""
        from pathlib import Path as _Pt
        # 从数据源配置中获取 SOURCE 路径
        sources = self._config.data_source.get("sources", {})
        for cfg in sources.values():
            src_path = _Pt(cfg.get("path", "")).resolve()
            if not src_path.exists():
                continue
            # 查找 OP 规划文件
            op_dir = src_path / "01-目标管理"
            if op_dir.exists():
                for f in sorted(op_dir.glob("*.md"), reverse=True):  # 最新OP优先
                    try:
                        text = f.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    # 去掉 frontmatter（如果有）
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        text = parts[2].strip() if len(parts) >= 3 else text
                    # 截断过长的 OP（保留核心：方向+目标+举措+责任人）
                    return text[:8000]
        return ""

    @staticmethod
    def _extract_op_keywords(op_text: str) -> list[str]:
        """从 OP 文档中提取搜索关键词（方向名、项目名、责任人）。

        关键词驱动近两周证据检索，OP 更新后自动适配。
        """
        import re
        keywords = []
        # 提取 OP 各层级的核心词（适配多种格式）
        # 方向标题：## 1、视觉检测 或 ## 方向一：质量...
        for m in re.finditer(r"##\s*(?:\d+[、.])?\s*(.+?)(?:\n|$)", op_text):
            title = m.group(1).strip()
            # 跳过非方向行（如空行、纯数字）
            if not title or len(title) < 3:
                continue
            clean = re.sub(r'[“”‘’\"\'＞>]', '', title)
            words = re.findall(r"[\w一-鿿]{2,}", clean)
            keywords.extend(words[:5])
        # 子项标题：### 1.1、Alpha项目
        for m in re.finditer(r"###\s+\d+\.\d+[、.]?\s*(.+?)(?:\n|$)", op_text):
            desc = m.group(1).strip()
            nouns = re.findall(r"[\w一-鿿A-Za-z]{2,}", desc)
            keywords.extend(nouns[:3])
        # 责任人、目标中的关键名词
        for m in re.finditer(r"责任人[：:]\s*(.+?)$", op_text, re.MULTILINE):
            names = m.group(1).strip()
            for name in re.split(r"[//、,，\s]+", names):
                if name and len(name) >= 2:
                    keywords.append(name)
        # 去重、去通用词、排序
        stop = {"方向", "检测", "覆盖", "实现", "推动", "提升", "建立", "持续", "支撑", "完成", "目标", "举措", "扩展", "能力"}
        seen = set()
        result = []
        for kw in keywords:
            if kw not in stop and kw not in seen and len(kw) >= 2:
                seen.add(kw)
                result.append(kw)
        return result[:20]

    def _retrieve_recent_evidence(self, query: str, top_k: int) -> str:
        """检索近两周的工作证据——多关键词并发搜索、按相关度合并去重。"""
        from iris.retrieval.searcher import LocalRetriever
        import re as _re
        retriever = LocalRetriever(self._config)

        # 从 OP 文档中动态提取搜索关键词（不硬编码）
        op_text = self._load_op_document()
        keywords = self._extract_op_keywords(op_text) if op_text else ["进展", "项目"]
        seen_ids = set()
        all_hits = []
        for kw in keywords:
            result = retriever.search(kw, top_k=max(top_k // 3, 3))
            for hit in result.hits:
                if hit.chunk_id not in seen_ids:
                    seen_ids.add(hit.chunk_id)
                    all_hits.append(hit)

        # 按 score 降序、去重，取 top 15
        all_hits.sort(key=lambda h: -h.score)
        top_hits = all_hits[:15]

        if not top_hits:
            # 回退：直接用原 query 搜索
            result = retriever.search(query, top_k=top_k)
            top_hits = list(result.hits[:15])

        lines = []
        for i, hit in enumerate(top_hits, 1):
            source_info = f"{hit.relative_path}:{hit.line_start}"
            date_hint = ""
            m = _re.search(r"(\d{8})", hit.relative_path)
            if m:
                date_hint = f" [{m.group(1)[:4]}.{m.group(1)[4:6]}.{m.group(1)[6:8]}]"
            lines.append(f"### 证据 {i}{date_hint}")
            lines.append(f"来源：{source_info}")
            lines.append(f"标题：{hit.title}")
            lines.append(f"内容：{hit.content_preview[:400]}")
            lines.append("")

        return "\n".join(lines) if lines else "（未检索到工作相关数据）"

    def _build_local_biweekly(self, period: str, evidence: str, wiki: str) -> str:
        """降级模式：生成简版双周报。"""
        lines = [
            f"*时间周期：{period}*",
            "",
            "## 本周进展汇总",
            "",
            "*以下为基于近两周数据的自动汇总，建议使用 LLM 模式获得更高质量报告。*",
            "",
            wiki[:3000] if wiki else "",
            "",
            evidence[:5000] if evidence else "",
            "",
            "---",
            "> This report was generated by Iris.",
        ]
        return "\n".join(lines)

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
