"""Wiki 页面上下文加载器 — 统一所有模块的 Wiki 页面读取逻辑。

消除 pipeline.py / service.py / chat_digest.py / handlers.py 中的
重复实现，提供一致的 frontmatter 解析、截断和目录遍历行为。

用法:
    loader = WikiContextLoader(wiki_root)
    ctx = loader.load_context(page_types=["domain", "project"], max_chars_per_page=2000)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ._constants import get_wiki_dir, get_display_name, get_all_types
from .searcher import _read_wiki_page, _infer_title_from_filename


@dataclass
class WikiPageInfo:
    """单个 Wiki 页面的结构化信息。"""
    path: Path
    title: str
    page_type: str
    status: str
    summary: str
    body: str
    relative_path: str = ""


class WikiContextLoader:
    """统一的 Wiki 页面上下文加载器。

    所有模块应通过此类加载 Wiki 页面内容，而非各自实现。
    支持按页面类型筛选、字符截断、页数限制和排序。
    """

    def __init__(self, wiki_root: Path):
        if not isinstance(wiki_root, Path):
            wiki_root = Path(wiki_root)
        self._root = wiki_root.resolve()

    # ── 公开 API ──────────────────────────────────────────

    def load_pages(
        self,
        *,
        page_types: Optional[List[str]] = None,
        sort_order: Optional[List[str]] = None,
    ) -> List[WikiPageInfo]:
        """加载 Wiki 页面结构化数据（不截断）。

        Args:
            page_types: 要加载的页面类型列表，None 表示全部
            sort_order: 目录遍历顺序，None 表示默认顺序

        Returns:
            WikiPageInfo 列表
        """
        if page_types is None:
            page_types = get_all_types()
        if sort_order is None:
            sort_order = page_types

        pages: List[WikiPageInfo] = []
        for ptype in sort_order:
            if ptype not in page_types:
                continue
            dir_path = self._root / get_wiki_dir(ptype)
            if not dir_path.exists():
                continue
            for md_file in sorted(dir_path.glob("*.md")):
                try:
                    title, found_type, status, summary, body = _read_wiki_page(md_file)
                    if not title:
                        title = _infer_title_from_filename(md_file)
                    pages.append(WikiPageInfo(
                        path=md_file,
                        title=title,
                        page_type=found_type,
                        status=status,
                        summary=summary,
                        body=body,
                        relative_path=str(md_file.relative_to(self._root)),
                    ))
                except Exception:
                    continue
        return pages

    def load_context(
        self,
        *,
        page_types: Optional[List[str]] = None,
        max_chars_per_page: int = 2000,
        max_pages: Optional[int] = None,
        sort_order: Optional[List[str]] = None,
        label_prefix: bool = True,
    ) -> str:
        """加载 Wiki 页面并格式化为 LLM prompt 上下文字符串。

        Args:
            page_types: 要包含的页面类型，None = 全部
            max_chars_per_page: 每页最大字符数（超出截断）
            max_pages: 最多包含的页面数，None = 不限制
            sort_order: 目录遍历顺序
            label_prefix: 是否添加 "## 类型：标题" 标签

        Returns:
            格式化的上下文文本，用双换行连接各页面
        """
        pages = self.load_pages(page_types=page_types, sort_order=sort_order)
        fragments: List[str] = []

        for page in pages:
            # 截断正文
            body = page.body
            if len(body) > max_chars_per_page:
                body = body[:max_chars_per_page] + "\n\n...（截断）"

            if label_prefix:
                type_name = get_display_name(page.page_type)
                fragments.append(f"## {type_name}：{page.title}\n{body}")
            else:
                fragments.append(f"## {page.path.stem}\n{body}")

            if max_pages and len(fragments) >= max_pages:
                break

        return "\n\n".join(fragments)

    @property
    def root(self) -> Path:
        return self._root
