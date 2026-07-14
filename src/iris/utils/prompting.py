"""Prompt 模板加载与渲染。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from iris.config.loader import ConfigBundle

# QA 通用模板（qa_project / qa_topic / qa_term 共用）
_QA_TEMPLATE = (
    "用户问题：{{question}}\n最近会话：{{session_context}}\n长期记忆：{{profile_context}}\n"
    "纠正规则：{{correction_context}}\n工作上下文：{{working_context}}\n知识图谱关联：{{graph_context}}\n"
    "上下文压缩信息：{{context_summary}}\n"
    "结构化证据：\n{{structured_context}}\nWiki 页面：\n{{wiki_lines}}\n候选证据：\n{{evidence_lines}}"
)

FALLBACK_TEMPLATES: Dict[str, str] = {
    "qa_project.md": _QA_TEMPLATE,
    "qa_topic.md": _QA_TEMPLATE,
    "qa_term.md": _QA_TEMPLATE,
    "retrieval_rerank.md": "你是检索重排器，仅返回 JSON 数组。\n用户问题：{{query}}\n候选列表：\n{{candidate_lines}}",
    "analysis_report.md": "分析主题：{{query}}\n问答整理结果：{{answer}}\n结构化证据：\n{{structured_context}}\n候选证据：{{blocks}}",
    "biweekly_report.md": "时间范围：{{period}}\n背景知识：\n{{wiki_context}}\n近两周工作数据：\n{{evidence}}",
}

# 模块级模板缓存，避免每次 render 都从磁盘读取
_template_cache: Dict[str, Optional[str]] = {}


class PromptTemplateLoader:
    """从 templates/prompt 读取纯文本模板并做简单变量替换。"""

    def __init__(self, config: ConfigBundle):
        self._root = config.root / "templates" / "prompt"

    def render(self, template_name: str, variables: Dict[str, Any]) -> str:
        if template_name not in _template_cache:
            path = self._root / template_name
            if path.exists():
                _template_cache[template_name] = path.read_text(encoding="utf-8")
            else:
                _template_cache[template_name] = FALLBACK_TEMPLATES.get(template_name)
        template = _template_cache[template_name]
        if template is None:
            raise FileNotFoundError(f"模板未找到: {template_name}")
        rendered = template
        # 按 key 长度降序替换，避免前缀冲突
        for key in sorted(variables.keys(), key=len, reverse=True):
            value = variables[key]
            rendered = rendered.replace("{{" + key + "}}", str(value) if value is not None else "")
        return rendered
