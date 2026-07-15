"""Wiki 页面生成服务 — 适配 4 种页面类型和模板。"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError, LLMService
from iris.retrieval.searcher import LocalRetriever
from iris.utils.logging import IrisLogger

from .searcher import WikiSearcher, FRONTMATTER_RE
from ._constants import (
    get_display_name, get_wiki_dir, get_wiki_prefix,
    get_dir_map, get_prefix_map, get_display_name_map,
)

# 向下兼容别名（推荐直接使用 get_* 访问器）
TYPE_NAMES = get_display_name_map()
PAGE_DIRS = get_dir_map()
PAGE_PREFIXES = get_prefix_map()


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
        self._llm = LLMService(config)
        self._template_root = config.root / "templates" / "wiki"
        if not config.wiki or not config.wiki.get("wiki_root"):
            raise ValueError("Wiki 配置缺失：请在 config/wiki.json 中设置 wiki_root")
        self._wiki_root = Path(config.wiki["wiki_root"]).resolve()
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
        ref_quality = self.check_reference_quality(markdown)
        log_payload = {"query": query, "page_type": page_type,
                       "title": title, "output_path": draft.output_path,
                       "ref_quality": ref_quality}
        self._logger.log("wiki_build_page", log_payload)
        if ref_quality.get("quality") in ("poor", "fair"):
            import logging as _logging
            _logging.getLogger("iris.wiki.generator").warning(
                "引用质量偏低 [%s] %s: %d/%d 条引用有描述",
                ref_quality.get("quality", "?"),
                title,
                ref_quality.get("described_refs", 0),
                ref_quality.get("total_refs", 0),
            )
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
        from iris.core.write_guard import safe_write_text
        output_path = Path(draft.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists() and not overwrite:
            return WikiWriteResult(path=str(output_path), action="skipped_exists")
        backup_path = None
        if output_path.exists() and overwrite and backup:
            backup_path = str(self._build_backup_path(output_path))
            Path(backup_path).write_text(output_path.read_text(encoding="utf-8"), encoding="utf-8")
        action = "created" if not output_path.exists() else "overwritten"
        # 使用安全检查写入，Wiki 目录为用户配置的外部路径
        safe_write_text(output_path, draft.markdown, self._config,
                        allow_existing_outside=True)
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

        if page_type == "person":
            prompt = self._build_person_prompt(title, query, evidence, related, now)
        else:
            prompt = self._build_generic_prompt(type_name, page_type, title, query, evidence, related, now)

        try:
            text = self._llm.generate(
                prompt, route_context={"input_type": "text", "task_type": "qa",
                                       "complexity": "standard", "use_case": "wiki_generate"}
            ).text
            return self._extract_wiki_content(text)
        except LLMProviderError as exc:
            return self._fallback_markdown(page_type=page_type, title=title, query=query,
                                           evidence=evidence, related=related)

    def _build_generic_prompt(self, type_name, page_type, title, query, evidence, related, now):
        template = self._load_template("wiki/generate_generic.txt")
        if template:
            return template.format(type_name=type_name, page_type=page_type, title=title,
                                   query=query, evidence=evidence, related=related, now=now)
        # 降级：内联 prompt（与外部模板保持同步）
        return f"""你是一个知识库编辑助手。请生成一份 {type_name} 类型的 Wiki 页面。

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
6. 参考来源格式：每条引用使用 [path.md:行号] 事实断言描述的格式，描述 10-30 字说明关键信息。禁止仅罗列文件路径。

请输出完整的 Markdown。"""

    def _build_person_prompt(self, title, query, evidence, related, now):
        template = self._load_template("wiki/generate_person.txt")
        if template:
            return template.format(title=title, query=query, evidence=evidence,
                                   related=related, now=now)
        # 降级：内联 prompt（与外部模板保持同步）
        return f"""你是一个知识库编辑助手。请为团队成员 **{title}** 生成一份人物 Wiki 页面。

## 参考证据（来自周报、会议纪要、项目文档）
{evidence}

