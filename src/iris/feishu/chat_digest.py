"""聊天记录提炼 — 从飞书群聊/单聊中提取结构化知识。"""

from __future__ import annotations

import hashlib
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle
from iris.feishu.client import FeishuClient, FeishuClientError
from iris.feishu._shared import (
    resolve_source_sub_dir, resolve_dedup_path,
    load_dedup_index, save_dedup_index, upsert_dedup_item,
    sanitize_title, extract_date, now_iso,
)
from iris.llm import LLMService, LLMProviderError
from iris.feishu.image_analyzer import MessageImageAnalyzer
from iris.core.write_guard import safe_write_text

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────

DEFAULT_RANGE_DAYS = 3
MAX_MESSAGES = 500
MAX_MESSAGES_IN_PROMPT = 50
SYSTEM_PROMPT = (
    "你是一个专业的聊天记录提炼助手。擅长从群聊/单聊对话中"
    "提取讨论主题、关键决策、待办事项和关联项目。"
    "你会仔细识别对话中的参与者和专业术语，准确提取结构化信息。"
    "注意：直接输出提取结果，不要输出任何前缀说明或开场白。"
)

_SYSTEM_MSG_KEYWORDS = [
    "加入了群聊", "移出了群聊", "修改群名为", "修改群头像",
    "设置了群公告", "群主", "管理员", "消息被撤回",
]

_TZ = timezone(timedelta(hours=8))


class ChatDigestError(RuntimeError):
    """聊天提炼错误。"""


