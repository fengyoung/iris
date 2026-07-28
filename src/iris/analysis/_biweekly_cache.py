"""双周报流水线缓存管理。

将 AnalysisReportService 中与缓存存储相关的职责独立出来，
使 Stage 方法可以直接通过 BiweeklyCache 读写磁盘而不感知路径细节。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


class BiweeklyCache:
    """双周报 Stage 缓存管理器。

    负责所有磁盘缓存的读写：
    - op_directions.json     (Stage 0a OP 方向解析结果)
    - stage1_filter.json     (Stage 1 文件过滤结果)
    - style_guide.json       (Stage 0b 风格指南)
    - file_briefs/           (Stage 2 文件摘要，按内容 hash 存储)
    """

    def __init__(self, cache_root: Path) -> None:
        self._root = cache_root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def cache_dir(self) -> Path:
        return self._root

    # ── Hash 工具 ──────────────────────────────────────────────

    @staticmethod
    def content_hash(text: str, prefix_len: int = 2000) -> str:
        return hashlib.md5(text[:prefix_len].encode("utf-8")).hexdigest()

    # ── Op Directions 缓存 ─────────────────────────────────────

    def load_op_directions(self, content_hash: str) -> list | None:
        """命中返回 directions 列表，未命中或失效返回 None。"""
        path = self._root / "op_directions.json"
        if not path.exists():
            return None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("content_hash") == content_hash:
                directions = cached.get("directions", [])
                if directions:
                    logger.info("  OP 方向定义命中缓存 (%d 个方向)", len(directions))
                    return directions
        except (json.JSONDecodeError, KeyError):
            logger.warning("  OP 方向缓存数据损坏，废弃重跑")
        return None

    def save_op_directions(self, content_hash: str, directions: list) -> None:
        path = self._root / "op_directions.json"
        path.write_text(json.dumps({
            "content_hash": content_hash,
            "directions": directions,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("  OP 解析完成: %d 个方向 → 已缓存", len(directions))

    # ── Stage 1 过滤缓存 ───────────────────────────────────────

    def load_stage1_filter(self, inv_hash: str, dir_hash: str, expected_count: int = 0) -> dict | None:
        """命中返回 dir_file_map，未命中或方向数不完整返回 None。"""
        path = self._root / "stage1_filter.json"
        if not path.exists():
            return None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("inv_hash") == inv_hash and cached.get("dir_hash") == dir_hash:
                dir_count = len(cached.get("dir_file_map", {}))
                if expected_count <= 0 or dir_count == expected_count:
                    logger.info("  Stage 1 命中缓存 (%d 个方向)", dir_count)
                    return cached["dir_file_map"]
                logger.warning("  Stage 1 缓存不完整（期望 %d 个方向，实际 %d 个），废弃重跑",
                               expected_count, dir_count)
        except (json.JSONDecodeError, KeyError):
            logger.warning("  Stage 1 缓存数据损坏，废弃重跑")
        return None

    def save_stage1_filter(self, inv_hash: str, dir_hash: str, dir_file_map: dict) -> None:
        path = self._root / "stage1_filter.json"
        path.write_text(json.dumps({
            "inv_hash": inv_hash,
            "dir_hash": dir_hash,
            "dir_file_map": dir_file_map,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("  Stage 1 完成，结果已缓存")

    # ── 风格指南缓存 ───────────────────────────────────────────

    def load_style_guide(self) -> dict | None:
        """读取已缓存的风格指南，失败返回 None。"""
        path = self._root / "style_guide.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save_style_guide(self, guide: dict) -> None:
        path = self._root / "style_guide.json"
        path.write_text(json.dumps(guide, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── File Briefs 缓存 ───────────────────────────────────────

    @property
    def briefs_dir(self) -> Path:
        d = self._root / "file_briefs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def load_brief_index(self) -> Dict[str, str]:
        """加载 brief 索引 {label: hash}。"""
        index_path = self.briefs_dir / "index.json"
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def load_brief(self, label: str, content_hash: str, brief_index: dict) -> dict | None:
        """按 label + hash 读取 brief，命中返回 dict，否则返回 None。"""
        cached_hash = brief_index.get(label)
        if not cached_hash or cached_hash != content_hash:
            return None
        path = self.briefs_dir / f"{content_hash}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save_brief(self, label: str, content_hash: str, brief: dict,
                   brief_index: dict) -> None:
        """写入 brief 文件并更新索引字典（不自动 flush，由调用方统一 flush）。"""
        (self.briefs_dir / f"{content_hash}.json").write_text(
            json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        brief_index[label] = content_hash

    def flush_brief_index(self, brief_index: dict) -> None:
        """将 brief_index 写回磁盘，并清理 30 天未使用的旧 brief 文件（FileLock 保护）。"""
        from iris.core.locks import FileLock
        index_path = self.briefs_dir / "index.json"
        with FileLock(index_path):
            index_path.write_text(json.dumps(brief_index, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
            cutoff = (datetime.now() - timedelta(days=30)).timestamp()
            valid_hashes = set(brief_index.values())
            for f in self.briefs_dir.glob("*.json"):
                if f.name == "index.json":
                    continue
                if f.name.replace(".json", "") not in valid_hashes:
                    try:
                        if f.stat().st_mtime < cutoff:
                            f.unlink()
                    except OSError:
                        logger.warning("  BiweeklyCache: 清理过期文件失败 %s", f.name)
