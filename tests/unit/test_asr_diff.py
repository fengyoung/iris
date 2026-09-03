"""ASR 校正差异对比 `_diff.py` — 单元测试（纯逻辑，无 mock）。"""

from __future__ import annotations

from iris.wiki.asr._diff import (
    _MAX_CHANGES,
    _describe_opcode,
    _diff_changes,
    _is_cjk,
    _scan_run,
    _tokenize,
)


class TestIsCjk:
    """_is_cjk：CJK 统一表意文字区间判定。"""

    def test_chinese_char_is_cjk(self):
        assert _is_cjk("中") is True
        assert _is_cjk("一") is True

    def test_non_chinese_is_not_cjk(self):
        assert _is_cjk("a") is False
        assert _is_cjk("1") is False
        assert _is_cjk("，") is False  # 全角标点不在 CJK 表意文字区
        assert _is_cjk(" ") is False


class TestScanRun:
    """_scan_run：从 start 起扫描满足谓词的连续字符。"""

    def test_scans_alpha_run(self):
        assert _scan_run("abc123", 0, str.isalpha) == 3

    def test_returns_start_when_first_char_fails(self):
        assert _scan_run("123abc", 0, str.isalpha) == 0

    def test_stops_at_end_of_text(self):
        assert _scan_run("   ", 0, str.isspace) == 3


class TestTokenize:
    """_tokenize：中文逐字、英文按词、空白连续、标点/数字单字。"""

    def test_pure_chinese_char_by_char(self):
        assert _tokenize("检测板") == ["检", "测", "板"]

    def test_english_word_is_single_token(self):
        assert _tokenize("hello") == ["hello"]

    def test_consecutive_whitespace_is_single_token(self):
        assert _tokenize("a   b") == ["a", "   ", "b"]
        assert _tokenize("a \t\nb") == ["a", " \t\n", "b"]

    def test_punctuation_and_digits_are_single_chars(self):
        assert _tokenize("3.11") == ["3", ".", "1", "1"]
        assert _tokenize("，。！") == ["，", "。", "！"]

    def test_mixed_string_exact_tokens(self):
        assert _tokenize("我用Python 3.11写代码，OK") == [
            "我", "用", "Python", " ", "3", ".", "1", "1",
            "写", "代", "码", "，", "OK",
        ]

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_roundtrip_join_preserves_text(self):
        text = "会议 meeting 2026-09-03，结束。"
        assert "".join(_tokenize(text)) == text


class TestDescribeOpcode:
    """_describe_opcode：opcode → 「旧→新」/「⊕新」/空串。"""

    def test_replace_with_old_and_new(self):
        assert _describe_opcode("replace", "检测", "剪切") == "检测→剪切"

    def test_replace_with_empty_old_becomes_insert_marker(self):
        assert _describe_opcode("replace", "", "新") == "⊕新"

    def test_replace_with_empty_new_is_empty(self):
        assert _describe_opcode("replace", "旧", "") == ""
        assert _describe_opcode("replace", "", "") == ""

    def test_insert_with_new(self):
        assert _describe_opcode("insert", "", "世界") == "⊕世界"

    def test_insert_with_empty_new_is_empty(self):
        assert _describe_opcode("insert", "", "") == ""

    def test_delete_and_equal_are_empty(self):
        assert _describe_opcode("delete", "删", "") == ""
        assert _describe_opcode("equal", "同", "同") == ""


class TestDiffChanges:
    """_diff_changes：词级差异列表。"""

    def test_identical_texts_no_changes(self):
        assert _diff_changes("今天开会", "今天开会") == []
        assert _diff_changes("", "") == []

    def test_adjacent_char_replacement_merged_by_sequence_matcher(self):
        """「检测板→剪切板」相邻两字替换被 SequenceMatcher 合并为一个 replace 块。"""
        assert _diff_changes("检测板", "剪切板") == ["检测→剪切"]

    def test_non_adjacent_replacements_are_separate(self):
        assert _diff_changes("甲乙丙", "丁乙戊") == ["甲→丁", "丙→戊"]

    def test_pure_insertion_starts_with_plus_marker(self):
        changes = _diff_changes("你好", "你好世界")
        assert changes == ["⊕世界"]
        assert changes[0].startswith("⊕")

    def test_pure_deletion_not_reported(self):
        assert _diff_changes("你好世界", "你好") == []

    def test_english_word_replacement_is_whole_word(self):
        assert _diff_changes("hello world", "hello there") == ["world→there"]

    def test_whitespace_only_tokens_are_stripped_out(self):
        """仅空白差异 strip 后为空，不产生条目。"""
        assert _diff_changes("a b", "a  b") == []

    def test_truncated_to_max_changes(self):
        """10 处独立替换（用不同数字隔开保证对齐）被截断为 8 条。"""
        before = "一1二2三3四4五5六6七7八8九9十0"
        after = "壹1贰2叁3肆4伍5陆6柒7捌8玖9拾0"
        changes = _diff_changes(before, after)
        assert _MAX_CHANGES == 8
        assert len(changes) == 8
        assert changes[0] == "一→壹"
        assert changes[-1] == "八→捌"
