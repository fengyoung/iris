"""ASR 文本特征检测单元测试 — _text_detector.py（纯函数，无 I/O，无 mock）。"""

from __future__ import annotations

import pytest

from iris.wiki.asr._text_detector import (
    _MIN_ASR_LENGTH,
    _MAX_ASR_LENGTH,
    _MIN_CHINESE_RATIO,
    _count_chinese,
    _is_asr_text,
)


# ────────────────────────────────────────────────────────────────────
# _count_chinese
# ────────────────────────────────────────────────────────────────────


class TestCountChinese:
    def test_pure_chinese(self):
        assert _count_chinese("你好世界") == 4

    def test_mixed_content(self):
        assert _count_chinese("hello世界123") == 2

    def test_no_chinese(self):
        assert _count_chinese("hello world 123") == 0

    def test_empty_string(self):
        assert _count_chinese("") == 0

    def test_single_char(self):
        assert _count_chinese("好") == 1

    def test_punctuation_ignored(self):
        # 中文标点不在 "一"~"鿿" 范围，不计入
        assert _count_chinese("你好！世界。") == 4

    def test_digits_and_symbols(self):
        # "年"、"搜"、"索" 均为汉字，英文数字和符号不计
        assert _count_chinese("2024年@#$搜索") == 3
        assert _count_chinese("2024@#$") == 0


# ────────────────────────────────────────────────────────────────────
# _is_asr_text — 长度边界
# ────────────────────────────────────────────────────────────────────


class TestIsAsrTextLength:
    def test_too_short_single_char(self):
        assert _is_asr_text("好") is False

    def test_too_short_four_chars(self):
        assert _is_asr_text("你好世界") is False

    def test_exactly_min_length(self):
        assert _is_asr_text("你好世界啊") is True

    def test_too_long_above_max(self):
        text = "这是测试" * 200
        assert len(text) > _MAX_ASR_LENGTH
        assert _is_asr_text(text) is False

    def test_near_max_length(self):
        # 接近上限仍为有效 ASR 文本
        text = "今天讨论了项目的整体方向和技术实现方案，" * 12
        assert len(text) <= _MAX_ASR_LENGTH
        assert _is_asr_text(text) is True

    def test_empty_string(self):
        assert _is_asr_text("") is False

    def test_whitespace_only(self):
        assert _is_asr_text("   \n\t  ") is False

    def test_custom_min_length(self):
        # 自定义更大的 min_length
        assert _is_asr_text("你好世界啊", min_length=10) is False

    def test_custom_max_length(self):
        # 自定义更小的 max_length
        text = "我们今天开会讨论方案"
        assert _is_asr_text(text, max_length=5) is False


# ────────────────────────────────────────────────────────────────────
# _is_asr_text — 中文比例
# ────────────────────────────────────────────────────────────────────


class TestIsAsrTextChineseRatio:
    def test_low_ratio_fails(self):
        # 主要是英文，中文 < 30%
        text = "Hello world testing demo example check " + "你好"
        ratio = _count_chinese(text) / len(text)
        assert ratio < _MIN_CHINESE_RATIO
        assert _is_asr_text(text) is False

    def test_sufficient_ratio_passes(self):
        text = "我们讨论一下 AI 在搜索推荐的应用场景"
        assert _is_asr_text(text) is True

    def test_all_chinese_passes(self):
        assert _is_asr_text("今天开会讨论了这个项目的整体方向") is True

    def test_custom_ratio(self):
        # 默认 30% 满足，但自定义 80% 不满足
        text = "我们的 AI team 搜索推荐"
        assert _is_asr_text(text, min_chinese_ratio=0.8) is False


# ────────────────────────────────────────────────────────────────────
# _is_asr_text — 代码特征
# ────────────────────────────────────────────────────────────────────


class TestIsAsrTextCodePattern:
    def test_braces_rejected(self):
        # 含中文使比例满足，由 {} 代码特征触发排除
        assert _is_asr_text("这段代码 function test() { return true } 执行") is False

    def test_semicolon_rejected(self):
        assert _is_asr_text("let x = 5; console.log(x);") is False

    def test_def_keyword_rejected(self):
        assert _is_asr_text("def calculate_sum(a, b): return a + b") is False

    def test_return_keyword_rejected(self):
        # 纯英文，中文比例 0% < 30%，被中文比例过滤排除
        assert _is_asr_text("return value if condition else default") is False

    def test_import_keyword_rejected(self):
        assert _is_asr_text("import os, sys from module") is False

    def test_from_import_rejected(self):
        assert _is_asr_text("from iris.wiki import discovery") is False

    def test_class_keyword_rejected(self):
        assert _is_asr_text("class MyClass: pass") is False

    def test_code_block_backtick_rejected(self):
        assert _is_asr_text("```python\nprint('hello')\n```") is False


# ────────────────────────────────────────────────────────────────────
# _is_asr_text — URL / Markdown 特征
# ────────────────────────────────────────────────────────────────────


class TestIsAsrTextUrlMarkdown:
    def test_http_url_rejected(self):
        assert _is_asr_text("请访问 http://example.com 查看") is False

    def test_https_url_rejected(self):
        assert _is_asr_text("我们的官网是 https://example.com") is False

    def test_markdown_h1_rejected(self):
        assert _is_asr_text("# 这是一个标题") is False

    def test_markdown_h2_rejected(self):
        assert _is_asr_text("## 二级标题内容") is False

    def test_markdown_bullet_star_rejected(self):
        assert _is_asr_text("* 第一项\n* 第二项") is False

    def test_markdown_bullet_dash_rejected(self):
        assert _is_asr_text("- 一项\n- 二项") is False

    def test_markdown_ordered_list_rejected(self):
        assert _is_asr_text("1. 第一步\n2. 第二步") is False


# ────────────────────────────────────────────────────────────────────
# _is_asr_text — 有效 ASR 场景
# ────────────────────────────────────────────────────────────────────


class TestIsAsrTextValid:
    def test_short_valid_asr(self):
        assert _is_asr_text("我写到检测板里头") is True

    def test_medium_valid_asr(self):
        assert _is_asr_text("我们今天讨论一下搜索推荐的算法优化方案") is True

    def test_asr_with_digits(self):
        assert _is_asr_text("2024年我们完成了50个项目") is True

    def test_chinese_with_punctuation(self):
        assert _is_asr_text("讨论了方案，包括算法优化。") is True

    def test_chinese_with_english_word(self):
        assert _is_asr_text("我们的 AI 团队搜索推荐应用") is True
