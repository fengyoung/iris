"""YAML Frontmatter 工具模块 — 构建、注入、解析。

提供项目中所有 Markdown 文档 frontmatter 的统一入口，确保输出格式一致。

用法:
    from iris.core.frontmatter import build_frontmatter, inject_frontmatter, parse_frontmatter

    fm = build_frontmatter({"title": "会议纪要", "date": "2026-07-30", "type": "meeting_minutes"})
    content = inject_frontmatter(markdown_body, fields)
    fields, body = parse_frontmatter(content)
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

# ── YAML frontmatter 正则 ──────────────────────────────────
# 匹配文档开头的 "---\\n...\\n---\\n" 块
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# 各文档类型对应的 type 字段值
DOC_TYPES = {
    "meeting_minutes": "会议纪要",
    "weekly_report": "成员周报",
    "chat_digest": "对话提取",
    "feishu_doc": "飞书文档",
    "discussion": "讨论思考",
    "proposal": "方案报告",
    "reference": "参考资料",
    "okr": "目标管理",
    "dept_mgmt": "部门管理",
    "work_briefing": "工作简报",
    "my_weekly": "我的周报",
}

# YAML 保留字 — 这些字符串不加引号会被解析为 non-string 类型
_YAML_RESERVED = frozenset({
    "true", "True", "TRUE", "false", "False", "FALSE",
    "yes", "Yes", "YES", "no", "No", "NO",
    "on", "On", "ON", "off", "Off", "OFF",
    "null", "Null", "NULL", "~",
    ".nan", ".NaN", ".NAN", ".inf", ".Inf", ".INF", "-.inf", "-.Inf", "-.INF",
    "+.inf", "+.Inf", "+.INF",  # YAML 正无穷
})

# YAML 数字模式 — 匹配会被解析为 int/float 的字符串
_YAML_NUMBER_RE = re.compile(
    r'^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$'  # decimal / float / sci
    r'|^[-+]?0[xX][0-9a-fA-F]+$'   # hex
    r'|^[-+]?0[oO]?[0-7]+$'        # octal
    r'|^[-+]?0[bB][01]+$'          # binary
    r'|^[-+]?\.(?:inf|Inf|INF|nan|NaN|NAN)$'  # special float
)


# ── 公共 API ───────────────────────────────────────────────


def build_frontmatter(fields: Dict[str, object]) -> str:
    """将字段字典渲染为 YAML frontmatter 块。

    Args:
        fields: 字段名 → 值的映射。值支持 str / int / float / bool / list / None。
                None 值和空列表将被跳过。

    Returns:
        ``---\\nkey: value\\n...\\n---\\n`` 格式的 frontmatter 字符串。
        如果所有字段均为空，返回空字符串。

    Example:
        >>> build_frontmatter({"title": "会议纪要", "date": "2026-07-30"})
        '---\\ntitle: 会议纪要\\ndate: 2026-07-30\\n---\\n'
    """
    lines = _render_fields(fields)
    if not lines:
        return ""
    return "---\n" + "\n".join(lines) + "\n---\n"


def inject_frontmatter(content: str, fields: Dict[str, object]) -> str:
    """在 Markdown 内容前注入 frontmatter 块（幂等安全）。

    如果内容已有 frontmatter 则跳过注入，直接返回原内容。

    Args:
        content: Markdown 正文（可包含已有 frontmatter）
        fields: 要注入的字段

    Returns:
        带 frontmatter 前缀的完整 Markdown 文本。

    Example:
        >>> result = inject_frontmatter("# Hello", {"title": "Test"})
        >>> result.startswith("---")
        True
    """
    if _has_frontmatter(content):
        return content
    fm = build_frontmatter(fields)
    if not fm:
        return content
    # 去掉 BOM 和空白，保证 frontmatter 在文件最开头
    body = content.lstrip("﻿").lstrip("\n")
    return fm + "\n" + body


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """解析 Markdown 文档的 YAML frontmatter。

    返回 (frontmatter 字段字典, 正文部分)。
    无 frontmatter 时返回 ({}, text)。
    自动处理 \\\\r\\\\n 换行符和 BOM 前缀。

    Args:
        text: 完整 Markdown 文本

    Returns:
        (字段字典, 正文) 元组。字段字典的 key 为原始 YAML key，value 为去除引号后的字符串。

    Example:
        >>> fields, body = parse_frontmatter('---\\ntitle: Test\\n---\\n\\n# Hello')
        >>> fields["title"]
        'Test'
        >>> body.startswith("# Hello")
        True
    """
    normalized = text.replace("\r\n", "\n").lstrip("﻿")
    fm_match = FRONTMATTER_RE.match(normalized)
    if not fm_match:
        return {}, normalized
    fields: Dict[str, str] = {}
    for line in fm_match.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip().strip("\"'")
    return fields, normalized[fm_match.end():]


def get_frontmatter_field(text: str, field: str) -> str:
    """从文档文本的 frontmatter 中获取指定字段值。

    Args:
        text: 完整 Markdown 文本
        field: 字段名

    Returns:
        字段值字符串，无此字段时返回空字符串。
    """
    return parse_frontmatter(text)[0].get(field, "")


def has_frontmatter(text: str) -> bool:
    """检查文本是否包含 frontmatter 块。"""
    return _has_frontmatter(text)


# ── 内部辅助 ───────────────────────────────────────────────


def _has_frontmatter(text: str) -> bool:
    """检查文本是否以 frontmatter 块开头。"""
    return bool(FRONTMATTER_RE.match(text.lstrip("﻿")))


def _render_fields(fields: Dict[str, object]) -> list:
    """将字段字典渲染为 YAML 行列表，跳过空值。"""
    lines: list = []
    for key, value in fields.items():
        rendered = _render_value(key, value)
        if rendered is not None:
            lines.append(rendered)
    return lines


def _render_value(key: str, value: object) -> Optional[str]:
    """渲染单个字段为 YAML 行，空值返回 None 表示跳过。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return f"{key}: {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}: {value}"
    if isinstance(value, list):
        if not value:
            return None
        items = "\n".join([f"  - {_scalar_str(v)}" for v in value])
        return f"{key}:\n{items}"
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # 多行字符串 → YAML block scalar
        if "\n" in s:
            indented = "\n".join(f"  {line}" for line in s.splitlines())
            return f"{key}: |\n{indented}"
        # 含特殊字符时加引号
        if _needs_quoting(s):
            escaped = s.replace("\\", "\\\\").replace('"', '\\"')
            return f'{key}: "{escaped}"'
        return f"{key}: {s}"
    # 其他类型尝试字符串化，同样需要 quoting
    s = str(value).strip()
    if not s:
        return None
    if _needs_quoting(s):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}: "{escaped}"'
    return f"{key}: {s}"


