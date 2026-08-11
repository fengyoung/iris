"""过程文档输出：Markdown 原子整体重写（frontmatter + 会议累计 + 逐段记录）。"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from .models import MeetingState, SegmentAnalysis, VoiceSegment

_logger = logging.getLogger(__name__)


class DocWriter:
    """每次 maybe_rewrite 从 MeetingState（内存事实源）全量渲染并原子写入。

    原子写：同目录唯一名 .tmp + os.replace —— 进程中断时旧文件安全、tmp 残留无害。
    并发防御：实例 RLock 串行化 + tempfile 唯一 tmp 名 —— 即使出现并发写也不交错损坏。
    """

    def __init__(self, path: Path, rewrite_every: int = 1):
        self._path = Path(path)
        self._rewrite_every = max(1, rewrite_every)
        self._last_segment_count = 0
        self._lock = threading.RLock()
        # 增量写入缓存：段渲染块（按 seq 顺序），供增量追加与退出全量校验
        self._rendered_segments: list[str] = []

    @property
    def path(self) -> Path:
        return self._path

    def initial_write(self, state: MeetingState) -> bool:
        """创建输出目录并写首帧（全量渲染，用于初始化缓存）；失败返回 False。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._rendered_segments = []  # 重置缓存
            return self._atomic_write(self._path, self.render(state))
        except Exception as e:
            _logger.warning("过程文档创建失败: %s", e)
            return False

    def maybe_rewrite(self, state: MeetingState, *, force: bool = False) -> bool:
        """按 rewrite_every 节流重写；force 强制（退出前，全量渲染自愈校验）。

        增量路径：新段追加渲染块到缓存 → 全量原子写入（header + cumulative +
        缓存的段块 + dropped）。单段渲染 O(1)，历史段块复用缓存。
        """
        count = len(state.segments)
        if not force and count - self._last_segment_count < self._rewrite_every:
            return False
        # 增量渲染：仅当有新段时追加渲染块到缓存
        new_count = count - len(self._rendered_segments)
        if new_count > 0:
            for seg in state.segments[-new_count:]:
                self._rendered_segments.append(
                    "\n".join(self._render_segment(seg))
                )
        try:
            if force:
                # 退出前全量渲染自愈：从 state 完整渲染并校验缓存
                content = self.render(state)
            else:
                content = self._assemble_from_cache(state)
            ok = self._atomic_write(self._path, content)
        except Exception as e:
            _logger.warning("过程文档写入失败: %s", e)
            return False
        if ok:
            self._last_segment_count = count
        return ok

    def _assemble_from_cache(self, state: MeetingState) -> str:
        """从缓存组装文档（header + cumulative + cached segments + dropped）。"""
        parts = self._render_header(state)
        parts.append("")
        parts.extend(self._render_cumulative(state))
        if state.summary:
            parts += ["", "## 📝 会议总结（AI 生成）", state.summary.strip()]
        parts.append("")
        parts.extend(self._rendered_segments)
        if state.dropped_count:
            parts += self._render_dropped(state)
        return "\n".join(parts)

    # ── 渲染（公开静态方法 + 内部组件） ──────────────────

    @staticmethod
    def render(state: MeetingState) -> str:
        """全量渲染 Markdown 文档（供测试和首次写入使用）。"""
        parts = DocWriter._render_header(state)
        parts.append("")
        parts.extend(DocWriter._render_cumulative(state))
        if state.summary:
            parts += ["", "## 📝 会议总结（AI 生成）", state.summary.strip()]
        parts.append("")
        for seg in state.segments:
            parts.extend(DocWriter._render_segment(seg))
        parts.extend(DocWriter._render_dropped(state))
        return "\n".join(parts)

    @staticmethod
    def _render_header(state: MeetingState) -> list[str]:
        return [
            "---",
            f"title: 实时会议记录 {state.started_at:%Y-%m-%d %H:%M}",
            f"date: {state.started_at:%Y-%m-%d %H:%M}",
            "type: 实时会议记录",
            "source: meeting-live-assistant",
            "---",
            "",
            f"# 实时会议记录 {state.started_at:%Y-%m-%d %H:%M}",
        ]

    @staticmethod
    def _render_cumulative(state: MeetingState) -> list[str]:
        return [
            "## 📋 会议累计（实时更新）",
            *DocWriter._bullets("关键要点", state.key_points),
            *DocWriter._bullets("决策点", state.decisions),
            *DocWriter._bullets("风险", state.risks),
            *DocWriter._bullets("待解决问题", state.open_questions),
        ]

    @staticmethod
    def _render_dropped(state: MeetingState) -> list[str]:
        parts: list[str] = []
        if state.dropped_count:
            parts += ["", f"> 本场积压丢弃 {state.dropped_count} 段（分析慢于说话节奏时自动丢弃中间段）"]
            if state.dropped_texts:
                parts += [
                    "",
                    "<details>",
                    "<summary>📎 丢弃段原文（最近 " + str(len(state.dropped_texts)) + " 条）</summary>",
                    "",
                ]
                for i, text in enumerate(state.dropped_texts, 1):
                    parts.append(f"- 丢弃段 {i}：{text}")
                parts.append("")
                parts.append("</details>")
        return parts

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
        if seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
            lines.append("**分析**：⏭（短反馈/快速模式，跳过分析）")
        elif analysis is None:
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

    def _atomic_write(self, path: Path, content: str) -> bool:
        # RLock 串行化（并发写不交错）+ 唯一 tmp 名（同目录保证同文件系统，os.replace 原子）
        with self._lock:
            fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp, path)
                return True
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                raise
