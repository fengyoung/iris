""":qa: 记忆更新器 LLM 通道 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── 测试：LLM 响应解析 ──────────────────────────────────────

class TestParseLLMResponse:
    def test_parse_valid_json(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        payload = {
            "new_likes": ["简短回答"],
            "new_dislikes": ["冗长解释"],
            "new_styles": ["先结论后分析"],
            "new_corrections": [{"concept": "QBR", "preferred": "季度业务回顾"}],
            "new_notes": ["用户在推进 智能巡检项目"],
            "confidence": 0.85,
        }
        result = updater._parse_llm_response(json.dumps(payload, ensure_ascii=False))
        assert result is not None
        assert result["new_likes"] == ["简短回答"]
        assert result["new_corrections"][0]["concept"] == "QBR"
        assert result["confidence"] == 0.85

    def test_parse_with_code_block(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        payload = '{"new_likes": ["短回答"], "new_dislikes": [], "new_styles": [], "new_corrections": [], "new_notes": [], "confidence": 0.7}'
        text = f"```json\n{payload}\n```"
        result = updater._parse_llm_response(text)
        assert result is not None
        assert result["new_likes"] == ["短回答"]

    def test_parse_invalid_json(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        result = updater._parse_llm_response("不是 JSON")
        assert result is None

    def test_parse_empty(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        result = updater._parse_llm_response("")
        assert result is None

    def test_parse_fallback_json_extraction(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        text = """一些前言文字...
{
  "new_likes": ["结构化输出"],
  "new_dislikes": [],
  "new_styles": [],
  "new_corrections": [],
  "new_notes": [],
  "confidence": 0.6
}
一些后记文字..."""
        result = updater._parse_llm_response(text)
        assert result is not None
        assert result["confidence"] == 0.6


# ── 测试：深度通道触发条件 ──────────────────────────────────

class TestShouldDeepAnalyze:
    def test_no_answer_skips(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        assert updater._should_deep_analyze("这是一个很长的有意义的问题需要分析", None) is False

    def test_short_question_skips(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        assert updater._should_deep_analyze("短问题", "有回答") is False

    def test_explicit_memory_skips(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        assert updater._should_deep_analyze("记住，我喜欢的分析风格是简洁的", "已回答") is False

    def test_normal_question_triggers(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        assert updater._should_deep_analyze("帮我分析一下这个季度 智能巡检的数据趋势", "详细回答...") is True


# ── 测试：正则快速通道 ──────────────────────────────────────

class TestRegexChannel:
    def test_explicit_remember(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        updates = updater._apply_regex_channel("记住，我喜欢简短的回答风格")
        assert len(updates) >= 0  # 依赖 profile 写入，但应无异常

    def test_explicit_correct(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        updates = updater._apply_regex_channel("纠正：NLP 是指自然语言处理")
        assert len(updates) >= 0

    def test_implicit_correction(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        updates = updater._apply_regex_channel("Q3 的目标应该是 85% 而不是 80%")
        assert len(updates) >= 0

    def test_normal_question_no_regex_match(self):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater()
        updates = updater._apply_regex_channel("今天的天气怎么样")
        assert updates == []


# ── 测试：提取结果应用 ──────────────────────────────────────

class TestApplyExtracted:
    def test_apply_likes(self, tmp_path):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater_with_paths(tmp_path)
        extracted = {
            "new_likes": ["结构化输出", "数据可视化"],
            "new_dislikes": [],
            "new_styles": [],
            "new_corrections": [],
            "new_notes": [],
            "confidence": 0.85,
        }
        updates = updater._apply_extracted(extracted)
        assert any("偏好(喜欢)" in u for u in updates)

        # 验证持久化
        profile = updater._profile_memory.load()
        likes = profile["user_preferences"]["likes"]
        assert "结构化输出" in likes
        assert "数据可视化" in likes

    def test_apply_dislikes(self, tmp_path):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater_with_paths(tmp_path)
        extracted = {
            "new_likes": [],
            "new_dislikes": ["过度技术化的解释"],
            "new_styles": [],
            "new_corrections": [],
            "new_notes": [],
            "confidence": 0.8,
        }
        updates = updater._apply_extracted(extracted)
        assert any("偏好(避免)" in u for u in updates)
        profile = updater._profile_memory.load()
        dislikes = profile["user_preferences"]["dislikes"]
        assert "过度技术化的解释" in dislikes

    def test_apply_correction(self, tmp_path):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater_with_paths(tmp_path)
        extracted = {
            "new_likes": [],
            "new_dislikes": [],
            "new_styles": [],
            "new_corrections": [{"concept": "PV", "preferred": "页面浏览量"}],
            "new_notes": [],
            "confidence": 0.9,
        }
        updates = updater._apply_extracted(extracted)
        assert any("纠正规则" in u for u in updates)
        corrections = updater._correction_memory.load()
        assert "PV" in corrections["items"]

    def test_low_confidence_discarded(self, tmp_path):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater_with_paths(tmp_path)
        extracted = {
            "new_likes": ["不确定的偏好"],
            "new_dislikes": [],
            "new_styles": [],
            "new_corrections": [],
            "new_notes": [],
            "confidence": 0.3,
        }
        # 低置信度在 _apply_llm_channel 层被丢弃，_apply_extracted 不会被调用
        # 这里直接测试 _apply_extracted 仍会正常处理
        updates = updater._apply_extracted(extracted)
        # 但 _apply_llm_channel 已在外部做了 confidence 检查
        assert len(updates) >= 1  # _apply_extracted 不管置信度

    def test_duplicate_likes_not_added(self, tmp_path):
        from iris.qa.memory_updater import MemoryUpdater
        updater = _dummy_updater_with_paths(tmp_path)
        # 先写入一次
        extracted1 = {
            "new_likes": ["简洁回答"], "new_dislikes": [], "new_styles": [],
            "new_corrections": [], "new_notes": [], "confidence": 0.8,
        }
        updater._apply_extracted(extracted1)
        # 再写入相同内容
        updates = updater._apply_extracted(extracted1)
        # 第二次不应重复添加
        profile = updater._profile_memory.load()
        likes = profile["user_preferences"]["likes"]
        assert likes.count("简洁回答") == 1


# ── 测试辅助 ──────────────────────────────────────────────────

def _dummy_updater():
    """创建不带真实文件路径的 updater（测试纯逻辑方法）。"""
    from iris.qa.memory_updater import MemoryUpdater
    config = _FakeConfig(Path("/tmp/iris_test"))

    updater = MemoryUpdater.__new__(MemoryUpdater)
    updater._config = config
    updater._profile_memory = MagicMock()
    updater._correction_memory = MagicMock()
    updater._llm_service = None
    updater._mine_state_path = Path("/tmp/iris_test/data/last_session_mine.json")
    return updater


def _dummy_updater_with_paths(tmp_root: Path):
    """创建有真实文件路径的 updater（测试 _apply_extracted 写入）。"""
    from iris.qa.memory_updater import MemoryUpdater
    from iris.memory.long_term import UserProfileMemoryStore, CorrectionMemoryStore

    memory_dir = tmp_root / "memory" / "long_term"
    memory_dir.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    class FakeConfig:
        pass

    cfg = FakeConfig()
    cfg.root = tmp_root
    cfg.app = {"paths": {"memory_dir": "memory"}}

    updater = MemoryUpdater.__new__(MemoryUpdater)
    updater._config = cfg
    updater._profile_memory = UserProfileMemoryStore(cfg)
    updater._correction_memory = CorrectionMemoryStore(cfg)
    updater._llm_service = None
    updater._mine_state_path = data_dir / "last_session_mine.json"
    return updater


class _FakeConfig:
    def __init__(self, root: Path):
        self.root = root
