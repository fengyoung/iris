"""双周报生成辅助函数 — 从 service.py 提取的纯函数，无外部状态依赖。

职责：Stage 3 数据准备、子领域分组、报告渲染、JSON 解析。
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional



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
        parts.append("**本方向自有概念/项目**（以下内容归属本方向）：")
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


# ── Stage 3 数据准备辅助函数 ─────────────────────────────────


def _s3_build_direction_index(directions: list) -> tuple[dict, dict]:
    """构建方向名称/ID → 方向定义的索引。返回 (dir_by_name, dir_by_id)。"""
    dir_by_name: dict = {}
    dir_by_id: dict = {}
    for d in directions:
        d_name = d.get("name", "")
        d_id = d.get("id", 0)
        if d_name:
            dir_by_name[d_name] = d
        if d_id:
            dir_by_id[d_id] = d
    return dir_by_name, dir_by_id


def _s3_index_briefs_by_direction(file_briefs: dict, dir_by_name: dict,
                                   dir_by_id: dict) -> dict:
    """按方向收集 brief，支持所有 relevant_directions（含 primary）。

    修复：不再因 primary 独占而遗漏跨方向内容（一份文件可能横跨多个方向）。
    """
    dir_brief_index: dict = {}
    for label, brief in file_briefs.items():
        relevant = brief.get("relevant_directions", [])
        if not relevant:
            primary = brief.get("primary_direction")
            if isinstance(primary, str) and primary.isdigit():
                primary = int(primary)
            if isinstance(primary, int):
                relevant = [primary]
        for ref in relevant:
            ref_str = str(ref)
            ref_int = int(ref) if isinstance(ref, (int, str)) and str(ref).isdigit() else None
            d_name = ""
            if ref in dir_by_name:
                d_name = dir_by_name[ref].get("name", "")
            elif ref_str in dir_by_name:
                d_name = dir_by_name[ref_str].get("name", "")
            elif ref_int is not None and ref_int in dir_by_id:
                d_name = dir_by_id[ref_int].get("name", "")
            if d_name:
                dir_brief_index.setdefault(d_name, []).append(brief)
    return dir_brief_index


def _s3_build_concept_boundaries(directions: list) -> dict:
    """从 OP 方向定义中提取概念边界。返回 {dir_name: {own: [...], others: {...}}}。"""
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
    return boundary_by_dir


def _s3_load_historical_context(collector, directions: list) -> tuple[dict, dict]:
    """加载多期历史双周报，提取去重参考。

    Returns:
        (multi_dedup, prev_by_dir)
        - multi_dedup: {dir_name: dedup_text} — 多期去重参考
        - prev_by_dir: {dir_name: content} — 最近一期按方向章节（兼容）
    """
    recent_reports = collector.load_recent_biweeklies(since_days=35)
    today = datetime.now().strftime("%Y%m%d")
    history_reports = [r for r in recent_reports
                       if r["date"].strftime("%Y%m%d") != today]
    multi_dedup: dict = {}
    if history_reports:
        multi_dedup = _build_multi_report_dedup_text(history_reports, directions)

    prev_report = recent_reports[0]["content"][:5000] if recent_reports else ""
    prev_by_dir: dict = {}
    if prev_report:
        prev_by_dir = _extract_previous_direction_sections(prev_report, directions)
    return multi_dedup, prev_by_dir


def _s3_extract_strategic_insights(briefs_for_dir: list) -> str:
    """从讨论思考类 brief 中提取战略洞察文本。"""
    strategic_insights: list[str] = []
    for b in briefs_for_dir:
        for si in b.get("strategic_insights", []) or []:
            if si and si not in strategic_insights:
                strategic_insights.append(si)
    if not strategic_insights:
        return ""
    insight_lines = ["## 战略洞察（来自讨论思考，优先用于战略分析段）", ""]
    insight_lines.extend(f"- {si}" for si in strategic_insights)
    return "\n".join(insight_lines) + "\n"


# ── 子领域分组 ──────────────────────────────────────────


# 预设子领域关键词（用于 brief 到 OP 子领域的模糊匹配）
# 当方向定义中未配置 sub_areas 时作为兜底，优先从 OP 文档的方向定义中读取。
# 用户可在此按实际业务子领域自定义，键为子领域名称，值为相关关键词列表。
_SUB_AREA_KEYWORDS: dict = {
    # 示例：
    # "子领域A": ["关键词1", "关键词2", "关键词3"],
    # "子领域B": ["关键词4", "关键词5"],
}


def _group_briefs_by_subarea(briefs: list, direction: dict) -> dict:
    """将 brief 按 OP 子领域概念分组。

    优先匹配方向定义中的 sub_areas，然后使用预设关键词兜底。
    返回 {sub_area_name: [brief, ...], "跨领域综合": [...]}

    每份 brief 只归入匹配度最高的一个组。
    """
    sub_areas = direction.get("sub_areas", [])
    direction.get("name", "")
    direction.get("id", 0)

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
