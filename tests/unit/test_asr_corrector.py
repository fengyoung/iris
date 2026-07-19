"""ASR 校正引擎 — 单元测试（Aho-Corasick + AsrCorrector）。"""

import pytest
from iris.wiki.asr._types import AsrCorrection
from iris.wiki.asr.corrector import (
    _AhoCorasick,
    _is_asr_text,
    _parse_hotkey,
    _count_chinese,
    correct_text_static,
)


class TestAhoCorasick:
    def test_basic_replacement(self):
        ac = _AhoCorasick({"检测板": "剪切板"})
        result, applied = ac.replace_all("我写到检测板里头")
        assert "剪切板" in result
        assert "检测板" not in result
        assert len(applied) == 1

    def test_longest_match_first(self):
        """最长匹配优先：避免短模式先覆盖长模式。"""
        ac = _AhoCorasick({
            "搜推": "搜索推荐",
            "搜推工程": "搜索推荐工程",
        })
        result, applied = ac.replace_all("搜推工程团队")
        # 应该先匹配 "搜推工程"（更长），而非 "搜推"
        assert "搜索推荐工程" in result
        assert "搜索推荐搜索推荐" not in result  # 防止双重匹配

    def test_multiple_replacements(self):
        ac = _AhoCorasick({
            "汪瑞": "汪蕊",
            "检测板": "剪切板",
            "智能画检测": "智能化检测",
        })
        result, applied = ac.replace_all("汪瑞在检测板上做智能画检测")
        assert "汪蕊" in result
        assert "剪切板" in result
        assert "智能化检测" in result

    def test_no_match(self):
        ac = _AhoCorasick({"张三": "李四"})
        result, applied = ac.replace_all("今天天气真好")
        assert result == "今天天气真好"
        assert len(applied) == 0

    def test_empty_text(self):
        ac = _AhoCorasick({"A": "B"})
        result, applied = ac.replace_all("")
        assert result == ""

    def test_overlapping_patterns(self):
        """重叠模式：匹配一个后不应破坏后续匹配。"""
        ac = _AhoCorasick({"AB": "12", "BC": "34"})
        result, applied = ac.replace_all("ABC")
        # 匹配 "AB" 后从 "C" 继续，不应匹配 "BC"
        assert result == "12C"


class TestAsrTextDetection:
    def test_valid_chinese_short_text(self):
        assert _is_asr_text("我写到检测板里头")

    def test_valid_medium_text(self):
        assert _is_asr_text("我们今天讨论一下搜索推荐的算法优化方案")

    def test_too_short(self):
        assert not _is_asr_text("好")

    def test_too_long(self):
        assert not _is_asr_text("长文本" * 200)

    def test_code_text(self):
        assert not _is_asr_text("def main(): print('hello world')")

    def test_url_text(self):
        assert not _is_asr_text("请访问 https://example.com 查看详情")

    def test_markdown_text(self):
        assert not _is_asr_text("# 标题\n这是一段 markdown 文本")

    def test_english_dominant(self):
        assert not _is_asr_text("hello world this is a test of english text")


class TestHotkeyParsing:
    def test_shift_control_z(self):
        mask, keycode = _parse_hotkey("shift+control+KeyZ")
        assert mask > 0  # 应有修饰键
        assert keycode > 0  # 应有键码

    def test_simple_shift(self):
        mask, keycode = _parse_hotkey("shift+KeyX")
        assert mask == 512  # shiftKey
        assert keycode > 0

    def test_empty(self):
        mask, keycode = _parse_hotkey("")
        assert mask == 0
        assert keycode == 0

    def test_case_insensitive(self):
        mask1, key1 = _parse_hotkey("Shift+Control+KeyZ")
        mask2, key2 = _parse_hotkey("shift+control+keyz")
        assert mask1 == mask2
        assert key1 == key2


class TestCorrectTextStatic:
    def test_fast_mode_only(self):
        result, applied = correct_text_static(
            "我写到检测板里头",
            {"检测板": "剪切板"},
        )
        assert "剪切板" in result
        assert "检测板" not in result

    def test_no_dict_returns_original(self):
        result, applied = correct_text_static(
            "今天天气真好",
            {},
        )
        assert result == "今天天气真好"
        assert applied == []


class TestChineseCount:
    def test_pure_chinese(self):
        assert _count_chinese("你好世界") == 4

    def test_mixed(self):
        assert _count_chinese("hello世界123") == 2

    def test_no_chinese(self):
        assert _count_chinese("hello world") == 0
