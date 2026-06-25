"""问答记忆更新检测与执行。"""

from __future__ import annotations

import re
from typing import List

from iris.config.loader import ConfigBundle
from iris.memory import CorrectionMemoryStore, UserProfileMemoryStore
from iris.qa.helpers import EXPLICIT_MEMORY_RE

IMPLICIT_CORRECTION_RE = re.compile(
    r"(不是\s*[^。；;]{2,40}?(?:而是|是)\s*[^。；;]{2,40})|"
    r"(应该是|应为|指的是|定义为)\s*[^。；;]{2,40}|"
    r"(纠正|更正)[：:]\s*[^。；;]{2,40}"
)


class MemoryUpdater:
    def __init__(self, config: ConfigBundle):
        self._profile_memory = UserProfileMemoryStore(config)
        self._correction_memory = CorrectionMemoryStore(config)

    def apply_updates(self, question: str) -> List[str]:
        updates = []
        if EXPLICIT_MEMORY_RE.search(question):
            updates.extend(self._profile_memory.apply_text_update(question))
            updates.extend(self._correction_memory.apply_text_update(question))
            if updates:
                self._summarize_frequent_corrections()
            return updates
        if IMPLICIT_CORRECTION_RE.search(question):
            updates.extend(self._correction_memory.apply_text_update(question))
            if updates:
                self._summarize_frequent_corrections()
        return updates

    def _summarize_frequent_corrections(self) -> None:
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
