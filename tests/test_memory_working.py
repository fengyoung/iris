"""memory/working.py WorkingContextStore 单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from iris.memory.working import WorkingContextStore


def _make_config(tmp_path: Path):
    """构建模拟 ConfigBundle 用于 WorkingContextStore。"""
    import types
    return types.SimpleNamespace(
        root=tmp_path,
        app={"paths": {"memory_dir": str(tmp_path / "memory")}},
    )


class TestWorkingContextStore:

    def test_load_empty(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        state = store.load()
        assert state["current_task"] == ""
        assert state["pending_items"] == []
        assert state["recent_changes"] == []
        assert state["notes"] == ""

    def test_save_and_load(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        payload = {
            "current_task": "实现用户认证",
            "pending_items": ["写测试", "重构"],
            "recent_changes": ["新增登录页面"],
            "notes": "本周完成",
        }
        result = store.save(payload)
        assert result["current_task"] == "实现用户认证"
        assert "updated_at" in result

        # 重新加载验证持久化
        state = store.load()
        assert state["current_task"] == "实现用户认证"
        assert state["pending_items"] == ["写测试", "重构"]

    def test_update_current_task(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        store.update(current_task="新任务")
        state = store.load()
        assert state["current_task"] == "新任务"

    def test_update_append_pending(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        store.update(pending_items=["A", "B"])
        store.update(append_pending=["C"])
        state = store.load()
        assert "A" in state["pending_items"]
        assert "B" in state["pending_items"]
        assert "C" in state["pending_items"]

    def test_update_append_changes(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        store.update(recent_changes=["初始变更"])
        store.update(append_changes=["追加变更"])
        state = store.load()
        assert "初始变更" in state["recent_changes"]
        assert "追加变更" in state["recent_changes"]

    def test_update_notes(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        store.update(notes="备注信息")
        state = store.load()
        assert state["notes"] == "备注信息"

    def test_render_for_prompt(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        store.update(current_task="任务X", pending_items=["A", "B"])
        rendered = store.render_for_prompt()
        assert "任务X" in rendered
        assert isinstance(rendered, str)

    def test_path_property(self, tmp_path):
        config = _make_config(tmp_path)
        store = WorkingContextStore(config)
        assert "working_context.md" in str(store.path)
