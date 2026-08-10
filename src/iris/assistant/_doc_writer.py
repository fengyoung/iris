"""过程文档输出：Markdown 原子整体重写（frontmatter + 会议累计 + 逐段记录）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from .models import MeetingState, SegmentAnalysis, VoiceSegment


class DocWriter:
    """每次 maybe_rewrite 从 MeetingState（内存事实源）全量渲染并原子写入。

    原子写：同目录 .tmp + os.replace —— 进程中断时旧文件安全、tmp 残留无害。
    """

    def __init__(self, path: Path, rewrite_every: int = 1):
        self._path = Path(path)
        self._rewrite_every = max(1, rewrite_every)
        self._last_segment_count = 0

    @property
    def path(self) -> Path:
        return self._path

    def initial_write(self, state: MeetingState) -> bool:
        """创建输出目录并写首帧；失败返回 False。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            return self._atomic_write(self._path, self.render(state))
        except Exception as e:
            print(f"[Iris] ⚠ 过程文档创建失败: {e}", file=sys.stderr)
            return False

    def maybe_rewrite(self, state: MeetingState, *, force: bool = False) -> bool:
        """按 rewrite_every 节流重写；force 强制（退出前）。"""
        count = len(state.segments)
        if not force and count - self._last_segment_count < self._rewrite_every:
            return False
        try:
            ok = self._atomic_write(self._path, self.render(state))
        except Exception as e:
            print(f"[Iris] ⚠ 过程文档写入失败: {e}", file=sys.stderr)
            return False
        if ok:
            self._last_segment_count = count
        return ok

    # ── 渲染 ──────────────────────────────────────────────

    @staticmethod
    def render(state: MeetingState) -> str:
        parts = [
            "---",
            f"title: 实时会议记录 {state.started_at:%Y-%m-%d %H:%M}",
            f"date: {state.started_at:%Y-%m-%d %H:%M}",
            "type: 实时会议记录",
            "source: meeting-live-assistant",
            "---",
            "",
            f"# 实时会议记录 {state.started_at:%Y-%m-%d %H:%M}",
            "",
            "## 📋 会议累计（实时更新）",
            *DocWriter._bullets("关键要点", state.key_points),
            *DocWriter._bullets("决策点", state.decisions),
            *DocWriter._bullets("风险", state.risks),
            *DocWriter._bullets("待解决问题", state.open_questions),
            "",
        ]
        for seg in state.segments:
            parts.extend(DocWriter._render_segment(seg))
        if state.dropped_count:
            parts += ["", f"> 本场积压丢弃 {state.dropped_count} 段（分析慢于说话节奏时自动丢弃中间段）"]
        return "\n".join(parts)

    @staticmethod
    def _bullets(section: str, items: list[str]) -> list[str]:
        lines = [f"### {section}"]
        lines.extend(f"- {item}" for item in items) if items else lines.append("- 无")
        return lines

    @staticmethod
    def _render_segment(seg: VoiceSegment) -> list[str]:
        lines = [
            "",
            f"## 🎙 段 {seg.seq}（{seg.started_at:%H:%M:%S}）",
            f"**校正文本**：{seg.corrected_text or seg.raw_text}",
        ]
        analysis: Optional[SegmentAnalysis] = seg.analysis
        if analysis is None:
            lines.append("**分析**：⚠ 分析不可用（LLM 调用失败或超时）")
        else:
            for label, field in (
                ("要点", analysis.key_points),
                ("风险", analysis.risks),
                ("问题", analysis.questions),
                ("决策点", analysis.decisions),
                ("建议提问", analysis.suggested_questions),
            ):
                if field:
                    lines.append(f"**{label}**：" + "；".join(field))
        return lines

    @staticmethod
    def _atomic_write(path: Path, content: str) -> bool:
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)
            return True
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
