"""LongTermMemoryManager 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path


from iris.config.loader import ConfigBundle, make_config_bundle
from iris.memory.manager import LongTermMemoryManager


def _make_config(tmp_path: Path) -> ConfigBundle:
    return make_config_bundle(
        root=tmp_path,
        app={"paths": {"memory_dir": "./memory"}},
        data_source={},
        llm={},
    )


def _seed_profile(tmp_path: Path, data: dict) -> None:
    lt_dir = tmp_path / "memory" / "long_term"
    lt_dir.mkdir(parents=True, exist_ok=True)
    (lt_dir / "profile.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


def _seed_corrections(tmp_path: Path, data: dict) -> None:
    lt_dir = tmp_path / "memory" / "long_term"
    lt_dir.mkdir(parents=True, exist_ok=True)
    (lt_dir / "corrections.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class TestListMemory:
    def test_list_all_contains_profile_and_corrections(self, tmp_path):
        config = _make_config(tmp_path)
        mgr = LongTermMemoryManager(config)
        result = mgr.list_memory("all")
        assert "profile" in result
        assert "corrections" in result

    def test_list_profile_only(self, tmp_path):
        config = _make_config(tmp_path)
        _seed_profile(tmp_path, {
            "iris_persona": {"description": "助手"},
            "user_preferences": {"likes": ["简洁"], "dislikes": [], "style_preferences": [], "notes": []},
        })
        mgr = LongTermMemoryManager(config)
        result = mgr.list_memory("profile")
        assert "profile" in result
        assert "corrections" not in result
        assert result["profile"]["iris_persona"]["description"] == "助手"

    def test_list_corrections_structure(self, tmp_path):
        config = _make_config(tmp_path)
        _seed_corrections(tmp_path, {
            "items": {
                "召回率": {"preferred": "recall", "update_count": 2, "updated_at": "2026-01-01"},
                "准确率": {"preferred": "precision", "update_count": 1, "updated_at": "2026-01-02"},
            }
        })
        mgr = LongTermMemoryManager(config)
        result = mgr.list_memory("corrections")
        assert "correction_count" in result
        assert result["correction_count"] == 2
        assert "items" in result
        # 按 concept 排序：准确率 < 召回率
        assert result["items"][0]["concept"] == "准确率"
        assert result["items"][1]["concept"] == "召回率"

    def test_list_corrections_fields(self, tmp_path):
        config = _make_config(tmp_path)
        _seed_corrections(tmp_path, {
            "items": {
                "BM25": {"preferred": "关键词检索算法", "update_count": 3, "updated_at": "2026-03-01"}
            }
        })
        mgr = LongTermMemoryManager(config)
        result = mgr.list_memory("corrections")
        item = result["items"][0]
        assert item["concept"] == "BM25"
        assert item["preferred"] == "关键词检索算法"
        assert item["update_count"] == 3


class TestDeleteCorrection:
    def test_delete_existing_concept(self, tmp_path):
        config = _make_config(tmp_path)
        _seed_corrections(tmp_path, {
            "items": {"召回率": {"preferred": "recall", "update_count": 1, "updated_at": "2026-01-01"}}
        })
        mgr = LongTermMemoryManager(config)
        result = mgr.delete_correction("召回率")
        assert result["deleted"] is True
        assert result["concept"] == "召回率"

    def test_delete_nonexistent_concept(self, tmp_path):
        config = _make_config(tmp_path)
        mgr = LongTermMemoryManager(config)
        result = mgr.delete_correction("不存在的概念")
        assert result["deleted"] is False
        assert result["concept"] == "不存在的概念"


class TestExportToFile:
    def test_export_creates_file(self, tmp_path):
        config = _make_config(tmp_path)
        _seed_profile(tmp_path, {
            "iris_persona": {"description": "测试助手"},
            "user_preferences": {"likes": [], "dislikes": [], "style_preferences": [], "notes": []},
        })
        mgr = LongTermMemoryManager(config)
        output_path = tmp_path / "export" / "memory_export.json"
        result = mgr.export_to_file(output_path)
        assert result == output_path
        assert output_path.exists()

    def test_export_contains_profile_and_corrections(self, tmp_path):
        config = _make_config(tmp_path)
        _seed_profile(tmp_path, {
            "iris_persona": {"description": "导出测试"},
            "user_preferences": {"likes": ["清晰"], "dislikes": [], "style_preferences": [], "notes": []},
        })
        _seed_corrections(tmp_path, {
            "items": {"测试概念": {"preferred": "test_concept", "update_count": 1, "updated_at": "2026-01-01"}}
        })
        mgr = LongTermMemoryManager(config)
        output_path = tmp_path / "out.json"
        mgr.export_to_file(output_path)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert "profile" in payload
        assert "corrections" in payload
        assert payload["profile"]["iris_persona"]["description"] == "导出测试"
        assert "测试概念" in payload["corrections"]["items"]


class TestImportFromFile:
    def _make_export_file(self, tmp_path: Path, profile: dict, corrections: dict) -> Path:
        export_path = tmp_path / "import_source.json"
        export_path.write_text(
            json.dumps({"profile": profile, "corrections": corrections}, ensure_ascii=False),
            encoding="utf-8"
        )
        return export_path

    def test_import_replace_mode(self, tmp_path):
        config = _make_config(tmp_path)
        # 先写入旧内容
        _seed_profile(tmp_path, {
            "iris_persona": {"description": "旧描述"},
            "user_preferences": {"likes": ["旧偏好"], "dislikes": [], "style_preferences": [], "notes": []},
        })
        profile = {
            "iris_persona": {"description": "新描述"},
            "user_preferences": {"likes": ["新偏好"], "dislikes": [], "style_preferences": [], "notes": []},
        }
        corrections = {"items": {}}
        import_path = self._make_export_file(tmp_path, profile, corrections)
        mgr = LongTermMemoryManager(config)
        result = mgr.import_from_file(import_path, replace=True)
        assert result["replace"] is True
        assert result["profile_updated"] is True
        # 验证替换成功
        stored = mgr.list_memory("profile")
        assert stored["profile"]["iris_persona"]["description"] == "新描述"

    def test_import_merge_mode_preferences_combined(self, tmp_path):
        config = _make_config(tmp_path)
        # 先写入旧偏好
        _seed_profile(tmp_path, {
            "iris_persona": {},
            "user_preferences": {"likes": ["旧偏好A"], "dislikes": [], "style_preferences": [], "notes": []},
        })
        profile = {
            "iris_persona": {},
            "user_preferences": {"likes": ["新偏好B"], "dislikes": [], "style_preferences": [], "notes": []},
        }
        corrections = {"items": {}}
        import_path = self._make_export_file(tmp_path, profile, corrections)
        mgr = LongTermMemoryManager(config)
        result = mgr.import_from_file(import_path, replace=False)
        assert result["replace"] is False
        stored = mgr.list_memory("profile")
        likes = stored["profile"]["user_preferences"]["likes"]
        # 合并模式：旧偏好和新偏好都存在
        assert "旧偏好A" in likes
        assert "新偏好B" in likes

    def test_import_merge_corrections_combined(self, tmp_path):
        config = _make_config(tmp_path)
        # 先写入旧纠正
        _seed_corrections(tmp_path, {
            "items": {"旧概念": {"preferred": "old_value", "update_count": 1, "updated_at": "2026-01-01"}}
        })
        profile = {"iris_persona": {}, "user_preferences": {}}
        corrections = {
            "items": {"新概念": {"preferred": "new_value", "update_count": 1, "updated_at": "2026-02-01"}}
        }
        import_path = self._make_export_file(tmp_path, profile, corrections)
        mgr = LongTermMemoryManager(config)
        mgr.import_from_file(import_path, replace=False)
        result = mgr.list_memory("corrections")
        concepts = [item["concept"] for item in result["items"]]
        assert "旧概念" in concepts
        assert "新概念" in concepts


class TestExportImportRoundtrip:
    def test_roundtrip_preserves_data(self, tmp_path):
        config = _make_config(tmp_path)
        _seed_profile(tmp_path, {
            "iris_persona": {"description": "roundtrip助手"},
            "user_preferences": {"likes": ["完整性"], "dislikes": ["冗长"], "style_preferences": [], "notes": []},
        })
        _seed_corrections(tmp_path, {
            "items": {
                "准确率": {"preferred": "precision", "update_count": 2, "updated_at": "2026-03-01"},
            }
        })
        mgr = LongTermMemoryManager(config)

        # 导出
        export_path = tmp_path / "roundtrip_export.json"
        mgr.export_to_file(export_path)

        # 清空当前状态（用新的临时目录）
        tmp2 = tmp_path / "new_instance"
        tmp2.mkdir()
        config2 = make_config_bundle(root=tmp2, app={"paths": {"memory_dir": "./memory"}}, data_source={}, llm={})
        mgr2 = LongTermMemoryManager(config2)

        # 导入
        mgr2.import_from_file(export_path, replace=True)

        stored_profile = mgr2.list_memory("profile")
        stored_corrections = mgr2.list_memory("corrections")

        assert stored_profile["profile"]["iris_persona"]["description"] == "roundtrip助手"
        assert "准确率" in [item["concept"] for item in stored_corrections["items"]]
