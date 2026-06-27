"""脚本委托加载工具：统一管理 scripts/ 下独立脚本的动态加载。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Optional


def load_script_module(script_name: str, project_root: Path) -> ModuleType:
    """从 project_root/scripts/ 加载一个 Python 脚本作为模块。

    Args:
        script_name: 脚本文件名，如 "trello.py"、"sync_memory.py"
        project_root: Iris 项目根目录

    Returns:
        加载的模块对象

    Raises:
        ImportError: 脚本不存在或无法加载
    """
    script_path = project_root / "scripts" / script_name
    if not script_path.exists():
        raise ImportError(f"脚本不存在: {script_path}")

    import importlib.util

    module_name = script_name.replace(".py", "")

    # 确保 project root 在 sys.path 中（用完恢复）
    path_added = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        path_added = True
    try:
        spec = importlib.util.spec_from_file_location(module_name, script_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载脚本: {script_path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        if path_added:
            sys.path.pop(0)


def run_delegated_script(
    script_name: str,
    project_root: Path,
    args: Optional[list[str]] = None,
) -> int:
    """运行委托脚本，调整 sys.argv 后调用其 main()。

    用于 trello、build-biweekly-report 等拥有独立 arg parser 的脚本。

    Args:
        script_name: 脚本文件名
        project_root: 项目根目录
        args: 脚本参数列表（不含脚本名），默认使用 sys.argv[2:]

    Returns:
        脚本 main() 的返回值
    """
    script_path = project_root / "scripts" / script_name
    old_argv = sys.argv[:]
    try:
        sys.argv = [str(script_path)] + (args if args is not None else sys.argv[2:])
        mod = load_script_module(script_name, project_root)
        return int(getattr(mod, "main", lambda: 0)() or 0)
    finally:
        sys.argv = old_argv
