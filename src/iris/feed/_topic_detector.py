"""信息汇聚管道 — 话题检测 + 跨群聚合。

两步法：
  Step 1: 规则分割 — 同群内按 30min 窗口切分候选话题组
  Step 2: LLM 聚合 — 跨群合并 + 命名 + 摘要 + 历史匹配
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.feed._types import DetectedTopic, Quote, RawMessage, SourceRef
from iris.feed.feed_config import FeedConfig

logger = logging.getLogger(__name__)

# ── 规则分割 ──────────────────────────────────────────────


def _segment_by_time(
    messages: List[RawMessage],
    window_minutes: int = 30,
) -> List[List[RawMessage]]:
    """按时间窗口将同群消息切分为候选话题组。

    策略：相邻消息间隔超过 window_minutes 分钟 → 断点，
    同一窗口内的消息归为一个候选话题组。
    """
    if not messages:
        return []
    # 按时间排序
    sorted_msgs = sorted(messages, key=lambda m: m.send_time)
    segments = []
    current_seg = [sorted_msgs[0]]
    for i in range(1, len(sorted_msgs)):
        gap = (sorted_msgs[i].send_time - sorted_msgs[i - 1].send_time).total_seconds() / 60
        if gap > window_minutes:
            segments.append(current_seg)
            current_seg = [sorted_msgs[i]]
        else:
            current_seg.append(sorted_msgs[i])
    if current_seg:
        segments.append(current_seg)
    return segments


def _build_candidate_groups(
    messages_by_chat: Dict[str, List[RawMessage]],
    window_minutes: int = 30,
    topic_min_messages: int = 2,
) -> List[Tuple[str, List[RawMessage]]]:
    """对所有会话的消息做规则分割，返回候选话题组列表。

    Returns:
        [(chat_name, candidate_messages), ...]
    """
    candidates = []
    for chat_id, msgs in messages_by_chat.items():
        if not msgs:
            continue
        chat_name = msgs[0].chat_name or chat_id[:12] + "..."
        segments = _segment_by_time(msgs, window_minutes)
        for seg in segments:
            if len(seg) >= topic_min_messages:
                candidates.append((chat_name, seg))
    return candidates


# ── LLM 聚合 Prompt ───────────────────────────────────────

_TOPIC_DETECT_PROMPT = """你是一个信息分析助手。请从以下飞书群聊消息中提取出有价值的话题。

## 消息来源
多条候选消息组来自不同的飞书群聊或单聊。每组消息可能是某个话题的讨论片段。

## 当前 OKR 目标
{okr_context}

## 任务
1. **判断**哪些消息组属于同一话题（跨群/跨聊出现需合并）
2. **排除**无实质内容的消息组（闲聊、寒暄、重复通知等）
3. **命名**每个话题（简洁中文标题，≤15字）
4. **摘要**每个话题的核心内容（2-3句话）
5. **判断**与已知历史话题的关系
6. **匹配 OKR**：判断话题内容与哪个 OKR/KR 相关（基于语义，不要硬匹配关键词），给出匹配强度

## 输入消息
{input_messages}

## 历史话题（用于判断是否为已有话题的更新）
{history_topics}

## 输出格式
请严格输出 JSON 数组（不要包含其他文字）：
```json
[
  {{
    "title": "话题标题",
    "summary": "核心摘要（2-3句话）",
    "key_status": "当前关键状态或进展",
    "discussion_points": ["讨论要点1", "讨论要点2"],
    "decisions": ["已明确的决策或结论"],
    "quotes": [{{"text": "原始引述", "speaker": "发言人", "time": "时间"}}],
    "participants": ["参与人1", "参与人2"],
    "group_indices": [0, 2],
    "is_update": false,
    "update_of": null,
    "is_valuable": true,
    "okr_match": {{
      "kr_id": "O2-KR3",
      "match_strength": "strong",
      "reason": "讨论内容与作业域原子化能力建设直接相关"
    }}
  }}
]
```
- `group_indices`: 属于此话题的消息组序号（从 0 开始）
- `is_update`: 是否是对历史话题的更新（true/false）
- `update_of`: 如果是更新，引用的历史话题标题
- `is_valuable`: 是否值得生成简报（无实质内容则 false）
- `okr_match`: 匹配的 OKR/KR（kr_id 如 O1-KR1 / O2 / null 表示不匹配；match_strength 为 strong/weak/none；reason 简述匹配理由）

