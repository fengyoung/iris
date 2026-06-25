"""写入路径守卫：校验目标路径是否在允许的写入范围内。

读取 config/app.json 的 safety.allowed_write_paths 配置，
在写入前校验目标路径。

用法：
    validate_write_path(target_path, config_bundle)
    若路径不在允许范围内，抛出 WriteGuardError。
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from iris.config.loader import ConfigBundle


class WriteGuardError(PermissionError):
    """写入路径不在允许范围内。"""


def resolve_allowed_paths(bundle: ConfigBundle) -> List[Path]:
    """从 app config 解析允许的写入路径列表（全部 resolve 为绝对路径）。

    优先级：
    1. safety.allowed_write_paths（最精确）
    2. 默认允许：output_dir, temp_dir, memory_dir, data_dir
    """
    safety = bundle.app.get("safety", {})
    raw_paths = safety.get("allowed_write_paths", [])
    if raw_paths:
        resolved: List[Path] = []
        for raw in raw_paths:
            p = Path(str(raw))
            if not p.is_absolute():
                p = bundle.root / p
            resolved.append(p.resolve())
        return resolved

    # 兜底：从 paths 段推导
    paths_cfg = bundle.app.get("paths", {})
    defaults = [
        bundle.root / paths_cfg.get("output_dir", "./output"),
        bundle.root / paths_cfg.get("temp_dir", "./temp"),
        bundle.root / paths_cfg.get("memory_dir", "./memory"),
        bundle.root / "data",
    ]
    return [p.resolve() for p in defaults]


def validate_write_path(target_path: Path | str, bundle: ConfigBundle) -> Path:
    """校验目标路径是否在允许的写入范围内。

    Args:
        target_path: 要写入的目标路径
        bundle: 配置对象

    Returns:
        规范化后的目标路径（resolve）

    Raises:
        WriteGuardError: 路径不在允许范围内
    """
    target = Path(str(target_path)).resolve()

    allowed = resolve_allowed_paths(bundle)
    for base in allowed:
        try:
            target.relative_to(base)
            return target  # 在允许范围内
        except ValueError:
            continue

    # 额外检查：是否在 project root 下的 data/ 或 temp/ 或 output/
    for subdir in ("data", "temp", "output", "memory", "logs"):
        guard = (bundle.root / subdir).resolve()
        try:
            target.relative_to(guard)
            return target
        except ValueError:
            continue

    raise WriteGuardError(
        f"拒绝写入：目标路径不在允许范围内\n"
        f"  目标：{target}\n"
        f"  允许：{allowed}"
    )


def safe_write_text(
    path: Path | str,
    content: str,
    bundle: ConfigBundle,
    *,
    encoding: str = "utf-8",
    allow_existing_outside: bool = False,
) -> Path:
    """安全写入文本文件，自动校验路径合法性。

    Args:
        path: 目标路径
        content: 文本内容
        bundle: 配置对象
        encoding: 文件编码
        allow_existing_outside: 是否允许写入已存在的、不在允许范围内的文件

    Returns:
        写入后的路径
    """
    target = Path(str(path))
    if not (allow_existing_outside and target.exists()):
        validate_write_path(target, bundle)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding=encoding)
    return target
