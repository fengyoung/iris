"""iris.utils.tokenization 全函数单元测试。"""

from __future__ import annotations

import pytest

from iris.utils.tokenization import (
    tokenize,
    count_chinese,
    exceeds_char_limit,
    estimate_tokens,
    truncate_by_tokens,
)


# ─────────────────────────────────────────────────────────────
# tokenize
# ─────────────────────────────────────────────────────────────

class TestTokenize:
    def test_empty_string(self):
        assert tokenize("") == []

    def test_chinese_text(self):
        tokens = tokenize("搜索引擎优化")
        assert isinstance(tokens, list)
        assert len(tokens) > 0

    def test_lowercase_english(self):
        tokens = tokenize("Hello WORLD")
        assert "hello" in tokens
        assert "world" in tokens
        assert "HELLO" not in tokens
        assert "WORLD" not in tokens

    def test_numbers_included(self):
        tokens = tokenize("version3 2026年")
        # 数字应包含在 token 中
        joined = " ".join(tokens)
        assert "3" in joined or "version3" in joined

    def test_mixed_content(self):
        tokens = tokenize("BM25算法优化")
        assert len(tokens) > 0


# ─────────────────────────────────────────────────────────────
# count_chinese
# ─────────────────────────────────────────────────────────────

class TestCountChinese:
    def test_empty_string(self):
        assert count_chinese("") == 0

    def test_pure_chinese(self):
        assert count_chinese("中文字符") == 4

    def test_pure_english(self):
        assert count_chinese("hello") == 0

    def test_mixed(self):
        assert count_chinese("hello世界") == 2

    def test_with_numbers(self):
        assert count_chinese("123") == 0

    def test_single_chinese_char(self):
        assert count_chinese("搜") == 1


# ─────────────────────────────────────────────────────────────
# exceeds_char_limit
# ─────────────────────────────────────────────────────────────

class TestExceedsCharLimit:
    def test_empty_string(self):
        assert exceeds_char_limit("") is False

    def test_total_length_exceeds(self):
        long_text = "a" * 21
        assert exceeds_char_limit(long_text, max_total=20, max_chinese=10) is True

    def test_chinese_count_exceeds(self):
        chinese_text = "中" * 11
        assert exceeds_char_limit(chinese_text, max_total=30, max_chinese=10) is True

    def test_exactly_at_limit_not_exceeded(self):
        # 恰好等于上限，不超过
        text = "a" * 20
        assert exceeds_char_limit(text, max_total=20, max_chinese=10) is False

    def test_short_text_not_exceeded(self):
        assert exceeds_char_limit("短文本", max_total=20, max_chinese=10) is False


# ─────────────────────────────────────────────────────────────
# estimate_tokens
# ─────────────────────────────────────────────────────────────

class TestEstimateTokens:
    def test_empty_string_at_least_one(self):
        assert estimate_tokens("") >= 1

    def test_chinese_more_than_equivalent_english(self):
        # 相同字符数的中文 token 估算应高于英文
        chinese_text = "中" * 10
        english_text = "a" * 10
        assert estimate_tokens(chinese_text) > estimate_tokens(english_text)

    def test_longer_text_more_tokens(self):
        short = "搜索"
        long = "搜索引擎优化技术平台架构"
        assert estimate_tokens(long) > estimate_tokens(short)

    def test_pure_english(self):
        result = estimate_tokens("hello world")
        assert result >= 1


# ─────────────────────────────────────────────────────────────
# truncate_by_tokens
# ─────────────────────────────────────────────────────────────

class TestTruncateByTokens:
    def test_short_text_unchanged(self):
        text = "短文本"
        result = truncate_by_tokens(text, max_tokens=100)
        assert result == text

    def test_long_text_contains_truncation_marker(self):
        # 构造足够长的中文文本，max_tokens=8 使 remaining_chars=12>10，触发截断标记
        long_text = "这是一段很长的测试文本内容。" * 100
        result = truncate_by_tokens(long_text, max_tokens=8)
        assert "截断" in result

    def test_empty_string(self):
        result = truncate_by_tokens("", max_tokens=50)
        assert result == ""

    def test_truncated_result_shorter_than_original(self):
        long_text = "搜索引擎优化技术。" * 200
        result = truncate_by_tokens(long_text, max_tokens=20)
        assert len(result) < len(long_text)