只输出有价值的话题（is_valuable=true），不输出无价值的话题。"""


# ── 历史话题匹配 ──────────────────────────────────────────

def _load_history_topics(brief_dir: Path, max_count: int = 50) -> List[Dict[str, Any]]:
    """从本地简报目录加载最近的话题列表。"""
    topics = []
    if not brief_dir.exists():
        return topics
    for month_dir in sorted(brief_dir.iterdir(), reverse=True):
        if not month_dir.is_dir():
            continue
        for f in sorted(month_dir.glob("*-简报-*.md"), reverse=True):
            try:
                content = f.read_text(encoding="utf-8")
                # 提取 title（# 后面的内容）
                title_match = re.search(r'^# (.+)$', content, re.MULTILINE)
                title = title_match.group(1).strip() if title_match else f.stem
                # 提取 topic_id
                topic_match = re.search(r'topic_id:\s*(\S+)', content)
                topic_id = topic_match.group(1) if topic_match else f.stem
                topics.append({
                    "title": title,
                    "topic_id": topic_id,
                    "file": f.name,
                    "path": str(f.relative_to(brief_dir)),
                })
            except Exception:
                continue
            if len(topics) >= max_count:
                break
        if len(topics) >= max_count:
            break
    return topics


# ── 主类 ──────────────────────────────────────────────────


class TopicDetector:
    """话题检测器 — 规则分割 + LLM 聚合。"""

    def __init__(
        self,
        llm_service,  # LLMService
        brief_dir: Path,
        topic_config: Optional[Dict[str, Any]] = None,
        okr_context: str = "",
    ):
        self._llm = llm_service
        self._brief_dir = brief_dir
        self._okr_context = okr_context
        cfg = topic_config or {}
        self._window_minutes = cfg.get("time_window_minutes", 30)
        self._topic_min_messages = cfg.get("topic_min_messages", 2)
        self._max_topics = cfg.get("max_topics_per_run", 30)

    def detect(
        self,
        messages_by_chat: Dict[str, List[RawMessage]],
    ) -> List[DetectedTopic]:
        """执行话题检测。

        Args:
            messages_by_chat: {chat_id: [过滤后的消息]}

        Returns:
            检测到的话题列表
        """
        total_msg_count = sum(len(v) for v in messages_by_chat.values())
        if total_msg_count == 0:
            return []

        # Step 1: 规则分割
        candidates = _build_candidate_groups(
            messages_by_chat, self._window_minutes, self._topic_min_messages,
        )
        logger.info("规则分割: %d 个候选话题组", len(candidates))
        if not candidates:
            return []

        # Step 2a: 消息太少 → 直接用规则结果生成话题
        if total_msg_count < 5 and len(candidates) <= 1:
            return self._simple_detect(candidates)

        # Step 2b: LLM 聚合
        history = _load_history_topics(self._brief_dir)
        return self._llm_detect(candidates, history, self._okr_context)

    def _simple_detect(self, candidates: List[Tuple[str, List[RawMessage]]]) -> List[DetectedTopic]:
        """简单话题检测（消息量少时不调 LLM）。"""
        topics = []
        for idx, (chat_name, msgs) in enumerate(candidates):
            # 用第一条消息的内容作为标题
            first_content = msgs[0].content.strip()
            title = _shorten_title(first_content)
            # 聚合所有消息内容
            combined = " ".join([m.content for m in msgs])
            summary = combined[:200] if len(combined) > 200 else combined
            # 提取参与者
            participant_set = {m.sender_name for m in msgs if m.sender_name}
            # 提取发言原文（最多3条）
            quotes = [
                Quote(text=m.content[:100], speaker=m.sender_name, time=m.send_time.strftime("%m-%d %H:%M"))
                for m in msgs[:3]
            ]
            topics.append(DetectedTopic(
                topic_id=f"feed-{datetime.now().strftime('%Y%m%d')}-{idx + 1:03d}",
                title=title,
                summary=summary,
                key_status="",
                discussion_points=[],
                decisions=[],
                quotes=quotes,
                participants=list(participant_set),
                messages=msgs,
                source_chats=[SourceRef(type="group", name=chat_name, msg_count=len(msgs))],
            ))
        return topics

    def _llm_detect(
        self,
        candidates: List[Tuple[str, List[RawMessage]]],
        history: List[Dict[str, Any]],
        okr_context: str = "",
    ) -> List[DetectedTopic]:
        """调用 LLM 进行话题聚合。"""
        # 构建输入消息文本
        input_lines = []
        for idx, (chat_name, msgs) in enumerate(candidates):
            input_lines.append(f"### 消息组 {idx} (来源: {chat_name})")
            for m in msgs[:10]:  # 每组最多取 10 条
                input_lines.append(f"[{m.send_time.strftime('%m-%d %H:%M')}] {m.sender_name}: {m.content[:200]}")
            input_lines.append("")
        input_text = "\n".join(input_lines)

        # 历史话题
        if history:
            history_text = "\n".join([
                f"- [{h['title']}] (topic_id: {h['topic_id']}, 最后版本: {h['file']})"
                for h in history[:30]
            ])
        else:
            history_text = "（无历史话题）"

        prompt = _TOPIC_DETECT_PROMPT.format(
            input_messages=input_text,
            history_topics=history_text,
            okr_context=okr_context or "（无可用的 OKR 文档）",
        )

        try:
            result = self._llm.generate(
                prompt,
                route_context={"input_type": "text", "task_type": "analysis", "complexity": "standard"},
                temperature=0.3,
                max_tokens=4096,
                extra_body={"thinking": {"type": "disabled"}},
            )
            topics = self._parse_llm_response(result.text, candidates)
            return topics[:self._max_topics]
        except Exception as e:
            logger.error("LLM 话题检测失败，退回简单检测: %s", e)
            return self._simple_detect(candidates)

    def _parse_llm_response(
        self,
        text: str,
        candidates: List[Tuple[str, List[RawMessage]]],
    ) -> List[DetectedTopic]:
        """解析 LLM 输出 JSON。"""
        # 提取 JSON 块
        json_match = re.search(r'```json\s*([\s\S]*?)```', text)
        if json_match:
            json_str = json_match.group(1)
        else:
            # 尝试直接找 JSON 数组
            bracket_start = text.find("[")
            bracket_end = text.rfind("]")
            if bracket_start >= 0 and bracket_end > bracket_start:
                json_str = text[bracket_start:bracket_end + 1]
            else:
                json_str = text

        try:
            items = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("LLM 输出非 JSON，退回简单检测: %s", text[:200])
            return self._simple_detect(candidates)

        topics = []
        exec_date = datetime.now().strftime("%Y%m%d")
        for idx, item in enumerate(items):
            if not item.get("is_valuable", True):
                continue
            # 聚合该话题的消息
            group_indices = item.get("group_indices", [idx])
            all_msgs = []
            raw_sources: dict[str, int] = {}  # name -> msg_count（合并同名来源）
            for gi in group_indices:
                if gi < len(candidates):
                    chat_name, msgs = candidates[gi]
                    all_msgs.extend(msgs)
                    raw_sources[chat_name] = raw_sources.get(chat_name, 0) + len(msgs)
            all_sources = [
                SourceRef(type="group", name=name, msg_count=count)
                for name, count in raw_sources.items()
            ]
            if not all_msgs:
                all_msgs = candidates[idx][1] if idx < len(candidates) else []
                all_sources = [SourceRef(
                    type="group",
                    name=candidates[idx][0] if idx < len(candidates) else "未知",
                    msg_count=len(all_msgs),
                )]

            # 构建 DetectedTopic
            quotes = [
                Quote(text=q.get("text", ""), speaker=q.get("speaker", ""), time=q.get("time", ""))
                for q in item.get("quotes", [])[:5]
            ]
            # OKR 匹配
            okr_match = item.get("okr_match") or {}
            kr_id = okr_match.get("kr_id", "")
            match_strength: str = okr_match.get("match_strength", "none")
            okr_tags = []
            if kr_id:
                okr_tags = [kr_id]
            elif match_strength != "none":
                # 只有强度但没具体 kr_id，按强度标记
                pass
            topic = DetectedTopic(
                topic_id=f"feed-{exec_date}-{idx + 1:03d}",
                title=item.get("title", f"话题{idx + 1}"),
                summary=item.get("summary", ""),
                key_status=item.get("key_status", ""),
                discussion_points=item.get("discussion_points", []),
                decisions=item.get("decisions", []),
                quotes=quotes,
                participants=item.get("participants", []),
                messages=all_msgs,
                source_chats=all_sources,
                is_update=item.get("is_update", False),
                previous_versions=[item["update_of"]] if item.get("update_of") else [],
                okr_tags=okr_tags,
                okr_match_strength=match_strength,  # type: ignore[assignment]
            )
            topics.append(topic)
        return topics


def _shorten_title(text: str, max_len: int = 15) -> str:
    """从消息文本中截取简短的标题。"""
    # 去掉 @mention 和链接
    text = re.sub(r'@\S+', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"
