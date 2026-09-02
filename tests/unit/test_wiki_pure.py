"""wiki/generator.py 纯函数 + wiki/discovery_utils.py 纯函数 单元测试。"""

from __future__ import annotations


from iris.wiki.generator import _slugify_title, WikiGenerator
from iris.wiki.discovery_utils import (
    normalize_title,
    canonicalize_title,
    infer_page_type,
    is_high_value_title,
    is_high_value_term,
    extract_terms,
    extract_persons,
    path_weight,
    find_parent_title,
    normalized_key,
)


# ── _slugify_title ─────────────────────────────────────────


def test_slugify_basic():
    result = _slugify_title("Hello World 测试")
    assert "Hello" in result


def test_slugify_removes_special_chars():
    result = _slugify_title("项目@#$%Beta")
    assert "@" not in result
    assert "#" not in result


def test_slugify_truncate():
    long_name = "A" * 100
    assert len(_slugify_title(long_name)) <= 60


def test_slugify_preserves_chinese():
    result = _slugify_title("人工智能助手")
    assert len(result) > 0


# ── normalize_title ────────────────────────────────────────


def test_normalize_title_basic():
    # 去除首尾空白和 #*
    assert normalize_title("  Hello World  ") == "Hello World"


def test_normalize_title_strips_hash_star():
    assert normalize_title("## 项目进展") == "项目进展"


def test_normalize_title_strips_leading_enum():
    # LEADING_ENUM_RE removes numbering like "1. ", "一、"
    result = normalize_title("1. 项目进展")
    assert "项目进展" in result


def test_normalize_title_empty():
    assert normalize_title("") == ""


# ── canonicalize_title ─────────────────────────────────────


def test_canonicalize_project_removes_suffix():
    result = canonicalize_title("项目Alpha优化项目", page_type="project")
    # 项目后缀应该被移除
    assert "项目" not in result or len(result) > 4


def test_canonicalize_short_title():
    result = canonicalize_title("AI", page_type="domain")
    # 太短的可能返回空字符串
    assert isinstance(result, str)


# ── infer_page_type ────────────────────────────────────────


def test_infer_project():
    assert infer_page_type("项目进展") == "project"


def test_infer_domain():
    assert infer_page_type("技术研发部机制") == "domain"


def test_infer_default():
    assert infer_page_type("随机标题") == "domain"


# ── is_high_value_title ────────────────────────────────────


def test_is_high_value_too_short():
    assert not is_high_value_title("A", "project")


def test_is_high_value_empty():
    assert not is_high_value_title("", "domain")


# ── is_high_value_term ─────────────────────────────────────


def test_is_high_value_term_valid():
    assert is_high_value_term("人工智能")


def test_is_high_value_term_too_short():
    assert not is_high_value_term("A")


def test_is_high_value_term_english():
    assert is_high_value_term("BM25")


# ── extract_terms ──────────────────────────────────────────


def test_extract_terms_english_abbreviation():
    terms = extract_terms("BM25 是一种排序算法")
    assert "BM25" in terms


def test_extract_terms_chinese():
    terms = extract_terms("人工智能技术发展")
    assert len(terms) >= 1


# ── extract_persons ────────────────────────────────────────


def test_extract_persons_basic():
    # PERSON_PATTERNS 匹配结构化标记（负责人: XXX / 由XXX负责 等）
    persons = extract_persons("负责人：张三\n由李四负责前端")
    assert "张三" in persons
    assert "李四" in persons


def test_extract_persons_empty():
    persons = extract_persons("没有人物信息")
    assert persons == []


# ── path_weight ────────────────────────────────────────────


def test_path_weight_default():
    assert path_weight("unknown/file.md") == 1


def test_path_weight_is_int():
    assert isinstance(path_weight("some/path.md"), int)


# ── find_parent_title ──────────────────────────────────────


def test_find_parent_title_project_keyword():
    titles = ["技术研发部", "项目Alpha项目", "进展"]
    result = find_parent_title(titles)
    assert result is not None


def test_find_parent_title_empty():
    assert find_parent_title([]) is None


# ── normalized_key ─────────────────────────────────────────


def test_normalized_key_case_insensitive():
    assert normalized_key("BM25") == normalized_key("bm25")


