"""Wiki 页面 I/O 工具 — 文件名转换、路径构建等纯函数。

从 generator.py 中抽出的无状态工具函数，供 WikiGenerator 和其他
Wiki 模块复用，零依赖。
"""

from __future__ import annotations

import re


def slugify_title(title: str) -> str:
    """将标题转换为安全文件名 slug（保留字母/数字/中文/连字符，截断 60 字符）。"""
    return re.sub(r'[^\w一-鿿\-]', '', title)[:60]
