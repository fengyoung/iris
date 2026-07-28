"""信息汇聚管道 — 话题检测 + 跨群聚合。

两阶段 LLM 架构：
  Phase 1 (轻量): 规则分割 → LLM 检测+合并+命名+OKR匹配
  Phase 2 (深度): 逐话题独立 LLM 深度摘要（并发执行）

利用 deepseek-v4-flash 1M 上下文窗口，不再截断消息内容。
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.feed._types import DetectedTopic, Quote, RawMessage, SourceRef

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


# ── Phase 1 Prompt: 话题检测+合并+命名+OKR匹配 ─────────────

_PHASE1_DETECT_PROMPT = """你是一个信息分析助手。请从以下飞书群聊消息中识别有价值的话题。

## 当前 OKR 目标
{okr_context}

## 任务
1. **合并**：判断哪些消息组属于同一话题（跨群出现需合并）
2. **排除**：无实质内容的消息组（闲聊、寒暄、纯通知等）标记 is_valuable=false
3. **命名**：每个话题取简洁中文标题（≤15字）
4. **OKR 匹配**：判断话题内容与哪个 KR 相关（语义匹配），给出匹配强度和理由

## 输入消息
{input_messages}

## 历史话题（用于判断是否为已有话题的更新）
{history_topics}

## 输出格式
严格输出 JSON 数组：
```json
[
  {{
    "title": "话题标题（≤15字）",
    "group_indices": [0, 2],
    "is_valuable": true,
    "is_update": false,
    "update_of": null,
    "okr_match": {{
      "kr_id": "O2-KR3",
      "match_strength": "strong",
      "reason": "简述匹配理由"
    }}
  }}
]
```
- `group_indices`: 属于此话题的消息组序号（从 0 开始）
- `is_valuable`: 是否值得生成简报（闲聊/无实质内容为 false）
- `is_update`: 是否是对历史话题的更新
- `update_of`: 引用的历史话题标题（is_update=true 时必填）
- `okr_match.kr_id`: 匹配的 KR ID 如 O1-KR1，不匹配则 null
- `okr_match.match_strength`: strong / weak / none

只输出有价值的话题（is_valuable=true），最多 {max_topics} 个。"""


# ── Phase 2 Prompt: 逐话题深度摘要 ────────────────────────

_PHASE2_DEEP_SUMMARIZE_PROMPT = """你是一个信息分析助手。请对以下飞书群聊讨论进行深度整理。

## 话题
**{topic_title}**

## 群聊消息（完整原文）
{full_messages}

## 当前 OKR
{okr_context}

## 任务
请基于以上消息原文，深度提炼以下内容：

1. **摘要** (summary): 3-5 句话概括讨论的核心内容、背景和结论
2. **关键状态** (key_status): 一句话描述当前进展或所处阶段
3. **讨论要点** (discussion_points): 3-6 条，每一条格式为 `{{"point": "要点标题", "detail": "具体讨论内容（1-2句）", "speaker": "主要发言人"}}`
4. **决策** (decisions): 已明确的结论或行动项，每条格式为 `{{"content": "决策内容", "by": "决策人"}}`
5. **引述** (quotes): 3-5 条关键发言原文，每条格式为 `{{"text": "原文", "speaker": "发言人", "time": "时间"}}`

