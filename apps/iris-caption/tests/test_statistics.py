"""Counting, overwrite semantics, privacy and concurrent history updates."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from iris_caption.session import Session, Segment
from iris_caption.statistics import StatsStore, count_characters, session_snapshot


def snapshot(identifier='one', raw=10, corrected=5, stamp='2026-01-01T00:00:00+00:00'):
    return dict(session_id=identifier, started_at=stamp, updated_at=stamp,
                raw_chars=raw, corrected_chars=corrected, finalized_segments=1)


def test_unicode_count_uses_letters_and_numbers_only():
    assert count_characters('中文 A９1，。\n\t!🙂+ e\u0301') == 6
    assert count_characters('') == 0
    assert count_characters('①Ⅲ한あ𠀀') == 5


def test_only_final_raw_and_completed_corrections():
    session = Session()
    for index, status in enumerate(('partial', 'pending', 'local', 'retry', 'error', 'corrected', 'manual')):
        session.segments[str(index)] = Segment(str(index), 0, 1, raw='原文，', corrected='已校正。', status=status)
    row = session_snapshot(session)
    assert row['raw_chars'] == 12
    assert row['corrected_chars'] == 6
    assert row['finalized_segments'] == 6
    session.edit('6', '')
    assert session_snapshot(session)['corrected_chars'] == 3


def test_idempotent_overwrite_and_edit_recalculation(tmp_path):
    store = StatsStore(tmp_path)
    store.record(snapshot())
    store.record(snapshot())
    assert store.summary()['totals']['raw_chars'] == 10
    store.record(snapshot(raw=3, corrected=1, stamp='2026-01-02T00:00:00+00:00'))
    row = store.rows()[0]
    assert row['raw_chars'] == 3 and row['corrected_chars'] == 1
    assert row['started_at'] == '2026-01-01T00:00:00+00:00'
    store.record(snapshot(raw=99))
    assert store.rows()[0]['raw_chars'] == 3


def test_summary_overlays_current_without_duplicate_or_write(tmp_path):
    store = StatsStore(tmp_path)
    store.record(snapshot())
    summary = store.summary(snapshot(raw=20))
    assert summary['totals'] == dict(raw_chars=20, corrected_chars=5, finalized_segments=1, sessions=1)
    assert store.rows()[0]['raw_chars'] == 10


def test_privacy_allowlist_drops_all_extra_fields(tmp_path):
    store = StatsStore(tmp_path)
    row = snapshot()
    row.update(text='SECRET TEXT', title='SECRET TITLE', materials=['SECRET MATERIAL'], state='SECRET')
    store.record(row)
    payload = store.path.read_text()
    assert 'SECRET' not in payload
    persisted = json.loads(payload)['sessions']['one']
    assert set(persisted) == {'session_id', 'started_at', 'updated_at', 'raw_chars',
                              'corrected_chars', 'finalized_segments'}


def test_independent_instances_merge_under_file_lock(tmp_path):
    def write(index):
        StatsStore(tmp_path).record(snapshot(str(index), raw=index, corrected=0))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(48)))
    totals = StatsStore(tmp_path).summary()['totals']
    assert totals['sessions'] == 48
    assert totals['raw_chars'] == sum(range(48))


def test_corrupt_history_is_not_silently_overwritten(tmp_path):
    store = StatsStore(tmp_path)
    store.path.write_text('{broken')
    with pytest.raises(ValueError):
        store.record(snapshot())
    assert store.path.read_text() == '{broken'


@pytest.mark.parametrize('field,value', [('raw_chars', -1), ('corrected_chars', True),
                                        ('started_at', 'SECRET TITLE')])
def test_invalid_metadata_rejected_without_write(tmp_path, field, value):
    row = snapshot()
    row[field] = value
    store = StatsStore(tmp_path)
    with pytest.raises(ValueError):
        store.record(row)
    assert not store.path.exists()


def test_history_dialog_loads_off_gui_thread(tmp_path):
    import os
    import threading
    import time
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from iris_caption.statistics_dialog import StatsDialog

    app = QApplication.instance() or QApplication([])
    gui_thread = threading.get_ident()
    threads = []

    class Store(StatsStore):
        def summary(self, current_snapshot=None):
            threads.append(threading.get_ident())
            return super().summary(current_snapshot)

    store = Store(tmp_path)
    store.record(snapshot())
    captures = []

    def current():
        captures.append(threading.get_ident())
        return snapshot(raw=25, corrected=12)

    dialog = StatsDialog(store, current)
    deadline = time.monotonic() + 3
    while dialog._task is not None and time.monotonic() < deadline:
        app.processEvents()
    assert dialog._task is None
    assert captures == [gui_thread]
    assert threads and threads[0] != gui_thread
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == '25'
    assert '25' in dialog.total_label.text()
    assert store.rows()[0]['raw_chars'] == 10
    dialog.close()

