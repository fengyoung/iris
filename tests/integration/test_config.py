"""配置加载模块测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iris.config.loader import (
    ConfigBundle,
    ConfigError,
    load_config_bundle,
    load_env_file,
    resolve_env_vars,
    resolve_path_vars,
)
from iris.config.models import ConfigBundleV2


class TestLoadEnvFile:
    def test_load_basic(self, temp_project):
        env_file = temp_project / ".env"
        env_file.write_text("KEY=value\nFOO=bar\n", encoding="utf-8")
        result = load_env_file(env_file)
        assert result == {"KEY": "value", "FOO": "bar"}

    def test_skip_comments_and_blanks(self, temp_project):
        env_file = temp_project / ".env"
        env_file.write_text("# comment\n\nKEY=value\n", encoding="utf-8")
        result = load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_strip_quotes(self, temp_project):
        env_file = temp_project / ".env"
        env_file.write_text('KEY="quoted"\nSINGLE=\'single\'\n', encoding="utf-8")
        result = load_env_file(env_file)
        assert result == {"KEY": "quoted", "SINGLE": "single"}

    def test_missing_file(self, temp_project):
        result = load_env_file(temp_project / ".env.nonexistent")
        assert result == {}

    def test_skip_no_equal(self, temp_project):
        env_file = temp_project / ".env"
        env_file.write_text("JUSTTEXT\nKEY=value\n", encoding="utf-8")
        result = load_env_file(env_file)
        assert result == {"KEY": "value"}


class TestResolveEnvVars:
    def test_replace_string(self):
        env = {"VAR": "world"}
        result = resolve_env_vars("hello ${VAR}", env)
        assert result == "hello world"

    def test_preserve_unknown_var(self):
        result = resolve_env_vars("hello ${UNKNOWN}", {})
        assert result == "hello ${UNKNOWN}"

    def test_recursive_dict(self):
        env = {"A": "1"}
        data = {"key": "value/${A}", "nested": {"inner": "pre${A}post"}}
        result = resolve_env_vars(data, env)
        assert result["key"] == "value/1"
        assert result["nested"]["inner"] == "pre1post"

    def test_recursive_list(self):
        env = {"X": "y"}
        result = resolve_env_vars(["${X}", "static"], env)
        assert result == ["y", "static"]

    def test_os_env_priority(self):
        """OS 环境变量优先于 .env 变量。"""
        import os
        os.environ["TEST_OVERRIDE"] = "from_os"
        result = resolve_env_vars("${TEST_OVERRIDE}", {"TEST_OVERRIDE": "from_env"})
        assert result == "from_os"
        del os.environ["TEST_OVERRIDE"]


class TestConfigBundle:
    def test_load_minimal(self, config_bundle):
        assert isinstance(config_bundle, ConfigBundleV2)
        assert config_bundle.root.exists()
        assert config_bundle.app["app"]["name"] == "Iris"

    def test_missing_required_config(self, temp_project):
        with pytest.raises(ConfigError):
            load_config_bundle(temp_project)

    def test_app_validation_missing_field(self, temp_project):
        """v3.19: from_dicts() 为空配置填充默认值，缺失字段不抛异常。"""
        config_dir = temp_project / "config"
        for name in ("app", "llm", "data_source"):
            (config_dir / f"{name}.json").write_text('{}', encoding="utf-8")
        # 默认值填充后加载成功
        bundle = load_config_bundle(temp_project)
        assert bundle is not None

    def test_env_var_resolution(self, config_bundle):
        """确认 ${VAR} 在配置加载时被解析。"""
        llm = config_bundle.llm
        base_key = llm["models"]["base_model"]["models"]["test-model"]["api_key"]
        assert base_key == "sk-test-key"

    def test_path_resolution(self, config_bundle):
        """确认 ${IRIS_XXX_DIR} 被解析为项目路径。"""
        output = config_bundle.app["paths"]["output_dir"]
        assert str(config_bundle.root) in output

    def test_wiki_optional(self, config_bundle):
        """wiki.json 是可选的。"""
        assert config_bundle.wiki is None


class TestResolvePathVars:
    def test_project_root(self, temp_project):
        data = {"path": "${IRIS_PROJECT_ROOT}/sub"}
        result = resolve_path_vars(data, temp_project)
        assert result["path"] == f"{temp_project}/sub"

    def test_data_dir(self, temp_project):
        data = {"path": "${IRIS_DATA_DIR}"}
        result = resolve_path_vars(data, temp_project)
        assert result["path"] == f"{temp_project}/data"


class TestWarnUnresolvedPlaceholders:
    """回归测试：_warn_unresolved_placeholders 必须在 resolve_path_vars 之后执行，
    确保内置路径占位符（${IRIS_DATA_DIR} 等）不被误报为未解析变量。

    v3.19.4 修复：调用顺序调整（resolve_path_vars → _warn_unresolved_placeholders），
    此测试防止回退。
    """

    def test_iris_data_dir_not_reported_as_unresolved(self, temp_project, caplog):
        """配置中包含 ${IRIS_DATA_DIR} 时，加载后不应产生 '未解析占位符' 警告。"""
        import logging
        config_dir = temp_project / "config"
        # app.json 中使用 ${IRIS_DATA_DIR}（由 resolve_path_vars 负责解析）
        app_cfg = {
            "version": "3.0",
            "app": {"name": "Iris", "env": "test"},
            "paths": {
                "output_dir": "${IRIS_DATA_DIR}/output",
                "project_root": "${IRIS_PROJECT_ROOT}",
            },
        }
        (config_dir / "app.json").write_text(
            __import__("json").dumps(app_cfg), encoding="utf-8"
        )
        for name in ("llm", "data_source"):
            (config_dir / f"{name}.json").write_text("{}", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="iris.config.loader"):
            load_config_bundle(temp_project)

        unresolved_warnings = [
            r for r in caplog.records
            if "未解析占位符" in r.message and "IRIS_DATA_DIR" in r.message
        ]
        assert not unresolved_warnings, (
            f"${'{IRIS_DATA_DIR}'} 被误报为未解析占位符，"
            "说明 _warn_unresolved_placeholders 在 resolve_path_vars 之前执行了"
        )

    def test_truly_missing_var_still_warns(self, temp_project, caplog):
        """真正缺失的环境变量（${MY_MISSING_SECRET}）应触发 warning。"""
        import logging
        import os
        # 确保变量确实不存在
        os.environ.pop("MY_MISSING_SECRET", None)

        config_dir = temp_project / "config"
        app_cfg = {
            "version": "3.0",
            "app": {"name": "Iris", "env": "test", "api_key": "${MY_MISSING_SECRET}"},
        }
        (config_dir / "app.json").write_text(
            __import__("json").dumps(app_cfg), encoding="utf-8"
        )
        for name in ("llm", "data_source"):
            (config_dir / f"{name}.json").write_text("{}", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="iris.config.loader"):
            load_config_bundle(temp_project)

        unresolved_warnings = [
            r for r in caplog.records
            if "未解析占位符" in r.message and "MY_MISSING_SECRET" in r.message
        ]
        assert unresolved_warnings, "真正缺失的变量应触发 '未解析占位符' warning，但未触发"
