"""Wiki 导航维护 — 维护 index.md 和 changelog.md。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle

from .searcher import WikiSearcher, _read_wiki_page, _infer_title_from_filename

# 页面类型配置
PAGE_TYPE_CONFIG = {
    "domain": {"dir": "01-领域", "name": "领域"},
    "concept": {"dir": "02-概念", "name": "概念"},
    "project": {"dir": "03-项目", "name": "项目"},
    "person": {"dir": "04-人物", "name": "人物"},
}


@dataclass(frozen=True)
class NavBuildResult:
    nav_path: str
    pages_written: int
    errors: List[str]


class WikiNavigationBuilder:
    """扫描 Wiki 目录并维护 index.md（总索引）。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._wiki_root = Path(config.wiki["wiki_root"]).resolve() if config.wiki else Path()

    def build(self, *, write: bool = True) -> NavBuildResult:
        """扫描 LLM-WIKI 目录，生成 index.md。"""
        if not self._wiki_root.exists():
            return NavBuildResult(nav_path="", pages_written=0, errors=["Wiki 根目录不存在"])

        pages: Dict[str, List[Dict[str, str]]] = {
            "领域": [], "概念": [], "项目": [], "人物": [],
        }
        errors: List[str] = []

        for ptype, cfg in PAGE_TYPE_CONFIG.items():
            dir_path = self._wiki_root / cfg["dir"]
            if not dir_path.exists():
                continue
            for md_file in sorted(dir_path.glob("*.md")):
                try:
                    title, page_type, status, summary, body = _read_wiki_page(md_file)
                    # 从文件名推断标题（若 frontmatter 未提供）
                    if not title:
                        title = _infer_title_from_filename(md_file)
                    relative = str(md_file.relative_to(self._wiki_root))
                    pages[cfg["name"]].append({
                        "title": title,
                        "path": relative,
                        "summary": summary[:120] if summary else "",
                        "status": status,
                    })
                except Exception as e:
                    errors.append(f"读取失败: {md_file.name} - {e}")

        # 生成 index.md 内容
        lines = ["# LLM-WIKI 索引", f"> 自动维护 | 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]

        for section_name in ("领域", "概念", "项目", "人物"):
            section_pages = pages.get(section_name, [])
            if not section_pages:
                continue
            lines.append(f"## {section_name}")
            lines.append("")
            for p in section_pages:
                status_icon = {"stable": "", "review": " 🔍", "draft": " ✏️"}.get(p["status"], "")
                summary_text = f" — {p['summary']}" if p["summary"] else ""
                lines.append(f"- [{p['title']}]({p['path']}){status_icon}{summary_text}")
            lines.append("")

        index_content = "\n".join(lines)

        if write:
            index_path = self._wiki_root / "index.md"
            index_path.write_text(index_content, encoding="utf-8")

        total = sum(len(v) for v in pages.values())
        index_str = str(self._wiki_root / "index.md") if write else "(dry-run)"
        return NavBuildResult(nav_path=index_str, pages_written=total, errors=errors)


def append_changelog(wiki_root: Path, entry: str) -> None:
    """向 changelog.md 追加变更记录。"""
    changelog_path = wiki_root / "changelog.md"
    if not changelog_path.exists():
        changelog_path.write_text("# 变更日志\n\n", encoding="utf-8")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with changelog_path.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp} {entry}\n")


def lint_wiki(wiki_root: Path) -> Dict[str, Any]:
    """Wiki 健康检查：孤立页、断裂链接、过时内容。"""
    if not wiki_root.exists():
        return {"error": "Wiki 根目录不存在", "page_count": 0}

    all_pages: List[Path] = []
    all_links: Dict[str, List[str]] = {}
    page_titles: Dict[str, Path] = {}
    orphan_pages: List[str] = []
    broken_links: List[str] = []
    stale_pages: List[str] = []
    page_count = 0

    import re
    LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

    for ptype, cfg in PAGE_TYPE_CONFIG.items():
        dir_path = wiki_root / cfg["dir"]
        if not dir_path.exists():
            continue
        for md_file in dir_path.glob("*.md"):
            title, _, status, _, _ = _read_wiki_page(md_file)
            if not title:
                title = _infer_title_from_filename(md_file)
            page_titles[title] = md_file
            all_pages.append(md_file)
            page_count += 1

            # 提取页面中的 [[链接]]
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            links = LINK_RE.findall(content)
            for link in links:
                # 支持 [[标题]] 和 [[标题|显示文本]]
                actual_title = link.split("|")[0].strip() if "|" in link else link.strip()
                all_links.setdefault(title, []).append(actual_title)

            # 检查过时内容
            if status == "draft":
                stale_pages.append(str(md_file.relative_to(wiki_root)))

    # 检查断裂链接
    for source_title, links in all_links.items():
        for link_title in links:
            if link_title not in page_titles:
                broken_links.append(f"{source_title} → [[{link_title}]]")

    # 检查孤立页（没有被其他页面引用的页面）
    linked_titles = set()
    for links in all_links.values():
        for link in links:
            linked_titles.add(link)
    for title, path in page_titles.items():
        if title not in linked_titles:
            # 至少有一个指向它的链接才不算孤立
            # 检查所有链接是否包含这个标题
            if not any(title in refs for refs in all_links.values()):
                orphan_pages.append(str(path.relative_to(wiki_root)))

    return {
        "page_count": page_count,
        "orphan_pages": orphan_pages[:10],
        "broken_links": broken_links[:10],
        "stale_pages": stale_pages[:10],
        "orphan_count": len(orphan_pages),
        "broken_count": len(broken_links),
        "stale_count": len(stale_pages),
    }
