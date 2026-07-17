"""script_loader 单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from iris.core.script_loader import load_script_module, run_delegated_script


class TestLoadScriptModule:
    """load_script_module 测试。"""

    def test_loads_valid_script(self, tmp_path: Path):
        """加载有效的 Python 脚本。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "hello.py"
        script.write_text("GREETING = 'Hello, World!'\n", encoding="utf-8")

        mod = load_script_module("hello.py", tmp_path)
        assert isinstance(mod, ModuleType)
        assert mod.GREETING == "Hello, World!"

    def test_raises_import_error_for_missing_script(self, tmp_path: Path):
        """不存在的脚本抛出 ImportError。"""
        with pytest.raises(ImportError, match="脚本不存在"):
            load_script_module("nonexistent.py", tmp_path)

    def test_sys_path_restored_after_load(self, tmp_path: Path):
        """加载脚本后 sys.path 恢复原状。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "simple.py"
        script.write_text("VALUE = 99\n", encoding="utf-8")

        old_path = sys.path[:]
        load_script_module("simple.py", tmp_path)
        assert sys.path == old_path

    def test_raises_import_error_for_invalid_spec(self, tmp_path: Path):
        """无法解析 spec 时抛出 ImportError。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        # 创建一个空文件 — spec 仍然可以解析
        # 用非 Python 文件测试
        script = scripts_dir / "not_a_module.txt"
        script.write_text("this is not python\n", encoding="utf-8")

        with pytest.raises(ImportError):
            load_script_module("not_a_module.txt", tmp_path)


class TestRunDelegatedScript:
    """run_delegated_script 测试。"""

    def test_runs_main_and_returns_exit_code(self, tmp_path: Path):
        """调用脚本的 main() 并返回退出码。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "delegated.py"
        script.write_text(
            "def main():\n    return 3\n",
            encoding="utf-8",
        )

        code = run_delegated_script("delegated.py", tmp_path, args=[])
        assert code == 3

    def test_sys_argv_saved_and_restored(self, tmp_path: Path):
        """sys.argv 在调用前后完整恢复。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "argv_test.py"
        script.write_text(
            "def main():\n    return 0\n",
            encoding="utf-8",
        )

        old_argv = sys.argv[:]
        run_delegated_script("argv_test.py", tmp_path, args=["--test", "value"])
        assert sys.argv == old_argv

    def test_defaults_to_zero_when_no_main(self, tmp_path: Path):
        """脚本无 main() 时默认返回 0。"""
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script = scripts_dir / "no_main.py"
        script.write_text("VALUE = 42\n", encoding="utf-8")

        code = run_delegated_script("no_main.py", tmp_path, args=[])
        assert code == 0
