"""Wiki 反向引用索引 — 扫描 [[wikilink]] 构建「谁引用了我」的映射。

用途:
  - wiki-lint: 孤页检测、断链分析
  - graph: 零 LLM 成本的基线边
  - generator: write_page 时追加「被以下页面引用」段落

用法:
    builder = BacklinkBuilder(wiki_root)
    index = builder.build()
    # index.inbound["人物-张三"] → ["项目-项目Alpha", "概念-A/B实验"]
    # index.orphans → 零入链页面列表
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from iris.utils.shared import atomic_write_json, now_iso

from .context_loader import WikiContextLoader

if TYPE_CHECKING:
    from .context_loader import WikiPageInfo

logger = logging.getLogger(__name__)

# 匹配 [[target]] 或 [[target|alias]] 或 [[target#anchor]]
_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# 噪音链接（标点、下划线等）
_NOISE_TARGET_RE = re.compile(r"^[.\-#]{1,3}$|^_{2,}$|^\.{2,}$")

# 源文档引用模式（如 "会议纪要/20260518-..."）
_SOURCE_REF_RE = re.compile(r"^(?:.*/)?\d{8}-")


@dataclass(frozen=True)
class BacklinkIndex:
    """反向引用索引。

    inbound:  {被引用页面标题: [引用它的页面标题列表]}
    outbound: {页面标题: [它引用的页面标题列表]}
    orphans:  零入链页面列表
    unique_inbound_edges: 去重后的入链边总数（= sum(len(v) for v in inbound.values())）
    """
    inbound: Dict[str, List[str]] = field(default_factory=dict)
    outbound: Dict[str, List[str]] = field(default_factory=dict)
    orphans: List[str] = field(default_factory=list)
    total_pages: int = 0
    unique_inbound_edges: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "inbound": self.inbound,
            "outbound": self.outbound,
            "orphans": self.orphans,
            "total_pages": self.total_pages,
            "unique_inbound_edges": self.unique_inbound_edges,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacklinkIndex":
        return cls(
            inbound=data.get("inbound", {}),
            outbound=data.get("outbound", {}),
            orphans=data.get("orphans", []),
            total_pages=data.get("total_pages", 0),
            # 兼容旧格式：total_links 是旧字段名
            unique_inbound_edges=data.get("unique_inbound_edges",
                                          data.get("total_links", 0)),
        )


class BacklinkBuilder:
    """反向引用索引构建器。

    扫描 Wiki 目录下所有 .md 页面，提取 [[wikilink]] 并构建
    双向引用关系。跳过 index.md / changelog.md / .bak 文件。

    内置缓存：build() 首次调用后缓存结果，invalidate_cache()
    或 Wiki 目录变更后需手动失效。
    """

    def __init__(self, wiki_root: Path):
        self._root = Path(wiki_root).resolve()
        self._cache: Optional[BacklinkIndex] = None

    # ── 公开 API ──────────────────────────────────────────────

    def build(self, *, force: bool = False) -> BacklinkIndex:
        """全量扫描并构建反向引用索引。

        结果被缓存。Wiki 目录变更后，调用 invalidate_cache()
        或传入 force=True 以重建。
        """
        if self._cache is not None and not force:
            return self._cache

        if not self._root.exists():
            self._cache = BacklinkIndex()
            return self._cache

        pages = self._load_pages_with_links()
        self._cache = self._build_index(pages)
        return self._cache

    def build_from_wiki_pages(self, pages: "List[WikiPageInfo]") -> BacklinkIndex:
        """从已加载的 WikiPageInfo 列表构建反向引用索引（零文件扫描）。

        供 WikiGraph.refresh() 使用，避免重复扫描 Wiki 目录。
        """
        links: Dict[str, List[str]] = {}
        for page_info in pages:
            title = page_info.title
            if not title:
                continue
            raw_links = _LINK_RE.findall(page_info.body)
            cleaned: List[str] = []
            for raw in raw_links:
                target = raw.split("|")[0].split("#")[0].strip()
                if not target:
                    continue
                if _NOISE_TARGET_RE.match(target):
                    continue
                if _SOURCE_REF_RE.match(target):
                    continue
                cleaned.append(target)
            links[title] = cleaned
        index = self._build_index(links)
        self._cache = index
        return index

    def invalidate_cache(self) -> None:
        """清除缓存，下次 build() 将重新扫描。"""
        self._cache = None

    def get_inbound(self, title: str) -> List[str]:
        """获取指定页面的入链页面列表（使用缓存）。"""
        return self.build().inbound.get(title, [])

    def get_outbound(self, title: str) -> List[str]:
        """获取指定页面的出链页面列表（使用缓存）。"""
        return self.build().outbound.get(title, [])

    def find_orphans(self) -> List[str]:
        """查找零入链（孤立）页面（使用缓存）。"""
        return self.build().orphans

    def save(self, path: Path) -> None:
        """将索引持久化为 JSON 文件（原子写入）。"""
        index = self.build()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = index.to_dict()
        data["updated_at"] = now_iso()
        atomic_write_json(path, data)

    def load(self, path: Path) -> Optional[BacklinkIndex]:
        """从 JSON 文件加载索引，不存在则返回 None。"""
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return BacklinkIndex.from_dict(data)
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("加载反向引用索引失败: %s", exc)
            return None

    # ── 内部方法 ──────────────────────────────────────────────

    def _load_pages_with_links(self) -> Dict[str, List[str]]:
        """加载所有页面标题及其出链列表。

        Returns:
            {page_title: [linked_title, ...]}
        """
        links: Dict[str, List[str]] = {}
        loader = WikiContextLoader(self._root)
        for page_info in loader.load_pages():
            title = page_info.title
            if not title:
                continue
            try:
                content = page_info.path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # 提取 [[wikilink]]
            raw_links = _LINK_RE.findall(content)
            cleaned: List[str] = []
            for raw in raw_links:
                # 取 | 或 # 之前的纯标题
                target = raw.split("|")[0].split("#")[0].strip()
                if not target:
                    continue
                if _NOISE_TARGET_RE.match(target):
                    continue
                if _SOURCE_REF_RE.match(target):
                    continue
                cleaned.append(target)
            links[title] = cleaned
        return links

    def _build_index(self, pages: Dict[str, List[str]]) -> BacklinkIndex:
        """从 {page: [links]} 构建完整 BacklinkIndex。"""
        inbound: Dict[str, List[str]] = {}
        raw_links = 0

        for source_title, linked_titles in pages.items():
            for target_title in linked_titles:
                raw_links += 1
                inbound.setdefault(target_title, []).append(source_title)

        # 去重（同一来源多次引用同一目标只计一次）
        for target in inbound:
            inbound[target] = sorted(set(inbound[target]))

        # 去重后统计唯一边数
        unique_links = sum(len(v) for v in inbound.values())

        # 找出孤立页面（零入链）
        all_titles = set(pages.keys())
        linked_titles = set(inbound.keys())
        orphans = sorted(all_titles - linked_titles)

        return BacklinkIndex(
            inbound=inbound,
            outbound=pages,
            orphans=orphans,
            total_pages=len(all_titles),
            unique_inbound_edges=unique_links,
        )