## 要求
1. 生成 YAML frontmatter，含 title/type(person)/status/created/updated/email/sync: false

2. 页面结构：
   - **摘要**：1-2 句概括该成员的角色和核心方向
   - **基本信息**：部门、职位（从证据中推断）、邮箱
   - **负责方向**：从证据中提取该成员负责的项目、技术方向、业务领域
   - **协作网络**：从会议纪要和周报中提取经常协作的同事
   - **周报时间线**：按时间顺序列出周报覆盖周期和主线变化
   - **关联页面**：链接到相关的项目/领域 Wiki 页面

3. 保持客观，仅从证据中提取事实，不编造
4. 使用 [[Wiki-链接]] 格式做交叉引用
5. email 字段提取证据中的邮箱信息
6. 参考来源格式：每条引用使用 [path.md:行号] 事实断言描述的格式，描述 10-30 字说明关键信息。禁止仅罗列文件路径。

请输出完整的 Markdown。"""

    @staticmethod
    def _load_template(name: str) -> Optional[str]:
        """从项目根目录的 templates/ 加载 Prompt 模板，不存在返回 None。"""
        templates_dir = Path(__file__).resolve().parent.parent.parent.parent / "templates"
        tmpl_path = templates_dir / name
        if tmpl_path.exists():
            return tmpl_path.read_text(encoding="utf-8")
        return None

    @staticmethod
    def _extract_wiki_content(text: str) -> str:
        """从 LLM 响应中提取 Wiki Markdown（剥离代码块包裹、定位 YAML frontmatter 起点）。"""
        text = text.strip()
        # 1. 优先严格正则：以 --- 开头、下一行是 title: 的 frontmatter（最可靠）
        m_strict = re.search(r"(?:^|\n)(---\s*\ntitle:.*?)(?=\Z)", text, re.DOTALL)
        if m_strict:
            return m_strict.group(1).strip()
        # 2. 提取 ```markdown ... ``` 中的内容
        m = re.search(r"```(?:markdown)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            if candidate.startswith("---"):
                return candidate
            # 若代码块内容不含 frontmatter，继续后续 heuristic
        # 3. 如果文本以 YAML frontmatter 开头，直接返回
        if text.startswith("---"):
            return text
        # 4. 尝试查找 ---\ntitle: 开头的 frontmatter
        idx = text.find("\n---\n")
        if idx != -1:
            candidate = text[idx + 1:]
            if candidate.strip().startswith("---"):
                return candidate.strip()
        # 5. 尝试查找 ---\ntitle:（文本可能以对话开始）
        fm_start = text.find("---\ntitle:")
        if fm_start != -1:
            prev = text.rfind("---", 0, fm_start)
            if prev != -1:
                return text[prev:].strip()
        # 降级：返回原文本（记录 warning 便于发现 LLM 输出异常）
        import logging as _logging
        _logging.getLogger("iris.wiki.generator").warning(
            "无法定位 Wiki frontmatter，已降级返回原始 LLM 输出（前100字）: %s", text[:100]
        )
        return text

    def _fallback_markdown(self, *, page_type: str, title: str, query: str,
                           evidence: str, related: str) -> str:
        """LLM 不可用时的降级生成。"""
        now = datetime.now().strftime("%Y-%m-%d")
        type_name = TYPE_NAMES.get(page_type, page_type)
        template = self._load_template("wiki/fallback_markdown.txt")
        if template:
            return template.format(
                title=title, page_type=page_type, now=now,
                type_name=type_name, evidence=evidence[:500], related=related,
            )
        # 降级：内联
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

    # ── 增量更新 ──────────────────────────────────────────────

    def _parse_frontmatter_field(self, content: str, field: str) -> str:
        """从 YAML frontmatter 中提取指定字段的值。"""
        from .searcher import get_frontmatter_field
        return get_frontmatter_field(content, field)

    def _find_page_by_title(self, title: str, page_type: Optional[str] = None) -> Optional[Tuple[Path, str, str]]:
        """按标题查找已有 Wiki 页面，返回 (path, page_type, content)。"""
        if not self._wiki_root.exists():
            return None
        from .context_loader import WikiContextLoader
        loader = WikiContextLoader(self._wiki_root)
        for page_info in loader.load_pages():
            if page_info.title == title:
                if page_type and page_info.page_type != page_type:
                    continue
                try:
                    content = page_info.path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                return (page_info.path, page_info.page_type, content)
        return None

    def _generate_incremental_update(
        self, *, existing_content: str, title: str, page_type: str,
        evidence: str, last_updated: str, related: str,
    ) -> Optional[str]:
        """LLM 判断是否需要更新，返回更新后的完整 Markdown（无变化则返回原样）。"""
        today = datetime.now().strftime("%Y-%m-%d")
        type_name = TYPE_NAMES.get(page_type, page_type)

        if page_type == "person":
            prompt = self._build_person_update_prompt(existing_content, last_updated, evidence, related, today)
        else:
            prompt = self._build_generic_update_prompt(existing_content, type_name, last_updated, evidence, related, today)

        try:
            text = self._llm.generate(
                prompt, route_context={"input_type": "text", "task_type": "qa",
                                       "complexity": "standard", "use_case": "wiki_update"}
            ).text
            return self._extract_wiki_content(text)
        except LLMProviderError as exc:
            self._logger.log("wiki_update_llm_failed", {"title": title, "error": str(exc)})
            return None

    def _build_generic_update_prompt(self, existing_content, type_name, last_updated, evidence, related, today):
        template = self._load_template("wiki/update_generic.txt")
        last_updated_str = last_updated or '未知'
        if template:
            return template.format(
                type_name=type_name, existing_content=existing_content,
                last_updated=last_updated_str, evidence=evidence,
                related=related, today=today,
            )
        # 降级：内联 prompt
        return f"""你是一个知识库编辑助手。请对一篇现有的 {type_name} 类型 Wiki 页面做增量更新。

