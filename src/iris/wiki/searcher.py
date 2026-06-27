"""LLM-WIKI 页面扫描与本地检索 — 适配 4 种页面类型和层级目录。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from ._constants import PAGE_TYPE_CONFIG, get_wiki_dir, get_wiki_prefix

# 向下兼容别名
PAGE_TYPE_DIRS = {k: v[0] for k, v in PAGE_TYPE_CONFIG.items()}
PREFIX_TO_TYPE = {v[1]: k for k, v in PAGE_TYPE_CONFIG.items()}

TOKEN_RE = re.compile(r"[A-Za-z0-9_\-\一-鿿]+")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


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
        query_tokens = _tokenize(query)
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
        # 跳过 index.md 和 changelog.md
        paths = [p for p in paths if p.name not in ("index.md", "changelog.md")]
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
    """读取 Wiki 页面，解析 frontmatter 提取元数据。"""
    text = path.read_text(encoding="utf-8")
    # 统一换行符（防止 Windows \r\n 破坏 frontmatter 正则）
    text = text.replace("\r\n", "\n")
    title = _infer_title_from_filename(path)
    page_type = "domain"  # default
    status = "draft"
    summary = ""

    # 解析 YAML frontmatter
    fm_match = FRONTMATTER_RE.match(text)
    if fm_match:
        fm_text = fm_match.group(1)
        for line in fm_text.splitlines():
            if line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("type:"):
                page_type = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("status:"):
                status = line.split(":", 1)[1].strip().strip("\"'")
        body = text[fm_match.end():]
    else:
        body = text

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


def _tokenize(text: str) -> List[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


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
    current_section = ""
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current_section = line[3:].strip()
        elif line.startswith("- [") and "](" in line:
            # - [标题](path) — 摘要
            bracket_end = line.index("](")
            title = line[3:bracket_end]
            rest = line[bracket_end + 2:]
            paren_end = rest.index(")")
            path_str = rest[:paren_end]
            summary = rest[paren_end + 2:].strip() if len(rest) > paren_end + 2 else ""
            if title and not title.startswith("http"):
                summaries[title] = summary
    return summaries
