"""Wiki 参考来源解析器 — 从 Wiki 页面的 ## 参考来源 段落提取引用条目。

支持多种引用格式：
  - [path.md:line] desc
  - 1. path.md:line（章节注释）desc
  - 1. path.md desc（无行号）
  - 行号范围 path.md:109-116
  - 内联内容格式（自动跳过）

用法:
    from iris.evaluation._reference_parser import parse_references, extract_page_title

    refs = parse_references(wiki_content)
    title = extract_page_title(wiki_content, filename)
"""

from __future__ import annotations

import re
from typing import List

from iris.evaluation._types import ReferenceEntry

# ── 参考来源解析模式 ──

# 格式1: [path.md:line] desc
BRACKET_PATTERN = re.compile(
    r"(?:\d+\.)?\s*\[([^\]]+\.md)(?::(\d+))?\](.*)"
)
# 格式2: 1. path.md:line（章节注释）desc
NUMBERED_PATH_PATTERN = re.compile(
    r"\d+\.\s*([^\s]+\.md):(\d+)(（[^）]*）)?\s*(.*)"
)
# 格式3: 1. path.md desc（无行号）
NUMBERED_PATH_NO_LINE_PATTERN = re.compile(
    r"\d+\.\s*([^\s]+\.md)\s*(.*)"
)
# 格式4: 行号范围 path.md:109-116
RANGE_PATTERN = re.compile(
    r".*(.+\.md):(\d+)-(\d+).*"
)
# 格式5: 内联内容格式（跳过）
INLINE_CONTENT_PATTERN = re.compile(
    r"\d+\.\s+\d+\.\s*使用语境"
)


def parse_references(wiki_content: str) -> List[ReferenceEntry]:
    """从 Wiki 页面内容中解析 ## 参考来源 下的所有引用条目。

    支持多种引用格式：
    - [path.md:line] desc
    - 1. path.md:line（章节注释）desc
    - 1. path.md:line desc
    - 1. path.md desc（无行号）
    - 内联格式（跳过，不计入校验）
    """
    ref_m = re.search(r"## 参考来源\n(.*?)(?=\n## |\Z)", wiki_content, re.DOTALL)
    if not ref_m:
        return []

    ref_text = ref_m.group(1).strip()
    entries = []
    for line in ref_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # 跳过内联内容格式
        if INLINE_CONTENT_PATTERN.match(line):
            continue

        e = None

        # 尝试格式1: [path.md:line]
        m = BRACKET_PATTERN.match(line)
        if m:
            source_path = m.group(1).strip()
            line_str = m.group(2)
            line_number = int(line_str) if line_str else None
            description = m.group(3).strip() if m.group(3) else ""
            e = ReferenceEntry(raw=line, source_path=source_path,
                               line_number=line_number, description=description)

        # 尝试格式2: 1. path.md:line（章节注释）
        if not e:
            m = NUMBERED_PATH_PATTERN.match(line)
            if m:
                source_path = m.group(1).strip().lstrip("[")
                line_number = int(m.group(2))
                chapter_note = (m.group(3) or "").strip()
                desc = (m.group(4) or "").strip()
                description = desc
                if chapter_note and not description:
                    description = chapter_note.strip("（）")
                elif chapter_note and description:
                    description = f"{chapter_note} {description}"
                e = ReferenceEntry(raw=line, source_path=source_path,
                                   line_number=line_number, description=description)

        # 尝试格式3: 1. path.md（无行号）
        if not e:
            m = NUMBERED_PATH_NO_LINE_PATTERN.match(line)
            if m:
                source_path = m.group(1).strip().lstrip("[")
                description = m.group(2).strip() if m.group(2) else ""
                e = ReferenceEntry(raw=line, source_path=source_path,
                                   line_number=None, description=description)

        # 尝试格式4: 行号范围 path.md:109-116（取起始行）
        if not e:
            m = RANGE_PATTERN.match(line)
            if m:
                source_path = m.group(1).strip().lstrip("[")
                line_number = int(m.group(2))
                description = ""
                e = ReferenceEntry(raw=line, source_path=source_path,
                                   line_number=line_number, description=description)

        if e:
            entries.append(e)

    return entries


def extract_page_title(content: str, filename: str) -> str:
    """从 frontmatter 或文件名提取页面标题。"""
    title_m = re.search(r'title:\s*(.+)', content)
    return title_m.group(1).strip() if title_m else filename.replace(".md", "")
