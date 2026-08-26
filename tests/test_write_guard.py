"""测试写入路径守卫 — core/write_guard.py。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from iris.config.loader import ConfigBundle
from iris.core.write_guard import (
    WriteGuardError,
    resolve_allowed_paths,
    validate_write_path,
    safe_write_bytes,
    safe_write_text,
)


class TestResolveAllowedPaths:
    """resolve_allowed_paths 的单元测试。"""

    def test_default_paths_from_paths_config(self, config_bundle):
        """无自定义 allowed_write_paths 时从 paths 段推断。"""
        allowed = resolve_allowed_paths(config_bundle)
        assert len(allowed) > 0
        # 内部关键目录必须存在
        essential_names = {"data", "temp", "output", "memory", "logs"}
        found_names = {p.name for p in allowed}
        assert essential_names.issubset(found_names)

    def test_user_defined_paths_respected(self, temp_project, minimal_app_config,
                                          minimal_llm_config, minimal_data_source_config):
        """用户自定义 allowed_write_paths 正确解析。"""
        minimal_app_config["safety"] = {
            "allowed_write_paths": ["/tmp/custom_output", "./custom_temp"],
            "enforce_write_guard": True,
        }
        config_dir = temp_project / "config"
        for name, data in [("app", minimal_app_config), ("llm", minimal_llm_config),
                            ("data_source", minimal_data_source_config)]:
            (config_dir / f"{name}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (temp_project / ".env").write_text("TEST_API_KEY=sk-test-key\n", encoding="utf-8")
        from iris.config.loader import load_config_bundle
        bundle = load_config_bundle(temp_project)
        allowed = resolve_allowed_paths(bundle)
        allowed_strs = [str(p) for p in allowed]
        assert any("/tmp/custom_output" in s for s in allowed_strs)
        assert any("custom_temp" in s for s in allowed_strs)


class TestValidateWritePath:
    """validate_write_path 的单元测试。"""

    def test_path_inside_allowed_succeeds(self, config_bundle, temp_project):
        """路径在允许范围内，应返回 resolved 路径。"""
        data_dir = temp_project / "data"
        data_dir.mkdir(exist_ok=True)
        target = data_dir / "test_output.md"
        result = validate_write_path(target, config_bundle)
        assert result == target.resolve()

    def test_path_outside_allowed_raises(self, config_bundle):
        """路径不在允许范围，应抛出 WriteGuardError。"""
        outside = Path("/tmp/some_random_path_iris_test")
        with pytest.raises(WriteGuardError, match="拒绝写入"):
            validate_write_path(outside, config_bundle)

    def test_relative_path_inside_allowed(self, config_bundle, temp_project):
        """相对路径在允许范围内，正确 resolve。"""
        data_dir = temp_project / "data"
        data_dir.mkdir(exist_ok=True)
        # 使用 str() 确保从临时项目根目录解析
        target = Path(str(temp_project)) / "data" / "test_output.md"
        result = validate_write_path(target, config_bundle)
        assert result.is_absolute()
        assert str(temp_project.resolve()) in str(result)

    def test_essential_subdirs_always_allowed(self, config_bundle, temp_project):
        """内部关键目录即使不在 allowed 列表中也会附加。"""
        mem_dir = temp_project / "memory"
        mem_dir.mkdir(exist_ok=True)
        result = validate_write_path(mem_dir / "test.json", config_bundle)
        assert result == (mem_dir / "test.json").resolve()


class TestSafeWriteText:
    """safe_write_text 的单元测试。"""

    def test_write_inside_allowed(self, config_bundle, temp_project):
        """允许路径内的写入成功。"""
        data_dir = temp_project / "data"
        data_dir.mkdir(exist_ok=True)
        target = data_dir / "test_write.md"
        result = safe_write_text(target, "# Test", config_bundle)
        assert result.exists()
        assert result.read_text(encoding="utf-8") == "# Test"

    def test_write_outside_allowed_raises(self, config_bundle):
        """不允许路径的写入抛出异常。"""
        outside = Path("/tmp/iris_test_forbidden.md")
        with pytest.raises(WriteGuardError):
            safe_write_text(outside, "content", config_bundle)

    def test_write_outside_allowed_with_existing_flag(self, config_bundle, temp_project):
        """allow_existing_outside=True 且文件已存在，允许写入。"""
        outside = Path("/tmp/iris_test_existing.md")
        outside.parent.mkdir(parents=True, exist_ok=True)
        try:
            outside.write_text("existing", encoding="utf-8")
            result = safe_write_text(
                outside, "updated", config_bundle, allow_existing_outside=True
            )
            assert result.exists()
        finally:
            outside.unlink(missing_ok=True)

    def test_write_creates_parent_dirs(self, config_bundle, temp_project):
        """自动创建父目录。"""
        data_dir = temp_project / "data"
        nested = data_dir / "sub1" / "sub2" / "test.md"
        result = safe_write_text(nested, "# Nested", config_bundle)
        assert result.exists()
        assert result.parent.exists()

    def test_disabled_guard_allows_explicit_outside_path(self, config_bundle, tmp_path):
        config_bundle.app.safety.enforce_write_guard = False
        target = tmp_path.parent / f"{tmp_path.name}-outside" / "result.md"
        try:
            result = safe_write_text(target, "allowed", config_bundle)
            assert result.read_text(encoding="utf-8") == "allowed"
        finally:
            target.unlink(missing_ok=True)
            target.parent.rmdir()

    def test_safe_write_bytes_is_atomic_writer(self, config_bundle, temp_project):
        target = temp_project / "data" / "artifact.bin"
        result = safe_write_bytes(target, b"\x00\x01", config_bundle)
        assert result.read_bytes() == b"\x00\x01"
