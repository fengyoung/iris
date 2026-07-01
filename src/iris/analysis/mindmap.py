"""思维导图生成服务。"""

from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError
from iris.llm.service import LLMService
from iris.qa import QAService
from iris.utils.logging import IrisLogger

from ._helpers import render_evidence_blocks, render_structured_evidence
from iris.utils.prompting import PromptTemplateLoader


@dataclass
class MindmapNode:
    title: str
    children: List[MindmapNode] = field(default_factory=list)
    note: str = ""


def _parse_json_tree(text: str) -> Dict[str, Any]:
    """解析 LLM 返回的 JSON 树结构（委托到中心化 tools）。"""
    from iris.utils.llm_parsing import try_parse_json
    result = try_parse_json(text)
    if result is not None:
        return result
    # 简单花括号匹配作为最后回退
    match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"error": "无法解析 LLM 响应", "raw_text": text[:500]}


def _build_xmind_bytes(tree: MindmapNode) -> bytes:
    import xml.etree.ElementTree as ET
    ns = {"": "urn:xmind:xmap:xmlns:content:2.0"}
    ET.register_namespace("", ns[""])
    root_xml = ET.Element("xmap-content")
    sheet = ET.SubElement(root_xml, "sheet")
    topic = ET.SubElement(sheet, "topic")
    title_el = ET.SubElement(topic, "title")
    title_el.text = tree.title
    def add_children(parent_xml, nodes):
        for node in nodes:
            child = ET.SubElement(parent_xml, "topic")
            t = ET.SubElement(child, "title")
            t.text = node.title
            if node.children:
                add_children(child, node.children)
    add_children(topic, tree.children)
    xml_bytes = ET.tostring(root_xml, xml_declaration=True, encoding="UTF-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", xml_bytes)
        zf.writestr("META-INF/manifest.xml", '<?xml version="1.0" encoding="UTF-8"?><manifest xmlns="urn:xmind:xmap:xmlns:manifest:1.0"><file-entry full-path="content.xml" media-type="text/xml"/></manifest>')
    return buf.getvalue()


@dataclass(frozen=True)
class MindmapResponse:
    query: str
    format: str
    markdown: str
    tree: MindmapNode | None = None
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    structured: Dict[str, Any] | None = None
    llm: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {"query": self.query, "format": self.format, "markdown": self.markdown,
                "blocks": self.blocks, "structured": self.structured, "llm": self.llm}


class MindmapService:
    def __init__(self, config: ConfigBundle):
        self._config = config
        self._qa = QAService(config)
        self._llm = LLMService(config)
        self._prompt_loader = PromptTemplateLoader(config)
        self._logger = IrisLogger(config)

    def build_mindmap(self, query: str, *, top_k: int = 6, mode: str = "llm", format: str = "mermaid") -> MindmapResponse:
        qa_response = self._qa.ask(query, top_k=top_k, mode=mode)
        blocks = [{"title": b.title, "summary": b.summary, "relative_path": b.citation.relative_path,
                    "line_start": b.citation.line_start, "section_path": b.citation.section_path,
                    "score": b.score, "evidence_type": b.evidence_type} for b in qa_response.blocks]
        structured = qa_response.structured or {}

        if mode != "llm":
            markdown = _build_mermaid_mindmap(query, blocks, structured)
            return MindmapResponse(query=query, format=format, markdown=markdown, blocks=blocks, structured=structured, llm={"fallback_used": True, "reason": "local mode"})

        try:
            prompt = self._prompt_loader.render("mindmap_generate.md",
                {"query": query, "blocks": render_evidence_blocks(blocks),
                 "structured_context": render_structured_evidence(structured)})
            markdown = self._llm.generate(prompt=prompt,
                route_context={"input_type": "text", "task_type": "analysis", "complexity": "complex", "use_case": "analysis_basic"})
            llm_payload = {"fallback_used": False}
            tree = None
            markdown = markdown.strip()
            if format in ("xmind", "both"):
                json_tree = _parse_json_tree(markdown)
                tree = _dict_to_tree(json_tree) if "error" not in json_tree else None
            return MindmapResponse(query=query, format=format, markdown=markdown, tree=tree, blocks=blocks, structured=structured, llm=llm_payload)
        except LLMProviderError as exc:
            markdown = _build_mermaid_mindmap(query, blocks, structured)
            return MindmapResponse(query=query, format=format, markdown=markdown, blocks=blocks, structured=structured, llm={"fallback_used": True, "reason": str(exc)})


def _build_mermaid_mindmap(query: str, blocks, structured) -> str:
    lines = ["mindmap", f"  root(({query}))"]
    for name in structured.get("ordered_groups", [])[:5]:
        items = structured.get("groups", {}).get(name, [])
        if items:
            lines.append(f"  {name}")
            for item in items[:3]:
                lines.append(f"    {item['summary'][:50]}")
    if not lines:
        for b in blocks[:5]:
            lines.append(f"  {b['title']}")
    return "\n".join(lines)


def _dict_to_tree(data: Dict[str, Any], title: str = "root") -> MindmapNode:
    node = MindmapNode(title=title, note=data.get("note", ""))
    children_data = data.get("children", [])
    if isinstance(children_data, list):
        for child in children_data:
            if isinstance(child, dict):
                child_title = child.get("title", child.get("name", "node"))
                node.children.append(_dict_to_tree(child, child_title))
    elif isinstance(children_data, dict):
        for key, val in children_data.items():
            if isinstance(val, dict):
                node.children.append(_dict_to_tree(val, key))
            else:
                node.children.append(MindmapNode(title=str(val)))
    return node
