"""wiki/generator.py — _validate_update_output 和 _extract_wiki_content 边界测试。
wiki/_constants.py — build_domain_context 测试。
"""

from __future__ import annotations

import pytest

from iris.wiki.generator import WikiGenerator, _slugify_title
from iris.wiki._constants import build_domain_context


# ── _validate_update_output ──────────────────────────────────────────


VALID_CONTENT = """\
---
title: 测试页面
type: domain
status: active
created: 2025-01-01
updated: 2025-01-10
---

## 摘要
测试内容。
"""


def test_validate_update_output_valid_passthrough():
    """正常输出：title 未被篡改，直接通过。"""
    result = WikiGenerator._validate_update_output(VALID_CONTENT, VALID_CONTENT, "测试页面")
    assert "title: 测试页面" in result


def test_validate_update_output_title_tampered():
    """LLM 篡改 title 时自动修复。"""
    tampered = VALID_CONTENT.replace("title: 测试页面", "title: 错误标题")
    result = WikiGenerator._validate_update_output(tampered, VALID_CONTENT, "测试页面")
    assert "title: 测试页面" in result
    assert "错误标题" not in result


def test_validate_update_output_missing_frontmatter_recovered():
    """frontmatter 缺失时尝试用原有 frontmatter 包裹正文。"""
    no_fm = "## 摘要\n内容在这里。"
    result = WikiGenerator._validate_update_output(no_fm, VALID_CONTENT, "测试页面")
    # 无法修复时应回退到原内容
    assert "测试页面" in result or result == VALID_CONTENT


def test_validate_update_output_trailing_code_block_stripped():
    """尾部多余代码块被清理。"""
    with_tail = VALID_CONTENT.strip() + "\n```"
    result = WikiGenerator._validate_update_output(with_tail, VALID_CONTENT, "测试页面")
    assert not result.endswith("```")


def test_validate_update_output_crlf_normalized():
    """Windows CRLF 换行符不破坏解析。"""
    crlf_content = VALID_CONTENT.replace("\n", "\r\n")
    result = WikiGenerator._validate_update_output(crlf_content, VALID_CONTENT, "测试页面")
    assert "title: 测试页面" in result


# ── _extract_wiki_content ─────────────────────────────────────────────


def test_extract_starts_with_frontmatter():
    text = "---\ntitle: 测试\ntype: domain\n---\n\n## 内容\n..."
    result = WikiGenerator._extract_wiki_content(text)
    assert result.startswith("---")
    assert "title: 测试" in result


def test_extract_strips_markdown_code_block():
    text = "好的，以下是生成结果：\n```markdown\n---\ntitle: 测试\n---\n\n内容\n```"
    result = WikiGenerator._extract_wiki_content(text)
    # frontmatter 必须被提取
    assert result.startswith("---")
    assert "title: 测试" in result


def test_extract_strict_regex_priority():
    """包含代码块 --- 时严格正则应从正确位置提取 frontmatter。"""
    text = (
        "这里有个代码示例：\n```yaml\n---\nsome: yaml\n---\n```\n\n"
        "实际页面：\n---\ntitle: 真实页面\ntype: domain\n---\n\n内容。"
    )
    result = WikiGenerator._extract_wiki_content(text)
    assert "title: 真实页面" in result


def test_extract_dialog_prefix_skipped():
    """LLM 在 frontmatter 前加了对话前缀，应定位到 frontmatter。"""
    text = "当然，以下是您请求的 Wiki 页面：\n\n---\ntitle: 实际内容\ntype: concept\n---\n\n内容"
    result = WikiGenerator._extract_wiki_content(text)
    assert "title: 实际内容" in result


def test_extract_fallback_returns_original():
    """完全无法定位 frontmatter 时返回原始文本（不抛出异常）。"""
    text = "这是一段没有任何 frontmatter 的普通文本，也没有代码块。"
    result = WikiGenerator._extract_wiki_content(text)
    assert isinstance(result, str)
    assert len(result) > 0


# ── _slugify_title ────────────────────────────────────────────────────


def test_slugify_title_chinese():
    assert _slugify_title("技术研发部") == "技术研发部"


def test_slugify_title_removes_special_chars():
    slug = _slugify_title("测试/页面 #1")
    assert "/" not in slug
    assert "#" not in slug
    assert " " not in slug


def test_slugify_title_max_length():
    long_title = "测" * 100
    assert len(_slugify_title(long_title)) <= 60


# ── build_domain_context ──────────────────────────────────────────────


def test_build_domain_context_empty_config():
    """未配置 organization 时返回通用占位描述。"""
    result = build_domain_context({})
    assert "专业团队" in result or result  # 非空


def test_build_domain_context_none_config():
    result = build_domain_context(None)
    assert isinstance(result, str) and len(result) > 0


def test_build_domain_context_full_config():
    cfg = {
        "organization": {
            "name": "Acme Corp",
            "department": "数据部",
            "domains": ["AI 质检", "推荐算法"],
        }
    }
    result = build_domain_context(cfg)
    assert "Acme Corp" in result
    assert "数据部" in result
    assert "AI 质检" in result
    assert "推荐算法" in result


def test_build_domain_context_name_only():
    cfg = {"organization": {"name": "Acme", "department": "", "domains": []}}
    result = build_domain_context(cfg)
    assert "Acme" in result


def test_build_domain_context_no_name_no_dept():
    """name 和 department 均为空时返回通用描述。"""
    cfg = {"organization": {"name": "", "department": "", "domains": ["AI"]}}
    result = build_domain_context(cfg)
    assert isinstance(result, str) and len(result) > 0
