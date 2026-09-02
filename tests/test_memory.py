"""记忆系统模块测试。"""

from __future__ import annotations



from iris.memory.long_term import CorrectionMemoryStore, UserProfileMemoryStore
from iris.memory.session import SessionMemoryStore
from iris.memory.working import WorkingContextStore
from iris.memory.lifecycle import MemoryLifecycle


class TestUserProfileMemoryStore:
    def test_load_empty(self, config_bundle):
        store = UserProfileMemoryStore(config_bundle)
        data = store.load()
        assert isinstance(data, dict)
        assert "user_preferences" in data

    def test_save_and_load(self, config_bundle):
        store = UserProfileMemoryStore(config_bundle)
        payload = {"user_preferences": {"likes": ["Python"], "dislikes": []},
                   "iris_persona": {"description": "助手"}, "updated_at": "2026-01-01"}
        store.save(payload)
        loaded = store.load()
        assert loaded["user_preferences"]["likes"] == ["Python"]
        assert loaded["iris_persona"]["description"] == "助手"


class TestCorrectionMemoryStore:
    def test_load_empty(self, config_bundle):
        store = CorrectionMemoryStore(config_bundle)
        data = store.load()
        assert isinstance(data, dict)

    def test_save_and_delete(self, config_bundle):
        store = CorrectionMemoryStore(config_bundle)
        # 直接操作存储结构
        payload = {"items": {"test_concept": {"preferred": "正确说法", "update_count": 1,
                                                "updated_at": "2026-06-26", "last_source": "来源"}}}
        store.save(payload)
        data = store.load()
        assert "test_concept" in data.get("items", {})

        deleted = store.delete("test_concept")
        assert deleted is True
        data = store.load()
        assert "test_concept" not in data.get("items", {})


class TestWorkingContextStore:
    def test_load_empty(self, config_bundle):
        store = WorkingContextStore(config_bundle)
        data = store.load()
        assert isinstance(data, dict)

    def test_update(self, config_bundle):
        store = WorkingContextStore(config_bundle)
        result = store.update(current_task="测试任务", pending_items=["事项1"])
        assert result["current_task"] == "测试任务"
        assert "事项1" in result.get("pending_items", [])

    def test_clear(self, config_bundle):
        store = WorkingContextStore(config_bundle)
        store.update(current_task="待清除")
        result = store.clear()
        assert result["current_task"] == ""


class TestSessionMemoryStore:
    def test_load_empty(self, config_bundle):
        store = SessionMemoryStore(config_bundle)
        data = store.load()
        assert isinstance(data, dict)

    def test_save_interaction(self, config_bundle):
        store = SessionMemoryStore(config_bundle)
        result = store.save_interaction(question="测试问题", mode="local", blocks=[], wiki_hits=[])
        assert "recent_questions" in result
        assert result["recent_questions"][0] == "测试问题"


class TestMemoryLifecycle:
    def test_maintenance_no_errors(self, config_bundle):
        lifecycle = MemoryLifecycle(config_bundle)
        report = lifecycle.maintenance()
        assert "conflicts" in report
        assert "stale_corrections" in report
        assert "summary" in report
