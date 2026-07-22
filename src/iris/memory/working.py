"""工作上下文记忆：记录当前正在进行的任务、待办、状态。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from iris.config.loader import ConfigBundle

logger = logging.getLogger(__name__)


class WorkingContextStore:
    """保存当前工作上下文，供问答和报告生成时参考。

    文件格式：Markdown，包含以下区块：
    - 当前任务（current_task）
    - 待办事项（pending_items）
    - 最近变更（recent_changes）
    - 备注（notes）
    """

    def __init__(self, config: ConfigBundle):
        memory_dir = config.root / config.app["paths"]["memory_dir"].replace("./", "")
        self._path = memory_dir / "working" / "working_context.md"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Dict[str, Any]:
        """读取工作上下文，若无文件则返回空字典。"""
        if not self._path.exists():
            return {"current_task": "", "pending_items": [], "recent_changes": [], "notes": "", "updated_at": None}
        return self._parse(self._path.read_text(encoding="utf-8"))

    def save(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """保存工作上下文到文件。"""
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        import os
        import tempfile
        rendered = self._render(payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".md", prefix=".tmp-", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rendered)
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                logger.warning("工作上下文临时文件清理失败: %s", tmp)
            raise
        return payload

    def update(self, *, current_task: str = None, pending_items: List[str] = None,
               recent_changes: List[str] = None, notes: str = None, append_pending: List[str] = None,
               append_changes: List[str] = None) -> Dict[str, Any]:
        """增量更新工作上下文的指定字段。"""
        from iris.core.locks import FileLock
        with FileLock(self._path):
            state = self.load()
            if current_task is not None:
                state["current_task"] = str(current_task).strip()
            if pending_items is not None:
                state["pending_items"] = list(pending_items)
            if append_pending:
                existing = set(state.get("pending_items", []))
                for item in append_pending:
                    if item not in existing:
                        state.setdefault("pending_items", []).append(item)
                        existing.add(item)
            if recent_changes is not None:
                state["recent_changes"] = list(recent_changes)
            if append_changes:
                existing_changes = set(state.get("recent_changes", []))
                for item in append_changes:
                    if item not in existing_changes:
                        state.setdefault("recent_changes", []).append(item)
                        existing_changes.add(item)
            if notes is not None:
                state["notes"] = str(notes).strip()
            if not state.get("pending_items"):
                state["pending_items"] = []
            if not state.get("recent_changes"):
                state["recent_changes"] = []
            return self.save(state)

    def clear(self) -> Dict[str, Any]:
        """清空工作上下文。"""
        return self.save({"current_task": "", "pending_items": [], "recent_changes": [], "notes": ""})

    def render_for_prompt(self) -> str:
        """渲染为给 LLM 的提示片段。"""
        state = self.load()
        parts = []
        if state.get("current_task"):
            parts.append(f"当前任务：{state['current_task']}")
        if state.get("pending_items"):
            items = state["pending_items"][:5]
            parts.append("待办事项：\n" + "\n".join(f"- {item}" for item in items))
        if state.get("recent_changes"):
            changes = state["recent_changes"][:3]
            parts.append("最近变更：\n" + "\n".join(f"- {item}" for item in changes))
        if state.get("notes"):
            parts.append(f"备注：{state['notes']}")
        return "\n".join(parts) if parts else "无"

    def _parse(self, text: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "current_task": "",
            "pending_items": [],
            "recent_changes": [],
            "notes": "",
            "updated_at": None,
        }
        section = None
        lines = text.splitlines()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("> ") and ("更新于" in stripped):
                ts = stripped[2:].split("：", 1)[-1].strip() if "：" in stripped else stripped[2:].split(":", 1)[-1].strip()
                if ts and ts != "未知":
                    payload["updated_at"] = ts
                continue
            hdr = line.lstrip()
            if hdr.startswith("## "):
                hdr_text = hdr[3:].strip()
                if hdr_text == "当前任务":
                    section = "current_task"
                elif hdr_text == "待办事项":
                    section = "pending_items"
                elif hdr_text == "最近变更":
                    section = "recent_changes"
                elif hdr_text == "备注":
                    section = "notes"
                else:
                    section = None
                continue
            if section == "current_task":
                stripped = line.strip()
                if stripped:
                    payload["current_task"] = stripped
            elif section == "pending_items":
                if line.strip().startswith("- "):
                    payload.setdefault("pending_items", []).append(line.strip()[2:])
            elif section == "recent_changes":
                if line.strip().startswith("- "):
                    payload.setdefault("recent_changes", []).append(line.strip()[2:])
            elif section == "notes":
                stripped = line.strip()
                if stripped:
                    existing = payload.get("notes", "")
                    payload["notes"] = (existing + "\n" + stripped).strip()
        return payload

    def _render(self, payload: Dict[str, Any]) -> str:
        lines = ["# 工作上下文"]
        lines.append("")
        lines.append(f"> 更新于：{payload.get('updated_at', '未知')}")
        lines.append("")
        lines.append("## 当前任务")
        lines.append("")
        lines.append(payload.get("current_task", "") or "无")
        lines.append("")
        lines.append("## 待办事项")
        lines.append("")
        items = payload.get("pending_items", [])
        if items:
            for item in items:
                lines.append(f"- {item}")
        else:
            lines.append("无")
        lines.append("")
        lines.append("## 最近变更")
        lines.append("")
        changes = payload.get("recent_changes", [])
        if changes:
            for item in changes:
                lines.append(f"- {item}")
        else:
            lines.append("无")
        lines.append("")
        lines.append("## 备注")
        lines.append("")
        lines.append(payload.get("notes", "") or "无")
        lines.append("")
        return "\n".join(lines)
