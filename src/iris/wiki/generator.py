"""Wiki 页面生成服务 — 适配 4 种页面类型和模板。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from iris.config.loader import ConfigBundle
from iris.llm import EnvironmentConfiguredLLMProvider, LLMProviderError, LLMRequest
from iris.retrieval.searcher import LocalRetriever
from iris.utils.logging import IrisLogger

from .searcher import WikiSearcher

# 页面类型 → 中文名
TYPE_NAMES = {
    "domain": "领域",
    "concept": "概念",
    "project": "项目",
    "person": "人物",
}

# 页面类型 → 目录名
PAGE_DIRS = {
    "domain": "01-领域",
    "concept": "02-概念",
    "project": "03-项目",
    "person": "04-人物",
}

# 页面类型 → 文件名前缀
PAGE_PREFIXES = {
    "domain": "领域-",
    "concept": "概念-",
    "project": "项目-",
    "person": "人物-",
}


@dataclass(frozen=True)
class WikiPageDraft:
    page_type: str
    title: str
    slug: str
    output_path: str
    markdown: str


@dataclass(frozen=True)
class WikiWriteResult:
    path: str
    action: str
    backup_path: str | None = None


@dataclass(frozen=True)
class BatchWikiItem:
    query: str
    title: str
    page_type: str


@dataclass(frozen=True)
class BatchWikiResult:
    items: List[dict]


class WikiGenerator:
    """基于检索证据 + LLM 生成 Wiki 页面。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._retriever = LocalRetriever(config)
        self._llm_provider = EnvironmentConfiguredLLMProvider(config)
        self._template_root = config.root / "templates" / "wiki"
        self._wiki_root = Path(config.wiki["wiki_root"]).resolve() if config.wiki else Path()
        self._wiki_searcher = WikiSearcher(config) if config.wiki else None
        self._logger = IrisLogger(config)

    def build_page(self, *, query: str, page_type: str, title: str, top_k: int = 5) -> WikiPageDraft:
        self._ensure_page_type(page_type)
        slug = _slugify_title(title)
        subdir = PAGE_DIRS[page_type]
        prefix = PAGE_PREFIXES[page_type]
        output_path = self._wiki_root / subdir / f"{prefix}{slug}.md"

        # 检索相关证据
        result = self._retriever.search(query, top_k=top_k)
        evidence_text = self._format_evidence(result.hits)

        # 查找相关页面
        related = self._compute_related_pages(title, query, exclude_slug=slug) if self._wiki_searcher else "暂无"

        # LLM 生成
        markdown = self._generate_markdown(page_type=page_type, title=title, query=query,
                                           evidence=evidence_text, related=related)

        draft = WikiPageDraft(page_type=page_type, title=title, slug=slug,
                              output_path=str(output_path), markdown=markdown)
        self._logger.log("wiki_build_page", {"query": query, "page_type": page_type,
                                              "title": title, "output_path": draft.output_path})
        return draft

    def build_pages(self, items: Iterable[BatchWikiItem], *, top_k: int = 5,
                    write: bool = False, overwrite: bool = False, backup: bool = False) -> BatchWikiResult:
        results: List[dict] = []
        for item in items:
            draft = self.build_page(query=item.query, page_type=item.page_type, title=item.title, top_k=top_k)
            payload = {"query": item.query, "page_type": draft.page_type, "title": draft.title,
                       "slug": draft.slug, "output_path": draft.output_path, "markdown": draft.markdown}
            if write:
                write_result = self.write_page(draft, overwrite=overwrite, backup=backup)
                payload["write_result"] = {"path": write_result.path, "action": write_result.action,
                                           "backup_path": write_result.backup_path}
            results.append(payload)
        return BatchWikiResult(items=results)

    def write_page(self, draft: WikiPageDraft, *, overwrite: bool = False, backup: bool = False) -> WikiWriteResult:
        output_path = Path(draft.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and not overwrite:
            return WikiWriteResult(path=str(output_path), action="skipped_exists")
        backup_path = None
        if output_path.exists() and overwrite and backup:
            backup_path = str(self._build_backup_path(output_path))
            Path(backup_path).write_text(output_path.read_text(encoding="utf-8"), encoding="utf-8")
        action = "created" if not output_path.exists() else "overwritten"
        output_path.write_text(draft.markdown, encoding="utf-8")
        self._logger.log("wiki_write_page", {"path": str(output_path), "action": action, "backup_path": backup_path})
        return WikiWriteResult(path=str(output_path), action=action, backup_path=backup_path)

    def _ensure_page_type(self, page_type: str) -> None:
        if page_type not in PAGE_DIRS:
            raise ValueError(f"不支持的页面类型: {page_type}")

    def _format_evidence(self, hits) -> str:
        if not hits:
            return "无相关证据"
        lines = []
        for i, hit in enumerate(hits[:8], 1):
            lines.append(f"{i}. 来源：{hit.relative_path}")
            lines.append(f"   标题：{hit.title}")
            lines.append(f"   内容：{hit.content_preview[:300]}")
            lines.append("")
        return "\n".join(lines)

    def _compute_related_pages(self, title: str, query: str, *, exclude_slug: str = "") -> str:
        if not self._wiki_searcher:
            return "暂无"
        wiki_hits = self._wiki_searcher.search(query, top_k=5)
        if not wiki_hits:
            return "暂无"
        lines = []
        seen_slugs = set()
        for hit in wiki_hits:
            hit_slug = _slugify_title(hit.title)
            if hit_slug == exclude_slug or hit_slug in seen_slugs:
                continue
            seen_slugs.add(hit_slug)
            lines.append(f"- [{hit.title}]({hit.relative_path})")
        return "\n".join(lines) if lines else "暂无"

    def _generate_markdown(self, *, page_type: str, title: str, query: str,
                           evidence: str, related: str) -> str:
        """用 LLM 生成 Wiki 页面内容。"""
        now = datetime.now().strftime("%Y-%m-%d")
        type_name = TYPE_NAMES.get(page_type, page_type)

        prompt = f"""你是一个知识库编辑助手。请生成一份 {type_name} 类型的 Wiki 页面。

## 页面信息
- 标题：{title}
- 类型：{page_type}
- 查询：{query}
- 日期：{now}

## 参考证据
{evidence}

## 要求
1. 生成 YAML frontmatter（title/type/status/created/updated）
2. 内容结构：摘要 → 正文（分小节） → 关联页面 → 参考来源
3. 使用 [[Wiki-链接]] 格式做交叉引用
4. 保持客观、事实驱动
5. 关联页面：{related}

请输出完整的 Markdown。"""

        try:
            response = self._llm_provider.generate(
                LLMRequest(prompt=prompt, route_context={"input_type": "text", "task_type": "qa",
                                                          "complexity": "standard", "use_case": "wiki_generate"})
            )
            return response.text.strip()
        except LLMProviderError as exc:
            return self._fallback_markdown(page_type=page_type, title=title, query=query,
                                           evidence=evidence, related=related)

    def _fallback_markdown(self, *, page_type: str, title: str, query: str,
                           evidence: str, related: str) -> str:
        """LLM 不可用时的降级生成。"""
        now = datetime.now().strftime("%Y-%m-%d")
        type_name = TYPE_NAMES.get(page_type, page_type)
        return f"""---
title: {title}
type: {page_type}
status: draft
created: {now}
updated: {now}
sources:
  - SOURCE/...

---

## 摘要
关于{title}的{type_name}知识页。

## 正文
{evidence[:500]}

## 关联页面
{related}

## 参考来源
- 待补充
"""

    def _build_backup_path(self, output_path: Path) -> Path:
        counter = 1
        while True:
            candidate = output_path.with_name(f"{output_path.stem}.bak.{counter}{output_path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1


def _slugify_title(title: str) -> str:
    import re
    return re.sub(r'[^\w一-鿿\-]', '', title)[:60]
