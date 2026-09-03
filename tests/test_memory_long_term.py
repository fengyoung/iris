"""iris.memory.long_term 单元测试：UserProfileMemoryStore + CorrectionMemoryStore。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from iris.memory.long_term import CorrectionMemoryStore, UserProfileMemoryStore


# ── config mock ──────────────────────────────────────────────


def _make_config(tmp_path: Path) -> MagicMock:
    """构造最小化 ConfigBundle mock。"""
    config = MagicMock()
    config.root = tmp_path
    config.app = {"paths": {"memory_dir": "./memory"}}
    return config


# ── UserProfileMemoryStore ───────────────────────────────────


class TestUserProfileLoad:
    def test_file_not_exists_returns_default(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        state = store.load()
        assert "iris_persona" in state
        assert "user_preferences" in state
        prefs = state["user_preferences"]
        assert prefs["likes"] == []
        assert prefs["dislikes"] == []
        assert state["updated_at"] is None


class TestUserProfileApplyTextUpdate:
    def test_wo_xi_huan_adds_to_likes(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        updates = store.apply_text_update("我喜欢简洁的回答")
        assert any("喜欢" in u or "偏好" in u for u in updates)
        state = store.load()
        assert any("简洁" in item for item in state["user_preferences"]["likes"])

    def test_bu_xi_huan_adds_to_dislikes(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        store.apply_text_update("我不喜欢冗长的回答")
        state = store.load()
        assert any("冗长" in item for item in state["user_preferences"]["dislikes"])

    def test_ji_zhu_adds_to_notes(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        store.apply_text_update("记住我的团队是技术研发部")
        state = store.load()
        notes = state["user_preferences"]["notes"]
        assert any("技术研发部" in n for n in notes)

    def test_no_match_returns_empty_updates(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        updates = store.apply_text_update("这句话没有任何可识别的偏好指令")
        assert updates == []

    def test_no_duplicate_likes(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        store.apply_text_update("我喜欢简洁")
        store.apply_text_update("我喜欢简洁")
        state = store.load()
        likes = state["user_preferences"]["likes"]
        simplified_likes = [item for item in likes if "简洁" in item]
        assert len(simplified_likes) == 1


class TestUserProfileRenderForPrompt:
    def test_empty_returns_wu(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        result = store.render_for_prompt()
        assert result == "无"

    def test_with_likes_contains_keyword(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        store.apply_text_update("我喜欢简洁的输出")
        result = store.render_for_prompt()
        assert "喜欢" in result or "偏好" in result


class TestUserProfileSaveAndLoad:
    def test_save_and_load_roundtrip(self, tmp_path):
        config = _make_config(tmp_path)
        store = UserProfileMemoryStore(config)
        payload = {
            "iris_persona": {"description": "专业助手"},
            "user_preferences": {
                "likes": ["简洁"],
                "dislikes": ["冗长"],
                "style_preferences": [],
                "notes": ["团队：技术研发部"],
            },
        }
        store.save(payload)
        loaded = store.load()
        assert loaded["iris_persona"]["description"] == "专业助手"
        assert "简洁" in loaded["user_preferences"]["likes"]
        assert "冗长" in loaded["user_preferences"]["dislikes"]
        assert loaded["updated_at"] is not None


# ── CorrectionMemoryStore ─────────────────────────────────────


class TestCorrectionLoad:
    def test_default_state(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        state = store.load()
        assert state["items"] == {}
        assert state["updated_at"] is None


class TestCorrectionGetRelevant:
    def _store_with_data(self, tmp_path) -> CorrectionMemoryStore:
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        store.save({
            "items": {
                "BM25": {"preferred": "基于词频的排序算法", "update_count": 2, "updated_at": "2026-07-01T00:00:00"},
                "召回率": {"preferred": "检索到的相关文档占比", "update_count": 1, "updated_at": "2026-07-02T00:00:00"},
            }
        })
        return store

    def test_empty_returns_empty(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        result = store.get_relevant("任意查询")
        assert result == []

    def test_matching_concept_returned(self, tmp_path):
        store = self._store_with_data(tmp_path)
        result = store.get_relevant("BM25 算法")
        concepts = [r["concept"] for r in result]
        assert "BM25" in concepts

    def test_no_match_returns_unmatched_sorted(self, tmp_path):
        store = self._store_with_data(tmp_path)
        result = store.get_relevant("完全不相关的查询词")
        # 无匹配时返回最近更新的记录
        assert len(result) >= 1


class TestCorrectionGetFrequent:
    def test_filters_by_min_count(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        store.save({
            "items": {
                "A": {"preferred": "val_a", "update_count": 5},
                "B": {"preferred": "val_b", "update_count": 2},
                "C": {"preferred": "val_c", "update_count": 3},
            }
        })
        result = store.get_frequent_corrections(min_count=3)
        concepts = [r["concept"] for r in result]
        assert "A" in concepts
        assert "C" in concepts
        assert "B" not in concepts

    def test_sorted_by_count_desc(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        store.save({
            "items": {
                "X": {"preferred": "vx", "update_count": 10},
                "Y": {"preferred": "vy", "update_count": 3},
            }
        })
        result = store.get_frequent_corrections(min_count=3)
        counts = [r["update_count"] for r in result]
        assert counts == sorted(counts, reverse=True)


class TestCorrectionRenderForPrompt:
    def test_empty_returns_wu(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        result = store.render_for_prompt("任意查询")
        assert result == "无"

    def test_with_data_contains_arrow(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        store.save({
            "items": {
                "BM25": {"preferred": "词频排序", "update_count": 1, "updated_at": "2026-07-01T00:00:00"},
            }
        })
        result = store.render_for_prompt("BM25")
        assert "=>" in result
        assert "BM25" in result
        assert "词频排序" in result


class TestCorrectionDelete:
    def test_delete_existing_returns_true(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        store.save({
            "items": {
                "BM25": {"preferred": "词频", "update_count": 1},
            }
        })
        result = store.delete("BM25")
        assert result is True
        state = store.load()
        assert "BM25" not in state["items"]

    def test_delete_nonexistent_returns_false(self, tmp_path):
        config = _make_config(tmp_path)
        store = CorrectionMemoryStore(config)
        result = store.delete("不存在的概念XYZ")
        assert result is False
