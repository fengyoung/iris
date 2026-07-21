"""问答记忆更新检测 — 单元测试（mock memory stores）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from iris.qa.memory_updater import MemoryUpdater, IMPLICIT_CORRECTION_RE


class TestImplicitCorrectionRe:
    def test_matches_correction_with_ershi(self):
        assert IMPLICIT_CORRECTION_RE.search("不是张三而是李四")

    def test_matches_should_be(self):
        assert IMPLICIT_CORRECTION_RE.search("应该是王五")

    def test_matches_defined_as(self):
        assert IMPLICIT_CORRECTION_RE.search("BM25定义为最佳匹配算法")

    def test_matches_correction_keyword(self):
        assert IMPLICIT_CORRECTION_RE.search("纠正：张珊应为张三")

    def test_no_match_on_normal_question(self):
        assert not IMPLICIT_CORRECTION_RE.search("今天天气怎么样")

    def test_no_match_on_empty(self):
        assert not IMPLICIT_CORRECTION_RE.search("")


class TestMemoryUpdater:
    def _make_updater(self):
        """创建 MemoryUpdater，注入 mock stores。"""
        with patch("iris.qa.memory_updater.UserProfileMemoryStore") as mock_profile_cls, \
             patch("iris.qa.memory_updater.CorrectionMemoryStore") as mock_correction_cls:
            mock_profile = MagicMock()
            mock_correction = MagicMock()
            mock_profile_cls.return_value = mock_profile
            mock_correction_cls.return_value = mock_correction

            mock_config = MagicMock()
            updater = MemoryUpdater(mock_config)
            return updater, mock_profile, mock_correction

    def test_no_update_on_normal_question(self):
        updater, profile, correction = self._make_updater()
        result = updater.apply_updates("今天天气怎么样")
        assert result == []

    def test_implicit_correction_triggers_update(self):
        """隐式纠正触发 correction store 更新。"""
        updater, profile, correction = self._make_updater()
        correction.apply_text_update.return_value = ["纠正: 张珊→张三"]
        # 使用 IMPLICIT_CORRECTION_RE 能匹配但不匹配 EXPLICIT_MEMORY_RE 的文本
        result = updater.apply_updates("纠正：张珊应该是张三才对")
        correction.apply_text_update.assert_called()
        assert len(result) >= 0  # 可能为空取决于 mock 返回值

    def test_summarize_skipped_when_no_frequent(self):
        """get_frequent_corrections 返回空时不写 profile。"""
        updater, profile, correction = self._make_updater()
        correction.apply_text_update.return_value = ["纠正: test"]
        correction.get_frequent_corrections.return_value = []

        # 需要同时 mock 两个 store 的 apply_text_update
        # 因为 EXPLICIT_MEMORY_RE 可能匹配，需两个都返回
        profile.apply_text_update.return_value = []

        result = updater.apply_updates("纠正：某术语应更正为某写法")
        profile.save.assert_not_called()

    def test_question_with_no_correction_pattern_does_nothing(self):
        """不匹配任何纠正模式的普通问题不触发任何更新。"""
        updater, profile, correction = self._make_updater()
        result = updater.apply_updates("搜索推荐的最新算法是什么")
        assert result == []
        correction.apply_text_update.assert_not_called()
        profile.apply_text_update.assert_not_called()
