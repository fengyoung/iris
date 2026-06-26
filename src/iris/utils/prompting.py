"""Prompt 模板加载与渲染。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from iris.config.loader import ConfigBundle

FALLBACK_TEMPLATES: Dict[str, str] = {
    "qa_project.md": "用户问题：{{question}}\n最近会话：{{session_context}}\n长期记忆：{{profile_context}}\n纠正规则：{{correction_context}}\n工作上下文：{{working_context}}\n上下文压缩信息：{{context_summary}}\n结构化证据：\n{{structured_context}}\nWiki 页面：\n{{wiki_lines}}\n候选证据：\n{{evidence_lines}}",
    "qa_topic.md": "用户问题：{{question}}\n最近会话：{{session_context}}\n长期记忆：{{profile_context}}\n纠正规则：{{correction_context}}\n工作上下文：{{working_context}}\n上下文压缩信息：{{context_summary}}\n结构化证据：\n{{structured_context}}\nWiki 页面：\n{{wiki_lines}}\n候选证据：\n{{evidence_lines}}",
    "qa_term.md": "用户问题：{{question}}\n最近会话：{{session_context}}\n长期记忆：{{profile_context}}\n纠正规则：{{correction_context}}\n工作上下文：{{working_context}}\n上下文压缩信息：{{context_summary}}\n结构化证据：\n{{structured_context}}\nWiki 页面：\n{{wiki_lines}}\n候选证据：\n{{evidence_lines}}",
    "retrieval_rerank.md": "你是检索重排器，仅返回 JSON 数组。\n用户问题：{{query}}\n候选列表：\n{{candidate_lines}}",
    "analysis_report.md": "分析主题：{{query}}\n问答整理结果：{{answer}}\n结构化证据：\n{{structured_context}}\n候选证据：{{blocks}}",
    "biweekly_report.md": "时间范围：{{period}}\n背景知识：\n{{wiki_context}}\n近两周工作数据：\n{{evidence}}",
}


class PromptTemplateLoader:
    """从 templates/prompt 读取纯文本模板并做简单变量替换。"""

    def __init__(self, config: ConfigBundle):
        self._root = config.root / "templates" / "prompt"

    def render(self, template_name: str, variables: Dict[str, Any]) -> str:
        path = self._root / template_name
        if path.exists():
            template = path.read_text(encoding="utf-8")
        else:
            template = FALLBACK_TEMPLATES.get(template_name, "")
        rendered = template
        # 按 key 长度降序替换，避免前缀冲突
        for key in sorted(variables.keys(), key=len, reverse=True):
            rendered = rendered.replace("{{" + key + "}}", str(variables[key]))
        return rendered
