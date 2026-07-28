"""问答记忆更新检测与执行。

两条通道：
- 快速通道：正则匹配显式命令（记住、纠正、我喜欢 等），免费毫秒级
- 深度通道：LLM 分析完整对话，提取隐含偏好/纠正/事实，轻量模型按需触发
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.memory import CorrectionMemoryStore, UserProfileMemoryStore
from iris.qa.helpers import EXPLICIT_MEMORY_RE
from iris.utils.llm_parsing import extract_json_object

logger = logging.getLogger(__name__)

IMPLICIT_CORRECTION_RE = re.compile(
    r"(不是\s*[^。；;]{2,40}?(?:而是|是)\s*[^。；;]{2,40})|"
    r"(应该是|应为|指的是|定义为)\s*[^。；;]{2,40}|"
    r"(纠正|更正)[：:]\s*[^。；;]{2,40}"
)

# ── 深度通道触发条件 ──────────────────────────────────────────
_MIN_QUESTION_LENGTH = 15      # 问题少于 15 字不触发 LLM 分析
_MIN_CONFIDENCE = 0.5           # LLM 提取置信度低于此值则丢弃
# ── 会话挖掘触发条件 ──────────────────────────────────────────
_SESSION_MINE_INTERVAL_HOURS = 24  # 两次挖掘最小间隔
_LAST_MINE_STATE_FILE = "last_session_mine.json"


class MemoryUpdater:
    """记忆更新器 — 正则快速通道 + LLM 深度通道 + 会话挖掘懒触发。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._profile_memory = UserProfileMemoryStore(config)
        self._correction_memory = CorrectionMemoryStore(config)
        self._llm_service = None     # 惰性初始化
        self._mine_state_path = self._resolve_mine_state_path(config)

    # ── 公共接口 ────────────────────────────────────────────────

    def apply_updates(
        self,
        question: str,
        *,
        answer: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        skip_regex: bool = False,
        skip_mine_check: bool = False,
    ) -> List[str]:
        """分析对话并更新记忆。返回更新描述列表。

        Args:
            question: 用户问题
            answer: 系统回答（传 None 则只走快速通道）
            context: 额外上下文（可选）
            skip_regex: 跳过正则通道（第二遍调用时避免重复）
            skip_mine_check: 跳过会话挖掘检查（第一遍调用时避免重复）
        """
        updates: List[str] = []

        # ── 通道 1：正则快速匹配 ──
        if not skip_regex:
            regex_updates = self._apply_regex_channel(question)
            updates.extend(regex_updates)

        # ── 通道 2：LLM 深度分析 ──
        if self._should_deep_analyze(question, answer):
            llm_updates = self._apply_llm_channel(question, answer, context)
            updates.extend(llm_updates)

        # ── 汇总并触发维护检查 ──
        if updates:
            self._summarize_frequent_corrections()

        # ── Phase 2：会话挖掘懒触发（仅第二遍调用时检查）──
        if not skip_mine_check:
            self._maybe_mine_sessions()

        return updates

    def mine_sessions(self) -> Dict[str, Any]:
        """公开的会话挖掘入口（供 daily-start 兜底调用）。"""
        from iris.memory.session_miner import SessionPatternMiner
        miner = SessionPatternMiner(self._config)
        return miner.mine_and_promote()

    # ── 通道 1：正则快速匹配 ──────────────────────────────────

    def _apply_regex_channel(self, question: str) -> List[str]:
        """正则快速通道：处理显式记忆命令和隐含纠正。"""
        updates: List[str] = []
        if EXPLICIT_MEMORY_RE.search(question):
            updates.extend(self._profile_memory.apply_text_update(question))
            updates.extend(self._correction_memory.apply_text_update(question))
            return updates
        if IMPLICIT_CORRECTION_RE.search(question):
            updates.extend(self._correction_memory.apply_text_update(question))
        return updates

    # ── 通道 2：LLM 深度分析 ──────────────────────────────────

    def _should_deep_analyze(self, question: str, answer: Optional[str]) -> bool:
        """判断是否需要 LLM 深度分析。"""
        if not answer:
            return False
        if len(question) < _MIN_QUESTION_LENGTH:
            return False
        if EXPLICIT_MEMORY_RE.search(question):
            return False  # 显式命令已被正则通道处理
        return True

    def _apply_llm_channel(
        self,
        question: str,
        answer: str,
        context: Optional[Dict[str, Any]],
    ) -> List[str]:
        """LLM 深度通道：用轻量模型从完整对话中提取隐含记忆。"""
        try:
            prompt = self._build_extraction_prompt(question, answer)
            response_text = self._call_llm(prompt)

            if not response_text:
                return []

            extracted = self._parse_llm_response(response_text)
            if not extracted:
                return []

            confidence = extracted.get("confidence", 0.0)
            if confidence < _MIN_CONFIDENCE:
                logger.debug("LLM 记忆提取置信度 %.2f < %.2f，丢弃", confidence, _MIN_CONFIDENCE)
                return []

            return self._apply_extracted(extracted)

        except Exception as exc:
            logger.warning("LLM 记忆深度分析失败（不影响主流程）: %s", exc)
            return []

    def _build_extraction_prompt(self, question: str, answer: str) -> str:
        """构建记忆提取 prompt。"""
        profile = self._profile_memory.load()
        prefs = profile.get("user_preferences", {})
        corrections = self._correction_memory.load()

        # 已有偏好
        existing_likes = "、".join(prefs.get("likes", [])[:10]) or "无"
        existing_dislikes = "、".join(prefs.get("dislikes", [])[:10]) or "无"
        existing_styles = "、".join(prefs.get("style_preferences", [])[:5]) or "无"
        existing_notes = "、".join(prefs.get("notes", [])[:5]) or "无"

        # 已有纠正规则
        corrections_items = corrections.get("items", {})
        if corrections_items:
            correction_lines = []
            for concept, item in list(corrections_items.items())[:10]:
                correction_lines.append(f"  {concept} => {item.get('preferred', '')}")
            existing_corrections = "\n".join(correction_lines)
        else:
            existing_corrections = "无"

        # 从模板渲染
        from iris.utils.prompting import PromptTemplateLoader
        loader = PromptTemplateLoader(self._config)
        return loader.render("memory_extract.md", {
            "existing_likes": existing_likes,
            "existing_dislikes": existing_dislikes,
            "existing_styles": existing_styles,
            "existing_corrections": existing_corrections,
            "existing_notes": existing_notes,
            "question": question,
            "answer": answer[:2000],
        })

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用轻量 LLM 进行记忆提取（静默失败，不影响主流程）。"""
        if self._llm_service is None:
            from iris.llm import LLMService
            self._llm_service = LLMService(self._config)

        # 使用 memory_extraction 路由上下文，让 provider 选择最便宜的模型
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
                temperature=0,          # 确定性输出 + 自动缓存
                use_cache=True,
            )
            return result.text.strip()
        except Exception as exc:
            logger.debug("LLM 记忆提取调用失败（静默）: %s", exc)
            return None

    def _parse_llm_response(self, text: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回的 JSON。"""
        # 去除可能的 markdown 代码块包装
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉首行 ```json 和末行 ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                return None
            return data
        except json.JSONDecodeError:
            # 尝试用括号计数提取最外层 JSON 对象
            json_text = extract_json_object(text)
            if json_text:
                try:
                    data = json.loads(json_text)
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    pass
            logger.debug("LLM 记忆提取响应 JSON 解析失败: %.200s", text)
            return None

    def _apply_extracted(self, extracted: Dict[str, Any]) -> List[str]:
        """将 LLM 提取的记忆写入 long_term 存储（单次 load-modify-save）。"""
        updates: List[str] = []
        profile = self._profile_memory.load()
        prefs = profile.setdefault("user_preferences", {})
        profile_changed = False

        # ── 偏好（喜欢）──
        new_likes = extracted.get("new_likes", [])
        if new_likes:
            existing_likes = set(prefs.get("likes", []))
            added = 0
            for item in new_likes:
                text = str(item).strip()
                if text and text not in existing_likes and len(text) < 80:
                    existing_likes.add(text)
                    added += 1
            if added:
                prefs["likes"] = sorted(existing_likes)
                profile_changed = True
                updates.append(f"LLM 提取偏好(喜欢): +{added} 条")

        # ── 偏好（避免）──
        new_dislikes = extracted.get("new_dislikes", [])
        if new_dislikes:
            existing_dislikes = set(prefs.get("dislikes", []))
            added = 0
            for item in new_dislikes:
                text = str(item).strip()
                if text and text not in existing_dislikes and len(text) < 80:
                    existing_dislikes.add(text)
                    added += 1
            if added:
                prefs["dislikes"] = sorted(existing_dislikes)
                profile_changed = True
                updates.append(f"LLM 提取偏好(避免): +{added} 条")

        # ── 回答风格偏好 ──
        new_styles = extracted.get("new_styles", [])
        if new_styles:
            existing_styles = set(prefs.get("style_preferences", []))
            added = 0
            for item in new_styles:
                text = str(item).strip()
                if text and text not in existing_styles and len(text) < 120:
                    existing_styles.add(text)
                    added += 1
            if added:
                prefs["style_preferences"] = sorted(existing_styles)
                profile_changed = True
                updates.append(f"LLM 提取回答风格偏好: +{added} 条")

        # ── 备注 ──
        new_notes = extracted.get("new_notes", [])
        if new_notes:
            existing_notes = list(prefs.get("notes", []))
            added = 0
            for item in new_notes:
                text = str(item).strip()
                if text and text not in existing_notes and len(text) < 500:
                    existing_notes.append(text)
                    added += 1
            if added:
                prefs["notes"] = existing_notes
                profile_changed = True
                updates.append(f"LLM 提取备注: +{added} 条")

        if profile_changed:
            profile["updated_at"] = _now_iso()
            self._profile_memory.save(profile)

        # ── 纠正规则（独立存储，不受 profile 合并影响）──
        new_corrections = extracted.get("new_corrections", [])
        if new_corrections:
            corrections = self._correction_memory.load()
            items = corrections.setdefault("items", {})
            for corr in new_corrections:
                concept = str(corr.get("concept", "")).strip()
                preferred = str(corr.get("preferred", "")).strip()
                if not concept or not preferred or len(concept) > 40:
                    continue
                existing = items.get(concept, {})
                entry = {
                    "preferred": preferred,
                    "update_count": int(existing.get("update_count", 0)) + 1,
                    "updated_at": _now_iso(),
                    "last_source": f"[LLM] {corr.get('context', 'from conversation')}"
                }
                items[concept] = entry

                # Phase 3：冲突自动解决 — 纠正 ≥ 3 次触发
                if entry["update_count"] >= 3:
                    resolved = self._auto_resolve_conflict(concept, entry, items)
                    if resolved:
                        updates.append(f"LLM 提取纠正 + 自动裁决: {concept} => {preferred}")
                        continue
                updates.append(f"LLM 提取纠正规则: {concept} => {preferred}")

            corrections["updated_at"] = _now_iso()
            self._correction_memory.save(corrections)

        return updates

    # ── Phase 3：冲突自动解决 ──────────────────────────────────

    def _auto_resolve_conflict(
        self,
        concept: str,
        entry: Dict[str, Any],
        items: Dict[str, Any],
    ) -> bool:
        """纠正 ≥ 3 次时自动裁决最终值。

        策略：
        - LLM 提取：update_count ≥ 5 自动确认（多轮一致的 LLM 提取可信任）
        - 正则提取：检测 "不是 X，而是 Y" 模式，若 preferred 指向了 X 则修正为 Y
        """
        preferred = str(entry.get("preferred", ""))
        last_source = str(entry.get("last_source", ""))
        is_llm = last_source.startswith("[LLM]")

        # ── LLM 提取的纠正：≥5 次一致提取 → 自动确认 ──
        if is_llm:
            if entry["update_count"] >= 5 and "[AUTO-CONFIRMED]" not in last_source:
                entry["last_source"] = f"[AUTO-CONFIRMED] {last_source}"
                items[concept] = entry
                logger.info("自动确认纠正（LLM 稳定 ≥5 次）: %s => %s", concept, preferred)
            return False  # LLM 提取不需要 regex 冲突检测

        # ── 正则提取的纠正：检测 "不是 X，而是 Y" 模式 ──
        if "不是" in last_source and preferred in last_source:
            conflict_match = re.search(
                r"不是\s*(\S{1,20})\s*[,，]?\s*(?:而是|是)\s*(\S{1,30})",
                last_source,
            )
            if conflict_match:
                negated = conflict_match.group(1).strip()
                affirmed = conflict_match.group(2).strip()
                if negated == preferred:
                    entry["preferred"] = affirmed
                    entry["last_source"] = f"[AUTO-RESOLVED] {last_source}"
                    items[concept] = entry
                    logger.info("自动裁决纠正: %s: %s → %s", concept, preferred, affirmed)
                    return True

        # 正则提取稳定 ≥ 5 次 → 确认
        if entry["update_count"] >= 5 and "[AUTO-CONFIRMED]" not in last_source:
            entry["last_source"] = f"[AUTO-CONFIRMED] {last_source}"
            items[concept] = entry
            logger.info("自动确认纠正（稳定 ≥5 次）: %s => %s", concept, preferred)

        return False

    # ── 频繁纠正汇总 ──────────────────────────────────────────

    def _summarize_frequent_corrections(self) -> None:
        """将反复纠正（≥3次）的规则汇总到 profile notes。"""
        frequent = self._correction_memory.get_frequent_corrections(min_count=3)
        if not frequent:
            return
        profile = self._profile_memory.load()
        notes = profile.setdefault("user_preferences", {}).setdefault("notes", [])
        summary_note = "反复纠正确认：\n" + "\n".join(
            f"- {item['concept']} => {item['preferred']}（已纠正 {item['update_count']} 次）" for item in frequent)
        existing_idx = None
        for idx, note in enumerate(notes):
            if note.startswith("反复纠正确认："):
                existing_idx = idx
                break
        if existing_idx is not None:
            notes[existing_idx] = summary_note
        else:
            notes.append(summary_note)
        self._profile_memory.save(profile)

    # ── Phase 2：会话挖掘懒触发 ────────────────────────────────

    def _maybe_mine_sessions(self) -> None:
        """懒触发会话模式挖掘：距上次 ≥24h 或累积 ≥10 个新会话。

        在后台线程执行，不阻塞当前 Q&A 响应。
        """
        try:
            if not self._should_trigger_session_mine():
                return
            self._save_last_mine_time()  # 先记录时间，避免重复触发

            # 后台线程执行 LLM 挖掘，不阻塞 Q&A
            from iris.core.thread_pool import shared_pool
            shared_pool.submit(self._run_session_mine)
        except Exception as exc:
            logger.debug("会话挖掘触发失败（静默）: %s", exc)

    def _run_session_mine(self) -> None:
        """后台执行会话挖掘（由线程池调用）。"""
        try:
            result = self.mine_sessions()
            if result.get("promoted", 0) > 0:
                logger.info("会话挖掘完成：晋升 %d 条发现", result["promoted"])
        except Exception as exc:
            logger.warning("会话挖掘后台执行异常: %s", exc)

    def _should_trigger_session_mine(self) -> bool:
        """检查是否满足挖掘触发条件。"""
        last_mine = self._load_last_mine_time()
        if last_mine is None:
            return True  # 从未挖掘过

        hours_since = (datetime.now(timezone.utc) - last_mine).total_seconds() / 3600
        if hours_since >= _SESSION_MINE_INTERVAL_HOURS:
            return True

        # 检查新会话数（通过 session memory 文件修改时间判断）
        try:
            from iris.memory import SessionMemoryStore
            session_store = SessionMemoryStore(self._config)
            session_path = session_store._path
            if session_path.exists():
                session_mtime = datetime.fromtimestamp(
                    session_path.stat().st_mtime, tz=timezone.utc
                )
                if session_mtime > last_mine:
                    return True  # 会话文件更新了
        except Exception:
            pass

        return False

    def _load_last_mine_time(self) -> Optional[datetime]:
        try:
            if self._mine_state_path.exists():
                data = json.loads(self._mine_state_path.read_text(encoding="utf-8"))
                return datetime.fromisoformat(data.get("last_mine", ""))
        except Exception:
            pass
        return None

    def _save_last_mine_time(self) -> None:
        try:
            self._mine_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._mine_state_path.write_text(
                json.dumps({"last_mine": datetime.now(timezone.utc).isoformat()}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("保存会话挖掘时间戳失败: %s", exc)

    @staticmethod
    def _resolve_mine_state_path(config: ConfigBundle) -> Path:
        data_dir = config.root / "data"
        return data_dir / _LAST_MINE_STATE_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
