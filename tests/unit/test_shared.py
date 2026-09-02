"""iris.utils.shared 单元测试 — atomic_write_json / now_iso。"""

from __future__ import annotations

import json
from pathlib import Path


from iris.utils.shared import atomic_write_json, now_iso


class TestAtomicWriteJson:
    """atomic_write_json 测试。"""

    def test_creates_file_with_content(self, tmp_path: Path):
        """成功写入 JSON 文件。"""
        path = tmp_path / "test.json"
        data = {"key": "value", "number": 42}
        atomic_write_json(path, data)

        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == data

    def test_creates_parent_directories(self, tmp_path: Path):
        """自动创建父目录。"""
        path = tmp_path / "deep" / "nested" / "test.json"
        data = {"a": 1}
        atomic_write_json(path, data)

        assert path.exists()
        assert json.loads(path.read_text(encoding="utf-8")) == data

    def test_no_temp_file_left_after_success(self, tmp_path: Path):
        """成功后无残留临时文件。"""
        path = tmp_path / "test.json"
        atomic_write_json(path, {"x": 1})

        # 目录中应只有一个文件
        files = list(tmp_path.glob("*"))
        assert len(files) == 1
        assert files[0].name == "test.json"

    def test_overwrites_existing_file(self, tmp_path: Path):
        """覆盖已存在的文件。"""
        path = tmp_path / "test.json"
        path.write_text('{"old": true}', encoding="utf-8")

        atomic_write_json(path, {"new": "data"})

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == {"new": "data"}

    def test_unicode_content(self, tmp_path: Path):
        """正确处理中文等 Unicode 内容。"""
        path = tmp_path / "test.json"
        data = {"标题": "测试内容", "标签": ["中文", "English"]}
        atomic_write_json(path, data)

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == data


class TestNowIso:
    """now_iso 测试。"""

    def test_returns_iso_format(self):
        """返回 ISO 8601 格式字符串。"""
        result = now_iso()
        # 格式: 2026-07-17T12:00:00.123456+00:00
        assert "T" in result
        assert "+" in result or "Z" in result

    def test_returns_consistent_format(self):
        """多次调用格式一致。"""
        r1 = now_iso()
        r2 = now_iso()
        assert len(r1) == len(r2)
        # 都应包含日期和时间分隔符
        assert "-" in r1
        assert ":" in r1
