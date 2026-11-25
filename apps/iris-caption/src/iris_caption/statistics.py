"""Privacy-preserving transcript counts; no transcript or meeting metadata is stored."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
import time
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
             if segment.status not in {'partial', 'transcribing', 'interrupted'}]
    return dict(session_id=session.id, started_at=started_at or now, updated_at=updated_at or now,
                raw_chars=sum(count_characters(s.raw) for s in final),
                corrected_chars=sum(count_characters(s.corrected) for s in final
                                    if s.status in {'corrected', 'manual'}),
                finalized_segments=len(final))


class SessionCounts:
    """GUI-owned integer ledger. No raw text, corrected text or speaker names retained."""

    def __init__(self, session_id, started_at):
        self.session_id, self.started_at = session_id, started_at
        self._contributions = {}
        self._totals = [0, 0, 0]

    def update(self, segment):
        if segment.status in {'partial', 'transcribing', 'interrupted'}:
            current = (0, 0, 0)
        else:
            current = (count_characters(segment.raw), count_characters(segment.corrected)
                       if segment.status in {'corrected', 'manual'} else 0, 1)
        old = self._contributions.get(segment.id, (0, 0, 0))
        if old == current:
            return False
        if current[2]:
            self._contributions[segment.id] = current
        else:
            self._contributions.pop(segment.id, None)
        for i in range(3):
            self._totals[i] += current[i] - old[i]
        return True

    def snapshot(self):
        return dict(session_id=self.session_id, started_at=self.started_at,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    **dict(zip(COUNT_FIELDS, self._totals)))


class StatisticsWriter:
    """One latest snapshot per session; periodic writes even under continuous updates."""

    def __init__(self, store, report_error, interval=1.0):
        self.store, self.report_error, self.interval = store, report_error, interval
        self._cv = threading.Condition()
        self._pending = {}
        self._due = {}
        self._closing = False
        self._thread = threading.Thread(target=self._run, name='caption-stats', daemon=True)
        self._thread.start()

    def submit(self, snapshot, *, flush=False):
        row = _clean(snapshot)  # Strict allowlist even in the pending buffer.
        key = row['session_id']
        with self._cv:
            if self._closing:
                return False
            self._pending[key] = row
            if flush:
                self._due[key] = 0
            else:
                self._due.setdefault(key, time.monotonic() + self.interval)
            self._cv.notify()
        return True

    def _next(self):
        with self._cv:
            while True:
                if self._pending:
                    key = min(self._due, key=self._due.get)
                    delay = self._due[key] - time.monotonic()
                    if self._closing or delay <= 0:
                        del self._due[key]
                        return self._pending.pop(key)
                    self._cv.wait(delay)
                elif self._closing:
                    return None
                else:
                    self._cv.wait()

    def _run(self):
        while (row := self._next()) is not None:
            try:
                self.store.record(row)
            except Exception:
                self.report_error()  # Sanitized caller-provided notice, never transcript data.

    def close(self):
        """Request immediate drain. Never wait for filesystem I/O on the GUI thread."""
        with self._cv:
            if self._closing:
                return
            self._closing = True
            self._cv.notify()
        # Keep the interpreter alive for a bounded grace period on normal quit.
        threading.Thread(target=self._finish, name='caption-stats-drain', daemon=False).start()

    def _finish(self):
        if not self.wait(3):
            self.report_error()

    def wait(self, timeout):
        self._thread.join(timeout)
        return not self._thread.is_alive()


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
