"""Translate a fresh capture worker's local clock and identifiers into one meeting."""
from dataclasses import dataclass, field
from uuid import uuid4

from .session import Session


@dataclass
class CaptureAttempt:
    session: Session
    resume: bool
    replacing: bool
    clock_base: int = 0
    id_base: int = 0
    # This wire ID changes even if the user is resuming the same meeting.
    wire_id: str = field(default_factory=lambda: uuid4().hex)
    ready: bool = False
    speaker_status: dict | None = None

    def translate(self, incoming):
        event = dict(incoming, session_id=self.session.id)
        if not self.resume:
            return event
        if event.get('segment_id'):
            # Production worker emits positive, increasing integer IDs as strings.
            sid = str(event['segment_id'])
            event['segment_id'] = str(self.id_base + int(sid)) if sid.isdecimal() else f'{self.wire_id}:{sid}'
        for key in ('start_ms', 'end_ms', 'recognition_start_ms', 'recognition_end_ms',
                    'owned_start_ms', 'owned_end_ms'):
            if type(event.get(key)) is int:
                event[key] += self.clock_base
        if 'overlap_intervals' in event:
            event['overlap_intervals'] = [[a + self.clock_base, b + self.clock_base]
                                          for a, b in event['overlap_intervals']]
        if event.get('speaker_id'):
            # The new process has new clusters, not the former voice A/B/C.
            event['speaker_id'] = f'{self.wire_id}:{event["speaker_id"]}'
        return event
