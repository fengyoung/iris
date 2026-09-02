"""记忆自治引擎：老化、摘要、冲突检测、智能合并。

由 memory-maintenance 命令驱动，
自动维护长期记忆的健康度，减少手动管理负担。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.memory.long_term import CorrectionMemoryStore, UserProfileMemoryStore


# ── 常量 ──────────────────────────────────────────────────────────

DEFAULT_CORRECTION_AGE_DAYS = 90
DEFAULT_MIN_CONFLICT_COUNT = 3
DEFAULT_SUMMARY_MAX_ITEMS = 20
ARCHIVE_FILENAME = "corrections_archive.json"


class MemoryLifecycle:
    """记忆自治引擎。

    职责：
    - 老化：归档长期未更新的纠正记录
    - 摘要：压缩碎片化偏好/备注
    - 冲突检测：发现同一概念的矛盾纠正
    - 合并：导入时智能合并

    用法：
        lifecycle = MemoryLifecycle(config)
        report = lifecycle.maintenance()
    """

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._profile = UserProfileMemoryStore(config)
        self._corrections = CorrectionMemoryStore(config)
        # 归档文件与 corrections.json 同目录
        self._archive_path = self._corrections._path.parent / ARCHIVE_FILENAME

    # ── 老化 ──────────────────────────────────────────────────────

    @staticmethod
    def _is_item_stale(item: dict, cutoff: datetime) -> bool:
        """判断记录是否已超期（updated_at < cutoff）。"""
        updated_str = item.get("updated_at", "")
        if not updated_str:
            return False
        try:
            updated = datetime.fromisoformat(updated_str)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            return updated < cutoff
        except (ValueError, TypeError):
            return False

    def age(self, *, days: int = DEFAULT_CORRECTION_AGE_DAYS) -> Dict[str, Any]:
        """将超过 N 天未更新的纠正记录移至归档文件。"""
        state = self._corrections.load()
        items = state.get("items", {})
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        archive = self._load_archive()
        archived: List[str] = []
        kept: Dict[str, Any] = {}

        for concept, item in items.items():
            if self._is_item_stale(item, cutoff):
                archive.setdefault("items", {})[concept] = item
                archived.append(concept)
            else:
                kept[concept] = item

        if archived:
            archive["items"] = archive.get("items", {})
            archive["updated_at"] = _now_iso()
            self._save_archive(archive)
            state["items"] = kept
            state["updated_at"] = _now_iso()
            self._corrections._save(state)

        return {
            "aged_count": len(archived),
            "kept_count": len(kept),
            "archived_concepts": archived,
        }

    def list_stale(self, *, days: int = DEFAULT_CORRECTION_AGE_DAYS) -> List[Dict[str, Any]]:
        """列出超期的纠正记录（不执行操作），供审核。"""
        state = self._corrections.load()
        items = state.get("items", {})
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stale: List[Dict[str, Any]] = []

        for concept, item in items.items():
            updated_str = item.get("updated_at", "")
            if not updated_str:
                continue
            if not self._is_item_stale(item, cutoff):
                continue
            try:
                updated = datetime.fromisoformat(updated_str)
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                stale.append({
                    "concept": concept,
                    "preferred": str(item.get("preferred", "")),
                    "days_since_update": (datetime.now(timezone.utc) - updated).days,
                    "updated_at": updated_str,
                })
            except (ValueError, TypeError):
                pass

        stale.sort(key=lambda x: x["days_since_update"], reverse=True)
        return stale

    # ── 摘要 ──────────────────────────────────────────────────────

    def summarize(self) -> Dict[str, Any]:
        """将碎片化偏好/备注压缩为结构化摘要。"""
        state = self._profile.load()
        prefs = state.get("user_preferences", {})
        changes: Dict[str, Any] = {}

        for key, label in [("likes", "偏好"), ("dislikes", "避免")]:
            items = list(prefs.get(key, [])[:DEFAULT_SUMMARY_MAX_ITEMS])
            if len(items) > 10:
                changes[f"summarized_{key}"] = len(items)

        # 裁剪方向：列表按 append 时间序排列，末尾是最新写入，
        # 必须保留末尾（与 long_term._trim_list 的 [-max_len:] 语义一致）。
        # 历史 bug（v3.28.1 修复）：曾用 [:10] 保留最旧、每日删除最新记忆。
        notes = list(prefs.get("notes", []))
        if len(notes) > 10:
            prefs["notes"] = notes[-10:]
            changes["trimmed_notes"] = len(notes)

        styles = list(prefs.get("style_preferences", []))
        if len(styles) > 15:
            prefs["style_preferences"] = styles[-15:]
            changes["trimmed_style_preferences"] = len(styles)

        if changes:
            state["user_preferences"] = prefs
            state["updated_at"] = _now_iso()
            self._profile._save(state)

        return changes

    # ── 冲突检测 ──────────────────────────────────────────────────

    def detect_conflicts(self, *, min_count: int = DEFAULT_MIN_CONFLICT_COUNT) -> List[Dict[str, Any]]:
        """检测反复纠正记录是否存在矛盾。"""
        state = self._corrections.load()
        items = state.get("items", {})
        frequent = self._corrections.get_frequent_corrections(min_count=min_count)
        conflicts: List[Dict[str, Any]] = []

        for entry in frequent:
            concept = entry["concept"]
            item = items.get(concept, {})
            preferred = str(item.get("preferred", ""))
            last_source = str(item.get("last_source", ""))
            count = entry.get("update_count", 0)

            has_conflict = False
            conflict_detail = ""
            if last_source and preferred:
                not_match = re.search(r"不是\s*(\S{1,20})\s*[,，]?\s*(?:而是|是)\s*(\S{1,30})", last_source)
                if not_match:
                    negated = not_match.group(1).strip()
                    affirmed = not_match.group(2).strip()
                    if negated == preferred:
                        has_conflict = True
                        conflict_detail = f"源语句否定 '{negated}' 但 preferred 仍为 '{preferred}'（期望 '{affirmed}'）"

            if has_conflict or count >= min_count * 2:
                conflicts.append({
                    "concept": concept,
                    "preferred": preferred,
                    "update_count": count,
                    "last_source": last_source[:100],
                    "conflict": conflict_detail or f"已纠正 {count} 次，需人工确认",
                })

        return conflicts

    # ── 智能合并 ──────────────────────────────────────────────────

    def merge(self, incoming: Dict[str, Any], *, strategy: str = "auto") -> Dict[str, Any]:
        """合并外部记忆，处理冲突。"""
        result: Dict[str, Any] = {"merged_concepts": 0, "conflicts": []}

        incoming_corrections = incoming.get("corrections", {}).get("items", {})
        if incoming_corrections:
            base = self._corrections.load()
            base_items = base.setdefault("items", {})

            for concept, item in incoming_corrections.items():
                concept_clean = concept.strip()
                if not concept_clean:
                    continue

                incoming_value = str(item.get("preferred", ""))
                existing = base_items.get(concept_clean)

                if existing:
                    existing_value = str(existing.get("preferred", ""))
                    if existing_value != incoming_value:
                        if strategy in ("auto", "latest"):
                            # auto 当前等价于 latest（时间戳优先）；后续可扩展为 LLM 辅助合并
                            # 时间相等时新值优先，允许重新导入来纠正错误记录
                            inc_time = _parse_iso(item.get("updated_at", ""))
                            ext_time = _parse_iso(existing.get("updated_at", ""))
                            if inc_time and ext_time and inc_time >= ext_time:
                                base_items[concept_clean] = item
                            elif inc_time and not ext_time:
                                # 旧记录无时间戳时，新记录优先
                                base_items[concept_clean] = item
                        elif strategy == "keep_both":
                            result.setdefault("conflicts", []).append({
                                "concept": concept_clean,
                                "existing": existing_value,
                                "incoming": incoming_value,
                            })
                else:
                    base_items[concept_clean] = item
                    result["merged_concepts"] += 1

            self._corrections._save(base)

        incoming_profile = incoming.get("profile", {})
        if incoming_profile:
            base = self._profile.load()
            base_prefs = base.setdefault("user_preferences", {"likes": [], "dislikes": [], "style_preferences": [], "notes": []})
            inc_prefs = incoming_profile.get("user_preferences", {})

            for key in ("likes", "dislikes", "style_preferences", "notes"):
                inc_list = inc_prefs.get(key, [])
                base_list = base_prefs.get(key, [])
                for item in inc_list:
                    text = str(item).strip()
                    if text and text not in base_list:
                        base_list.append(text)
                base_prefs[key] = base_list

            base["updated_at"] = _now_iso()
            self._profile._save(base)

        result.setdefault("merged_concepts", 0)
        return result

    # ── 周期维护 ──────────────────────────────────────────────────

    def maintenance(self, *, age_days: int = DEFAULT_CORRECTION_AGE_DAYS) -> Dict[str, Any]:
        """一键执行全部自治维护任务。"""
        report: Dict[str, Any] = {
            "checked_at": _now_iso(),
            "conflicts": [],
            "stale_corrections": [],
            "summary": {},
        }

        report["conflicts"] = self.detect_conflicts()
        report["stale_corrections"] = self.list_stale(days=age_days)
        report["summary"] = self.summarize()

        return report

    # ── 归档管理 ──────────────────────────────────────────────────

    def restore_archived(self, concept: Optional[str] = None) -> int:
        """从归档恢复纠正记录。"""
        archive = self._load_archive()
        if not archive.get("items"):
            return 0

        state = self._corrections.load()
        items = state.setdefault("items", {})
        count = 0

        if concept:
            if concept in archive.get("items", {}):
                items[concept] = archive["items"].pop(concept)
                count = 1
        else:
            for key, val in archive.get("items", {}).items():
                items[key] = val
            count = len(archive["items"])
            archive["items"] = {}

        if count:
            self._corrections._save(state)
            self._save_archive(archive)

        return count

    def clear_archive(self) -> int:
        """清空归档。"""
        count = len(self._load_archive().get("items", {}))
        self._save_archive({"items": {}, "updated_at": _now_iso()})
        return count

    def _load_archive(self) -> Dict[str, Any]:
        if not self._archive_path.exists():
            return {"items": {}, "updated_at": None}
        try:
            return json.loads(self._archive_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"items": {}, "updated_at": None}

    def _save_archive(self, data: Dict[str, Any]) -> None:
        from iris.memory.long_term import _atomic_write_json
        _atomic_write_json(self._archive_path, data)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> Optional[datetime]:
    """安全解析 ISO 时间戳。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