## 输出格式
严格输出 JSON（不要包含其他文字）：
```json
{{
  "summary": "话题摘要（3-5句话）",
  "key_status": "当前进展阶段",
  "discussion_points": [
    {{"point": "要点标题", "detail": "具体讨论内容", "speaker": "发言人"}}
  ],
  "decisions": [
    {{"content": "决策内容", "by": "决策人"}}
  ],
  "quotes": [
    {{"text": "关键发言原文", "speaker": "发言人", "time": "时间"}}
  ]
}}
```"""


# ── 历史话题加载 ──────────────────────────────────────────

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
                title_match = re.search(r'^# (.+)$', content, re.MULTILINE)
                title = title_match.group(1).strip() if title_match else f.stem
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


# ── JSON 提取 ─────────────────────────────────────────────

def _extract_json(text: str) -> Optional[str]:
    """从 LLM 输出中提取 JSON 字符串（对象或数组）。

    以最先出现的边界符为准，处理 JSON 对象内含嵌套数组的情况。
    """
    json_match = re.search(r'```json\s*([\s\S]*?)```', text)
    if json_match:
        return json_match.group(1)
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        # 对象在外层（Phase 2 输出）或数组在外层但对象先出现
        if bracket_start < 0 or brace_start < bracket_start:
            return text[brace_start:brace_end + 1]
    if bracket_start >= 0 and bracket_end > bracket_start:
        # 数组在外层（Phase 1 输出）
        return text[bracket_start:bracket_end + 1]
    return None


def _parse_json_safe(text: str, fallback_label: str = "LLM输出") -> Any:
    """安全解析 JSON，失败时记录日志并返回 None。"""
    json_str = _extract_json(text)
    if json_str is None:
        logger.warning("%s 中未找到 JSON: %s", fallback_label, text[:200])
        return None
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        logger.warning("%s JSON 解析失败: %s", fallback_label, json_str[:200])
        return None


# ── 主类 ──────────────────────────────────────────────────


class TopicDetector:
    """话题检测器 — 规则分割 + 两阶段 LLM。

    Phase 1: 检测+合并+命名+OKR 匹配（轻量，单次 LLM 调用）
    Phase 2: 逐话题深度摘要（重量，并发 LLM 调用）
    """

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
        # Phase 2 并发度：每个话题独立调用 LLM，默认 4 并发
        self._phase2_workers = cfg.get("phase2_workers", 4)
        # 性能阈值：消息量太少时跳过 Phase 2（直接用完整消息做 _simple_detect）
        self._skip_llm_msg_count = cfg.get("skip_llm_msg_count", 3)

    def detect(
        self,
        messages_by_chat: Dict[str, List[RawMessage]],
    ) -> List[DetectedTopic]:
        """执行话题检测。

        Args:
            messages_by_chat: {chat_id: [过滤后的消息]}

        Returns:
            检测到的话题列表（已完成 Phase 2 深度摘要）
        """
        total_msg_count = sum(len(v) for v in messages_by_chat.values())
        if total_msg_count == 0:
            return []

        # Step 1: 规则分割
        candidates = _build_candidate_groups(
            messages_by_chat, self._window_minutes, self._topic_min_messages,
        )
        logger.info("规则分割: %d 个候选话题组 (共 %d 条消息)", len(candidates), total_msg_count)
        if not candidates:
            return []

        # Step 2: 消息太少 → 直接用规则结果（不调 LLM）
        if total_msg_count <= self._skip_llm_msg_count and len(candidates) <= 1:
            return self._simple_detect(candidates)

        # Phase 1: LLM 检测 + 合并 + 命名 + OKR 匹配
        history = _load_history_topics(self._brief_dir)
        topics = self._phase1_detect(candidates, history)

        # Phase 2: 逐话题深度摘要（并发）
        if topics:
            topics = self._phase2_deep_summarize(topics, candidates)

        return topics[:self._max_topics]

    # ── Phase 1: 检测+合并+命名+OKR匹配 ────────────────────

    def _phase1_detect(
        self,
        candidates: List[Tuple[str, List[RawMessage]]],
        history: List[Dict[str, Any]],
    ) -> List[DetectedTopic]:
        """Phase 1 — LLM 检测话题边界、合并、命名和 OKR 匹配。"""
        # 构建输入：完整消息内容，每组最多取 20 条
        input_lines = []
        for idx, (chat_name, msgs) in enumerate(candidates):
            input_lines.append(f"### 消息组 {idx} (来源: {chat_name}, {len(msgs)} 条)")
            for m in msgs[:20]:
                input_lines.append(
                    f"[{m.send_time.strftime('%m-%d %H:%M')}] {m.sender_name}: {m.content}"
                )
            input_lines.append("")
        input_text = "\n".join(input_lines)

        # 历史话题
        if history:
            history_text = "\n".join([
                f"- [{h['title']}] (topic_id: {h['topic_id']})"
                for h in history[:30]
            ])
        else:
            history_text = "（无历史话题）"

        prompt = _PHASE1_DETECT_PROMPT.format(
            input_messages=input_text,
            history_topics=history_text,
            okr_context=self._okr_context or "（无可用的 OKR 文档）",
            max_topics=self._max_topics,
        )

        try:
            result = self._llm.generate(
                prompt,
                route_context={"input_type": "text", "task_type": "analysis", "complexity": "standard"},
                temperature=0.3,
                max_tokens=4096,
                extra_body={"thinking": {"type": "disabled"}},
            )
            items = _parse_json_safe(result.text, "Phase1 LLM")
            if items is None or not isinstance(items, list):
                return self._simple_detect(candidates)
        except Exception as e:
            logger.error("Phase 1 LLM 调用失败，退回简单检测: %s", e)
            return self._simple_detect(candidates)

        topics = []
        exec_date = datetime.now().strftime("%Y%m%d")
        for idx, item in enumerate(items):
            if not item.get("is_valuable", True):
                continue

            # 聚合该话题的消息
            group_indices = item.get("group_indices", [idx])
            all_msgs: List[RawMessage] = []
            raw_sources: Dict[str, int] = {}
            for gi in group_indices:
                if gi < len(candidates):
                    chat_name, msgs = candidates[gi]
                    all_msgs.extend(msgs)
                    raw_sources[chat_name] = raw_sources.get(chat_name, 0) + len(msgs)

            if not all_msgs:
                all_msgs = candidates[idx][1] if idx < len(candidates) else []
                all_sources = [SourceRef(
                    type="group",
                    name=candidates[idx][0] if idx < len(candidates) else "未知",
                    msg_count=len(all_msgs),
                )]
            else:
                all_sources = [
                    SourceRef(type="group", name=name, msg_count=count)
                    for name, count in raw_sources.items()
                ]

            # 收集基础参与者（从消息中，Phase 2 会覆盖）
            participants = list({m.sender_name for m in all_msgs if m.sender_name})

            # OKR 匹配信息
            okr_match = item.get("okr_match") or {}
            kr_id = okr_match.get("kr_id", "")
            match_strength: str = okr_match.get("match_strength", "none")
            okr_tags = [kr_id] if kr_id else []

            topic = DetectedTopic(
                topic_id=f"feed-{exec_date}-{idx + 1:03d}",
                title=item.get("title", f"话题{idx + 1}"),
                summary="",  # Phase 2 填充
                key_status="",  # Phase 2 填充
                discussion_points=[],  # Phase 2 填充
                decisions=[],  # Phase 2 填充
                quotes=[],  # Phase 2 填充
                participants=participants,
                messages=all_msgs,
                source_chats=all_sources,
                is_update=item.get("is_update", False),
                previous_versions=[item["update_of"]] if item.get("update_of") else [],
                okr_tags=okr_tags,
                okr_match_strength=match_strength,
            )
            topics.append(topic)
        return topics

    # ── Phase 2: 逐话题深度摘要 ───────────────────────────

    def _phase2_deep_summarize(
        self,
        topics: List[DetectedTopic],
        candidates: List[Tuple[str, List[RawMessage]]],
    ) -> List[DetectedTopic]:
        """Phase 2 — 对每个话题并发调用 LLM 做深度摘要。

        每个话题得到独立的 LLM 调用，传入该话题关联的完整消息原文。
        不截断消息内容，充分利用 deepseek-v4-flash 1M 上下文窗口。
        """
        def _summarize_one(topic: DetectedTopic, idx: int) -> Optional[DetectedTopic]:
            # 构建该话题的完整消息原文
            msgs = topic.messages
            if not msgs:
                return topic
            # 按时间排序
            sorted_msgs = sorted(msgs, key=lambda m: m.send_time)
            msg_lines = []
            for m in sorted_msgs:
                msg_lines.append(
                    f"[{m.send_time.strftime('%m-%d %H:%M')}] {m.sender_name}: {m.content}"
                )
            full_text = "\n".join(msg_lines)

            prompt = _PHASE2_DEEP_SUMMARIZE_PROMPT.format(
                topic_title=topic.title,
                full_messages=full_text,
                okr_context=self._okr_context or "（无可用的 OKR 文档）",
            )

            try:
                result = self._llm.generate(
                    prompt,
                    route_context={"input_type": "text", "task_type": "analysis", "complexity": "standard"},
                    temperature=0.3,
                    max_tokens=4096,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                data = _parse_json_safe(result.text, f"Phase2 [{topic.title}]")
                if data is None:
                    self._fill_fallback_summary(topic)
                    return topic

                # 填充深度摘要字段
                topic.summary = str(data.get("summary", "") or "")
                topic.key_status = str(data.get("key_status", "") or "")

                # 讨论要点（结构化）
                dp_raw = data.get("discussion_points", [])
                if isinstance(dp_raw, list) and dp_raw:
                    topic.discussion_points = [
                        str(dp.get("point", dp if isinstance(dp, str) else ""))
                        for dp in dp_raw
                    ]

                # 决策
                dec_raw = data.get("decisions", [])
                if isinstance(dec_raw, list) and dec_raw:
                    topic.decisions = [
                        str(d.get("content", d if isinstance(d, str) else ""))
                        for d in dec_raw
                    ]

                # 引述（Phase 2 输出优先）
                quotes_raw = data.get("quotes", [])
                if isinstance(quotes_raw, list) and quotes_raw:
                    topic.quotes = [
                        Quote(
                            text=str(q.get("text", "")),
                            speaker=str(q.get("speaker", "")),
                            time=str(q.get("time", "")),
                        )
                        for q in quotes_raw[:8]
                    ]

                # 参与者（Phase 2 输出覆盖 Phase 1 的基础提取）
                participants_raw = data.get("participants", [])
                if isinstance(participants_raw, list) and participants_raw:
                    phase2_participants = [str(p) for p in participants_raw]
                    # 合并而非覆盖：Phase 1 提取的也保留
                    combined = set(topic.participants) | set(phase2_participants)
                    topic.participants = list(combined)

                return topic
            except Exception as e:
                logger.error("Phase 2 深度摘要失败 [%s]: %s", topic.title, e)
                self._fill_fallback_summary(topic)
                return topic

        # 并发执行 Phase 2
        enriched: List[DetectedTopic] = []
        with ThreadPoolExecutor(max_workers=self._phase2_workers) as executor:
            futures = {
                executor.submit(_summarize_one, topic, idx): idx
                for idx, topic in enumerate(topics)
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    enriched.append(result)

        # 按原始顺序排列
        enriched.sort(key=lambda t: t.topic_id)
        return enriched

    # ── 降级：简单检测 ────────────────────────────────────

    def _simple_detect(self, candidates: List[Tuple[str, List[RawMessage]]]) -> List[DetectedTopic]:
        """简单话题检测（消息量少时直接用规则，不调 LLM）。"""
        topics = []
        exec_date = datetime.now().strftime("%Y%m%d")
        for idx, (chat_name, msgs) in enumerate(candidates):
            first_content = msgs[0].content.strip()
            title = _shorten_title(first_content)
            combined = " ".join([m.content for m in msgs])
            # 不再截断到 200 字，保留完整内容
            summary = combined if len(combined) <= 500 else combined[:497] + "..."
            participant_set = {m.sender_name for m in msgs if m.sender_name}
            quotes = [
                Quote(text=m.content, speaker=m.sender_name,
                      time=m.send_time.strftime("%m-%d %H:%M"))
                for m in msgs[:5]
            ]
            topics.append(DetectedTopic(
                topic_id=f"feed-{exec_date}-{idx + 1:03d}",
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

    @staticmethod
    def _fill_fallback_summary(topic: DetectedTopic) -> None:
        """Phase 2 失败时的兜底：从原始消息生成简单摘要。"""
        if not topic.summary:
            combined = " ".join([m.content for m in topic.messages[:10]])
            topic.summary = combined[:500] if len(combined) > 500 else combined
        if not topic.quotes:
            topic.quotes = [
                Quote(text=m.content, speaker=m.sender_name,
                      time=m.send_time.strftime("%m-%d %H:%M"))
                for m in topic.messages[:5]
            ]


def _shorten_title(text: str, max_len: int = 15) -> str:
    """从消息文本中截取简短的标题。"""
    text = re.sub(r'@\S+', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"