class ChatDigester:
    """飞书聊天记录 → 结构化知识提炼。"""

    def __init__(self, bundle: ConfigBundle) -> None:
        self._bundle = bundle
        self._client = FeishuClient(as_user=True)
        self._llm = LLMService(bundle)
        self._wiki_root = self._resolve_wiki_root()
        self._dedup_path = resolve_dedup_path(
            bundle, "chat_digest.dedup_index", "data/dedup/chat_digest_index.json")
        self._dedup_index_cache: Optional[Dict[str, Any]] = None

    # ── 对外接口 ────────────────────────────────────────────

    def digest(self, *,
               group: str = "",
               user: str = "",
               time_range: str = "",
               output: str = "to_source",
               force: bool = False,
               dry_run: bool = False) -> Dict[str, Any]:
        """提炼指定群聊或用户聊天记录。"""
        if not group and not user:
            return {"status": "error", "error": "需要 --group 或 --user"}

        # 1. 解析目标
        identifier, target_name, target_type = "", "", ""
        try:
            if group:
                identifier = self._client.search_group_by_name(group)
                if not identifier:
                    return {"status": "error", "error": f"未找到群聊: {group}"}
                target_name, target_type = group, "group"
            else:
                identifier = self._resolve_user_open_id(user)
                if not identifier:
                    return {"status": "error", "error": f"未找到用户: {user}"}
                target_name, target_type = user, "user"
        except FeishuClientError as e:
            return {"status": "error", "error": f"查找失败: {e}"}

        # 2. 解析时间范围
        time_start, time_end = self._parse_time_range(time_range)

        # 3. 排重
        dedup_key = f"{identifier}|{time_start}|{time_end}"
        if not force:
            existing = self._check_dedup(dedup_key)
            if existing:
                return {
                    "status": "skipped", "reason":
                    f"⏭️ 已提取于 {existing.get('extracted_at', '?')}，使用 --force 覆盖",
                    "output": existing.get("output_path", ""),
                }

        # 4. 拉取消息
        try:
            if target_type == "group":
                raw_messages = self._client.fetch_all_messages(
                    identifier, time_start=time_start, time_end=time_end,
                    max_messages=MAX_MESSAGES)
            else:
                raw_messages = self._client.fetch_all_messages(
                    user_id=identifier, time_start=time_start, time_end=time_end,
                    max_messages=MAX_MESSAGES)
        except FeishuClientError as e:
            return {"status": "error", "error": f"拉取消息失败: {e}"}
        if not raw_messages:
            return {"status": "error", "error": "该时间范围内无消息"}

        # 5. 补全发送者
        raw_messages = self._client.batch_enrich_messages(raw_messages)

        # 5b. 图片理解（下载 → 多模态分析 → 补描述）
        fng = getattr(self._bundle, "feishu_ingest", None)
        chat_digest_cfg = fng.get("chat_digest", {}) if fng else {}
        img_cfg = chat_digest_cfg.get("image_understanding", {}) or {}
        img_enabled = img_cfg.get("enabled", True)
        img_max = img_cfg.get("max_per_run", 10)
        image_descriptions: Dict[str, str] = {}
        if img_enabled:
            analyzer = MessageImageAnalyzer(
                self._client, self._llm,
                cache_dir=Path(self._bundle.root) / "data" / "image_analysis",
                enabled=True, max_per_run=img_max,
            )
            for msg in raw_messages[:MAX_MESSAGES_IN_PROMPT]:
                if msg.get("msg_type") == "image":
                    desc = analyzer.describe_dict_message(msg)
                    if desc:
                        image_descriptions[msg.get("message_id", "")] = desc

        # 6. 格式化（按消息条数截断）
        conversation = self._format_conversation(
            raw_messages, identifier, image_descriptions=image_descriptions)

        # 7. AI 提炼
        try:
            wiki_context = self._load_wiki_context()
            extracted = self._call_llm(conversation, wiki_context, target_name,
                                        message_count=len(raw_messages))
        except LLMProviderError as e:
            logger.warning("AI 提炼 LLM 调用失败 [%s]: %s", target_name, e)
            return {"status": "error", "error": f"AI 提炼失败: {e}"}
        except Exception as e:
            logger.error("AI 提炼意外失败 [%s]: %s", target_name, e, exc_info=True)
            return {"status": "error", "error": f"AI 提炼失败: {e}"}

        # 8. 生成输出（注入 frontmatter 元数据）
        output_md = self._build_markdown(extracted, target_name, target_type,
                                          identifier, time_start, time_end,
                                          len(raw_messages), now_iso())
        topic = extracted.get("topic", target_name)
        date_str = datetime.now(_TZ).strftime("%Y%m%d")
        clean_topic = sanitize_title(topic)
        filename = f"{date_str}-对话记录提取-{clean_topic}.md"
        fingerprint = hashlib.sha256(output_md[:500].encode()).hexdigest()

        # 9. 路由
        if output == "to_source":
            route = self._classify(extracted)
            output_path = resolve_source_sub_dir(self._bundle, route,
                                                 filename)
        else:
            output_path = Path(output)
            route = ""

        # 10. 写入
        if dry_run:
            return {
                "status": "dry_run", "target": target_name, "chat_id": identifier,
                "message_count": len(raw_messages), "route": route,
                "output": str(output_path), "topic": topic,
                "content_preview": output_md[:300],
            }

        try:
            safe_write_text(output_path, output_md, self._bundle,
                            allow_existing_outside=True)
        except OSError as e:
            logger.error("写入文件失败 [%s]: %s", output_path, e)
            return {"status": "error", "error": f"写入失败: {e}"}

        # 11. 更新排重（含 fingerprint）
        self._update_dedup(dedup_key, identifier, target_name, time_start, time_end,
                           fingerprint, topic, str(output_path))

        return {
            "status": "success", "target": target_name, "chat_id": identifier,
            "message_count": len(raw_messages), "route": route,
            "output": str(output_path), "topic": topic,
        }

    def digest_from_config(self, **kwargs) -> List[Dict[str, Any]]:
        """从配置文件读取目标列表并批量提炼。"""
        cfg = self._bundle.feishu_ingest or {}
        targets = cfg.get("chat_digest", {}).get("targets", [])
        return [
            r for t in targets if t.get("enabled", True)
            for r in [self.digest(
                group=t["chat_name"] if t.get("type") == "group" else "",
                user=t["user_name"] if t.get("type") == "user" else "",
                **kwargs
            )]
        ]

    def list_available_groups(self, keyword: str = "") -> List[Dict[str, str]]:
        """列出可用的群聊（供交互模式使用）。"""
        try:
            chats = self._client.list_chats()
            return [
                {"chat_id": c.get("chat_id", ""), "name": c.get("name", ""),
                 "member_count": c.get("member_count", 0)}
                for c in chats
                if not keyword or keyword.lower() in c.get("name", "").lower()
            ]
        except FeishuClientError:
            return []

    # ── 内部方法 ──────────────────────────────────────────

    def _resolve_user_open_id(self, user_name: str) -> Optional[str]:
        user = self._client.search_user(user_name)
        return user.get("user_id", "") or None if user else None

    def _parse_time_range(self, time_range: str) -> Tuple[str, str]:
        """解析时间范围，返回带时区的 (start, end) ISO 字符串。"""
        now = datetime.now(_TZ)
        if not time_range:
            cfg = self._bundle.feishu_ingest or {}
            days = cfg.get("chat_digest", {}).get("default_range_days", DEFAULT_RANGE_DAYS)
            start = now - timedelta(days=days)
            return start.isoformat(), now.isoformat()

        if "~" in time_range:
            parts = time_range.split("~", 1)
            start_s, end_s = parts[0].strip(), parts[1].strip() if parts[1] else ""
            start = self._coerce_iso(start_s, start_of_day=True)
            end = self._coerce_iso(end_s, start_of_day=False) if end_s else now
            return start.isoformat(), end.isoformat()

        try:
            days = int(time_range)
            start = now - timedelta(days=days)
            return start.isoformat(), now.isoformat()
        except ValueError:
            print(f"[chat-digest] 无法解析时间范围 '{time_range}'，使用默认 {DEFAULT_RANGE_DAYS} 天", file=sys.stderr)
            start = now - timedelta(days=DEFAULT_RANGE_DAYS)
            return start.isoformat(), now.isoformat()

    @staticmethod
    def _coerce_iso(s: str, *, start_of_day: bool) -> datetime:
        dt = datetime.fromisoformat(s) if "T" in s else datetime.strptime(s, "%Y-%m-%d")
        if dt.tzinfo is None:
            suffix = "T00:00:00+08:00" if start_of_day else "T23:59:59+08:00"
            if len(s) == 10:
                dt = datetime.fromisoformat(s + suffix)
            else:
                dt = dt.replace(tzinfo=_TZ)
        return dt

    @staticmethod
    def _format_conversation(
        messages: List[Dict[str, Any]], _identifier: str,
        image_descriptions: Optional[Dict[str, str]] = None,
    ) -> str:
        """格式化消息流，最多注入 MAX_MESSAGES_IN_PROMPT 条。"""
        lines = []
        for msg in messages[:MAX_MESSAGES_IN_PROMPT]:
            content = msg.get("content", "") or ""
            body = msg.get("body", {})
            if isinstance(body, dict) and not content:
                content = body.get("content", "") or ""

            if any(kw in content for kw in _SYSTEM_MSG_KEYWORDS):
                continue
            if "invited" in content.lower():
                continue

            msg_type = msg.get("msg_type", "")
            sender_name = ""
            sender = msg.get("sender", {})
            if isinstance(sender, dict):
                sender_name = sender.get("name", "") or sender.get("localized_name", "")

            ts = msg.get("create_time", "")
            time_str = ""
            if ts:
                if isinstance(ts, str) and len(ts) >= 16 and ts[4] == "-":
                    time_str = ts[5:16]
                else:
                    try:
                        dt = datetime.fromtimestamp(int(ts), tz=_TZ)
                        time_str = dt.strftime("%m-%d %H:%M")
                    except (ValueError, OSError):
                        time_str = str(ts)

            prefix = f"{sender_name} ({time_str})" if sender_name else f"({time_str})"
            if msg_type == "image":
                desc = (image_descriptions or {}).get(msg.get("message_id", ""))
                lines.append(f"{prefix}: （图片：{desc}）" if desc else f"{prefix}: [图片]")
            elif msg_type == "file":
                lines.append(f"{prefix}: [文件]")
            else:
                lines.append(f"{prefix}: {content.strip()}")

        return "\n".join(lines)

    def _call_llm(self, conversation: str, wiki_context: str,
                  target_name: str, *, message_count: int = 0) -> Dict[str, Any]:
        prompt = f"""{SYSTEM_PROMPT}

以下是从飞书群聊「{target_name}」中抽取的最近 {message_count} 条消息中的前 {MAX_MESSAGES_IN_PROMPT} 条对话流水。

## 背景知识（Wiki 上下文）

{wiki_context or "（无）"}

## 对话内容

{conversation}

## 任务

1. 主题 — 1-3 个主要讨论话题
2. 决策 — 有明确结论的关键决策点
3. 待办 — 明确的行动项（含责任人和截止时间）
4. 关联项目/OP — 与哪个项目或目标相关
5. 风险/问题 — 提出但未解决的问题

## 输出格式

TOPIC: <讨论主题，一句话概括>
DECISIONS:
- <决策内容>
TODOS:
- <事项> | @<负责人> | <截止时间>
RELATED: <关联项目/OP，无则填"无">
RISKS:
- <风险内容>
SUMMARY:
<100-200字的讨论总结>"""

        text = self._llm.generate(
            prompt, route_context={"input_type": "text"},
            temperature=0.1, max_tokens=4096).text
        return self._parse_extraction(text)

    @staticmethod
    def _parse_extraction(text: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "topic": "", "decisions": [], "todos": [],
            "related": "", "risks": [], "summary": "", "raw": text,
        }
        section_map = {
            "decisions": "decisions", "todos": "todos",
            "risks": "risks", "summary": "summary",
        }
        current_section = ""
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            prefix, sep, _ = line.partition(":") if ":" in line else (line, "", "")
            if line.startswith("TOPIC:"):
                result["topic"] = line[len("TOPIC:"):].strip()
            elif line.startswith("DECISIONS:"):
                current_section = "decisions"
            elif line.startswith("TODOS:"):
                current_section = "todos"
            elif line.startswith("RELATED:"):
                result["related"] = line[len("RELATED:"):].strip()
            elif line.startswith("RISKS:"):
                current_section = "risks"
            elif line.startswith("SUMMARY:"):
                current_section = "summary"
            elif current_section in section_map and line.startswith("- "):
                key = section_map[current_section]
                if key == "summary":
                    result[key] += (" " if result[key] else "") + line[2:]
                else:
                    result[key].append(line[2:])
            elif current_section == "summary":
                result["summary"] += (" " if result["summary"] else "") + line
        return result

    @staticmethod
    def _build_markdown(extracted: Dict[str, Any], target_name: str,
                         target_type: str, chat_id: str,
                         time_start: str, time_end: str,
                         msg_count: int, now_iso: str) -> str:
        from iris.core.frontmatter import inject_frontmatter

        src_label = "群聊" if target_type == "group" else "单聊"
        topic = extracted.get("topic", target_name)
        today = now_iso[:10] if len(now_iso) >= 10 else now_iso

        # ── 构建 frontmatter ──────────────────────────────
        _fm_fields = {
            "title": f"对话记录提取 - {topic}",
            "date": today,
            "type": "对话提取",
            "source_chat": target_name,
            "chat_id": chat_id,
            "chat_type": target_type,
            "message_count": msg_count,
            "time_start": time_start,
            "time_end": time_end,
            "extracted_at": now_iso,
        }

        lines = [
            f"# 对话记录提取 - {topic}",
            "",
            "## 文档信息",
            f"- 提取时间：{now_iso}",
            f"- 聊天时间范围：{time_start} ~ {time_end}",
            f"- 来源{src_label}：{target_name}（{chat_id}）",
            f"- 消息数量：{msg_count} 条",
            "",
            "## 讨论总结",
            extracted.get("summary", ""),
            "",
        ]

        for section, title in [("decisions", "## 关键决策"), ("risks", "## 风险/问题")]:
            items = extracted.get(section, [])
            if items:
                lines.extend([title] + [f"- {d}" for d in items] + [""])

        todos = extracted.get("todos", [])
        if todos:
            lines.extend([
                "## 待办事项",
                "| 事项 | 负责人 | 截止时间 |",
                "|------|--------|---------|",
            ])
            for todo in todos:
                parts = [p.strip() for p in todo.split("|")]
                if len(parts) >= 3:
                    # 字段数超过3时，多余部分合并到事项列（防止LLM输出含|的任务名）
                    if len(parts) > 3:
                        task = "|".join(parts[:-2])
                        owner, deadline = parts[-2], parts[-1]
                    else:
                        task, owner, deadline = parts[0], parts[1], parts[2]
                    lines.append(f"| {task} | {owner} | {deadline} |")
                else:
                    lines.append(f"| {todo} | | |")
            lines.append("")

        related = extracted.get("related", "")
        if related and related != "无":
            lines.extend(["## 关联项目/OP", related, ""])

        lines.extend([
            "---",
            f"*由 Iris chat-digest 于 {now_iso} 提取*",
            f"*聊天数据来源：飞书{src_label}「{target_name}」*",
        ])
        body = "\n".join(lines)

        # ── 注入 frontmatter ──────────────────────────────
        try:
            return inject_frontmatter(body, _fm_fields)
        except Exception:
            return body  # 降级：返回无 frontmatter 的正文

    def _classify(self, extracted: Dict[str, Any]) -> str:
        has_decisions = bool(extracted.get("decisions"))
        has_todos = bool(extracted.get("todos"))
        has_risks = bool(extracted.get("risks"))
        return "05-会议纪要" if (has_decisions and (has_todos or has_risks)) else "04-讨论思考"

    def _resolve_wiki_root(self) -> Path:
        if self._bundle.wiki:
            return Path(self._bundle.wiki["wiki_root"]).resolve()
        raise ValueError("Wiki 配置缺失：请在 config/wiki.json 中设置 wiki_root")

    def _load_wiki_context(self) -> str:
        from iris.wiki.context_loader import WikiContextLoader
        root = self._wiki_root
        if not root.exists():
            return ""
        loader = WikiContextLoader(root)
        return loader.load_context(
            page_types=["domain", "concept", "project"],  # 跳过人物
            max_chars_per_page=2000,
            max_pages=10,
            label_prefix=False,
        )

    # ── 排重 ────────────────────────────────────────────────

    def _check_dedup(self, dedup_key: str) -> Optional[Dict[str, Any]]:
        if self._dedup_index_cache is None:
            self._dedup_index_cache = load_dedup_index(self._dedup_path)
        lookup = {it.get("dedup_key"): it for it in self._dedup_index_cache.get("items", []) if it.get("dedup_key")}
        return lookup.get(dedup_key)

    def _update_dedup(self, dedup_key: str, identifier: str, target_name: str,
                       time_start: str, time_end: str,
                       fingerprint: str, topic: str, output_path: str) -> None:
        index = load_dedup_index(self._dedup_path)  # 写操作总是读最新状态
        upsert_dedup_item(index, dedup_key, {
            "dedup_key": dedup_key,
            "chat_id": identifier,
            "target_name": target_name,
            "time_start": time_start,
            "time_end": time_end,
            "fingerprint": fingerprint,
            "topic": topic,
            "output_path": output_path,
            "extracted_at": now_iso(),
        })
        save_dedup_index(self._dedup_path, index)
        self._dedup_index_cache = None  # 写后清除缓存，下次重新加载
