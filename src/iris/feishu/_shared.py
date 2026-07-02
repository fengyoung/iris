"""feishu 模块共享工具 — 路径解析、排重索引、标题清理、时间解析。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle


def resolve_source_root(bundle: ConfigBundle) -> Optional[Path]:
    """获取 SOURCE 根目录。"""
    ds = bundle.data_source
    for cfg in ds.get("sources", {}).values():
        if cfg.get("enabled") and cfg.get("path"):
            p = Path(cfg["path"]).resolve()
            if p.exists():
                return p
    return None


def resolve_source_sub_dir(bundle: ConfigBundle, sub_dir: str) -> Path:
    """获取 SOURCE 子目录，不存在则创建。"""
    src = resolve_source_root(bundle)
    if src:
        d = src / sub_dir
        d.mkdir(parents=True, exist_ok=True)
        return d
    return bundle.root / "output"


def resolve_pic_dir(bundle: ConfigBundle) -> Path:
    """确定图片存储目录。

    优先级：feishu_ingest.pic_dir > SOURCE/../Pic > data/pic。
    """
    feishu = bundle.feishu_ingest or {}
    pic = feishu.get("pic_dir", "")
    if pic:
        p = Path(pic).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    src = resolve_source_root(bundle)
    if src:
        p = src.parent / "Pic"
        p.mkdir(parents=True, exist_ok=True)
        return p

    p = bundle.root / "data" / "pic"
    p.mkdir(parents=True, exist_ok=True)
    return p


def resolve_dedup_path(bundle: ConfigBundle, config_key: str, fallback: str) -> Path:
    """确定排重索引路径。

    Args:
        bundle: 配置包
        config_key: feishu_ingest 中的子路径，如 "doc_convert.dedup_index"
        fallback: 默认文件名
    """
    feishu = bundle.feishu_ingest or {}
    cfg = feishu
    for part in config_key.split("."):
        if isinstance(cfg, dict):
            cfg = cfg.get(part, {})
        else:
            cfg = {}
    path_str = cfg if isinstance(cfg, str) else ""
    if path_str:
        p = Path(path_str)
        if not p.is_absolute():
            p = bundle.root / p
    else:
        p = bundle.root / fallback
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_dedup_index(path: Path) -> Dict[str, Any]:
    """加载排重索引。"""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": "1.0", "items": []}


def save_dedup_index(path: Path, index: Dict[str, Any]) -> None:
    """保存排重索引。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_dedup_item(index: Dict[str, Any], dedup_key: str, item: Dict[str, Any]) -> None:
    """在排重索引中 upsert 一条记录（按 dedup_key 去重；若 source_url 非空也排重）。"""
    new_source_url = item.get("source_url", "")
    kept = []
    for it in index.get("items", []):
        if it.get("dedup_key") == dedup_key:
            continue  # 按 dedup_key 去重
        if new_source_url and it.get("source_url") == new_source_url:
            continue  # 按 source_url 去重（仅当两者都非空时）
        kept.append(it)
    kept.append(item)
    index["items"] = kept


def sanitize_title(title: str, max_len: int = 60) -> str:
    """清理标题为安全的文件名。"""
    clean = re.sub(r'[\\/:*?"<>|]', "", title)
    clean = re.sub(r"\s+", "-", clean.strip())
    clean = re.sub(r"-{2,}", "-", clean)
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip("-")
    return clean if clean else "未命名"


def extract_date(time_str: str) -> str:
    """从 ISO 时间字符串或 Unix 时间戳中提取 YYYYmmdd 格式日期。"""
    if not time_str:
        return ""
    try:
        # Try ISO format first
        dt = datetime.fromisoformat(time_str)
        return dt.strftime("%Y%m%d")
    except (ValueError, TypeError):
        pass
    try:
        # Try Unix timestamp (seconds since epoch)
        dt = datetime.fromtimestamp(int(time_str))
        return dt.strftime("%Y%m%d")
    except (ValueError, TypeError, OSError):
        return ""


def now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串（统一使用 UTC，避免本地/UTC 混用）。"""
    return datetime.now(timezone.utc).isoformat()