## 现有页面内容
```markdown
{existing_content}
```

## 页面上次更新时间
{last_updated_str}

## 最新的参考证据（来自更新的原始文档）
{evidence}

## 关联页面
{related}

## 要求
1. 判断证据中是否包含页面尚未覆盖的**实质性新内容**
   - 如果无新内容 → 直接将 existing_content 原样输出，不做任何修改
   - 如果有新内容 → 在适当位置插入更新（新增章节或在现有章节中补充）
2. 保留原有内容结构和 wording，不要删改已有的有效信息
3. 更新 YAML frontmatter 中的 updated 字段为 {today}
4. 保持 [[Wiki-链接]] 交叉引用格式
5. 在「参考来源」中补充新证据的来源，每条引用附 10-30 字事实断言描述
6. **输出纯 Markdown，以 --- 开头，不要任何对话前缀或代码块包裹**"""

    def _build_person_update_prompt(self, existing_content, last_updated, evidence, related, today):
        template = self._load_template("wiki/update_person.txt")
        last_updated_str = last_updated or '未知'
        if template:
            return template.format(
                existing_content=existing_content, last_updated=last_updated_str,
                evidence=evidence, related=related, today=today,
            )
        # 降级：内联 prompt
        return f"""你是一个知识库编辑助手。请对团队成员 Wiki 页面做增量更新。

## 现有页面内容
```markdown
{existing_content}
```

## 页面上次更新时间
{last_updated_str}

## 最新的参考证据（来自周报、会议纪要、项目文档）
{evidence}

## 关联页面
{related}

## 要求
1. 判断证据中是否包含页面尚未覆盖的**实质性新信息**
   - 新负责方向 → 追加到「负责方向」，标记开始时间
   - 新增协作人 → 追加到「协作网络」
   - 新的周报 → 追加到「周报时间线」
   - 旧方向 >6月未提及 → 移入「过往负责」章节
   - 无新内容 → 原样输出
