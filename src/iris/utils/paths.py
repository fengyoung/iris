"""路径工具函数。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


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
