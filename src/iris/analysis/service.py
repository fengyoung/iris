"""分析报告生成服务。"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from iris.utils.paths import resolve_source_root as _resolve_source_root

from ._helpers import render_evidence_blocks, render_structured_evidence
from ._biweekly_collector import BiweeklyCollector
from ._biweekly_cache import BiweeklyCache

logger = logging.getLogger(__name__)


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
        self._collector = BiweeklyCollector(config)
        self._cache = BiweeklyCache(config.root / "data" / "build-biweekly-report")

    # ── 缓存路径（向后兼容属性） ─────────────────────────────────

    @property
    def _biweekly_cache_dir(self):
        return self._cache.cache_dir

    @staticmethod
    def _content_hash(text: str, prefix_len: int = 2000) -> str:
        return BiweeklyCache.content_hash(text, prefix_len)

    # ── _review_and_revise（供 build_report 使用）────────────────

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

    def build_biweekly_report(self, *, query: str = "", mode: str = "llm",
                              style_from: Optional[str] = None,
                              dry_run: bool = False) -> ReportResponse:
        """多阶段流水线：文件圈选 → 智能摘要 → 方向合成 → 终稿审查。

        Stage 0a: OP 文档解析 → 结构化方向定义（缓存）
        Stage 0b: 风格指南加载（条件，默认读缓存）
        Stage 1:  按方向过滤文件（LLM 语义判定）
        Stage 2:  文件深度摘要（全文不截断，每文件独立 LLM）
        Stage 3:  单方向章节合成
        Stage 4:  终稿组装 + 质量审查修订

        dry_run=True 时：仅收集文件 + 解析 OP，输出清单和方向匹配预览，不执行 LLM 阶段。
        """
        now = datetime.now()  # 本地时间，与文件名日期语义一致
        biweekly_cfg = self._config.app.get("biweekly_report", {})
        lookback_days = biweekly_cfg.get("lookback_days", 14)
        two_weeks_ago = now - timedelta(days=lookback_days)
        period = f"{two_weeks_ago.strftime('%Y.%m.%d')}～{now.strftime('%Y.%m.%d')}"

        if not query:
            query = f"近两周({period})工作进展"

        op_doc = self._collector.load_op_document()
        files = self._collector.collect_recent_files(two_weeks_ago)

        if not files:
            return ReportResponse(query=query, mode="llm",
                markdown=f"*时间周期：{period}*\n\n近两周无数据源文件。",
                blocks=[], structured={}, llm={"fallback_used": False})

        if dry_run:
            # 仅输出文件清单和 OP 方向预览，不调用 LLM
            op_result = self._stage0a_parse_op(op_doc)
            directions = op_result.get("directions", [])
            dir_summary = "\n".join(
                f"- 方向{d.get('id', '?')}: {d.get('name', '?')} — {d.get('scope_summary', '')[:120]}"
                for d in directions
            )
            file_list = "\n".join(
                f"- [{f['dir']}] {f['label']} ({f['date'].strftime('%Y-%m-%d')}, {f['char_count']}字)"
                for f in files
            )
            markdown = (
                f"*时间周期：{period}*\n\n"
                f"## 📋 数据源文件清单（{len(files)} 份）\n\n{file_list}\n\n"
                f"## 🎯 OP 方向定义（{len(directions)} 个）\n\n{dir_summary}\n\n"
                f"---\n> 这是 dry-run 预览模式，未执行 LLM 生成。"
                f"去掉 --dry-run 参数即可正式生成双周报。\n"
            )
            return ReportResponse(query=query, mode="dry_run", markdown=markdown,
                                  blocks=[], structured={"op_directions": directions},
                                  llm={"fallback_used": False})

        if mode == "local":
            file_manifest = _build_file_manifest(files)
            markdown = _build_local_fallback(period, op_doc, file_manifest)
            return ReportResponse(query=query, mode="local", markdown=markdown,
                                  blocks=[], structured={})

        try:
            # ── Stage 0a: OP 文档解析 ──
            logger.info("Stage 0a: 解析 OP 文档…")
            op_result = self._stage0a_parse_op(op_doc)
            directions = op_result.get("directions", [])
            if not directions:
                raise LLMProviderError("Stage 0a 未能解析出任何方向")

            # ── Stage 0b: 风格指南 ──
            style_guide = self._stage0b_load_style(style_from)

            # ── Stage 1: 方向-文件匹配 ──
            logger.info("Stage 1: 方向-文件匹配 (%d 个方向)…", len(directions))
            dir_file_map = self._stage1_filter_files(directions, files)

            # ── Stage 2: 文件深度摘要 ──
            logger.info("Stage 2: 文件深度摘要…")
            file_briefs = self._stage2_summarize_files(directions, dir_file_map, files)

            # ── Stage 3: 单方向章节合成 ──
            logger.info("Stage 3: 单方向章节合成…")
            sections = self._stage3_synthesize_directions(directions, style_guide, file_briefs)

            # ── Stage 4: 组装 + 审查 ──
            logger.info("Stage 4: 终稿组装 + 质量审查…")
            markdown = self._stage4_assemble_and_review(period, sections, directions)

            # 后处理
            markdown = markdown.strip()
            if not markdown.startswith("*时间周期"):
                markdown = f"*时间周期：{period}*\n\n{markdown}"
            footer = "\n\n---\n> This report was written by Iris and revised by maintainer."
            if not markdown.endswith(footer.strip()):
                markdown += footer

            llm_payload = {
                "fallback_used": False,
                "file_count": len(files),
                "brief_count": len(file_briefs),
                "direction_count": len(directions),
            }
            result = ReportResponse(query=query, mode="llm", markdown=markdown,
                                     blocks=[], structured={}, llm=llm_payload)
            self._logger.log("biweekly_report", result.to_dict())
            return result

        except LLMProviderError as exc:
            self._logger.log("biweekly_llm_fallback", {"query": query, "reason": str(exc)})
            file_manifest = _build_file_manifest(files)
            markdown = _build_local_fallback(period, op_doc, file_manifest)
            return ReportResponse(query=query, mode="local_fallback", markdown=markdown,
                                  blocks=[], structured={},
                                  llm={"fallback_used": True, "reason": str(exc)})

    # ── Stage 0a: OP 文档解析 ──────────────────────────────────

    def _stage0a_parse_op(self, op_doc: str) -> dict:
        """解析 OP 文档为结构化方向定义（content_hash 缓存）。"""
        if not op_doc:
            return {"directions": []}

        content_hash = self._cache.content_hash(op_doc, len(op_doc))

        cached_directions = self._cache.load_op_directions(content_hash)
        if cached_directions is not None:
            return {"directions": cached_directions}

        prompt = self._prompt_loader.render("biweekly_stage0a_op.md", {"op_doc": op_doc})
        result = self._llm.generate(prompt=prompt, route_context={
            "input_type": "text", "task_type": "analysis",
            "complexity": "standard", "use_case": "biweekly_report_stage0a",
        })

        parsed = _try_parse_json(result.text)
        directions = parsed.get("directions", []) if parsed else []

        if directions:
            self._cache.save_op_directions(content_hash, directions)
        else:
            logger.warning("  Stage 0a 未能解析出方向，返回空")

        return {"directions": directions}

    # ── Stage 0b: 风格指南 ──────────────────────────────────

    _DEFAULT_STYLE_GUIDE: dict = {
        "narrative_voice": "决策者视角，有判断有观点，不罗列事实",
        "paragraph_structure": "引用块(OP方向定位) → 战略分析段(4-8句) → 关键进展bullets",
        "citation_style": "（来源：标签1 / 标签2），原样复制不修改",
        "density_note": "每方向3-6条关键进展，每条含量化数据+来源",
        "strategic_patterns": [
            "整体评价开头 → 关键突破引述 → 风险暴露分析 → 下一步重心指向",
            "用'但'、'然而'标记结构性风险，用'下一步'指明行动方向",
        ],
        "writing_rules": [
            "每个方向一个 ## 章节",
            "bullet 中须含量化数据",
            "引用标签放在句末括号内",
            "无实质进展方向标注「本期无显著进展」",
        ],
    }

    def _stage0b_load_style(self, style_from: Optional[str] = None) -> dict:
        """加载风格指南：--style-from 时分析并缓存，否则读缓存/默认。"""
        if style_from:
            style_source = Path(style_from)
            if not style_source.is_absolute():
                source_root = _resolve_source_root(self._config)
                if source_root:
                    style_source = source_root / "06-我的周报" / style_from
            if not style_source.exists():
                logger.warning("  风格源文件不存在: %s，回退", style_from)
                cached = self._cache.load_style_guide()
                return cached if cached else dict(self._DEFAULT_STYLE_GUIDE)

            content = style_source.read_text(encoding="utf-8")
            content = re.sub(r'\n*---\n> This report was.*$', '',
                             content, flags=re.DOTALL | re.MULTILINE).strip()

            prompt = self._prompt_loader.render("biweekly_stage0b_style.md",
                                                {"previous_report": content[:8000]})
            result = self._llm.generate(prompt=prompt, route_context={
                "input_type": "text", "task_type": "analysis",
                "complexity": "standard", "use_case": "biweekly_report_stage0b",
            })

            guide = _try_parse_json(result.text)
            if guide:
                guide.setdefault("version", 1)
                guide["source_file"] = str(style_source)
                self._cache.save_style_guide(guide)
                logger.info("  风格指南已更新并缓存: %s", style_source.name)
                return guide
            logger.warning("  Stage 0b 风格解析失败，回退")

        cached = self._cache.load_style_guide()
        if cached:
            return cached

        logger.info("  使用内置默认风格指南")
        return dict(self._DEFAULT_STYLE_GUIDE)

    def _read_style_cache(self, cache_path: Path) -> dict:
        try:
            return json.loads(cache_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return dict(self._DEFAULT_STYLE_GUIDE)

    # ── Stage 1: 方向-文件匹配 ────────────────────────────────

    def _stage1_filter_files(self, directions: list, files: list) -> dict:
        """按方向并行过滤文件（LLM 语义判定，结果缓存）。"""
        inventory_lines = []
        for f in files:
            preview = f["content"][:300].replace("\n", " ")
            inventory_lines.append(
                f"[{f['label']}] | {f['date'].strftime('%m%d')} | {f['dir']} | "
                f"{f['char_count']}字 | 摘要：{preview}"
            )
        file_inventory = "\n".join(inventory_lines)
        all_labels = {f["label"] for f in files}

        inv_hash = self._cache.content_hash(file_inventory, 2000)
        dir_hash = self._cache.content_hash(
            json.dumps([{"id": d.get("id"), "name": d.get("name")} for d in directions],
                       ensure_ascii=False, sort_keys=True), 2000)

        cached = self._cache.load_stage1_filter(inv_hash, dir_hash)
        if cached is not None:
            return cached

        def _filter_one(direction: dict) -> dict:
            d_id = direction.get("id", 0)
            d_name = direction.get("name", f"方向{d_id}")

            dir_def = json.dumps(direction, ensure_ascii=False, indent=2)
            prompt = self._prompt_loader.render("biweekly_stage1_filter.md", {
                "direction_def": dir_def,
                "file_inventory": file_inventory,
                "direction_id": str(d_id),
            })

            result = self._llm.generate(prompt=prompt, route_context={
                "input_type": "text", "task_type": "analysis",
                "complexity": "standard", "use_case": "biweekly_report_stage1",
            })

            parsed = _try_parse_json(result.text)
            if not parsed:
                logger.warning("  Stage 1 过滤 %s 失败，所有文件归入 low", d_name)
                return {"direction_name": d_name,
                        "high": [], "medium": [],
                        "low": [{"label": l} for l in all_labels], "none": []}

            for level in ("high", "medium", "low", "none"):
                items = parsed.get(level, [])
                parsed[level] = [i for i in items
                                 if isinstance(i, dict) and i.get("label", "") in all_labels]

            parsed["direction_name"] = d_name
            logger.info("  %s: high=%d medium=%d low=%d",
                        d_name[:25], len(parsed.get("high", [])),
                        len(parsed.get("medium", [])), len(parsed.get("low", [])))
            return parsed

        dir_file_map: dict = {}
        with ThreadPoolExecutor(max_workers=min(len(directions), 6)) as executor:
            futures = {executor.submit(_filter_one, d): d for d in directions}
            for future in as_completed(futures):
                try:
                    r = future.result()
                    dir_file_map[r["direction_name"]] = r
                except Exception as e:
                    d = futures[future]
                    d_name = d.get("name", f"方向{d.get('id','?')}")
                    logger.warning("  Stage 1 过滤失败 %s: %s", d_name, e)
                    dir_file_map[d_name] = {
                        "direction_name": d_name,
                        "high": [], "medium": [],
                        "low": [{"label": l} for l in all_labels], "none": [],
                    }

        self._cache.save_stage1_filter(inv_hash, dir_hash, dir_file_map)
        return dir_file_map

    # ── Stage 2: 文件深度摘要 ─────────────────────────────────

    def _stage2_summarize_files(self, directions: list, dir_file_map: dict,
                                files: list) -> dict:
        """对 high+medium+low 文件并集进行深度摘要（缓存感知）。"""
        needed_labels: dict = {}
        for d_name, mapping in dir_file_map.items():
            for level in ("high", "medium", "low"):
                for item in mapping.get(level, []):
                    label = item.get("label", "")
                    if label:
                        needed_labels.setdefault(label, []).append(d_name)

        if not needed_labels:
            logger.info("  Stage 2: 无 high/medium 文件，跳过")
            return {}

        file_by_label = {f["label"]: f for f in files}
        brief_index = self._cache.load_brief_index()

        to_summarize: list = []
        briefs: dict = {}

        for label, dir_names in needed_labels.items():
            f_data = file_by_label.get(label)
            if not f_data:
                continue

            hash_key = self._cache.content_hash(f_data["content"], 2000)
            cached_brief = self._cache.load_brief(label, hash_key, brief_index)
            if cached_brief is not None:
                briefs[label] = cached_brief
                continue

            f_copy = dict(f_data)
            f_copy["_dir_names"] = dir_names
            to_summarize.append(f_copy)

        if cached := len(briefs):
            logger.info("  命中缓存: %d 份", cached)
        if to_summarize:
            logger.info("  需要摘要: %d 份", len(to_summarize))

        if not to_summarize:
            return briefs

        with ThreadPoolExecutor(max_workers=min(len(to_summarize), 8)) as executor:
            futures = {executor.submit(self._summarize_one_file, f, directions): f["label"]
                       for f in to_summarize}
            for future in as_completed(futures):
                label = futures[future]
                try:
                    brief = future.result()
                    if brief:
                        brief["dir_type"] = file_by_label.get(label, {}).get("dir", "")
                        briefs[label] = brief
                        f_data = file_by_label.get(label)
                        if f_data:
                            hk = self._cache.content_hash(f_data["content"], 2000)
                            self._cache.save_brief(label, hk, brief, brief_index)
                except Exception as e:
                    logger.warning("  Stage 2 摘要失败 [%s]: %s", label, e)

        self._cache.flush_brief_index(brief_index)
        logger.info("  Stage 2 完成: %d 份 Brief", len(briefs))
        return briefs

    def _summarize_one_file(self, f_data: dict, _directions: list) -> Optional[dict]:
        """对单份文件进行深度摘要。"""
        MAX_CHARS = 50000

        content = f_data["content"]
        if len(content) > MAX_CHARS:
            logger.warning("  文件过长 [%s]: %d 字 → 截断至 %d 字",
                           f_data["label"], len(content), MAX_CHARS)
            content = content[:MAX_CHARS]

        dir_names = f_data.get("_dir_names", [])
        dir_parts = []
        for d in _directions:
            d_name = d.get("name", "")
            d_id = d.get("id", 0)
            if d_name in dir_names:
                dir_parts.append(f"方向{d_id}: {d_name} — {d.get('scope_summary', '')}")
        dir_context = "\n".join(dir_parts) if dir_parts else "通用"

        prompt = self._prompt_loader.render("biweekly_stage2_summarize.md", {
            "direction_context": dir_context,
            "file_label": f_data["label"],
            "dir_type": f_data["dir"],
            "file_date": f_data["date"].strftime("%Y-%m-%d"),
            "file_content": content,
        })

        result = self._llm.generate(prompt=prompt, route_context={
            "input_type": "text", "task_type": "extraction",
            "complexity": "standard", "use_case": "biweekly_report_stage2",
        })

        return _try_parse_json(result.text)

    # ── Brief 缓存管理（向后兼容，委托给 BiweeklyCache）──────────

    @staticmethod
    def _load_brief_index(index_path: Path) -> dict:
        try:
            return json.loads(index_path.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save_brief_index(index_path: Path, brief_index: dict, briefs_dir: Path) -> None:
        index_path.write_text(json.dumps(brief_index, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        cutoff = (datetime.now() - timedelta(days=30)).timestamp()
        valid_hashes = set(brief_index.values())
        for f in briefs_dir.glob("*.json"):
            if f.name == "index.json":
                continue
            if f.name.replace(".json", "") not in valid_hashes:
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                except OSError:
                    pass

    # ── Stage 3: 单方向章节合成 ───────────────────────────────

    def _stage3_synthesize_directions(self, directions: list, style_guide: dict,
                                      file_briefs: dict) -> dict:
        """按方向并行合成章节。Returns {direction_name: markdown_section}."""
        style_text = json.dumps(style_guide, ensure_ascii=False, indent=2)

        # 构建名称→方向映射，支持 ID 和名称两种匹配方式
        dir_by_name: dict = {}
        dir_by_id: dict = {}
        for d in directions:
            d_name = d.get("name", "")
            d_id = d.get("id", 0)
            if d_name:
                dir_by_name[d_name] = d
            if d_id:
                dir_by_id[d_id] = d

        # 按方向收集 brief — 加入所有 relevant_directions（含 primary）
        # 修复：不再因 primary 独占而遗漏跨方向内容（如团队成员A周报含 某检测项目/某品牌项目 进展）
        dir_brief_index: dict = {}
        assigned_labels: set = set()
        for label, brief in file_briefs.items():
            relevant = brief.get("relevant_directions", [])
            if not relevant:
                # fallback: 无 relevant_directions 时用 primary
                primary = brief.get("primary_direction")
                if isinstance(primary, str) and primary.isdigit():
                    primary = int(primary)
                if isinstance(primary, int):
                    relevant = [primary]
            for ref in relevant:
                ref_str = str(ref)
                ref_int = int(ref) if isinstance(ref, (int, str)) and str(ref).isdigit() else None
                matched = False
                if ref in dir_by_name:
                    d_name = dir_by_name[ref].get("name", "")
                    matched = True
                elif ref_str in dir_by_name:
                    d_name = dir_by_name[ref_str].get("name", "")
                    matched = True
                elif ref_int is not None and ref_int in dir_by_id:
                    d_name = dir_by_id[ref_int].get("name", "")
                    matched = True
                if matched:
                    dir_brief_index.setdefault(d_name, []).append(brief)
                    assigned_labels.add(label)

        # ── 概念边界：从 OP 方向定义中提取 ──
        boundary_by_dir: dict = {}
        for d in directions:
            d_name = d.get("name", "")
            own_concepts = _collect_direction_concepts(d)
            other_concepts: dict = {}
            for od in directions:
                if od.get("name") == d_name:
                    continue
                other_concepts[od.get("name", "")] = _collect_direction_concepts(od)
            boundary_by_dir[d_name] = {"own": own_concepts, "others": other_concepts}

        # ── 多期历史双周报逐方向提取（去重衔接） ──
        recent_reports = self._collector.load_recent_biweeklies(since_days=35)
        # 排除最新一份（如果存在），因为我们不希望与自己对比
        # 实际上 _load_recent_biweeklies 返回的列表可能包含今天刚生成的报告，
        # 但我们只关心历史报告（至少是 1 天前的），所以排除当天的。
        today = datetime.now().strftime("%Y%m%d")
        history_reports = [r for r in recent_reports
                          if r["date"].strftime("%Y%m%d") != today]
        multi_dedup: dict = {}
        if history_reports:
            multi_dedup = _build_multi_report_dedup_text(history_reports, directions)
        # 兼容旧逻辑：保留最新一份完整内容用于风格参考
        prev_report = recent_reports[0]["content"][:5000] if recent_reports else ""
        prev_by_dir: dict = {}
        if prev_report:
            prev_by_dir = _extract_previous_direction_sections(prev_report, directions)

        def _synthesize_one(direction: dict) -> tuple:
            d_name = direction.get("name", "")
            briefs_for_dir = dir_brief_index.get(d_name, [])

            brief_lines = []
            if briefs_for_dir:
                # 当方向有子领域定义或 brief 较多时，按子领域分组
                has_sub_areas = bool(direction.get("sub_areas"))
                many_briefs = len(briefs_for_dir) > 8

                if has_sub_areas and many_briefs:
                    sub_groups = _group_briefs_by_subarea(briefs_for_dir, direction)
                    for sub_name, items in sub_groups.items():
                        brief_lines.append(f"### {sub_name}（{len(items)} 份）")
                        for b in items:
                            md = b.get("brief_md", "")
                            if md:
                                brief_lines.append(md)
                                brief_lines.append("")
                else:
                    # brief 较少时仍按 dir_type 分组
                    by_dir_type: dict = {}
                    for b in briefs_for_dir:
                        by_dir_type.setdefault(b.get("dir_type", "其他"), []).append(b)
                    for dir_type, items in by_dir_type.items():
                        brief_lines.append(f"### {dir_type}（{len(items)} 份）")
                        for b in items:
                            md = b.get("brief_md", "")
                            if md:
                                brief_lines.append(md)
                                brief_lines.append("")
            brief_text = "\n".join(brief_lines) if brief_lines else "（本期无相关文件）"

            # 提取讨论思考类文件的战略洞察（用于增强分析段）
            strategic_insights: list[str] = []
            for b in briefs_for_dir:
                for si in b.get("strategic_insights", []) or []:
                    if si and si not in strategic_insights:
                        strategic_insights.append(si)
            insights_text = ""
            if strategic_insights:
                insight_lines = ["## 战略洞察（来自讨论思考，优先用于战略分析段）", ""]
                insight_lines.extend(f"- {si}" for si in strategic_insights)
                insights_text = "\n".join(insight_lines) + "\n"

            dir_def = json.dumps(direction, ensure_ascii=False, indent=2)
            # 概念边界
            bounds = boundary_by_dir.get(d_name, {"own": [], "others": {}})
            boundaries_text = _build_boundaries_text(d_name, bounds)
            # 多期历史去重参考（优先使用多期去重文本）
            dedup_text = multi_dedup.get(d_name, "")
            if dedup_text:
                prev_text = (
                    "## 历史去重参考（仅供去重检查，禁止作为信息来源）\n\n"
                    "⚠️ 以下内容来自历史双周报，**仅供检查本期是否重复**。"
                    "**绝对禁止**从以下内容中提取进展、数据或来源标签用于本期报告。"
                    "本期所有进展必须且只能来自「本期相关素材」中的 brief。\n\n"
                    "去重规则：如果本期某条进展在历史中已出现（即使措辞不同，但事实相同），"
                    "则不要重复输出。同一项工作的增量更新，只输出增量部分，"
                    "并在表述中体现连续性（如'继上期xxx后，本期进一步yyy'）。\n\n"
                    f"{dedup_text}\n"
                )
            else:
                # 回退到单期去重
                prev_text = prev_by_dir.get(d_name, "")
                if prev_text:
                    prev_text = f"## 上期双周报中该方向的内容（本期不要重复以下已提过的进展）\n\n{prev_text[:1500]}\n"
                else:
                    prev_text = ""
            prompt = self._prompt_loader.render("biweekly_stage3_direction.md", {
                "direction_def": dir_def,
                "style_guide": style_text,
                "direction_boundaries": boundaries_text,
                "previous_direction_content": prev_text,
                "strategic_insights": insights_text,
                "file_briefs": brief_text,
            })

            result = self._llm.generate(prompt=prompt, route_context={
                "input_type": "text", "task_type": "analysis",
                "complexity": "standard", "use_case": "biweekly_report_stage3",
            })

            section = result.text.strip()
            if section.startswith("```"):
                section = re.sub(r'^```\w*\n?', '', section)
                section = re.sub(r'\n?```$', '', section)

            logger.info("  %s: 合成完成 (%d 字)", d_name[:25], len(section))
            return d_name, section

        sections: dict = {}
        with ThreadPoolExecutor(max_workers=min(len(directions), 4)) as executor:
            futures = {executor.submit(_synthesize_one, d): d for d in directions}
            for future in as_completed(futures):
                try:
                    name, section = future.result()
                    sections[name] = section
                except Exception as e:
                    d = futures[future]
                    d_name = d.get("name", "?")
                    logger.warning("  Stage 3 合成失败 %s: %s", d_name, e)
                    sections[d_name] = f"## {d_name}\n\n> 战略定位待补充\n\n本期无显著进展。\n"

        return sections

    # ── Stage 4: 终稿组装 + 质量审查 ─────────────────────────

    def _stage4_assemble_and_review(self, period: str, sections: dict,
                                     directions: list) -> str:
        """组装各方向章节并执行质量审查修订。"""
        ordered_sections = []
        for d in directions:
            d_name = d.get("name", "")
            if d_name in sections:
                ordered_sections.append(sections[d_name])

        direction_sections = "\n\n".join(ordered_sections)

        directions_summary = "\n".join(
            f"- {d.get('name', '')}: {d.get('scope_summary', '')[:100]}"
            for d in directions
        )

        prompt = self._prompt_loader.render("biweekly_stage4_assemble.md", {
            "period": period,
            "direction_sections": direction_sections,
            "directions_summary": directions_summary,
        })

        result = self._llm.generate(prompt=prompt, route_context={
            "input_type": "text", "task_type": "analysis",
            "complexity": "standard", "use_case": "biweekly_report_stage4",
        })

        markdown = result.text.strip()
        if markdown.startswith("```"):
            markdown = re.sub(r'^```\w*\n?', '', markdown)
            markdown = re.sub(r'\n?```$', '', markdown)

        if not markdown.startswith("*时间周期"):
            markdown = f"*时间周期：{period}*\n\n{markdown}"

        logger.info("  Stage 4 完成 (%d 字)", len(markdown))
        return markdown

    # ── 向后兼容委托方法（供外部代码或子类访问） ──────────────────

    def _load_op_document(self) -> str:
        """向后兼容：委托给 BiweeklyCollector。"""
        return self._collector.load_op_document()

    def _load_recent_biweeklies(self, since_days: int = 35) -> list[dict]:
        """向后兼容：委托给 BiweeklyCollector。"""
        return self._collector.load_recent_biweeklies(since_days=since_days)

    def _load_previous_biweekly(self) -> str:
        """向后兼容：委托给 BiweeklyCollector。"""
        return self._collector.load_previous_biweekly()

    def _collect_recent_files(self, since_date: datetime) -> list[dict]:
        """向后兼容：委托给 BiweeklyCollector。"""
        return self._collector.collect_recent_files(since_date)

    @staticmethod
    def _extract_date_from_path(relative_path: str) -> Optional[datetime]:
        """向后兼容：委托给 BiweeklyCollector。"""
        return BiweeklyCollector._extract_date_from_path(relative_path)

    @staticmethod
    def _extract_date_from_frontmatter(content: str) -> Optional[datetime]:
        """向后兼容：委托给 BiweeklyCollector。"""
        return BiweeklyCollector._extract_date_from_frontmatter(content)

    @staticmethod
    def _extract_person_from_filename(filename: str) -> Optional[str]:
        """向后兼容：委托给 BiweeklyCollector。"""
        return BiweeklyCollector._extract_person_from_filename(filename)

    @staticmethod
    def _build_citation_label(filename: str, dir_label: str) -> str:
        """向后兼容：委托给 BiweeklyCollector。"""
        return BiweeklyCollector._build_citation_label(filename, dir_label)

    # ── 质量审查（供 build_report 两阶段模式使用）──────────────

    def _review_and_revise(self, query, draft, structured, llm_payload) -> Tuple[str, Optional[Dict], bool]:
        structured_ctx = render_structured_evidence(structured)
        try:
            review_prompt = self._prompt_loader.render("report_review.md", {"query": query, "draft": draft, "structured_context": structured_ctx})
            review_resp = self._llm.generate(prompt=review_prompt, route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "user_selected_role": "adv_model"}).text
            review_data = _parse_review_json(review_resp)
            if not review_data:
                return draft, review_data, False
            score = review_data.get("quality_score")
            if score is None:
                logger.warning("review_data 缺少 quality_score 字段，跳过修订")
                return draft, review_data, False
            if score >= 4:
                return draft, review_data, False
            issues_text = "\n".join(f"- {i}" for i in review_data.get("issues", []))
            suggestions_text = "\n".join(f"- {s}" for s in review_data.get("suggestions", []))
            revise_prompt = self._prompt_loader.render("report_revise.md", {"query": query, "issues": issues_text or "无", "suggestions": suggestions_text or "无", "draft": draft, "structured_context": structured_ctx})
            revise_resp = self._llm.generate(prompt=revise_prompt, route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "use_case": "analysis_basic"}).text
            return revise_resp.strip(), review_data, True
        except LLMProviderError:
            return draft, None, False


# ── Stage 3 辅助函数 ─────────────────────────────────────────


def _collect_direction_concepts(direction: dict) -> list:
    """从一个 OP 方向定义中收集概念/项目名称列表。"""
    concepts = []
    for sa in direction.get("sub_areas", []):
        name = sa.get("name", "")
        if name:
            # 取子领域名的核心部分（去掉编号前缀如 "1.1 【验功能】"）
            core = name.split("】", 1)[-1] if "】" in name else name.split(" ", 1)[-1] if " " in name else name
            concepts.append(core.strip())
    return concepts


def _build_boundaries_text(dir_name: str, bounds: dict) -> str:
    """构建概念边界文本（注入 Stage 3 prompt）。"""
    parts = []
    own = bounds.get("own", [])
    if own:
        parts.append(f"**本方向自有概念/项目**（以下内容归属本方向）：")
        parts.append("、".join(own[:12]))
    others = bounds.get("others", {})
    if others:
        parts.append("")
        parts.append("**其他方向的概念/项目**（以下是其他方向的内容，如在素材中提及应严格排除）：")
        for other_dir, concepts in others.items():
            if concepts:
                short_name = other_dir.split("：")[-1] if "：" in other_dir else other_dir
                parts.append(f"- {short_name[:20]}：{'、'.join(concepts[:8])}")
    return "\n".join(parts)


def _extract_previous_direction_sections(prev_report: str, directions: list) -> dict:
    """从上一期双周报中提取每个方向的章节内容（兼容旧接口）。"""
    result: dict = {}
    for d in directions:
        d_name = d.get("name", "")
        content = _extract_direction_section(prev_report, d_name)
        if content:
            result[d_name] = content
    return result


def _build_multi_report_dedup_text(recent_reports: list[dict], directions: list) -> dict:
    """从多期历史双周报中提取每个方向的已覆盖内容，构建去重参考文本。

    Returns:
        {direction_name: dedup_text} — 每方向一段 Markdown，列出各期已提过的进展。
    """
    result: dict = {}
    if not recent_reports:
        return result

    for d in directions:
        d_name = d.get("name", "")
        parts: list[str] = []
        for report in recent_reports:
            section = _extract_direction_section(report["content"], d_name)
            if section:
                week = report.get("week", "?")
                date_str = report.get("date_str", "")
                # 截取每条 bullet 的前 100 字作为去重指纹
                bullets = _extract_key_bullets(section, max_per_report=6)
                if bullets:
                    parts.append(f"### w{week} 期（{date_str}）\n" + "\n".join(f"- {b}" for b in bullets))
        if parts:
            result[d_name] = "\n\n".join(parts)

    return result


def _extract_key_bullets(section: str, max_per_report: int = 6) -> list[str]:
    """从章节中提取关键进展 bullets（每条的摘要指纹）。

    只提取 bullet 行（以 - 或 * 开头），取前 max_per_report 条，
    每条截断到 120 字作为去重指纹。
    """
    bullets: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            # 去掉引用标签（来源：xxx）以减少去重噪音
            clean = re.sub(r'[（(]来源[：:][^)）]*[)）]', '', stripped).strip()
            bullets.append(clean[:120])
            if len(bullets) >= max_per_report:
                break
    return bullets


def _extract_direction_section(report: str, direction_name: str) -> str:
    """从双周报中提取某个方向的章节内容。

    兼容旧版（无「方向N：」前缀）和新版标题格式。
    """
    # 构建匹配关键词：去掉 "方向N：" 前缀，提取方向名核心部分
    core_name = re.sub(r'^方向[一二三四五六七八九十\d]+[：:]\s*', '', direction_name)
    # 取冒号前的关键词用于匹配
    keys = [direction_name, core_name]
    if "：" in core_name:
        keys.append(core_name.split("：")[-1])

    lines = report.splitlines()
    result_lines = []
    in_section = False
    for line in lines:
        if in_section:
            if line.startswith("## ") and "关键进展" not in line:
                break
            result_lines.append(line)
        elif line.startswith("## ") and any(k in line for k in keys):
            in_section = True
    return "\n".join(result_lines).strip()


# ── 子领域分组 ──────────────────────────────────────────


# 预设子领域关键词（用于 brief 到 OP 子领域的模糊匹配）
_SUB_AREA_KEYWORDS: dict = {
    "某检测项目": ["xray", "x光", "拆修", "拆机", "漏检", "检出率", "对抗", "检测点", "螺丝", "散热片"],
    "图像采集3.0": ["拍照3", "外观定级", "瑕疵", "清洁", "脏污", "无尘", "消毒", "一致性", "v21", "v22", "定级"],
    "图像验证": ["验真", "鉴定", "假货", "买断", "直检", "包袋", "chanel", "dior", "gucci", "奢侈品", "初鉴", "正式鉴定"],
    "视频审核": ["视频审核", "动作识别", "合规", "标准sop", "质检码", "视频成本"],
    "在线评估": ["在线评估", "基础信息", "颜色识别", "错杀", "99新", "审核"],
    "工作流": ["作业域", "流程编排", "要素", "端到端", "质检流程", "sop数字化"],
    "消费品类": ["消费品类", "流量调控", "低价", "热销", "sku", "降价", "手机同款"],
    "二手商品": ["二手商品", "奢品", "好逛", "冷启", "重排序", "价格分桶"],
    "兴趣品类": ["兴趣品类", "query结构化", "相关性", "意图识别", "长尾"],
    "推荐": ["推荐", "首页", "生成式召回", "ctr", "pctr", "特征工程"],
    "搜索": ["搜索", "召回", "排序", "精排", "粗排", "低质量商品"],
}


def _group_briefs_by_subarea(briefs: list, direction: dict) -> dict:
    """将 brief 按 OP 子领域概念分组。

    优先匹配方向定义中的 sub_areas，然后使用预设关键词兜底。
    返回 {sub_area_name: [brief, ...], "跨领域综合": [...]}

    每份 brief 只归入匹配度最高的一个组。
    """
    sub_areas = direction.get("sub_areas", [])
    d_name = direction.get("name", "")
    d_id = direction.get("id", 0)

    # 从子领域提取关键词
    groups: dict = {}
    area_keywords: list[tuple[str, list[str]]] = []

    for sa in sub_areas:
        sa_name = sa.get("name", "")
        groups[sa_name] = []
        # 从子领域名称提取关键词
        sa_keywords = set()
        # 去除编号前缀和【】标记
        clean_name = re.sub(r'^\d+\.\d+\s*【.*?】\s*', '', sa_name)
        # 中文词拆解
        for kw in [clean_name] + sa_name.replace("【", " ").replace("】", " ").split():
            kw = kw.strip()
            if kw and not re.match(r'^\d+\.?\d*$', kw):
                sa_keywords.add(kw.lower())
        # 从预设关键词中匹配该子领域
        for preset_group, preset_kws in _SUB_AREA_KEYWORDS.items():
            if any(pkw in sa_name.lower() or sa_name.lower() in pkw for pkw in preset_kws):
                sa_keywords.update(preset_kws)
        area_keywords.append((sa_name, list(sa_keywords)))

    # 额外：从方向整体描述中匹配预设分组
    fallback_groups: dict = {}
    for preset_group, preset_kws in _SUB_AREA_KEYWORDS.items():
        d_text = json.dumps(direction, ensure_ascii=False).lower()
        if any(kw in d_text for kw in preset_kws):
            fallback_groups[preset_group] = []

    # 无子领域但有预设匹配
    if not area_keywords and fallback_groups:
        for preset_group in fallback_groups:
            groups[f"【{preset_group}】"] = []
        groups["跨领域综合"] = []

    # 逐个 brief 归类
    for b in briefs:
        b_text = json.dumps({
            "label": b.get("label", ""),
            "key_facts": b.get("key_facts", []),
            "quantitative_data": b.get("quantitative_data", []),
        }, ensure_ascii=False).lower()

        best_match = None
        best_score = 0
        for sa_name, keywords in area_keywords:
            score = sum(1 for kw in keywords if kw.lower() in b_text) * 2
            # 权重加分：如果 label 直接包含关键词，更可靠
            label_lower = b.get("label", "").lower()
            for kw in keywords:
                if kw.lower() in label_lower:
                    score += 5
            if score > best_score:
                best_score = score
                best_match = sa_name

        # 没有子领域匹配时尝试预设分组兜底
        if best_match is None and fallback_groups:
            for preset_group, _ in fallback_groups.items():
                if preset_group in b_text or any(
                    kw in b_text for kw in _SUB_AREA_KEYWORDS.get(preset_group, [])
                ):
                    score = sum(1 for kw in _SUB_AREA_KEYWORDS.get(preset_group, [])
                                if kw.lower() in b_text)
                    if score > best_score:
                        best_score = score
                        best_match = f"【{preset_group}】"

        group_key = best_match if best_match and best_score > 0 else "跨领域综合"
        if group_key not in groups:
            groups[group_key] = []
        groups[group_key].append(b)

    # 清理空组
    return {k: v for k, v in groups.items() if v}

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


def _try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 输出的 JSON（容错）。"""
    from iris.utils.llm_parsing import try_parse_json as _parse
    # 先尝试整体解析
    result = _parse(text)
    if result is not None:
        return result
    # 再尝试提取 JSON 块
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        return _parse(m.group(0))
    return None


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
