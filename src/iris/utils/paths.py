"""路径工具函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def resolve_source_root(bundle) -> Optional[Path]:
    """解析数据源根目录（第一个启用的数据源的 path）。"""
    sources = bundle.data_source.get("sources", {})
    for cfg in sources.values():
        if cfg.get("enabled") and cfg.get("path"):
            p = Path(cfg["path"]).resolve()
            if p.exists():
                return p
    return None
