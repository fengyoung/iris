""":memory: 会话模式挖掘器 单元测试。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── 测试数据 ──────────────────────────────────────────────────

def _sample_session_data():
    return {
        "recent_questions": [
            "ASR 校正的准确率怎么看？",
            "双周报的格式能改吗？",
            "ASR 热词怎么添加？",
            "帮我查一下 智能巡查的目标",
            "ASR 的 Aho-Corasick 算法原理？",
        ],
        "recent_topics": [
            "ASR 校正引擎", "双周报格式", "ASR 热词",
            "智能巡查", "ASR 算法",
        ],
        "topic_threads": {
            "ASR 校正引擎": {"count": 5, "last_question": "ASR 的 Aho-Corasick 算法原理？", "last_mode": "local"},
            "双周报格式": {"count": 2, "last_question": "双周报的格式能改吗？", "last_mode": "local"},
            "智能巡查": {"count": 1, "last_question": "帮我查一下 智能巡查的目标", "last_mode": "local"},
        },
        "recent_summary": "最近问题集中在 ASR 校正引擎的使用和配置",
    }


# ── 测试：数据充足性检查 ───────────────────────────────────

class TestHasEnoughData:
    def test_enough_topics(self):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner()
        data = {"recent_topics": ["A", "B", "C"], "recent_questions": []}
        assert miner._has_enough_data(data) is True

    def test_enough_questions(self):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner()
        data = {"recent_topics": [], "recent_questions": ["Q"] * 5}
        assert miner._has_enough_data(data) is True

    def test_insufficient_data(self):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner()
        data = {"recent_topics": ["A"], "recent_questions": ["Q1", "Q2"]}
        assert miner._has_enough_data(data) is False


# ── 测试：prompt 构建 ────────────────────────────────────────

class TestBuildMinePrompt:
    def test_contains_key_sections(self):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner()
        prompt = miner._build_mine_prompt(_sample_session_data())
        assert "近期问题" in prompt
        assert "近期主题" in prompt
        assert "高频主题线程" in prompt
        assert "ASR 校正引擎" in prompt
        assert "5次" in prompt


# ── 测试：LLM 响应解析 ──────────────────────────────────────

class TestParseMineResponse:
    def test_parse_valid_json(self):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner()
        payload = {
            "recurring_themes": [
                {"theme": "ASR 校正", "count": 5, "suggest_wiki": True, "suggest_note": True}
            ],
            "preference_patterns": [
                {"pattern": "喜欢简短回答", "evidence": "多次要求精简", "confidence": 0.8}
            ],
            "new_facts": [
                {"fact": "用户是数据部门负责人", "category": "工作背景"}
            ],
            "confidence": 0.8,
        }
        discoveries = miner._parse_mine_response(json.dumps(payload, ensure_ascii=False))
        assert len(discoveries) == 3
        assert discoveries[0]["type"] == "recurring_theme"
        assert discoveries[1]["type"] == "preference_pattern"
        assert discoveries[2]["type"] == "new_fact"

    def test_parse_with_code_block(self):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner()
        payload = '{"recurring_themes": [], "preference_patterns": [], "new_facts": [], "confidence": 0.0}'
        text = f"```json\n{payload}\n```"
        discoveries = miner._parse_mine_response(text)
        assert len(discoveries) == 0

    def test_parse_invalid_json_returns_empty(self):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner()
        discoveries = miner._parse_mine_response("not json at all")
        assert discoveries == []


# ── 测试：晋升逻辑 ───────────────────────────────────────────

class TestPromote:
    def test_promote_recurring_theme_to_notes(self, tmp_path):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner_with_paths(tmp_path)
        discovery = {
            "type": "recurring_theme",
            "theme": "智能巡查",
            "count": 5,
            "suggest_wiki": True,
            "suggest_note": True,
            "confidence": 0.9,
        }
        prefs: dict = miner._profile_memory.load().setdefault("user_preferences", {})
        changed = miner._apply_promotion(discovery, prefs)
        assert changed is True
        assert any("智能巡查" in n for n in prefs.get("notes", []))
        assert any("建议创建 Wiki" in n for n in prefs.get("notes", []))

    def test_promote_preference_to_styles(self, tmp_path):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner_with_paths(tmp_path)
        discovery = {
            "type": "preference_pattern",
            "pattern": "先数据后分析",
            "evidence": "多次要求先看数据",
            "confidence": 0.85,
        }
        prefs: dict = miner._profile_memory.load().setdefault("user_preferences", {})
        changed = miner._apply_promotion(discovery, prefs)
        assert changed is True
        styles = prefs.get("style_preferences", [])
        assert any("先数据后分析" in s for s in styles)

    def test_promote_new_fact_to_notes(self, tmp_path):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner_with_paths(tmp_path)
        discovery = {
            "type": "new_fact",
            "fact": "用户负责数据部门",
            "category": "工作背景",
            "confidence": 0.75,
        }
        prefs: dict = miner._profile_memory.load().setdefault("user_preferences", {})
        changed = miner._apply_promotion(discovery, prefs)
        assert changed is True
        notes = prefs.get("notes", [])
        assert any("用户负责数据部门" in n for n in notes)

    def test_duplicate_theme_not_promoted(self, tmp_path):
        from iris.memory.session_miner import SessionPatternMiner
        miner = _dummy_miner_with_paths(tmp_path)
        discovery = {
            "type": "recurring_theme",
            "theme": "ASR",
            "count": 3,
            "suggest_wiki": False,
            "suggest_note": True,
            "confidence": 0.8,
        }
        prefs: dict = miner._profile_memory.load().setdefault("user_preferences", {})
        # 第一次晋升
        assert miner._apply_promotion(discovery, prefs) is True
        # 第二次相同内容 → 不应重复
        assert miner._apply_promotion(discovery, prefs) is False


# ── 测试辅助 ──────────────────────────────────────────────────

class _FakeConfig:
    """最小化模拟 ConfigBundle，满足 SessionPatternMiner 初始化需要。"""
    def __init__(self, root: Path):
        self.root = root


def _dummy_miner():
    """创建不带真实文件路径的 miner（仅测试纯逻辑方法）。"""
    import iris.memory.session_miner as sm
    config = _FakeConfig(Path("/tmp/iris_test"))
    miner = sm.SessionPatternMiner.__new__(sm.SessionPatternMiner)
    miner._config = config
    # 内存 mock profile / corrections
    miner._profile_memory = MagicMock()
    miner._correction_memory = MagicMock()
    miner._profile_memory.load.return_value = {
        "user_preferences": {"likes": [], "dislikes": [], "style_preferences": [], "notes": []}
    }
    miner._correction_memory.load.return_value = {"items": {}}
    miner._llm_service = None
    miner._session_store = MagicMock()
    return miner


def _dummy_miner_with_paths(tmp_root: Path):
    """创建有真实文件路径的 miner（测试 _promote 写入）。"""
    from iris.memory.long_term import UserProfileMemoryStore, CorrectionMemoryStore
    import iris.memory.session_miner as sm

    # 创建必要的目录结构
    memory_dir = tmp_root / "memory" / "long_term"
    memory_dir.mkdir(parents=True, exist_ok=True)

    miner = sm.SessionPatternMiner.__new__(sm.SessionPatternMiner)

    class FakeConfig:
        pass

    cfg = FakeConfig()
    cfg.root = tmp_root
    cfg.app = {"paths": {"memory_dir": "memory"}}

    miner._config = cfg
    miner._profile_memory = UserProfileMemoryStore(cfg)
    miner._correction_memory = CorrectionMemoryStore(cfg)
    miner._session_store = MagicMock()
    miner._llm_service = None
    return miner