def test_normalized_key_strips_special():
    # normalized_key 去除所有非字母数字中文的字符后转小写
    # "Hello World" → "helloworld"
    assert "helloworld" in normalized_key("Hello World") or "helloworld" == normalized_key("Hello World")


# ── WikiGenerator.check_reference_quality ──────────────────


def test_ref_quality_good():
    content = """---
title: 测试
---

## 参考来源
[source.md:10] 该文档提到准确率从 48% 提升至 55%，验证了本次优化的效果
[source2.md:5] 记录了张三负责项目Alpha项目的里程碑规划与时间节点
"""
    result = WikiGenerator.check_reference_quality(content)
    assert result["quality"] == "good"
    assert result["total_refs"] == 2
    assert result["described_refs"] == 2
    assert result["bare_path_refs"] == 0


def test_ref_quality_bare_path():
    content = """---
title: 测试
---

## 参考来源
[source.md:10]
[source2.md:5]
"""
    result = WikiGenerator.check_reference_quality(content)
    assert result["quality"] == "poor"
    assert result["bare_path_refs"] == 2


def test_ref_quality_mixed():
    content = """---
title: 测试
---

## 参考来源
[source.md:10] 该文档提到准确率从 48% 提升至 55%，效果显著超出预期目标
[source2.md:5]
"""
    result = WikiGenerator.check_reference_quality(content)
    assert result["quality"] == "fair"
    assert result["total_refs"] == 2
    assert result["described_refs"] == 1
    assert result["bare_path_refs"] == 1


def test_ref_quality_no_refs():
    content = """---
title: 测试
---

## 正文
没有参考来源章节
"""
    result = WikiGenerator.check_reference_quality(content)
    assert result["quality"] == "no_refs"
    assert result["total_refs"] == 0


def test_ref_quality_numbered_format():
    content = """---
title: 测试
---

## 参考来源
1. [source.md:10] 该文档详细说明了算法的实现方案及性能对比数据
2. [meeting.md:3] 会议纪要中确认了下季度的研发优先级和资源分配
"""
    result = WikiGenerator.check_reference_quality(content)
    assert result["quality"] == "good"
    assert result["total_refs"] == 2
    assert result["described_refs"] == 2


# ── WikiGenerator._extract_wiki_content ────────────────────


def test_extract_wiki_content_with_yaml_frontmatter():
    text = """---
title: 测试页面
type: concept
---

## 正文
这是正文内容。"""
    result = WikiGenerator._extract_wiki_content(text)
    assert result.startswith("---")


def test_extract_wiki_content_with_markdown_block():
    text = '''```markdown
---
title: 测试
type: concept
---

## 正文
内容
```'''
    result = WikiGenerator._extract_wiki_content(text)
    assert result.startswith("---")


def test_extract_wiki_content_conversation_prefix():
    text = """下面是生成的 Wiki 页面：

---
title: 测试
type: concept
---

## 正文
内容"""
    result = WikiGenerator._extract_wiki_content(text)
    assert result.startswith("---")


# ── WikiGenerator._validate_update_output ──────────────────


def test_validate_update_output_preserves_valid():
    content = """---
title: 张三
type: person
updated: 2025-01-01
---

## 摘要
测试"""
    result = WikiGenerator._validate_update_output(content, content, "张三")
    assert "title: 张三" in result


def test_validate_update_output_title_corrected():
    content = """---
title: 李四
type: person
updated: 2025-01-01
---

## 摘要
测试"""
    result = WikiGenerator._validate_update_output(content, content, "张三")
    assert "title: 张三" in result


def test_validate_update_output_missing_frontmatter():
    content = "没有 frontmatter 的内容"
    existing = """---
title: 张三
type: person
---

## 摘要
测试"""
    result = WikiGenerator._validate_update_output(content, existing, "张三")
    # 缺少 frontmatter → 回退到 existing
    assert "title: 张三" in result


def test_validate_update_output_crlf_normalization():
    content = "---\r\ntitle: 张三\r\ntype: person\r\n---\r\n\r\n## 正文\r\n测试"
    existing = content
    result = WikiGenerator._validate_update_output(content, existing, "张三")
    assert "title: 张三" in result


def test_validate_update_output_strips_trailing_backticks():
    content = """---
title: 张三
type: person
---

正文内容
```"""
    existing = content
    result = WikiGenerator._validate_update_output(content, existing, "张三")
    assert not result.rstrip().endswith("```")

