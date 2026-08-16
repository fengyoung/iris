"""过程文档输出：Markdown 原子整体重写（frontmatter + 会议累计 + 逐段记录）。"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from .models import CONF_ICON, MeetingState, SegmentAnalysis, VoiceSegment

_logger = logging.getLogger(__name__)


class DocWriter:
    """每次 maybe_rewrite 从 MeetingState（内存事实源）全量渲染并原子写入。

    原子写：同目录唯一名 .tmp + os.replace —— 进程中断时旧文件安全、tmp 残留无害。
    并发防御：实例 RLock 串行化 + tempfile 唯一 tmp 名 —— 即使出现并发写也不交错损坏。

    v3.26.1: 连续写入失败追踪 + is_failing 公开属性，供面板告警。
    """

    _MAX_CONSECUTIVE_FAILURES = 3  # 连续失败阈值，超限触发面板告警

    def __init__(self, path: Path, rewrite_every: int = 3):
        self._path = Path(path)
        self._rewrite_every = max(1, rewrite_every)
        self._last_segment_count = 0
        self._last_dropped_count = 0   # 跟踪段丢弃，用于即时插入标记
        self._lock = threading.RLock()
        # 增量写入缓存：段渲染块（按 seq 顺序），供增量追加与退出全量校验
        self._rendered_segments: list[str] = []
        # v3.26.1 写入健康追踪
        self._consecutive_failures = 0
        self._total_failures = 0

    @property
    def is_failing(self) -> bool:
        """连续写入失败是否达到告警阈值。"""
        return self._consecutive_failures >= self._MAX_CONSECUTIVE_FAILURES

    @property
    def path(self) -> Path:
        return self._path

    def initial_write(self, state: MeetingState) -> bool:
        """创建输出目录并写首帧（全量渲染，用于初始化缓存）；失败返回 False。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._rendered_segments = []  # 重置缓存
            self._last_dropped_count = state.dropped_count
            return self._atomic_write(self._path, self.render(state))
        except Exception as e:
            _logger.warning("过程文档创建失败: %s", e)
            return False

    def maybe_rewrite(self, state: MeetingState, *, force: bool = False) -> bool:
        """按 rewrite_every 节流重写；force 强制（退出前，全量渲染自愈校验）。

        增量路径：新段追加渲染块到缓存 → 全量原子写入（header + cumulative +
        缓存的段块 + dropped）。单段渲染 O(1)，历史段块复用缓存。

        结构说明：会议进行中为线性增量（实时可读）；退出 force 时全量渲染，
        若已有话题则切换为话题结构化文档（最终形态）。
        """
        count = len(state.segments)
        if not force and count - self._last_segment_count < self._rewrite_every:
            return False
        # 增量渲染：仅当有新段时追加渲染块到缓存
        new_count = count - len(self._rendered_segments)
        if new_count > 0:
            # 段丢弃即时标记：段号断层时在文档正文中插标记，避免用户困惑
            new_drops = state.dropped_count - self._last_dropped_count
            if new_drops > 0:
                recent = state.dropped_texts[-new_drops:] if state.dropped_texts else []
                preview = "、".join(
                    f'"{t[:30]}{"…" if len(t) > 30 else ""}"' for t in recent
                )
                self._rendered_segments.append(
                    f"\n> ⚠ 积压丢弃 {new_drops} 段：{preview}\n"
                )
                self._last_dropped_count = state.dropped_count
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
            self._consecutive_failures += 1
            self._total_failures += 1
            if self.is_failing:
                _logger.error("⚠ 文档写入连续失败 %d 次（磁盘空间不足？）", self._consecutive_failures)
            return False
        if ok:
            self._last_segment_count = count
            self._consecutive_failures = 0  # 成功则重置失败计数
        else:
            self._consecutive_failures += 1
            self._total_failures += 1
            if self.is_failing:
                _logger.error("⚠ 文档写入连续失败 %d 次（磁盘空间不足？）", self._consecutive_failures)
        return ok

    def _assemble_from_cache(self, state: MeetingState) -> str:
        """从缓存组装文档（header + cumulative + cached segments + dropped）。"""
        parts = self._render_header(state)
        parts.append("")
        parts.extend(self._render_cumulative(state))
        parts.extend(self._render_mini_summaries(state))
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
        # v3.25.3: 优先按话题结构渲染
        if state.topics:
            return DocWriter._render_topic_structured(state)
        return DocWriter._render_linear(state)

    @staticmethod
    def _render_linear(state: MeetingState) -> str:
        """传统线性渲染（无话题时兼容）。"""
        parts = DocWriter._render_header(state)
        parts.append("")
        parts.extend(DocWriter._render_cumulative(state))
        parts.extend(DocWriter._render_mini_summaries(state))
        if state.summary:
            parts += ["", "## 📝 会议总结（AI 生成）", state.summary.strip()]
        parts.append("")
        for seg in state.segments:
            parts.extend(DocWriter._render_segment(seg))
        parts.extend(DocWriter._render_dropped(state))
        return "\n".join(parts)

    @staticmethod
    def _render_topic_structured(state: MeetingState) -> str:
        """v3.25.3 按话题渲染：概览 → 话题卡 → 决策/待办汇总 → 附录转写。"""
        parts = DocWriter._render_header(state)
        parts.append("")
        # 概览行
        duration = ""
        if state.segments:
            first_ts = state.segments[0].started_at
            last_ts = state.segments[-1].started_at
            mins = int((last_ts - first_ts).total_seconds() / 60)
            duration = f" · {mins} 分钟" if mins > 0 else ""
        confirmed_decisions = sum(
            1 for s in state.segments if s.analysis
            for d in s.analysis.decisions if d.confidence == "confirmed"
        )
        parts.append(f"**概览**：{len(state.topics)} 个话题{duration}"
                     f" · {confirmed_decisions} 决策"
                     f" · {len(state.todos)} 待办"
                     f" · {len(state.risks)} 风险")
        parts.append("")
        # 话题卡片
        last_seq = state.segments[-1].seq if state.segments else 0
        for i, t in enumerate(state.topics, 1):
            label = t.get("label", f"话题{i}")
            start_seq = t.get("start_seq", 0)
            # 进行中话题（end_seq=0）回退到最后一个段的 seq（非段数量——有丢弃时 seq 不连续）
            end_seq = t.get("end_seq", 0) or last_seq
            summary = t.get("summary", "")
            # 找到该话题范围内的段
            topic_segs = [s for s in state.segments
                         if start_seq <= s.seq <= end_seq]
            parts.append(f"## 📌 话题 {i}：{label}（段 {start_seq}-{end_seq}）")
            if summary:
                parts.append(f"**讨论**：{summary}")
            # 收集该话题的决策
            topic_decisions = []
            for s in topic_segs:
                if s.analysis:
                    for d in s.analysis.decisions:
                        if d.confidence == "confirmed":
                            topic_decisions.append(f"✅ {d.text}")
                        elif d.confidence == "proposed":
                            topic_decisions.append(f"💬 {d.text}")
            if topic_decisions:
                parts.append("**决策**：" + "；".join(topic_decisions))
            parts.append("")
        # 决策汇总
        if state.decisions:
            parts.append("## ✅ 决策汇总")
            for d in state.decisions:
                parts.append(f"- {d}")
            parts.append("")
        # 待办汇总
        if state.todos:
            parts.append("## 📋 待办汇总")
            for t in state.todos:
                parts.append(f"- {t}")
            parts.append("")
        # 风险汇总（前 10 条）
        if state.risks:
            parts.append("## ⚠ 风险汇总")
            for r in state.risks[:10]:
                parts.append(f"- {r}")
            parts.append("")
        # 阶段性总结（v3.26.1）
        parts.extend(DocWriter._render_mini_summaries(state))
        if parts and parts[-1] != "":
            parts.append("")
        # 会议总结
        if state.summary:
            parts += ["## 📝 会议总结（AI 生成）", state.summary.strip(), ""]
        # 附录：逐段转写（折叠）
        parts.append("## 📎 附录：完整逐段转写")
        parts.append("<details>")
        parts.append(f"<summary>{len(state.segments)} 段 · 展开查看</summary>")
        parts.append("")
        for seg in state.segments:
            parts.extend(DocWriter._render_segment(seg))
        parts.append("")
        parts.append("</details>")
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
    def _render_mini_summaries(state: MeetingState) -> list[str]:
        """v3.26.1 阶段性总结渲染（会议中每 15 分钟生成的轻量总结）。"""
        if not state.mini_summaries:
            return []
        return [
            "",
            "## 📌 阶段性总结（会议中自动生成）",
            *[f"- {s}" for s in state.mini_summaries],
            "",
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
        sp = f" · {seg.speaker.speaker_id}" if (
            seg.speaker and seg.speaker.speaker_id) else ""
        lines = [
            "",
            f"## 🎙 段 {seg.seq}（{seg.started_at:%H:%M:%S}）{sp}",
            f"**校正文本**：{seg.corrected_text or seg.raw_text}",
        ]
        analysis: Optional[SegmentAnalysis] = seg.analysis
        if seg.analysis_status == VoiceSegment.ANALYSIS_SKIPPED:
            lines.append("**分析**：⏭（短反馈/快速模式，跳过分析）")
        elif seg.analysis_status == VoiceSegment.ANALYSIS_MERGED:
            lines.append("**分析**：🔗 合并分析（见批次首段）")
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
                    if label == "决策点":
                        # DecisionItem → 带置信度标注的字符串
                        parts = [
                            f"{CONF_ICON.get(d.confidence, '')}{d.text}"
                            for d in field
                        ]
                        lines.append(f"**{label}**：" + "；".join(parts))
                    else:
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
