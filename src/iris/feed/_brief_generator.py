"""信息汇聚管道 — 话题简报生成。

每个话题生成一篇 Markdown 简报，归档到 SOURCE/09-工作简报/YYYYMM/。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from iris.feed._types import ConvertedDoc, DetectedTopic, Quote

logger = logging.getLogger(__name__)


def _sanitize_filename(title: str) -> str:
    """将话题标题转换为安全的文件名。"""
    cleaned = re.sub(r'[^\w\s一-鿿-]', '', title)
    cleaned = re.sub(r'\s+', '', cleaned)
    return cleaned.strip()


def _build_filename(topic: DetectedTopic, exec_date: str) -> str:
    """构建简报文件名。

    格式: YYYYMMDD-简报-{标题}（from飞书）.md
    """
    title_part = _sanitize_filename(topic.title)
    return f"{exec_date}-简报-{title_part}（from飞书）.md"


# ── 简报模板 ───────────────────────────────────────────────

_BRIEF_TEMPLATE = """---
type: discussion
date: {exec_date}
updated: {exec_date}
topic_id: {topic_id}
status: {status}
okr_tags: {okr_tags_json}
sources:
{sources_yaml}
documents:
{documents_yaml}
previous_versions:
{previous_versions_yaml}
---

# {title}

> **来源**：{source_description}
> **整理日期**：{today}
> **话题 ID**：{topic_id}

---

## 话题概览

{summary}

---

## 关键信息

### 当前状态

{key_status}

### 讨论要点

{discussion_points}

### 已明确的决策

{decisions}

---

## OKR 关联

{okr_section}

---

## 相关文档

{doc_links}

---

## 参与者

{participants}

---

## 原始消息精选

{quotes_section}

---

