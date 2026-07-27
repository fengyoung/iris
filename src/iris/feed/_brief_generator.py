"""信息汇聚管道 — 话题简报生成。

每个话题生成一篇 Markdown 简报，归档到 SOURCE/09-工作简报/YYYYMM/。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from iris.feed._types import ConvertedDoc, DetectedTopic

logger = logging.getLogger(__name__)


def _sanitize_filename(title: str) -> str:
    """将话题标题转换为安全的文件名。"""
    # 去除特殊字符，保留中文、字母、数字、空格
    cleaned = re.sub(r'[^\w\s一-鿿-]', '', title)
    cleaned = re.sub(r'\s+', '', cleaned)
    return cleaned.strip()


def _build_filename(topic: DetectedTopic, exec_date: str) -> str:
    """构建简报文件名。

    格式: YYYYMMDD-简报-{标题}（from{来源}）.md
    """
    title_part = _sanitize_filename(topic.title)
    source_tag = "from飞书"
    return f"{exec_date}-简报-{title_part}（{source_tag}）.md"


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
            topics: 检测到的话题列表
            converted_docs: 转换的文档列表
            exec_date: 执行日期 YYYYMMDD
            dry_run: 仅构建内容不写磁盘

        Returns:
            生成的文件路径列表
        """
        # 确保输出目录存在
        output_dir = self._source_root / "09-工作简报" / exec_date[:6]
        if not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)

        files = []
        for topic in topics:
            path = self._generate_one(topic, converted_docs, exec_date, output_dir, dry_run=dry_run)
            if path:
                files.append(path)
        return files

    def _generate_one(
        self,
        topic: DetectedTopic,
        converted_docs: List[ConvertedDoc],
        exec_date: str,
        output_dir: Path,
        dry_run: bool = False,
    ) -> Optional[Path]:
        """生成单篇简报。"""
        filename = _build_filename(topic, exec_date)
        filepath = output_dir / filename

        # 构建 frontmatter
        sources_yaml = "\n".join([
            f"  - type: {s.type}\n    name: {s.name}\n    msg_count: {s.msg_count}"
            for s in topic.source_chats
        ]) if topic.source_chats else "  []"

        # 匹配关联文档
        related_docs = self._match_docs(topic, converted_docs)
        documents_yaml = "\n".join([
            f"  - path: {d.relative_path}\n    title: {d.title}"
            for d in related_docs
        ]) if related_docs else "  []"

        previous_yaml = "\n".join([
            f"  - {v}" for v in topic.previous_versions
        ]) if topic.previous_versions else "  []"

        # 来源描述
        source_names = ", ".join([s.name for s in topic.source_chats]) if topic.source_chats else "飞书"
        source_desc = f"{source_names} · {sum(s.msg_count for s in topic.source_chats)} 条消息"

        # 讨论要点
        if topic.discussion_points:
            dp_lines = "\n".join([f"1. {p}" for p in topic.discussion_points])
        else:
            dp_lines = "（暂无）"

        # 决策
        if topic.decisions:
            dec_lines = "\n".join([f"- {d}" for d in topic.decisions])
        else:
            dec_lines = "（暂无明确决策）"

        # OKR 关联
        if topic.okr_tags:
            okr_lines = []
            for tag in topic.okr_tags:
                status_icon = "✅" if topic.okr_match_strength == "strong" else "🟡"
                okr_lines.append(f"- {status_icon} {tag}")
            okr_section = "\n".join(okr_lines)
            okr_section += f"\n\n匹配强度：**{topic.okr_match_strength}**"
        else:
            okr_section = "（未关联 OKR）"

        # 相关文档链接
        if related_docs:
            doc_lines = "\n".join([f"- [{d.title}]({d.relative_path})" for d in related_docs])
        else:
            doc_lines = "（无）"

        # 参与者
        if topic.participants:
            participants = " · ".join(topic.participants)
        else:
            participants = "（未识别）"

        # 原始消息精选
        if topic.quotes:
            quote_lines = "\n\n".join([
                f'> 「{q.text}」\n> —— {q.speaker} {q.time}'
                for q in topic.quotes[:8]
            ])
        else:
            # 从 messages 中提取
            sample = topic.messages[:5]
            quote_lines = "\n\n".join([
                f'> 「{m.content[:120]}」\n> —— {m.sender_name} {m.send_time.strftime("%m-%d %H:%M")}'
                for m in sample
            ])

        # 状态
        status = topic.okr_match_strength if topic.okr_match_strength != "none" else "unmatched"

        content = _BRIEF_TEMPLATE.format(
            exec_date=exec_date,
            topic_id=topic.topic_id,
            title=topic.title,
            source_description=source_desc,
            today=datetime.now().strftime("%Y-%m-%d"),
            summary=topic.summary or "（暂无摘要）",
            key_status=topic.key_status or "（暂无）",
            discussion_points=dp_lines,
            decisions=dec_lines,
            okr_section=okr_section,
            doc_links=doc_lines,
            participants=participants,
            quotes_section=quote_lines,
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
            source_summary=", ".join([f"{s.name} ({s.msg_count}条)" for s in topic.source_chats]) if topic.source_chats else "飞书",
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
