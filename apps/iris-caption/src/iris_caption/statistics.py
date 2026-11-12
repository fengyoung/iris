"""Privacy-preserving transcript counts; no transcript or meeting metadata is stored."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unicodedata

from .persistence import FileLock, atomic_write_json, check_schema, read_json_object

STATISTICS_SCHEMA_VERSION = 1


COUNT_FIELDS = ('raw_chars', 'corrected_chars', 'finalized_segments')


def count_characters(text: str) -> int:
    """Count Unicode letters/numbers, excluding spaces, punctuation, marks and symbols."""
    return sum(unicodedata.category(char)[0] in 'LN' for char in text)


def session_snapshot(session, *, started_at=None, updated_at=None) -> dict:
    """Build a text-free snapshot on the owner thread before queueing disk work."""
    now = datetime.now(timezone.utc).isoformat()
    final = [segment for segment in session.segments.values()
             if segment.status not in {'partial', 'transcribing'}]
    return dict(session_id=session.id, started_at=started_at or now, updated_at=updated_at or now,
                raw_chars=sum(count_characters(s.raw) for s in final),
                corrected_chars=sum(count_characters(s.corrected) for s in final
                                    if s.status in {'corrected', 'manual'}),
                finalized_segments=len(final))


def _clean(snapshot: dict) -> dict:
    session_id = snapshot['session_id']
    if not isinstance(session_id, str) or not session_id or len(session_id) > 128:
        raise ValueError('Invalid statistics session ID')
    row = {'session_id': session_id}
    for key in ('started_at', 'updated_at'):
        value = snapshot[key]
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        row[key] = parsed.astimezone(timezone.utc).isoformat()
    for key in COUNT_FIELDS:
        value = snapshot[key]
        if type(value) is not int or value < 0:
            raise ValueError('Invalid statistics count')
        row[key] = value
    return row


class StatsStore:
    """Thread-safe across instances/processes; callers run disk methods on workers."""

    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'statistics.json'
        self.lock_path = self.root / '.statistics.lock'

    def _document(self):
        if not self.path.exists():
            return {'version': STATISTICS_SCHEMA_VERSION, 'sessions': {}}
        payload = read_json_object(self.path)
        if (check_schema(payload, STATISTICS_SCHEMA_VERSION, key='version') != STATISTICS_SCHEMA_VERSION
                or not isinstance(payload.get('sessions'), dict)):
            raise ValueError('Invalid statistics file')
        try:
            for key, value in payload['sessions'].items():
                if key != value['session_id']:
                    raise ValueError('Invalid statistics session key')
                _clean(value)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError('Invalid statistics file') from exc
        return payload

    def _read(self):
        return {key: _clean(value) for key, value in self._document()['sessions'].items()}

    def record(self, snapshot: dict) -> dict:
        row = _clean(snapshot)
        with FileLock(self.lock_path):
            document = self._document()
            rows = document['sessions']
            previous = rows.get(row['session_id'])
            if previous:
                row['started_at'] = previous['started_at']
                if row['updated_at'] < previous['updated_at']:
                    return previous
            rows[row['session_id']] = {**(previous or {}), **row}
            atomic_write_json(self.path, document)
        return row

    def rows(self) -> list[dict]:
        with FileLock(self.lock_path):
            rows = self._read()
        return sorted(rows.values(), key=lambda row: row['started_at'], reverse=True)

    def summary(self, current_snapshot=None) -> dict:
        rows = {row['session_id']: row for row in self.rows()}
        if current_snapshot is not None:
            current = _clean(current_snapshot)
            if current['finalized_segments'] or current['session_id'] in rows:
                rows[current['session_id']] = current
        ordered = sorted(rows.values(), key=lambda row: row['started_at'], reverse=True)
        totals = {key: sum(row[key] for row in ordered) for key in COUNT_FIELDS}
        totals['sessions'] = len(ordered)
        return {'rows': ordered, 'totals': totals}
