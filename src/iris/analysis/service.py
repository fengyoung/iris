"""分析报告生成服务。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError
from iris.llm.service import LLMService
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
        self._llm = LLMService(config)
        self._prompt_loader = PromptTemplateLoader(config)
        self._logger = IrisLogger(config)
        self._op_text_cache: Optional[str] = None  # OP 文档缓存，避免重复加载

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
                markdown = self._llm.generate(prompt=prompt,
                    route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "use_case": "analysis_basic"}).text
                markdown = markdown.strip()
                llm_payload = {"fallback_used": False}
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
        """生成双周报——文件级时间窗口扫描 + LLM 直接理解。

        新管道：扫描 SOURCE 中 4 类目录的近两周文件 → 构建文件清单
        → LLM 按 OP 方向理解、整合、排重、输出结构化周报。
        不再依赖 chunk 检索，不再遗漏关键词匹配不到的文档。
        """
        from datetime import timezone

        now = datetime.now(timezone.utc)
        two_weeks_ago = now - timedelta(days=14)
        period = f"{two_weeks_ago.strftime('%Y.%m.%d')}～{now.strftime('%Y.%m.%d')}"

        if not query:
            query = f"近两周({period})工作进展"

        # 1. 加载 OP 规划文档作为框架
        op_doc = self._load_op_document()

        # 2. 加载上期双周报作为格式范例
        previous_report = self._load_previous_biweekly()

        # 3. 收集近两周的数据源文件
        files = self._collect_recent_files(two_weeks_ago.replace(tzinfo=None))

        # 4. 构建文件清单文本
        file_manifest = _build_file_manifest(files)

        if mode == "local":
            markdown = _build_local_fallback(period, op_doc, file_manifest)
            return ReportResponse(query=query, mode="local", markdown=markdown, blocks=[],
                                  structured={}, llm={"fallback_used": False})

        # 5. LLM 模式：模板 + LLM 生成
        try:
            cfg = self._config.app.get("biweekly_report", {})
            author_info = cfg.get("author_info", "你是一个技术团队的写作助手。")
            prompt = self._prompt_loader.render("biweekly_report.md", {
                "period": period,
                "author_info": author_info,
                "op_doc": op_doc or "（未找到 OP 规划文档）",
                "previous_report": previous_report or "（无上期双周报）",
                "file_manifest": file_manifest,
            })
            markdown = self._llm.generate(
                prompt=prompt,
                route_context={
                    "input_type": "text", "task_type": "analysis",
                    "complexity": "complex", "use_case": "biweekly_report",
                }
            ).text
            markdown = markdown.strip()
            from iris.wiki.generator import WikiGenerator
            markdown = WikiGenerator._extract_wiki_content(markdown)

            if not markdown.startswith("*时间周期"):
                markdown = f"*时间周期：{period}*\n\n{markdown}"

            llm_payload = {"fallback_used": False, "file_count": len(files)}
            result = ReportResponse(query=query, mode="llm", markdown=markdown,
                                     blocks=[], structured={}, llm=llm_payload)
            self._logger.log("biweekly_report", result.to_dict())
            return result

        except LLMProviderError as exc:
            self._logger.log("biweekly_llm_fallback", {"query": query, "reason": str(exc)})
            markdown = _build_local_fallback(period, op_doc, file_manifest)
            return ReportResponse(query=query, mode="local_fallback", markdown=markdown,
                                  blocks=[], structured={},
                                  llm={"fallback_used": True, "reason": str(exc)})

    # ── OP 文档加载 ──────────────────────────────────────────

    def _load_op_document(self) -> str:
        """加载 SOURCE 中的 OP 规划文档（缓存，避免重复加载）。"""
        if self._op_text_cache is not None:
            return self._op_text_cache
        from pathlib import Path as _Pt
        sources = self._config.data_source.get("sources", {})
        for cfg in sources.values():
            src_path = _Pt(cfg.get("path", "")).resolve()
            if not src_path.exists():
                continue
            op_dir = src_path / "01-目标管理"
            if op_dir.exists():
                for f in sorted(op_dir.glob("*.md"), reverse=True):
                    try:
                        text = f.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    if text.startswith("---"):
                        parts = text.split("---", 2)
                        text = parts[2].strip() if len(parts) >= 3 else text
                    self._op_text_cache = text[:8000]
                    return self._op_text_cache
        self._op_text_cache = ""
        return ""

    # ── 上期双周报 ──────────────────────────────────────────

    def _load_previous_biweekly(self) -> str:
        """加载上期双周报作为格式范例（最新一份 06-我的周报/ 下的文件）。"""
        source_root = _resolve_source_root(self._config)
        if not source_root:
            return ""
        report_dir = source_root / "06-我的周报"
        if not report_dir.exists():
            return ""
        files = sorted(report_dir.glob("双周报-*.md"), reverse=True)
        if not files:
            return ""
        try:
            content = files[0].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ""
        # 去掉尾部生成标记行，保留正文
        content = re.sub(r'\n*> This report was.*$', '', content, flags=re.MULTILINE)
        return content[:5000]

    # ── 文件收集 ────────────────────────────────────────────

    @staticmethod
    def _extract_date_from_path(relative_path: str) -> Optional[datetime]:
        """从文件路径中提取日期（YYYYMMDD 格式）。"""
        m = re.search(r"(\d{8})", relative_path)
        if not m:
            return None
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            return None

    @staticmethod
    def _build_citation_label(filename: str, dir_label: str) -> str:
        """构建简化引用标签。

        规则：
        - 成员周报: 提取人名 → "团队成员B周报-0703"
        - 会议纪要: 保留类型前缀 + 完整描述 → "项目讨论/某检测项目拆修检测-0626"
        - 讨论思考: 保留类型前缀 + 完整描述 → "内部讨论/质检执行智能化-0701"
        - 其他（方案报告等）: 显式标注目录类型 → "方案报告/图验技术进展-0625"
        """
        m = re.match(r'(\d{4})(\d{2})(\d{2})', filename)
        mmdd = f"{m.group(2)}{m.group(3)}" if m else ""

        # 去掉日期前缀和扩展名
        name = re.sub(r'^\d{8}-?', '', filename).replace('.md', '')

        if dir_label == "成员周报":
            person_m = re.search(r'[-—]([一-鿿]{2,3})(?:\.|$)', name)
            if person_m:
                return f"{person_m.group(1)}周报-{mmdd}"
            return f"{name[:10]}-{mmdd}"

        # 会议纪要 / 讨论思考 / 方案报告：完整描述名 + MMDD
        # 例：项目讨论-某检测项目拆修检测-H2检出率目标测算及少人工机会点讨论-0702
        if dir_label in ("会议纪要", "讨论思考", "方案报告"):
            return f"{name}-{mmdd}"

    def _collect_recent_files(self, since_date: datetime) -> list[dict]:
        """收集近两周的数据源文件。

        扫描 03-方案报告、04-讨论思考、05-会议纪要、07-成员周报，
        按文件名 YYYYMMDD 过滤。成员周报每人只保留最新一份。
        每文件截断到 2000 字。
        """
        target_dirs = [
            ("03-方案报告", "方案报告"),
            ("04-讨论思考", "讨论思考"),
            ("05-会议纪要", "会议纪要"),
            ("07-成员周报", "成员周报"),
        ]
        source_root = _resolve_source_root(self._config)
        if not source_root:
            return []

        all_files = []
        for dir_name, dir_label in target_dirs:
            dir_path = source_root / dir_name
            if not dir_path.exists():
                continue
            for f in sorted(dir_path.glob("*.md")):
                d = self._extract_date_from_path(f.name)
                if d is None or d < since_date:
                    continue
                try:
                    content = f.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                # 去掉 frontmatter
                if content.startswith("---"):
                    parts = content.split("---", 2)
                    content = parts[2].strip() if len(parts) >= 3 else content
                label = self._build_citation_label(f.name, dir_label)
                all_files.append({
                    "date": d,
                    "dir": dir_label,
                    "filename": f.name,
                    "label": label,
                    "content": content,
                    "char_count": len(content),
                })

        # 成员周报去重：同一人只保留最新一份
        all_files.sort(key=lambda x: (-x["date"].timestamp(), x["dir"]))
        seen_persons = set()
        deduped = []
        for f in all_files:
            if f["dir"] == "成员周报":
                person_key = f["label"].replace("周报", "").rsplit("-", 1)[0]
                if person_key in seen_persons:
                    continue
                seen_persons.add(person_key)
            deduped.append(f)

        return deduped


# ── 模块级辅助函数 ──────────────────────────────────────────


def _resolve_source_root(bundle) -> Optional[Path]:
    """解析数据源根目录（第一个启用的数据源的 path）。"""
    sources = bundle.data_source.get("sources", {})
    for cfg in sources.values():
        if cfg.get("enabled") and cfg.get("path"):
            p = Path(cfg["path"]).resolve()
            if p.exists():
                return p
    return None


def _build_file_manifest(files: list[dict]) -> str:
    """构建 LLM 输入的文件清单文本。

    按目录分组，每文件输出：引用标签 + 内容（截断到 2000 字）。
    """
    if not files:
        return "（近两周无数据源文件）"

    MAX_CHARS = 2000
    lines = []
    # 按目录分组
    from collections import OrderedDict
    groups = OrderedDict()
    for f in files:
        groups.setdefault(f["dir"], []).append(f)

    for dir_label, group_files in groups.items():
        lines.append(f"### {dir_label}（{len(group_files)} 份）")
        lines.append("")
        for f in group_files:
            content = f["content"]
            truncated = content if len(content) <= MAX_CHARS else content[:MAX_CHARS] + "\n…[截断]"
            lines.append(f"#### 引用标签: {f['label']}")
            lines.append(f"文件: {f['filename']}")
            lines.append(f"日期：{f['date'].strftime('%Y-%m-%d')} | 字数：{f['char_count']}")
            lines.append("")
            lines.append(truncated)
            lines.append("")
        lines.append("")

    return "\n".join(lines)


def _build_local_fallback(period: str, op_doc: str, file_manifest: str) -> str:
    """降级模式：简版双周报（LLM 不可用时的回退）。"""
    lines = [
        f"*时间周期：{period}*",
        "",
        "## 本周进展汇总",
        "",
        "*以下为基于近两周文件的自动汇总，建议使用 LLM 模式获得更高质量报告。*",
        "",
        op_doc[:3000] if op_doc else "",
        "",
        file_manifest[:5000] if file_manifest else "",
        "",
        "---",
        "> This report was generated by Iris.",
    ]
    return "\n".join(lines)

    def _review_and_revise(self, query, draft, structured, llm_payload) -> Tuple[str, Optional[Dict], bool]:
        structured_ctx = render_structured_evidence(structured)
        try:
            review_prompt = self._prompt_loader.render("report_review.md", {"query": query, "draft": draft, "structured_context": structured_ctx})
            review_resp = self._llm.generate(prompt=review_prompt, route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "user_selected_role": "adv_model"}).text
            review_data = _parse_review_json(review_resp)
            if not review_data or review_data.get("quality_score", 5) >= 4:
                return draft, review_data, False
            issues_text = "\n".join(f"- {i}" for i in review_data.get("issues", []))
            suggestions_text = "\n".join(f"- {s}" for s in review_data.get("suggestions", []))
            revise_prompt = self._prompt_loader.render("report_revise.md", {"query": query, "issues": issues_text or "无", "suggestions": suggestions_text or "无", "draft": draft, "structured_context": structured_ctx})
            revise_resp = self._llm.generate(prompt=revise_prompt, route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "use_case": "analysis_basic"}).text
            return revise_resp.strip(), review_data, True
        except LLMProviderError:
            return draft, None, False


def _parse_review_json(text: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 审查 JSON（委托到中心化工具）。"""
    from iris.utils.llm_parsing import try_parse_json, extract_json_from_text
    result = try_parse_json(text)
    if result is not None and "quality_score" in result:
        return result
    # 按 key 提取
    extracted = extract_json_from_text(text, "quality_score")
    if extracted is not None:
        return extracted
    # raw JSON 作为回退
    return try_parse_json(text)


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
