"""模板文件加载工具 — 统一从 templates/ 目录加载 Prompt 模板。

用法:
    from iris.utils.template_loader import load_template

    content = load_template("wiki/generate_generic.txt")   # → templates/wiki/...
    content = load_template("prompt/accuracy_check.md")    # → templates/prompt/...

路径相对于项目根目录下的 templates/ 目录。文件不存在时返回 None。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

# templates/ 目录：src/iris/utils/../../../.. / templates
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "templates"


def load_template(relative_path: str) -> Optional[str]:
    """加载 templates/ 下的模板文件，不存在时返回 None。

    Args:
        relative_path: 相对于 templates/ 的路径，如 "wiki/generate_generic.txt"
                       或 "prompt/stage1_instruction.md"
    """
    tmpl_path = _TEMPLATES_DIR / relative_path
    if tmpl_path.exists():
        return tmpl_path.read_text(encoding="utf-8")
    return None
