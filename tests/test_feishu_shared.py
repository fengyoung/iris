"""测试 feishu/_shared.py — 路径解析、排重索引、标题清理、时间解析。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iris.feishu._shared import (
    resolve_source_root,
    resolve_source_sub_dir,
    resolve_pic_dir,
    resolve_dedup_path,
    load_dedup_index,
    save_dedup_index,
    upsert_dedup_item,
    sanitize_title,
    extract_date,
    now_iso,
)


class TestResolveSourceRoot:
    def test_enabled_source_returns_path(self, temp_project):
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project,
            app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "test",
                "sources": {"test": {"enabled": True, "path": str(source_dir)}}},
            llm_dict={},
        )
        result = resolve_source_root(bundle)
        assert result == source_dir.resolve()

    def test_disabled_source_returns_none(self, temp_project):
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project,
            app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "test",
                "sources": {"test": {"enabled": False, "path": str(source_dir)}}},
            llm_dict={},
        )
        result = resolve_source_root(bundle)
        assert result is None

    def test_no_enabled_sources_returns_none(self, temp_project):
        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project, app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "test",
                "sources": {"test": {"enabled": False, "path": "/tmp/test"}}},
            llm_dict={},
        )
        result = resolve_source_root(bundle)
        assert result is None


class TestResolveSourceSubDir:
    def test_creates_subdirectory(self, temp_project):
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project,
            app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "test",
                "sources": {"test": {"enabled": True, "path": str(source_dir)}}},
            llm_dict={},
        )
        result = resolve_source_sub_dir(bundle, "05-会议纪要")
        assert result.exists()
        assert result.name == "05-会议纪要"


class TestResolvePicDir:
    def test_falls_back_to_data_pic(self, temp_project):
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        from iris.config.models import ConfigBundleV2
        bundle = ConfigBundleV2.from_dicts(
            root=temp_project,
            app_dict={"version": "3.0"},
            data_source_dict={"version": "1.0", "default_source": "test",
                "sources": {"test": {"enabled": False, "path": str(source_dir)}}},
            llm_dict={},
        )
        result = resolve_pic_dir(bundle)
        assert result.exists()
        assert "pic" in str(result)


class TestDedupIndex:
    def test_load_nonexistent_returns_default(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        result = load_dedup_index(path)
        assert result["version"] == "1.0"
        assert result["items"] == []

    def test_load_valid_json(self, tmp_path):
        path = tmp_path / "index.json"
        path.write_text(json.dumps({"version": "1.0", "items": [{"key": "v"}]}), encoding="utf-8")
        result = load_dedup_index(path)
        assert result["version"] == "1.0"
        assert len(result["items"]) == 1

    def test_load_invalid_json_returns_default(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("not json", encoding="utf-8")
        result = load_dedup_index(path)
        assert result["version"] == "1.0"

    def test_save_creates_file(self, tmp_path):
        path = tmp_path / "out" / "index.json"
        save_dedup_index(path, {"version": "1.0", "items": []})
        assert path.exists()

    def test_upsert_new_item(self):
        index = {"version": "1.0", "items": []}
        upsert_dedup_item(index, "key1", {"dedup_key": "key1", "value": "new"})
        assert len(index["items"]) == 1
        assert index["items"][0]["value"] == "new"

    def test_upsert_replaces_duplicate_key(self):
        index = {"version": "1.0", "items": [
            {"dedup_key": "key1", "value": "old"},
            {"dedup_key": "key2", "value": "keep"},
        ]}
        upsert_dedup_item(index, "key1", {"dedup_key": "key1", "value": "new"})
        assert len(index["items"]) == 2
        values = {it["value"] for it in index["items"]}
        assert "keep" in values
        assert "new" in values
        assert "old" not in values

    def test_upsert_dedup_by_source_url(self):
        index = {"version": "1.0", "items": [
            {"dedup_key": "k1", "source_url": "http://a.com/doc1"},
        ]}
        upsert_dedup_item(index, "k2", {"dedup_key": "k2", "source_url": "http://a.com/doc1"})
        assert len(index["items"]) == 1
        assert index["items"][0]["dedup_key"] == "k2"


class TestSanitizeTitle:
    def test_normal_title(self):
        assert sanitize_title("测试标题") == "测试标题"

    def test_removes_illegal_chars(self):
        result = sanitize_title("test:file*name")
        assert ":" not in result
        assert "*" not in result

    def test_replaces_spaces(self):
        result = sanitize_title("hello world test")
        assert result == "hello-world-test"

    def test_truncates_long_title(self):
        result = sanitize_title("a" * 100)
        assert len(result) <= 60

    def test_empty_title_fallback(self):
        assert sanitize_title("") == "未命名"


class TestExtractDate:
    def test_iso_format(self):
        assert extract_date("2026-07-16T10:30:00+08:00") == "20260716"

    def test_unix_timestamp(self):
        result = extract_date("1752691200")  # 2026-07-16 UTC
        assert len(result) == 8  # YYYYmmdd

    def test_empty_returns_empty(self):
        assert extract_date("") == ""

    def test_invalid_returns_empty(self):
        assert extract_date("not a date") == ""


class TestNowIso:
    def test_returns_iso_format(self):
        result = now_iso()
        assert "T" in result
        assert "+" in result or "Z" in result
