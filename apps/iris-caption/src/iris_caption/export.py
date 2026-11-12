"""Explicit user exports only; never auto-save a session."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .session import Segment, Session


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
    if segment.speaker_state == "overlap" or segment.overlap_intervals:
        tags.append("重叠语音")
    return " ".join(f"[{tag}]" for tag in tags)


def render_markdown(session: Session) -> str:
    lines = [f"# {session.title.replace(chr(10), ' ')}", "", "声笺 · Iris Caption", ""]
    for segment in session.ordered():
        annotation = speaker_annotation(session, segment)
        suffix = f" {annotation}" if annotation else ""
        lines += [f"## {timestamp(segment.start_ms)} — {timestamp(segment.end_ms)}{suffix}", "",
                  "**原文**", "", segment.raw, "", "**校正**", "",
                  segment.corrected or segment.dictionary_text or segment.raw, ""]
        if segment.status not in {"corrected", "manual"}:
            lines += ["*未完成云端校正*", ""]
    return "\n".join(lines)


def render_srt(session: Session, mode: str = "corrected") -> str:
    if mode not in {"raw", "corrected", "both"}:
        raise ValueError("未知字幕导出模式")
    lines = []
    for index, segment in enumerate(session.ordered(), 1):
        raw = " ".join(segment.raw.splitlines()).strip()
        corrected = " ".join((segment.corrected or segment.raw).splitlines()).strip()
        text = raw if mode == "raw" else corrected
        if mode == "both":
            text = f"原文：{raw}\n校正：{corrected}"
        annotation = speaker_annotation(session, segment)
        if annotation:
            text = f"{annotation} {text}"
        lines += [str(index), f"{timestamp(segment.start_ms, True)} --> "
                  f"{timestamp(max(segment.start_ms + 1, segment.end_ms), True)}", text, ""]
    return "\n".join(lines) + "\n"


def export_session(session: Session, path: Path, kind: str, mode: str = "corrected") -> None:
    if kind not in {"markdown", "srt"}:
        raise ValueError("不支持的导出格式")
    text = render_markdown(session) if kind == "markdown" else render_srt(session, mode)
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
