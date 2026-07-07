"""双周报流水线 Stage 间数据契约（TypedDict）。

替代裸 dict 传递，提供静态可检查的字段名约定。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


class FileEntry(dict):
    """数据源文件条目（TypedDict 语义，兼容 Python 3.9）。

    字段：
        label:      引用标签（如"团队成员B周报-0703"）
        date:       文件日期（datetime，从文件名或 frontmatter 提取）
        dir:        目录标签（如"成员周报"）
        filename:   文件名（不含路径）
        content:    文件正文（已去 frontmatter）
        char_count: 正文字符数
    """


class FileBrief(dict):
    """Stage 2 对单份文件的摘要结果（TypedDict 语义）。

    字段：
        brief_md:            Markdown 格式摘要
        primary_direction:   主要方向 ID（int 或 str 数字）
        relevant_directions: 相关方向列表（含 primary）
        strategic_insights:  战略洞察列表（来自讨论思考）
        key_facts:           关键事实列表
        quantitative_data:   量化数据列表
        dir_type:            目录类型（Stage 2 后由 service 注入）
    """
