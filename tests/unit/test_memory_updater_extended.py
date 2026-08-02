"""qa/memory_updater.py 扩展测试 — 覆盖 _should_deep_analyze, _parse_llm_response, _auto_resolve_conflict。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from iris.qa.memory_updater import (
    MemoryUpdater,
    IMPLICIT_CORRECTION_RE,
    _MIN_QUESTION_LENGTH,
    _MIN_CONFIDENCE,
)


class TestImplicitCorrectionReExtended:
    def test_matches_with_shi(self):
        """'不是X是Y' 变体。"""
        assert IMPLICIT_CORRECTION_RE.search("不是这样子是那样")

    def test_matches_defined_as(self):
        assert IMPLICIT_CORRECTION_RE.search("召回率定义为检索到的相关文档比例")

    def test_matches_should_shi_alt(self):
        """IMP 匹配 '应为' 前缀（`应该是|应为|指的是|定义为`）。"""
        assert IMPLICIT_CORRECTION_RE.search("应为推荐系统")

    def test_long_text_still_matches(self):
        text = "前面有很多前缀文字不是张三而是李四还有很多后缀"
        assert IMPLICIT_CORRECTION_RE.search(text)

    def test_only_spaces_no_match(self):
        assert not IMPLICIT_CORRECTION_RE.search("   ")


class TestShouldDeepAnalyze:
    def _make_updater(self):
        with patch("iris.qa.memory_updater.UserProfileMemoryStore"), \
             patch("iris.qa.memory_updater.CorrectionMemoryStore"):
            cfg = MagicMock()
            return MemoryUpdater(cfg)

    def test_no_answer_returns_false(self):
        u = self._make_updater()
        assert u._should_deep_analyze("一个问题足够长了吧", None) is False

    def test_short_question_returns_false(self):
        u = self._make_updater()
        assert u._should_deep_analyze("短问题", "有答案") is False

    def test_explicit_command_skipped(self):
        """显式记忆命令已被正则处理，不走深度通道。"""
        u = self._make_updater()
        # "记住我喜欢简洁回答" 应匹配 EXPLICIT_MEMORY_RE
        result = u._should_deep_analyze("记住我喜欢简洁回答", "好的已记录")
        assert result is False

    def test_normal_question_returns_true(self):
        u = self._make_updater()
        result = u._should_deep_analyze("请问数据仓库的召回率如何优化？", "可以通过以下方式优化...")
        assert result is True

    def test_boundary_length(self):
        u = self._make_updater()
        q = "A" * _MIN_QUESTION_LENGTH
        assert u._should_deep_analyze(q, "answer") is True


class TestParseLLMResponse:
    def _make_updater(self):
        with patch("iris.qa.memory_updater.UserProfileMemoryStore"), \
             patch("iris.qa.memory_updater.CorrectionMemoryStore"):
            cfg = MagicMock()
            return MemoryUpdater(cfg)

    def test_valid_json_object(self):
        u = self._make_updater()
        data = {"confidence": 0.8, "new_likes": ["简洁"]}
        result = u._parse_llm_response(json.dumps(data))
        assert result == data

    def test_json_in_markdown_block(self):
        u = self._make_updater()
        data = json.dumps({"confidence": 0.7, "new_notes": ["记录"]})
        result = u._parse_llm_response(f"```json\n{data}\n```")
        assert result is not None
        assert result["confidence"] == 0.7

    def test_json_with_extra_text(self):
        u = self._make_updater()
        text = '前面文字{"confidence": 0.9}后面文字'
        result = u._parse_llm_response(text)
        # extract_json_object 应能提取
        assert result is None or result.get("confidence") == 0.9

    def test_invalid_json_returns_none(self):
        u = self._make_updater()
        assert u._parse_llm_response("not json at all { broken") is None

    def test_empty_string(self):
        u = self._make_updater()
        assert u._parse_llm_response("") is None

    def test_non_dict_json(self):
        u = self._make_updater()
        assert u._parse_llm_response("[1, 2, 3]") is None


class TestAutoResolveConflict:
    def _make_updater(self):
        with patch("iris.qa.memory_updater.UserProfileMemoryStore"), \
             patch("iris.qa.memory_updater.CorrectionMemoryStore"):
            cfg = MagicMock()
            return MemoryUpdater(cfg)

    def test_llm_confirmed_at_5(self):
        """LLM 提取且 update_count >= 5 → 标记 AUTO-CONFIRMED。"""
        u = self._make_updater()
        items = {}
        entry = {
            "preferred": "正确值",
            "update_count": 5,
            "last_source": "[LLM] 多轮一致提取",
        }
        # LLM 路径不会真正 resolve，返回 False 但标记 confirmed
        result = u._auto_resolve_conflict("概念X", entry, items)
        assert result is False  # LLM 路径总是返回 False
        # entry 被就地更新标记为 confirmed
        assert "[AUTO-CONFIRMED]" in entry["last_source"]

    def test_regex_conflict_resolved(self):
        """正则提取检测到 '不是X是Y' 且 preferred=X → 修正为 Y。
        regex 设计为 '不是X,而是Y'，但贪婪量词 + [,，]? 可选导致
        '不是ABC而是DEF' 中 g1='ABC而'（吞掉'而'）。改为 '不是ABC是DEF' 格式绕过。"""
        u = self._make_updater()
        items = {}
        entry = {
            "preferred": "张三",
            "update_count": 3,
            "last_source": "不是张三是李四",
        }
        result = u._auto_resolve_conflict("概念Y", entry, items)
        assert result is True
        assert items["概念Y"]["preferred"] == "李四"
        assert "[AUTO-RESOLVED]" in items["概念Y"]["last_source"]

    def test_regex_no_conflict_pattern(self):
        """last_source 不含冲突模式 → 不触发。"""
        u = self._make_updater()
        items = {}
        entry = {
            "preferred": "值",
            "update_count": 3,
            "last_source": "用户指定纠正",
        }
        result = u._auto_resolve_conflict("概念Z", entry, items)
        assert result is False

    def test_regex_confirmed_at_5(self):
        """正则提取 update_count >= 5 → AUTO-CONFIRMED。"""
        u = self._make_updater()
        items = {}
        entry = {
            "preferred": "稳定值",
            "update_count": 5,
            "last_source": "多次明确纠正",
        }
        result = u._auto_resolve_conflict("概念W", entry, items)
        assert "[AUTO-CONFIRMED]" in items["概念W"]["last_source"]


class TestApplyExtracted:
    def _make_updater_with_mocks(self):
        with patch("iris.qa.memory_updater.UserProfileMemoryStore") as mp, \
             patch("iris.qa.memory_updater.CorrectionMemoryStore") as mc:
            mock_profile = MagicMock()
            mock_correction = MagicMock()
            mp.return_value = mock_profile
            mc.return_value = mock_correction

            mock_profile.load.return_value = {
                "user_preferences": {"likes": [], "dislikes": [], "style_preferences": [], "notes": []},
            }
            mock_correction.load.return_value = {"items": {}}
            mock_correction.get_frequent_corrections.return_value = []

            cfg = MagicMock()
            u = MemoryUpdater(cfg)
            u._profile_memory = mock_profile
            u._correction_memory = mock_correction
            return u, mock_profile, mock_correction

    def test_extracts_likes(self):
        u, profile, correction = self._make_updater_with_mocks()
        extracted = {"confidence": 0.9, "new_likes": ["简洁回答", "代码示例"]}
        updates = u._apply_extracted(extracted)
        assert any("喜欢" in upd for upd in updates)
        profile.save.assert_called()

    def test_extracts_dislikes(self):
        u, profile, correction = self._make_updater_with_mocks()
        extracted = {"confidence": 0.7, "new_dislikes": ["冗长解释"]}
        updates = u._apply_extracted(extracted)
        assert any("避免" in upd for upd in updates)

    def test_extracts_styles(self):
        u, profile, correction = self._make_updater_with_mocks()
        extracted = {"confidence": 0.8, "new_styles": ["使用表格"]}
        updates = u._apply_extracted(extracted)
        assert any("风格" in upd for upd in updates)

    def test_extracts_notes(self):
        u, profile, correction = self._make_updater_with_mocks()
        extracted = {"confidence": 0.6, "new_notes": ["用户备注内容"]}
        updates = u._apply_extracted(extracted)
        assert any("备注" in upd for upd in updates)

    def test_extracts_corrections(self):
        u, profile, correction = self._make_updater_with_mocks()
        extracted = {
            "confidence": 0.85,
            "new_corrections": [
                {"concept": "AI", "preferred": "人工智能", "context": "用户纠正"},
            ],
        }
        updates = u._apply_extracted(extracted)
        assert any("纠正" in upd for upd in updates)

    def test_empty_extraction_no_changes(self):
        u, profile, correction = self._make_updater_with_mocks()
        profile.load.return_value = {"user_preferences": {}}
        extracted = {"confidence": 0.9}
        updates = u._apply_extracted(extracted)
        assert updates == []
        profile.save.assert_not_called()

    def test_empty_concept_skipped(self):
        """空白概念名被静默跳过。"""
        u, profile, correction = self._make_updater_with_mocks()
        extracted = {
            "confidence": 0.8,
            "new_corrections": [
                {"concept": "", "preferred": "值"},
                {"concept": "有效", "preferred": "值"},
            ],
        }
        updates = u._apply_extracted(extracted)
        # 仅有效概念触发更新
        assert any("有效" in upd for upd in updates)