*生成时间：{generated_at} · 数据源：{source_summary}*
"""


class BriefGenerator:
    """话题简报生成器。"""

    def __init__(self, source_root: Path):
        self._source_root = source_root

    def generate(
        self,
        topics: List[DetectedTopic],
        converted_docs: List[ConvertedDoc],
        exec_date: str,
        dry_run: bool = False,
    ) -> List[Path]:
        """生成简报文件。

        Args:
            topics: 检测到的话题列表（已完成 Phase 2 深度摘要）
            converted_docs: 转换的文档列表
            exec_date: 执行日期 YYYYMMDD
            dry_run: 仅构建内容不写磁盘

        Returns:
            生成的文件路径列表
        """
        files = []
        for topic in topics:
            filename = _build_filename(topic, exec_date)
            if dry_run:
                # dry-run 不创建目录，手动拼路径
                filepath = self._source_root / "09-工作简报" / exec_date[:6] / filename
            else:
                from iris.utils.paths import resolve_source_archive_path
                filepath = resolve_source_archive_path(
                    self._source_root, "09-工作简报", filename)
            path = self._generate_one(topic, converted_docs, exec_date, filepath, dry_run=dry_run)
            if path:
                files.append(path)
        return files

    def _generate_one(
        self,
        topic: DetectedTopic,
        converted_docs: List[ConvertedDoc],
        exec_date: str,
        filepath: Path,
        dry_run: bool = False,
    ) -> Optional[Path]:
        """生成单篇简报。"""
        filename = filepath.name

        # ── Frontmatter ──────────────────────────────────
        sources_yaml = "\n".join([
            f"  - type: {s.type}\n    name: {s.name}\n    msg_count: {s.msg_count}"
            for s in topic.source_chats
        ]) if topic.source_chats else "  []"

        related_docs = self._match_docs(topic, converted_docs)
        documents_yaml = "\n".join([
            f"  - path: {d.relative_path}\n    title: {d.title}"
            for d in related_docs
        ]) if related_docs else "  []"

        previous_yaml = "\n".join([
            f"  - {v}" for v in topic.previous_versions
        ]) if topic.previous_versions else "  []"

        # ── 来源描述 ──────────────────────────────────────
        source_names = ", ".join([s.name for s in topic.source_chats]) if topic.source_chats else "飞书"
        total_msgs = sum(s.msg_count for s in topic.source_chats) if topic.source_chats else len(topic.messages)
        source_desc = f"{source_names} · {total_msgs} 条消息"

        # ── 讨论要点（正确编号） ───────────────────────────
        if topic.discussion_points:
            dp_lines = "\n".join(
                [f"{i}. {p}" for i, p in enumerate(topic.discussion_points, 1)]
            )
        else:
            dp_lines = "（暂无）"

        # ── 决策 ──────────────────────────────────────────
        if topic.decisions:
            dec_lines = "\n".join([f"- {d}" for d in topic.decisions])
        else:
            dec_lines = "（暂无明确决策）"

        # ── 关键状态：优先用 LLM 输出，否则从首条讨论要点提取 ──
        key_status = topic.key_status or ""
        if not key_status and topic.discussion_points:
            key_status = topic.discussion_points[0]
        if not key_status:
            key_status = "（暂无）"

        # ── OKR 关联 ──────────────────────────────────────
        if topic.okr_tags:
            okr_lines = []
            for tag in topic.okr_tags:
                status_icon = "✅" if topic.okr_match_strength == "strong" else "🟡"
                okr_lines.append(f"- {status_icon} {tag}")
            okr_section = "\n".join(okr_lines)
            okr_section += f"\n\n匹配强度：**{topic.okr_match_strength}**"
        else:
            okr_section = "（未关联 OKR）"

        # ── 相关文档 ──────────────────────────────────────
        if related_docs:
            doc_lines = "\n".join([f"- [{d.title}]({d.relative_path})" for d in related_docs])
        else:
            doc_lines = "（无）"

        # ── 参与者 ────────────────────────────────────────
        if topic.participants:
            participants = " · ".join(topic.participants)
        else:
            participants = "（未识别）"

        # ── 原始消息精选（合并 LLM 输出 + 原始消息兜底）───
        quotes_section = self._build_quotes_section(topic)

        status = topic.okr_match_strength if topic.okr_match_strength != "none" else "unmatched"

        content = _BRIEF_TEMPLATE.format(
            exec_date=exec_date,
            topic_id=topic.topic_id,
            title=topic.title,
            source_description=source_desc,
            today=datetime.now().strftime("%Y-%m-%d"),
            summary=topic.summary or "（暂无摘要）",
            key_status=key_status,
            discussion_points=dp_lines,
            decisions=dec_lines,
            okr_section=okr_section,
            doc_links=doc_lines,
            participants=participants,
            quotes_section=quotes_section,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            source_summary=", ".join(
                [f"{s.name} ({s.msg_count}条)" for s in topic.source_chats]
            ) if topic.source_chats else "飞书",
            okr_tags_json=json.dumps(topic.okr_tags, ensure_ascii=False),
            status=status,
            sources_yaml=sources_yaml,
            documents_yaml=documents_yaml,
            previous_versions_yaml=previous_yaml,
        )

        if dry_run:
            logger.info("(dry-run) 简报预览: %s", filename)
            return filepath

        filepath.write_text(content, encoding="utf-8")
        logger.info("简报已生成: %s", filename)
        return filepath

    @staticmethod
    def _build_quotes_section(topic: DetectedTopic) -> str:
        """构建原始消息精选段落。

        策略：
        1. 优先使用 LLM（Phase 2）输出的 quotes
        2. LLM quotes 不足 3 条时，从 topic.messages 中补充
        3. 总共最多 8 条
        """
        all_quotes: List[Quote] = []

        # 收集 LLM 输出的 quotes
        if topic.quotes:
            all_quotes.extend(topic.quotes)

        # LLM quotes 不足时，从原始消息补充（去重：按 text 前 80 字判断）
        if len(all_quotes) < 3 and topic.messages:
            seen_texts = {q.text[:80] for q in all_quotes}
            sorted_msgs = sorted(topic.messages, key=lambda m: m.send_time)
            for m in sorted_msgs:
                if len(all_quotes) >= 8:
                    break
                prompt_content = m.content_for_prompt()
                preview = prompt_content[:80]
                if preview not in seen_texts and len(prompt_content.strip()) > 5:
                    all_quotes.append(Quote(
                        text=prompt_content,
                        speaker=m.sender_name,
                        time=m.send_time.strftime("%m-%d %H:%M"),
                    ))
                    seen_texts.add(preview)

        if not all_quotes:
            return "（暂无）"

        quote_lines = "\n\n".join([
            f'> 「{q.text}」\n> —— {q.speaker} {q.time}'
            for q in all_quotes[:8]
        ])
        return quote_lines

    @staticmethod
    def _match_docs(
        topic: DetectedTopic,
        converted_docs: List[ConvertedDoc],
    ) -> List[ConvertedDoc]:
        """匹配话题关联的文档。"""
        related = []
        for doc in converted_docs:
            if doc.source_chat in [s.name for s in topic.source_chats]:
                related.append(doc)
        return related
