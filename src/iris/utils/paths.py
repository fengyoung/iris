"""路径工具函数。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

# ── 项目根目录解析 ──────────────────────────────────────────────
# 通过 __file__ 向上遍历目录树定位项目根目录。
# 适配 editable install (pip install -e .) 和直接从源码运行时的情况。
# 从 src/iris/utils/paths.py 向上 4 级到达项目根。


def _find_project_root() -> Path:
    """优先使用环境变量，再向上查找 pyproject.toml 定位项目根目录。"""
    configured = os.environ.get("IRIS_PROJECT_ROOT", "").strip()
    if configured:
        root = Path(configured).expanduser().resolve()
        if not (root / "pyproject.toml").is_file():
            raise RuntimeError(f"IRIS_PROJECT_ROOT 不是有效 Iris 项目目录: {root}")
        return root
    candidate = Path(__file__).resolve().parent
    for _ in range(6):  # 最多向上查找 6 级
        if (candidate / "pyproject.toml").exists():
            return candidate
        candidate = candidate.parent
    # fallback: 从 __file__ 向上 4 级（src/iris/utils/paths.py → root）
    return Path(__file__).resolve().parent.parent.parent.parent


_PROJECT_ROOT: Optional[Path] = None


def get_project_root() -> Path:
    """返回项目根目录（缓存结果）。"""
    global _PROJECT_ROOT
    if _PROJECT_ROOT is None:
        _PROJECT_ROOT = _find_project_root()
    return _PROJECT_ROOT


def resolve_data_path(relative_path: str) -> Path:
    """将相对于项目根目录的 data/ 路径解析为绝对路径。

    用法::

        >>> resolve_data_path("data/asr_feedback.jsonl")
        Path("/path/to/iris3/data/asr_feedback.jsonl")

    安全约束：relative_path 必须以 "data/" 或 "config/" 开头。
    """
    allowed_prefixes = ("data/", "config/", "output/")
    if not any(relative_path.startswith(p) for p in allowed_prefixes):
        raise ValueError(
            f"resolve_data_path: 仅允许 data/config/output 子路径，"
            f"收到: {relative_path!r}"
        )
    return get_project_root() / relative_path


def get_agent_data_dir(data_root: Path) -> Path:
    """根据 IRIS_AGENT_ID 环境变量计算 agent 专属数据目录。

    未设置时返回 data/agents/default/，用于向后兼容。
    多 agent 并发时不互相覆盖 session / working context。
    """
    agent_id = os.environ.get("IRIS_AGENT_ID", "default")
    # 安全过滤：仅允许字母数字、连字符和下划线
    safe_id = "".join(c for c in agent_id if c.isalnum() or c in "-_") or "default"
    return data_root / "agents" / safe_id


def resolve_source_root(bundle) -> Optional[Path]:
    """解析数据源根目录（第一个启用的数据源的 path）。"""
    sources = bundle.data_source.get("sources", {})
    for cfg in sources.values():
        if cfg.get("enabled") and cfg.get("path"):
            p = Path(cfg["path"]).resolve()
            if p.exists():
                return p
    return None


# ── SOURCE 目录归档配置 ──────────────────────────────────────────────

_ARCHIVE_CONFIG: Optional[Dict[str, str]] = None
_ARCHIVE_CONFIG_PATH = "config/source_archive.json"


def _load_archive_config(project_root: Optional[Path] = None) -> Dict[str, str]:
    """加载归档配置，返回 { category: mode } 映射。"""
    global _ARCHIVE_CONFIG
    if _ARCHIVE_CONFIG is not None:
        return _ARCHIVE_CONFIG

    root = project_root or get_project_root()
    path = root / _ARCHIVE_CONFIG_PATH
    if not path.exists():
        # v3.28.1：与 config/loader.py 惯例一致，缺失时回退 .example——
        # 否则干净 checkout（config/*.json gitignored）归档模式全部退化 flat，
        # 依赖归档路径的行为（含测试）与生产环境不一致。
        path = root / (_ARCHIVE_CONFIG_PATH + ".example")
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        # fallback: all flat
        data = {"categories": {}}
    _ARCHIVE_CONFIG = {
        cat: info["mode"]
        for cat, info in data.get("categories", {}).items()
    }
    return _ARCHIVE_CONFIG


def get_archive_mode(category: str, project_root: Optional[Path] = None) -> str:
    """获取指定类别的归档模式：'yearly' | 'monthly' | 'flat'。"""
    config = _load_archive_config(project_root)
    return config.get(category, "flat")


def resolve_source_archive_path(
    source_root: Path, category: str, filename: str,
    project_root: Optional[Path] = None,
) -> Path:
    """按归档规则计算文件最终路径，自动创建子目录。

    归档模式（由 config/source_archive.json 定义）：
      - yearly:   {category}/{YYYY}/{filename}
      - monthly:  {category}/{YYYYMM}/{filename}
      - flat:     {category}/{filename}    （不归档）

    文件名需以 ``YYYYMMDD-`` 开头才能提取时间前缀。
    """
    mode = get_archive_mode(category, project_root)
    m = re.match(r"(\d{4})(\d{2})\d{2}-", filename)
    if m and mode != "flat":
        if mode == "yearly":
            sub = m.group(1)  # YYYY
        elif mode == "monthly":
            sub = m.group(1) + m.group(2)  # YYYYMM
        else:
            sub = ""
        target = source_root / category / sub
    else:
        target = source_root / category
    target.mkdir(parents=True, exist_ok=True)
    return target / filename
