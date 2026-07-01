"""长期记忆管理：查看、删除、导入导出。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from iris.config.loader import ConfigBundle

from .long_term import CorrectionMemoryStore, UserProfileMemoryStore


class LongTermMemoryManager:
    def __init__(self, config: ConfigBundle):
        self._profile = UserProfileMemoryStore(config)
        self._corrections = CorrectionMemoryStore(config)

    def list_memory(self, memory_type: str = "all") -> Dict[str, Any]:
        if memory_type == "profile":
            return {"profile": self._profile.load()}
        if memory_type == "corrections":
            corrections = self._corrections.load()
            items = [
                {
                    "concept": concept,
                    "preferred": str(item.get("preferred", "")),
                    "update_count": int(item.get("update_count", 0)),
                    "updated_at": str(item.get("updated_at", "")),
                }
                for concept, item in sorted(corrections.get("items", {}).items(), key=lambda pair: pair[0])
            ]
            return {"correction_count": len(items), "items": items}
        return {
            "profile": self._profile.load(),
            "corrections": self.list_memory("corrections"),
        }

    def delete_correction(self, concept: str) -> Dict[str, Any]:
        removed = self._corrections.delete(concept)
        return {"concept": concept, "deleted": removed}

    def export_to_file(self, output_path: Path) -> Path:
        payload = {
            "profile": self._profile.load(),
            "corrections": self._corrections.load(),
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def import_from_file(self, input_path: Path, *, replace: bool = False) -> Dict[str, Any]:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        incoming_profile = payload.get("profile", {})
        incoming_corrections = payload.get("corrections", {})
        if replace:
            self._profile.save(incoming_profile)
            self._corrections.save(incoming_corrections)
        else:
            self._merge_profile(incoming_profile)
            self._merge_corrections(incoming_corrections)
        return {
            "replace": replace,
            "profile_updated": bool(incoming_profile),
            "corrections_updated": bool(incoming_corrections),
            "correction_count": len(self._corrections.load().get("items", {})),
        }

    def _merge_profile(self, incoming: Dict[str, Any]) -> None:
        if not incoming:
            return
        base = self._profile.load()
        base_persona = base.setdefault("iris_persona", {})
        incoming_persona = incoming.get("iris_persona", {})
        if incoming_persona.get("description"):
            base_persona["description"] = str(incoming_persona["description"])

        base_prefs = base.setdefault("user_preferences", {"likes": [], "dislikes": [], "style_preferences": [], "notes": []})
        incoming_prefs = incoming.get("user_preferences", {})
        for key in ("likes", "dislikes", "style_preferences", "notes"):
            base_list = list(base_prefs.get(key, []))
            for item in incoming_prefs.get(key, []):
                text = str(item).strip()
                if text and text not in base_list:
                    base_list.append(text)
            base_prefs[key] = base_list
        self._profile.save(base)

    def _merge_corrections(self, incoming: Dict[str, Any]) -> None:
        if not incoming:
            return
        base = self._corrections.load()
        base_items = base.setdefault("items", {})
        for concept, item in incoming.get("items", {}).items():
            concept_text = str(concept).strip()
            if not concept_text:
                continue
            base_items[concept_text] = {
                "preferred": str(item.get("preferred", "")),
                "update_count": int(item.get("update_count", 1)),
                "updated_at": str(item.get("updated_at", "")),
                "last_source": str(item.get("last_source", "")),
            }
        self._corrections.save(base)