2. 保留原有内容结构和 wording
3. 更新 YAML frontmatter 中的 updated 字段为 {today}
4. 保持 [[Wiki-链接]] 交叉引用格式
5. 在「参考来源」中补充新来源，每条引用附 10-30 字事实断言描述
6. **输出纯 Markdown，以 --- 开头，不要任何对话前缀或代码块包裹**"""

    @staticmethod
    def _validate_update_output(new_content: str, existing_content: str, expected_title: str) -> str:
        """校验 LLM 输出：确保 frontmatter 完整且 title 未被篡改。"""
        import re as _re
        # 统一换行符（防止 Windows \r\n 破坏正则匹配）
        normalized = new_content.replace("\r\n", "\n")
        existing_normalized = existing_content.replace("\r\n", "\n")
        fm = _re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
        m = fm.match(normalized)
        if not m:
            # 尝试用现有 frontmatter 包裹正文以挽救更新
            existing_fm = fm.match(existing_normalized)
            if existing_fm:
                body_start = normalized.find("\n---", 3)
                if body_start > 0:
                    recovered = existing_fm.group(0).rstrip() + normalized[body_start:]
                    return recovered
            return existing_content  # 无法修复 → 回退

        # 提取新内容中的 title
        new_title = ""
        for line in m.group(1).splitlines():
            if line.startswith("title:"):
                new_title = line.split(":", 1)[1].strip().strip("\"'")
                break

        # title 被篡改 → 修复
        if new_title != expected_title:
            new_content = _re.sub(
                r"^title:.*$",
                f"title: {expected_title}",
                new_content,
                count=1,
                flags=_re.MULTILINE,
            )

        # 确保结尾没有多余代码块
        new_content = new_content.strip()
        if new_content.endswith("```"):
            new_content = new_content[:-3].strip()

        return new_content

    @staticmethod
    def check_reference_quality(content: str) -> Dict[str, Any]:
        """检查 Wiki 内容中参考来源的引用质量。

        评估维度：
          - total_refs：引用总数
          - described_refs：带有事实断言描述（≥10 字）的引用数
          - bare_path_refs：仅文件路径无描述的引用数
          - quality：good（全部有描述）/ fair（≥50% 有描述）/ poor（<50% 或有裸路径）

        用于在 wiki 生成后做质量追踪，不阻塞生成流程。
        """
        import re as _re
        ref_section = _re.search(r"## 参考来源\n(.*?)(?=\n## |\Z)", content, _re.DOTALL)
        if not ref_section:
            return {"total_refs": 0, "described_refs": 0, "bare_path_refs": 0, "quality": "no_refs"}

        ref_lines = [l.strip() for l in ref_section.group(1).split("\n") if l.strip()]
        # 过滤掉非引用行（如空行、注释行）
        ref_entries = [l for l in ref_lines if _re.match(r"^(?:\d+\.\s*)?\[?.*\.md", l)]

        total = len(ref_entries)
        if total == 0:
            return {"total_refs": 0, "described_refs": 0, "bare_path_refs": 0, "quality": "no_refs"}

        bare_path_count = 0
        for entry in ref_entries:
            # 移除文件路径部分，检查剩余描述
            desc = _re.sub(r"^(?:\d+\.\s*)?\[?[^\]]+\.md(?::\d+(?:-\d+)?)?\]?\s*", "", entry).strip()
            # 检查描述长度（中文算实际字数）
            desc_chars = len(desc.replace(" ", ""))
            if desc_chars < 10:
                bare_path_count += 1

        described = total - bare_path_count
        ratio = described / total if total > 0 else 0

        if ratio >= 0.9:
            quality = "good"
        elif ratio >= 0.5:
            quality = "fair"
        else:
            quality = "poor"

        return {
            "total_refs": total,
            "described_refs": described,
            "bare_path_refs": bare_path_count,
            "quality": quality,
            "quality_ratio": round(ratio, 2),
        }

    def update_page(self, *, title: str, page_type: Optional[str] = None, top_k: int = 8) -> Dict[str, Any]:
        """增量更新单个 Wiki 页面。"""
        found = self._find_page_by_title(title, page_type)
        if not found:
            return {"status": "not_found", "title": title}
        path, existing_type, existing_content = found
        return self._update_page_with_content(
            title=title, page_type=existing_type, path=path,
            existing_content=existing_content, top_k=top_k,
        )

    def update_all_pages(self, *, top_k: int = 8) -> Dict[str, Any]:
        """遍历并增量更新所有现有 Wiki 页面。"""
        if not self._wiki_root.exists():
            return {"status": "error", "reason": "Wiki 目录不存在"}

        from .context_loader import WikiContextLoader
        loader = WikiContextLoader(self._wiki_root)

        # 构建 title→(path, type, content) 索引
        page_index: Dict[str, Tuple[Path, str, str]] = {}
        for page_info in loader.load_pages():
            if not page_info.title:
                continue
            try:
                content = page_info.path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            page_index[page_info.title] = (page_info.path, page_info.page_type, content)

        results: List[Dict[str, Any]] = []
        items = [(t, p, pt, c) for t, (p, pt, c) in page_index.items()]
        if not items:
            return {"total": 0, "updated": 0, "unchanged": 0, "not_found": 0, "errors": 0, "details": []}

        def _update_one(args: Tuple) -> Dict[str, Any]:
            title, path, page_type, content = args
            return self._update_page_with_content(
                title=title, page_type=page_type, path=path,
                existing_content=content, top_k=top_k,
            )

        with ThreadPoolExecutor(max_workers=min(len(items), 6)) as executor:
            futures = {executor.submit(_update_one, item): item[0] for item in items}
            for future in as_completed(futures):
                title = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append({"status": "error", "title": title, "reason": str(exc)})

        updated = [r for r in results if r.get("status") == "updated"]
        unchanged = [r for r in results if r.get("status") == "no_changes"]
        not_found = [r for r in results if r.get("status") == "not_found"]
        errors = [r for r in results if r.get("status") == "error"]
        return {
            "total": len(results),
            "updated": len(updated),
            "unchanged": len(unchanged),
            "not_found": len(not_found),
            "errors": len(errors),
            "details": results,
        }

    def _update_page_with_content(
        self, *, title: str, page_type: str, path: Path,
        existing_content: str, top_k: int,
    ) -> Dict[str, Any]:
        """增量更新单个页面（已有内容和路径，避免重复扫描文件）。"""
        last_updated = self._parse_frontmatter_field(existing_content, "updated")

        result = self._retriever.search(title, top_k=top_k)
        evidence_text = self._format_evidence(result.hits)

        slug = _slugify_title(title)
        related = self._compute_related_pages(title, title, exclude_slug=slug) if self._wiki_searcher else "暂无"

        new_content = self._generate_incremental_update(
            existing_content=existing_content, title=title, page_type=page_type,
            evidence=evidence_text, last_updated=last_updated, related=related,
        )

        if new_content is None:
            return {"status": "error", "title": title, "reason": "LLM 调用失败"}

        validated = self._validate_update_output(new_content, existing_content, title)
        if validated != new_content:
            self._logger.log("wiki_update_validation", {"title": title, "action": validated})
            new_content = validated

        if new_content.strip() == existing_content.strip():
            return {"status": "no_changes", "title": title, "path": str(path)}

        draft = WikiPageDraft(page_type=page_type, title=title, slug=slug,
                              output_path=str(path), markdown=new_content)
        write_result = self.write_page(draft, overwrite=True, backup=True)

        ref_quality = WikiGenerator.check_reference_quality(new_content)
        self._logger.log("wiki_update_page", {"title": title, "path": str(path),
                                                "action": write_result.action,
                                                "ref_quality": ref_quality})
        return {
            "status": "updated",
            "title": title,
            "path": str(path),
            "action": write_result.action,
            "backup_path": write_result.backup_path,
            "ref_quality": ref_quality,
        }


def _slugify_title(title: str) -> str:
    import re
    return re.sub(r'[^\w一-鿿\-]', '', title)[:60]
