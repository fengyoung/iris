"""Memory-only session state and versioned asynchronous event boundary."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from uuid import uuid4


@dataclass
class Segment:
    id: str
    start_ms: int
    end_ms: int
    raw: str = ""
    corrected: str = ""
    dictionary_text: str = ""
    status: str = "partial"
    manual: bool = False
    revision: int = 0
    background_version: int = 0
    audio_revision: int = 0
    speaker_id: str | None = None
    speaker_state: str = "off"
    speaker_manual: bool = False
    speaker_name: str = ""
    overlap_intervals: list[list[int]] = field(default_factory=list)
    boundary_reason: str = ""
    boundary_alignment: str = ""
    recognition_start_ms: int | None = None
    recognition_end_ms: int | None = None

    def view(self) -> dict:
        return asdict(self)


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid4().hex)
    segments: dict[str, Segment] = field(default_factory=dict)
    background_version: int = 0
    title: str = "未命名会议"
    dirty: bool = False
    speakers_enabled: bool = False
    speaker_names: dict[str, str] = field(default_factory=dict)

    def _refresh_speaker_name(self, segment: Segment) -> None:
        segment.speaker_name = ""
        if not self.speakers_enabled or segment.speaker_state == "off":
            return
        if segment.speaker_id:
            if segment.speaker_id not in self.speaker_names:
                # Stable session-local anonymous labels, including beyond speaker Z.
                number, label = len(self.speaker_names) + 1, ""
                while number:
                    number, digit = divmod(number - 1, 26)
                    label = chr(65 + digit) + label
                self.speaker_names[segment.speaker_id] = f"说话人 {label}"
            segment.speaker_name = self.speaker_names[segment.speaker_id]

    def set_speakers_enabled(self, enabled: bool) -> None:
        self.speakers_enabled = bool(enabled)
        for segment in self.segments.values():
            self._refresh_speaker_name(segment)

    @staticmethod
    def _audio_metadata(segment: Segment, event: dict) -> None:
        # Deliberate allowlist: never accept text/manual flags or arbitrary attributes.
        for key in ("boundary_reason", "boundary_alignment"):
            if key in event and isinstance(event[key], str):
                setattr(segment, key, event[key])
        for key in ("recognition_start_ms", "recognition_end_ms"):
            value = event.get(key)
            if type(value) is int and value >= 0:
                setattr(segment, key, value)
        if "overlap_intervals" in event:
            value = event["overlap_intervals"]
            if isinstance(value, list):
                segment.overlap_intervals = [list(pair) for pair in value
                    if isinstance(pair, (list, tuple)) and len(pair) == 2
                    and all(type(item) is int for item in pair)
                    and 0 <= pair[0] < pair[1]]

    def _speaker_metadata(self, segment: Segment, event: dict) -> None:
        if segment.speaker_manual:
            return
        state = event.get("speaker_state")
        if not isinstance(state, str) or state not in {"off", "pending", "confirmed", "unknown", "overlap"}:
            return
        speaker_id = event.get("speaker_id")
        segment.speaker_id = speaker_id if isinstance(speaker_id, str) and speaker_id.strip() else None
        segment.speaker_state = "unknown" if state == "confirmed" and not segment.speaker_id else state
        if state == "off":
            segment.speaker_id = None
        self._refresh_speaker_name(segment)

    def apply_audio(self, event: dict) -> Segment | None:
        if event.get("session_id") != self.id or event.get("type") not in {"partial", "final"}:
            return None
        sid = str(event["segment_id"])
        segment = self.segments.get(sid)
        if segment and segment.status != "partial":
            return None
        revision = event.get("audio_revision", event.get("revision"))
        if revision is not None and (type(revision) is not int or revision < 0):
            return None
        if segment and revision is not None and revision < segment.audio_revision:
            return None
        start = max(0, int(event.get("owned_start_ms", event["start_ms"])))
        end = max(start + 1, int(event.get("owned_end_ms", event["end_ms"])))
        if segment is None:
            segment = Segment(sid, start, end,
                              speaker_state='pending' if self.speakers_enabled else 'off')
            self.segments[sid] = segment
        segment.start_ms = start
        segment.end_ms = end
        next_revision = revision if revision is not None else segment.audio_revision + 1
        if (next_revision != segment.audio_revision and not segment.speaker_manual
                and segment.speaker_state != "off"):
            segment.speaker_id = None
            segment.speaker_state = "pending"
            segment.speaker_name = ""
            segment.overlap_intervals = []
        segment.audio_revision = next_revision
        self._audio_metadata(segment, event)
        self._speaker_metadata(segment, event)
        segment.raw = str(event.get("text", ""))
        if event["type"] == "final":
            segment.status = "pending"
            segment.revision += 1
            segment.background_version = self.background_version
        self.dirty = True
        return segment

    def apply_speaker(self, event: dict) -> Segment | None:
        """Apply a speaker result for exactly one audio revision, not text revision."""
        if event.get("session_id") != self.id or not self.speakers_enabled:
            return None
        segment = self.segments.get(str(event.get("segment_id")))
        revision = event.get("audio_revision", event.get("revision"))
        if (segment is None or segment.speaker_manual or type(revision) is not int
                or revision != segment.audio_revision):
            return None
        state = event.get("speaker_state")
        if not isinstance(state, str) or state not in {"off", "pending", "confirmed", "unknown", "overlap"}:
            return None
        self._speaker_metadata(segment, event)
        # Speaker results may describe overlap, but cannot change audio boundaries.
        if "overlap_intervals" in event:
            self._audio_metadata(segment, {"overlap_intervals": event["overlap_intervals"]})
        self.dirty = True
        return segment

    def rename_speaker(self, speaker_id: str, name: str) -> None:
        """Rename a registered anonymous speaker in memory only."""
        if speaker_id not in self.speaker_names:
            raise ValueError("未知说话人")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("说话人名称须为 1–80 字符")
        if any(ord(char) < 32 for char in name):
            raise ValueError("说话人名称不能包含控制字符")
        self.speaker_names[speaker_id] = name.strip()
        for segment in self.segments.values():
            if segment.speaker_id == speaker_id:
                self._refresh_speaker_name(segment)
        self.dirty = True

    def assign_speaker(self, segment_id: str, speaker_id: str | None) -> Segment:
        """Lock a segment's speaker; None explicitly locks it as unknown."""
        if speaker_id is not None and (not isinstance(speaker_id, str) or not speaker_id.strip()):
            raise ValueError("说话人标识不能为空")
        segment = self.segments[segment_id]
        segment.speaker_id = speaker_id
        segment.speaker_state = "confirmed" if speaker_id else "unknown"
        segment.speaker_manual = True
        self._refresh_speaker_name(segment)
        self.dirty = True
        return segment

    def reading_groups(self) -> list[list[Segment]]:
        """Presentation groups only: retain every physical segment and its identity."""
        groups: list[list[Segment]] = []
        for segment in self.ordered():
            previous = groups[-1][-1] if groups else None
            if (self.speakers_enabled and previous is not None
                    and previous.speaker_state == segment.speaker_state == "confirmed"
                    and previous.speaker_id == segment.speaker_id and segment.speaker_id
                    and not previous.overlap_intervals and not segment.overlap_intervals
                    and previous.end_ms == segment.start_ms):
                groups[-1].append(segment)
            else:
                groups.append([segment])
        return groups

    def discard_partial(self, event: dict) -> bool:
        if event.get('session_id') != self.id or event.get('type') != 'discard':
            return False
        sid = str(event.get('segment_id'))
        segment = self.segments.get(sid)
        if segment is None or segment.status != 'partial' or segment.manual:
            return False
        del self.segments[sid]
        self.dirty = bool(self.segments) and self.dirty
        return True

    def apply_correction(self, event: dict) -> Segment | None:
        if event.get("session_id") != self.id:
            return None
        segment = self.segments.get(str(event.get("segment_id")))
        if not segment or segment.manual or segment.revision != event.get("revision"):
            return None
        if event.get("type") == "correction":
            segment.corrected = str(event.get("corrected_text", ""))
            segment.status = "corrected"
        else:
            segment.status = "retry"
        self.dirty = True
        return segment

    def edit(self, sid: str, text: str) -> Segment:
        segment = self.segments[sid]
        if segment.status == "partial":
            raise ValueError("句段定稿后才能编辑校正结果")
        segment.corrected = text
        segment.manual = True
        segment.revision += 1
        segment.status = "manual"
        self.dirty = True
        return segment

    def ordered(self) -> list[Segment]:
        return sorted(self.segments.values(), key=lambda s: (s.start_ms, s.id))
