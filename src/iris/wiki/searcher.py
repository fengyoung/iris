"""LLM-WIKI 页面扫描与本地检索 — 适配 4 种页面类型和层级目录。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from iris.utils.tokenization import TOKEN_RE, tokenize  # noqa: F811
from ._constants import (
    get_wiki_dir, get_wiki_prefix,
    get_dir_map, get_prefix_to_type_map,
)

logger = logging.getLogger(__name__)

# 向下兼容别名（推荐直接使用 get_* 访问器）
PAGE_TYPE_DIRS = get_dir_map()
PREFIX_TO_TYPE = get_prefix_to_type_map()

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """解析 Wiki 页面的 YAML frontmatter。

    返回 (frontmatter字段字典, 正文部分)。无 frontmatter 时返回 ({}, text)。
    自动处理 \\r\\n 换行符。
    """
    normalized = text.replace("\r\n", "\n")
    fm_match = FRONTMATTER_RE.match(normalized)
    if not fm_match:
        return {}, normalized
    fields: Dict[str, str] = {}
    for line in fm_match.group(1).splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip().strip("\"'")
    return fields, normalized[fm_match.end():]


def get_frontmatter_field(text: str, field: str) -> str:
    """从 Wiki 页面文本的 frontmatter 中获取指定字段值。"""
    return parse_frontmatter(text)[0].get(field, "")


@dataclass(frozen=True)
class WikiHit:
    title: str
    relative_path: str
    page_type: str
    summary: str
    score: float
    status: str = "draft"
    source: str = "wiki"
    matched_terms: List[str] | None = None


class WikiSearcher:
    """扫描 LLM-WIKI 页面并做本地检索。

    支持 4 种页面类型（domain/concept/project/person），
    优先读取 index.md 获取页面摘要用于快速匹配。
    """

    _WIKI_CACHE: Dict[Path, Tuple[tuple, List[Tuple[Path, str, str, str, str, str]]]] = {}

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._wiki_root = Path(config.wiki["wiki_root"]).resolve() if config.wiki else Path()

    def search(self, query: str, *, top_k: int = 3, page_type: Optional[str] = None) -> List[WikiHit]:
        if not self._wiki_root.exists():
            return []
        query_tokens = tokenize(query)
        hits: List[WikiHit] = []
        for path, title, ptype, status, summary, body in self._load_pages():
            if page_type and ptype != page_type:
                continue
            score, matched_terms = _score_page(query, query_tokens, title, summary, body)
            if score <= 0:
                continue
            hits.append(WikiHit(title=title, relative_path=str(path.relative_to(self._wiki_root)),
                                page_type=ptype, summary=summary, score=round(score, 4),
                                status=status, matched_terms=matched_terms))
        hits.sort(key=lambda item: (-item.score, item.relative_path))
        return hits[:top_k]

    def get_page_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """按完整标题查找页面（用于交叉引用 [[链接]] 解析）。"""
        for path, page_title, ptype, status, summary, body in self._load_pages():
            if page_title == title:
                return {"title": page_title, "path": str(path), "page_type": ptype,
                        "status": status, "summary": summary}
        return None

    def list_pages_by_type(self, page_type: str) -> List[Dict[str, Any]]:
        """按类型列出所有页面。"""
        result = []
        for path, title, ptype, status, summary, body in self._load_pages():
            if ptype == page_type:
                result.append({"title": title, "path": str(path), "page_type": ptype,
                               "status": status, "summary": summary})
        return result

    def _load_pages(self) -> List[Tuple[Path, str, str, str, str, str]]:
        if not self._wiki_root.exists():
            return []
        paths = sorted(self._wiki_root.rglob("*.md"))
        # 跳过 index.md / changelog.md / 备份文件（*.bak.*.md）
        paths = [p for p in paths
                 if p.name not in ("index.md", "changelog.md") and ".bak." not in p.stem]
        signature = []
        for path in paths:
            try:
                signature.append((str(path), path.stat().st_mtime))
            except OSError:
                signature.append((str(path), 0.0))
        signature_tuple = tuple(signature)

        cached = self._WIKI_CACHE.get(self._wiki_root)
        if cached and cached[0] == signature_tuple:
            return cached[1]

        pages = []
        for path in paths:
            title, ptype, status, summary, body = _read_wiki_page(path)
            pages.append((path, title, ptype, status, summary, body))
        self._WIKI_CACHE[self._wiki_root] = (signature_tuple, pages)
        return pages


def _read_wiki_page(path: Path) -> Tuple[str, str, str, str, str]:
    """读取 Wiki 页面，解析 frontmatter 提取元数据。

    对编码错误、权限不足等异常返回安全默认值，避免单个损坏文件
    导致整个搜索/导航/索引功能崩溃。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError, OSError) as exc:
        logger.warning("无法读取 Wiki 页面 %s: %s", path, exc)
        title = _infer_title_from_filename(path)
        return title, "domain", "draft", "", ""
    # 统一换行符 + 解析 frontmatter
    fm, body = parse_frontmatter(text)
    title = fm.get("title", _infer_title_from_filename(path))
    page_type = fm.get("type", "domain")
    status = fm.get("status", "draft")
    summary = ""

    # 提取摘要：找第一个 ## 摘要 后的内容或第一段非空文字
    body_lines = [line.strip() for line in body.splitlines() if line.strip()]
    for i, line in enumerate(body_lines):
        if line.startswith("## 摘要") and i + 1 < len(body_lines):
            summary = body_lines[i + 1]
            break
    if not summary and body_lines:
        for line in body_lines:
            if not line.startswith("#") and not line.startswith("---"):
                summary = line[:150]
                break
    return title, page_type, status, summary, body


def _infer_title_from_filename(path: Path) -> str:
    """从文件名推断标题（去掉前缀和 .md 后缀）。"""
    name = path.stem
    for prefix in PREFIX_TO_TYPE:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name




def _score_page(query: str, query_tokens: List[str], title: str, summary: str, body: str) -> Tuple[float, List[str]]:
    query_lower = query.lower().strip()
    if not query_lower:
        return 0.0, []
    title_lower = title.lower()
    summary_lower = summary.lower()
    body_lower = body.lower()
    score = 0.0
    matched_terms: List[str] = []
    if query_lower in title_lower:
        score += 10.0
    if query_lower in summary_lower:
        score += 6.0
    if query_lower in body_lower:
        score += 3.0
    for token in query_tokens:
        if token in title_lower:
            score += 3.0
            if token not in matched_terms:
                matched_terms.append(token)
        if token in summary_lower:
            score += 2.0
            if token not in matched_terms:
                matched_terms.append(token)
        if token in body_lower:
            score += 0.8
            if token not in matched_terms:
                matched_terms.append(token)
    return score, matched_terms[:6]


def load_index_summaries(wiki_root: Path) -> Dict[str, str]:
    """从 index.md 加载页面标题→摘要映射（快速查找用）。"""
    index_path = wiki_root / "index.md"
    if not index_path.exists():
        return {}
    summaries: Dict[str, str] = {}
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            line[3:].strip()
        elif line.startswith("- [") and "](" in line:
            # - [标题](path) — 摘要
            bracket_end = line.index("](")
            title = line[3:bracket_end]
            rest = line[bracket_end + 2:]
            paren_end = rest.index(")")
            rest[:paren_end]
            summary = rest[paren_end + 2:].strip() if len(rest) > paren_end + 2 else ""
            if title and not title.startswith("http"):
                summaries[title] = summary
    return summaries
