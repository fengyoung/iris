"""分析模块共享辅助函数。"""

from __future__ import annotations

from typing import Any, Dict, List


def render_evidence_blocks(blocks: List[Dict[str, Any]]) -> str:
    if not blocks:
        return "暂无候选证据"
    lines = []
    for index, block in enumerate(blocks, start=1):
        section = " > ".join(block["section_path"]) if block["section_path"] else block["title"]
        lines.append(f"{index}. 类型：{block['evidence_type']}；标题：{block['title']}；"
                     f"章节：{section}；来源：{block['relative_path']}:{block['line_start']}；"
                     f"内容：{block['summary']}")
    return "\n".join(lines)


def render_structured_evidence(structured: Dict[str, Any]) -> str:
    if not structured:
        return "无"
    lines = [f"- 总览：{structured.get('overview', '无')}"]
    for name in structured.get("ordered_groups", []):
        items = structured.get("groups", {}).get(name, [])
        if items:
            lines.append(f"- {name}: " + " | ".join(item["summary"] for item in items[:2]))
    return "\n".join(lines)
