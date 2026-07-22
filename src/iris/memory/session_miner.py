"""会话模式挖掘器：从多次会话中识别模式，自动晋升为长期记忆。

由 MemoryUpdater 懒触发（距上次 ≥24h 或累积 ≥10 个新会话），
daily-start 兜底调用。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.core.locks import FileLock
from iris.memory import SessionMemoryStore, UserProfileMemoryStore, CorrectionMemoryStore

logger = logging.getLogger(__name__)

_MIN_TOPIC_COUNT_FOR_MINE = 3    # 主题出现 ≥ 3 次才纳入分析
_MIN_CONFIDENCE = 0.5             # LLM 置信度阈值


class SessionPatternMiner:
    """从会话记忆中发现模式并晋升为长期记忆。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._session_store = SessionMemoryStore(config)
        self._profile_memory = UserProfileMemoryStore(config)
        self._correction_memory = CorrectionMemoryStore(config)
        self._llm_service = None

    # ── 公共接口 ────────────────────────────────────────────────

    def mine_and_promote(self) -> Dict[str, Any]:
        """挖掘会话模式并晋升高置信度发现。

        Returns:
            {"mined": bool, "discoveries": [...], "promoted": int, "skipped": int}
        """
        session_data = self._session_store.load()
        if not self._has_enough_data(session_data):
            return {"mined": False, "reason": "会话数据不足", "discoveries": [], "promoted": 0, "skipped": 0}

        discoveries = self._mine_patterns(session_data)
        if not discoveries:
            return {"mined": False, "reason": "未发现新模式", "discoveries": [], "promoted": 0, "skipped": 0}

        # 批量晋升：单次 load-modify-save，避免多次 FileLock 竞争
        profile = self._profile_memory.load()
        prefs = profile.setdefault("user_preferences", {})
        profile_changed = False
        promoted = 0
        skipped = 0

        for disc in discoveries:
            if disc.get("confidence", 0.0) >= _MIN_CONFIDENCE:
                if self._apply_promotion(disc, prefs):
                    promoted += 1
                    profile_changed = True
                else:
                    skipped += 1
            else:
                skipped += 1

        if profile_changed:
            profile["updated_at"] = _now_iso()
            self._profile_memory.save(profile)

        return {
            "mined": True,
            "discoveries": discoveries,
            "promoted": promoted,
            "skipped": skipped,
            "mined_at": _now_iso(),
        }

    # ── 内部 ────────────────────────────────────────────────────

    def _has_enough_data(self, session_data: Dict[str, Any]) -> bool:
        """判断会话数据是否足够进行挖掘。"""
        topics = session_data.get("recent_topics", [])
        questions = session_data.get("recent_questions", [])
        return len(topics) >= 3 or len(questions) >= 5

    def _mine_patterns(self, session_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """用 LLM 从会话数据中挖掘模式。"""
        prompt = self._build_mine_prompt(session_data)
        response = self._call_llm(prompt)
        if not response:
            return []
        return self._parse_mine_response(response)

    def _build_mine_prompt(self, session_data: Dict[str, Any]) -> str:
        """构建会话挖掘 prompt。"""
        questions = session_data.get("recent_questions", [])
        topics = session_data.get("recent_topics", [])
        threads = session_data.get("topic_threads", {})
        summary = session_data.get("recent_summary", "")

        # 高频主题（出现 ≥ MIN_TOPIC_COUNT_FOR_MINE 次）
        frequent_threads = []
        for topic, info in sorted(threads.items(), key=lambda x: -x[1].get("count", 0)):
            count = info.get("count", 0)
            if count >= _MIN_TOPIC_COUNT_FOR_MINE:
                frequent_threads.append(f"  - {topic}（{count}次）")
        freq_text = "\n".join(frequent_threads) if frequent_threads else "无高频主题"

        questions_text = "\n".join(f"  - {q}" for q in questions[:8]) if questions else "无"
        topics_text = "、".join(topics[:12]) if topics else "无"

        # 已有长期记忆摘要
        profile = self._profile_memory.load()
        prefs = profile.get("user_preferences", {})
        existing_likes = "、".join(prefs.get("likes", [])[:5]) or "无"
        existing_notes = "、".join(prefs.get("notes", [])[:5]) or "无"

        return f"""你是一个会话模式分析师。分析以下用户的近期提问模式，识别有价值的长期记忆。

近期问题（最近 8 个）：
{questions_text}

近期主题：
{topics_text}

高频主题线程：
{freq_text}

会话摘要：
{summary}

已有偏好（喜欢）：{existing_likes}
已有备注：{existing_notes}

请识别：

1. **recurring_themes**：反复出现的主题（≥3次），可能值得创建 Wiki 页面的
   [{{"theme": "主题名", "count": N, "suggest_wiki": true/false, "suggest_note": true/false}}]

2. **preference_patterns**：从问题模式中推断的用户偏好
   [{{"pattern": "偏好描述", "evidence": "基于哪些问题得出", "confidence": 0.0-1.0}}]

3. **new_facts**：从对话中提取的新事实信息
   [{{"fact": "事实描述", "category": "工作背景/项目信息/个人偏好/其他"}}]

提取规则：
- 只提取确有证据的模式，不要推测
- 已经存在于"已有偏好"和"已有备注"中的不要重复
- suggest_wiki 仅当主题确实有实质性知识积累时才为 true
- 空列表用 [] 表示

仅输出 JSON：
{{
  "recurring_themes": [],
  "preference_patterns": [],
  "new_facts": [],
  "confidence": 0.0
}}"""

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM 进行会话模式挖掘。"""
        if self._llm_service is None:
            from iris.llm import LLMService
            self._llm_service = LLMService(self._config)

        route_context = {
            "input_type": "text",
            "task_type": "memory_extraction",
            "complexity": "minimal",
            "use_case": "memory",
        }
        try:
            result = self._llm_service.generate(
                prompt,
                route_context=route_context,
                temperature=0,
                use_cache=True,
            )
            return result.text.strip()
        except Exception as exc:
            logger.debug("会话模式挖掘 LLM 调用失败（静默）: %s", exc)
            return None

    def _parse_mine_response(self, text: str) -> List[Dict[str, Any]]:
        """解析 LLM 返回的挖掘结果。"""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return []
        except json.JSONDecodeError:
            json_text = _extract_json_object(text)
            if json_text:
                try:
                    data = json.loads(json_text)
                    if not isinstance(data, dict):
                        return []
                except json.JSONDecodeError:
                    return []
            else:
                return []

        # 组装发现列表，每条带置信度
        discoveries: List[Dict[str, Any]] = []
        overall_confidence = data.get("confidence", 0.5)

        for theme in data.get("recurring_themes", []):
            discoveries.append({
                "type": "recurring_theme",
                "theme": theme.get("theme", ""),
                "count": theme.get("count", 0),
                "suggest_wiki": theme.get("suggest_wiki", False),
                "suggest_note": theme.get("suggest_note", True),
                "confidence": min(overall_confidence + 0.1, 1.0),
            })

        for pattern in data.get("preference_patterns", []):
            discoveries.append({
                "type": "preference_pattern",
                "pattern": pattern.get("pattern", ""),
                "evidence": pattern.get("evidence", ""),
                "confidence": pattern.get("confidence", overall_confidence),
            })

        for fact in data.get("new_facts", []):
            discoveries.append({
                "type": "new_fact",
                "fact": fact.get("fact", ""),
                "category": fact.get("category", "其他"),
                "confidence": min(overall_confidence + 0.05, 1.0),
            })

        return discoveries

    def _apply_promotion(self, discovery: Dict[str, Any], prefs: Dict[str, Any]) -> bool:
        """将一条发现写入已加载的 prefs dict。返回 True 表示有变更。"""
        disc_type = discovery.get("type", "")
        confidence = discovery.get("confidence", 0.0)

        if disc_type == "recurring_theme":
            theme = discovery.get("theme", "")
            if not theme:
                return False
            notes = prefs.setdefault("notes", [])
            note = f"[会话挖掘] 高频主题：{theme}（出现 {discovery.get('count', 0)} 次，置信度 {confidence:.0%}）"
            if discovery.get("suggest_wiki"):
                note += " → 建议创建 Wiki 页面"
            # 去重：检查 theme 是否已出现在现有 notes 的前 80 字符中
            if not any(theme in str(n)[:80] for n in notes):
                notes.append(note)
                logger.info("会话挖掘晋升: 高频主题 '%s' → profile notes", theme)
                return True
            return False

        elif disc_type == "preference_pattern":
            pattern = discovery.get("pattern", "")
            if not pattern:
                return False
            styles = prefs.setdefault("style_preferences", [])
            entry = f"[会话挖掘] {pattern}（置信度 {confidence:.0%}，证据：{discovery.get('evidence', '')[:80]}）"
            if entry not in styles:
                styles.append(entry)
                if len(styles) > 15:
                    styles[:] = styles[-15:]  # 保留最新 15 条
                logger.info("会话挖掘晋升: 偏好模式 '%s' → style_preferences", pattern[:80])
                return True
            return False

        elif disc_type == "new_fact":
            fact = discovery.get("fact", "")
            if not fact:
                return False
            notes = prefs.setdefault("notes", [])
            entry = f"[会话挖掘] {fact}（类别：{discovery.get('category', '')}）"
            if entry not in notes:
                notes.append(entry)
                logger.info("会话挖掘晋升: 新事实 '%s' → notes", fact[:80])
                return True
            return False

        return False


def _extract_json_object(text: str) -> Optional[str]:
    """括号计数提取最外层 JSON 对象（LLM 响应中混有非 JSON 文本时的回退方案）。"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
