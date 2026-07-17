"""长期记忆：用户偏好、Iris 人设与纠正规则。"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from iris.config.loader import ConfigBundle
from iris.core.locks import FileLock

SPLIT_RE = re.compile(r"[。\n；;]+")
CORRECTION_PATTERNS = [
    re.compile(r"(?P<concept>[A-Za-z0-9_\-一-鿿]{1,30})\s*不是\s*[^，。；;]+?[，,]?\s*(?:而是|是)\s*(?P<value>[^，。；;]+)"),
    re.compile(r"(?P<concept>[A-Za-z0-9_\-一-鿿]{1,30})\s*(?:应该是|应为|指的是|定义为)\s*(?P<value>[^，。；;]+)"),
    re.compile(r"纠正[:：]?\s*(?P<concept>[^=：:]+)\s*[=:：]\s*(?P<value>.+)$"),
]


class UserProfileMemoryStore:
    """保存 Iris 人设与用户偏好。"""

    def __init__(self, config: ConfigBundle):
        self._path = _long_term_dir(config) / "profile.json"

    def load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {
                "iris_persona": {},
                "user_preferences": {"likes": [], "dislikes": [], "style_preferences": [], "notes": []},
                "updated_at": None,
            }
        return json.loads(self._path.read_text(encoding="utf-8"))

    def apply_text_update(self, text: str) -> List[str]:
        with FileLock(self._path):
            state = self.load()
            persona = state.setdefault("iris_persona", {})
            prefs = state.setdefault("user_preferences", {"likes": [], "dislikes": [], "style_preferences": [], "notes": []})
            updates: List[str] = []

            for sentence in _split_sentences(text):
                if not sentence:
                    continue

                if ("Iris" in sentence or "你" in sentence) and any(keyword in sentence for keyword in ("人设", "角色", "定位")):
                    desc = _extract_after(sentence, ("改为", "改成", "是", "为"))
                    if desc:
                        persona["description"] = desc
                        updates.append(f"Iris 人设已更新：{desc}")
                    continue

                if "我喜欢" in sentence or "我偏好" in sentence:
                    value = _extract_after(sentence, ("我喜欢", "我偏好"))
                    if value:
                        _append_unique(prefs.setdefault("likes", []), value)
                        updates.append(f"已记录你的偏好：喜欢 {value}")
                    continue

                if "我不喜欢" in sentence or "我不希望" in sentence or "我不要" in sentence:
                    value = _extract_after(sentence, ("我不喜欢", "我不希望", "我不要"))
                    if value:
                        _append_unique(prefs.setdefault("dislikes", []), value)
                        updates.append(f"已记录你的偏好：避免 {value}")
                    continue

                if "回答" in sentence and any(keyword in sentence for keyword in ("请", "要", "希望")):
                    _append_unique(prefs.setdefault("style_preferences", []), sentence)
                    updates.append("已记录回答风格偏好")
                    continue

                if "记住" in sentence:
                    note = _extract_after(sentence, ("记住",))
                    if note:
                        _append_unique(prefs.setdefault("notes", []), note)
                        updates.append(f"已记录说明：{note}")

            if updates:
                state["updated_at"] = _now_iso()
                self._save(state)
        return updates

    def render_for_prompt(self) -> str:
        state = self.load()
        persona = state.get("iris_persona", {})
        prefs = state.get("user_preferences", {})
        lines = []
        if persona.get("description"):
            lines.append(f"- Iris 人设：{persona['description']}")
        likes = prefs.get("likes", [])[:3]
        dislikes = prefs.get("dislikes", [])[:3]
        styles = prefs.get("style_preferences", [])[:2]
        notes = prefs.get("notes", [])[:2]
        if likes:
            lines.append(f"- 用户偏好（喜欢）：{' | '.join(likes)}")
        if dislikes:
            lines.append(f"- 用户偏好（避免）：{' | '.join(dislikes)}")
        if styles:
            lines.append(f"- 输出风格偏好：{' | '.join(styles)}")
        if notes:
            lines.append(f"- 其他说明：{' | '.join(notes)}")
        return "\n".join(lines) if lines else "无"

    def save(self, payload: Dict[str, Any]) -> None:
        payload.setdefault("iris_persona", {})
        payload.setdefault("user_preferences", {"likes": [], "dislikes": [], "style_preferences": [], "notes": []})
        payload["updated_at"] = payload.get("updated_at") or _now_iso()
        with FileLock(self._path):
            self._save(payload)

    def _save(self, payload: Dict[str, Any]) -> None:
        _atomic_write_json(self._path, payload)


class CorrectionMemoryStore:
    """保存术语/概念纠正规则，支持更新覆盖。"""

    def __init__(self, config: ConfigBundle):
        self._path = _long_term_dir(config) / "corrections.json"

    def load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {"items": {}, "updated_at": None}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def apply_text_update(self, text: str) -> List[str]:
        with FileLock(self._path):
            state = self.load()
            items = state.setdefault("items", {})
            updates: List[str] = []

            for sentence in _split_sentences(text):
                normalized_sentence = re.sub(r"^\s*纠正[:：]\s*", "", sentence).strip()
                for pattern in CORRECTION_PATTERNS:
                    match = pattern.search(normalized_sentence)
                    if not match:
                        continue
                    concept = _clean_term(match.group("concept"))
                    value = _clean_term(match.group("value"))
                    if not concept or not value:
                        continue
                    entry = items.get(concept, {"preferred": "", "update_count": 0, "last_source": ""})
                    entry["preferred"] = value
                    entry["update_count"] = int(entry.get("update_count", 0)) + 1
                    entry["updated_at"] = _now_iso()
                    entry["last_source"] = sentence.strip()
                    items[concept] = entry
                    updates.append(f"纠正规则已更新：{concept} => {value}")
                    break

            if updates:
                state["updated_at"] = _now_iso()
                self._save(state)
        return updates

    def get_relevant(self, query: str, *, top_k: int = 5) -> List[Dict[str, str]]:
        state = self.load()
        items = state.get("items", {})
        if not items:
            return []
        query_lower = query.lower()
        matched = []
        unmatched = []
        for concept, item in items.items():
            payload = {
                "concept": concept,
                "preferred": str(item.get("preferred", "")),
                "updated_at": str(item.get("updated_at", "")),
            }
            if concept.lower() in query_lower or str(item.get("preferred", "")).lower() in query_lower:
                matched.append(payload)
            else:
                unmatched.append(payload)
        if matched:
            matched.sort(key=lambda item: item["concept"])
            return matched[:top_k]
        unmatched.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return unmatched[:top_k]

    def get_frequent_corrections(self, *, min_count: int = 3) -> List[Dict[str, Any]]:
        """获取反复纠正次数超过阈值的记录，用于自动摘要。"""
        state = self.load()
        items = state.get("items", {})
        result = []
        for concept, item in items.items():
            count = int(item.get("update_count", 0))
            if count >= min_count:
                result.append({
                    "concept": concept,
                    "preferred": str(item.get("preferred", "")),
                    "update_count": count,
                    "updated_at": str(item.get("updated_at", "")),
                })
        result.sort(key=lambda x: (-x["update_count"], x["concept"]))
        return result

    def render_for_prompt(self, query: str) -> str:
        rows = self.get_relevant(query, top_k=5)
        if not rows:
            return "无"
        return "\n".join(f"- {item['concept']} => {item['preferred']}" for item in rows)

    def delete(self, concept: str) -> bool:
        concept = _clean_term(concept)
        if not concept:
            return False
        with FileLock(self._path):
            state = self.load()
            items = state.setdefault("items", {})
            if concept not in items:
                return False
            items.pop(concept, None)
            state["updated_at"] = _now_iso()
            self._save(state)
        return True

    def save(self, payload: Dict[str, Any]) -> None:
        payload.setdefault("items", {})
        payload["updated_at"] = payload.get("updated_at") or _now_iso()
        with FileLock(self._path):
            self._save(payload)

    def _save(self, payload: Dict[str, Any]) -> None:
        _atomic_write_json(self._path, payload)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """原子写入 JSON：先写临时文件，再 os.replace 保证崩溃不损坏数据。"""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            logger.warning("原子写入临时文件清理失败: %s", tmp)
        raise


def _long_term_dir(config: ConfigBundle) -> Path:
    memory_root = config.root / config.app["paths"]["memory_dir"].replace("./", "")
    return memory_root / "long_term"


def _append_unique(target: List[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _split_sentences(text: str) -> List[str]:
    return [item.strip(" ，,") for item in SPLIT_RE.split(text) if item.strip(" ，,")]


def _extract_after(text: str, tokens: tuple[str, ...]) -> str:
    for token in tokens:
        if token in text:
            return _clean_term(text.split(token, 1)[1])
    return ""


def _clean_term(value: str) -> str:
    return value.strip().strip("：:，,。；; ").strip("“”\"'")


def _now_iso() -> str:
    from datetime import timezone as _tz
    return datetime.now(_tz.utc).isoformat()
