#!/usr/bin/env python3
"""轻量 AST 安全门禁：阻断高风险动态执行和 shell 注入回归。"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def scan_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [f"{path}: 无法解析: {exc}"]
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name in {"eval", "exec"}:
                findings.append(f"{path}:{node.lineno}: 禁止动态执行 {name}()")
            if name in {"loads", "load", "dump", "dumps"} and isinstance(func, ast.Attribute):
                module = func.value.id if isinstance(func.value, ast.Name) else ""
                if module == "pickle":
                    findings.append(f"{path}:{node.lineno}: 禁止 pickle 序列化")
            if name in {"run", "Popen", "call", "check_call", "check_output"}:
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        findings.append(f"{path}:{node.lineno}: subprocess 禁止 shell=True")
    return findings


def main() -> int:
    roots = [Path("src"), Path("scripts")]
    findings = [finding for root in roots for path in root.rglob("*.py") for finding in scan_file(path)]
    if findings:
        print("安全静态扫描失败：", file=sys.stderr)
        print("\n".join(f"- {item}" for item in findings), file=sys.stderr)
        return 1
    print("安全静态扫描通过：未发现 eval/exec、pickle 或 shell=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
