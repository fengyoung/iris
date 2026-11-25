"""Explicit user exports only; never auto-save a session."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .session import UNFINALIZED, Segment, Session


def timestamp(ms: int, srt: bool = False) -> str:
    seconds, milli = divmod(max(0, ms), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{',' if srt else '.'}{milli:03}"


def speaker_annotation(session: Session, segment: Segment) -> str:
    """Never imply that uncertain or overlapping speech has a known sole speaker."""
    if not session.speakers_enabled or segment.speaker_state == "off":
        return ""
    tags = []
    if segment.speaker_state == "confirmed" and segment.speaker_name:
        tags.append(segment.speaker_name)
    elif segment.speaker_state == "pending":
        tags.append("说话人待识别")
    elif segment.speaker_state != "overlap":
        tags.append("说话人未知")
    if segment.speaker_manual:
        tags.append('人工确认')
    if segment.has_overlap:
        tags.extend(('重叠语音', '可能不完整'))
    return " ".join(f"[{tag}]" for tag in tags)


def _draft_label(segment: Segment) -> str:
    if segment.status == 'interrupted':
        return '转写中断 · 未定稿，内容可能不完整'
    return '实时初稿 · 未定稿，内容可能变更'


def render_markdown(session: Session, *, live_snapshot: bool = False) -> str:
    lines = [f"# {session.title.replace(chr(10), ' ')}", "", "声笺 · Iris Caption", ""]
    segments = session.ordered()
    if live_snapshot or any(s.status in {'partial', 'transcribing'} for s in segments):
        cutoff = timestamp(max((s.end_ms for s in segments), default=0))
        lines += [f'> 实时快照 · 字幕截至 {cutoff}；后续转写与校正不包含在此文件中。', '']
    for segment in segments:
        annotation = speaker_annotation(session, segment)
        suffix = f" {annotation}" if annotation else ""
        lines += [f"## {timestamp(segment.start_ms)} — {timestamp(segment.end_ms)}{suffix}", '']
        if segment.status in UNFINALIZED:
            lines += [f'*{_draft_label(segment)}*', '', '**原始初稿**', '', segment.raw, '']
            if segment.manual:
                lines += ['**人工修订（仍未定稿）**', '', segment.corrected, '']
        else:
            lines += ['**原文**', '', segment.raw, '']
            if segment.status in {'corrected', 'manual'}:
                lines += ['**校正**', '', segment.corrected, '']
            else:
                lines += ['**本地结果（转写已定稿，未完成云端校正）**', '',
                          segment.dictionary_text or segment.corrected or segment.raw, '']
    return "\n".join(lines)


def render_srt(session: Session, mode: str = "corrected", *, include_unfinalized: bool = False) -> str:
    if mode not in {"raw", "corrected", "both"}:
        raise ValueError("未知字幕导出模式")
    lines = []
    segments = (s for s in session.ordered() if include_unfinalized or s.status not in UNFINALIZED)
    for index, segment in enumerate(segments, 1):
        raw = " ".join(segment.raw.splitlines()).strip()
        corrected = " ".join((segment.corrected or segment.raw).splitlines()).strip()
        text = raw if mode == "raw" else corrected
        local = segment.status not in UNFINALIZED and segment.status not in {'corrected', 'manual'}
        if mode == 'both':
            label = ('人工修订' if segment.manual else '初稿') if segment.status in UNFINALIZED else ('本地结果' if local else '校正')
            text = f'原文：{raw}\n{label}：{corrected}'
        annotation = speaker_annotation(session, segment)
        if segment.status in UNFINALIZED:
            text = f'[{_draft_label(segment)}] {text}'
        elif local and mode != 'raw':
            text = f'[本地结果 · 未完成云端校正] {text}'
        if annotation:
            text = f"{annotation} {text}"
        lines += [str(index), f"{timestamp(segment.start_ms, True)} --> "
                  f"{timestamp(max(segment.start_ms + 1, segment.end_ms), True)}", text, ""]
    return "\n".join(lines) + ("\n" if lines else "")


def export_session(session: Session, path: Path, kind: str, mode: str = "corrected", *,
                   include_unfinalized: bool = False, live_snapshot: bool = False) -> None:
    if kind not in {"markdown", "srt"}:
        raise ValueError("不支持的导出格式")
    if kind == 'markdown':
        text = render_markdown(session, live_snapshot=live_snapshot)
    else:
        text = render_srt(session, mode, include_unfinalized=include_unfinalized)
        if not text:
            raise ValueError('没有可导出的字幕。' if include_unfinalized else
                             '没有已定稿字幕；请导出 Markdown 快照或选择包含未定稿的应急 SRT。')
    # Explicit user-selected destination; atomic replacement avoids partial exports.
    fd, temporary = tempfile.mkstemp(prefix=".caption-export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