def _scalar_str(value: object) -> str:
    """将列表元素渲染为字符串（用于 YAML 行内值）。"""
    s = str(value).strip()
    if _needs_quoting(s):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _needs_quoting(s: str) -> bool:
    """判断字符串是否需要 YAML 引号包裹。

    以下情况返回 True：
    - YAML 保留字（true/false/null/yes/no/on/off/~/.nan/.inf 等）
    - 含 ``: ``、``#``、``[`` ``]`` ``{`` ``}`` ``,`` ``&`` ``*`` ``!`` ``|`` ``>`` ``%`` ``@`` ``` `` `` ``?``
    - 以 ``"`` ``'`` ``- `` 开头
    - 首字符为 ``-`` 且后跟空格
    """
    if not s:
        return False
    # YAML 保留字
    if s in _YAML_RESERVED:
        return True
    # 数字形式的字符串（会被 YAML 解析器解释为 int/float）
    if _YAML_NUMBER_RE.match(s):
        return True
    # 以引号开头/结尾
    if s[0] in "'\"" or s[-1] in "'\"":
        return True
    # 以 "- " 开头会被 YAML 解释为序列项
    if s.startswith("- ") or s == "-":
        return True
    # 以 "? " 开头会被 YAML 解释为复杂映射键
    if s.startswith("? "):
        return True
    # 冒号后跟空格才是 YAML 特殊语义
    if ": " in s or ":\t" in s:
        return True
    # 以下字符在 YAML 中有特殊含义（出现在字符串任意位置即需加引号）
    # 注意：`-` 和 `?` 仅在行首有特殊含义，不在此列
    special_chars = {":", "#", "[", "]", "{", "}", ",", "&", "*", "!", "|", ">",
                     "%", "@", "`"}
    if any(c in s for c in special_chars):
        return True
    return False
