"""Synthetic Qt capture lifecycle and native protocol integration tests."""
import json
import os
import threading

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QProcess, QTimer, QThreadPool, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QTabWidget, QWidget

from iris_interview.capture_ui import _CaptureSession, _complete_devices, _merge_managed_project


@pytest.fixture(scope='module')
def app(qt_application_lifetime):
    return qt_application_lifetime


class Window(QWidget):
    audio_event = Signal(object)

    def __init__(self):
        super().__init__()
        self.project = {'id': 'p', 'revision': 1, 'status': 'ready', 'transcript': []}
        self.runtime = object()
        self.pause_button, self.tabs = QPushButton(), QTabWidget()
        self.tabs.addTab(QWidget(), 'a')
        self.tabs.addTab(QWidget(), 'b')
        self.saved, self.errors, self.events = [], [], []
        self.tasks, self.pool = [], QThreadPool.globalInstance()
        self.audio_event.connect(self.events.append)

    def save(self):
        self.saved.append(threading.get_ident())
        return True

    def error(self, message):
        self.errors.append(message)

    def statusBar(self):
        return self

    def showMessage(self, _message):
        pass


def emit_thread(app, session, event):
    worker = threading.Thread(target=lambda: session.received.emit(event))
    worker.start()
    worker.join()
    app.processEvents()


def test_worker_status_only_real_recording_starts_project(app):
    window = Window()
    session = _CaptureSession(window, 'p')
    emit_thread(app, session, {'type': 'status', 'state': 'loading'})
    emit_thread(app, session, {'type': 'status', 'state': 'starting'})
    assert window.project['status'] == 'ready'
    emit_thread(app, session, {'type': 'status', 'state': 'recording'})
    assert window.project['status'] == 'interviewing'
    assert window.saved == [threading.get_ident()]
    emit_thread(app, session, {'type': 'finalized', 'state': 'failed'})
    assert window.runtime is None and window.project['status'] == 'review'


def test_native_device_fields_and_errors_are_visible(app):
    class Process:
        def readAllStandardOutput(self):
            return '\n'.join(json.dumps(e) for e in [
                {'protocol': 1, 'event': 'devices', 'applications': [{'pid': 12, 'name': 'Synthetic'}],
                 'microphones': [{'id': 'synthetic-uid', 'name': 'Mic'}]},
                {'protocol': 1, 'event': 'error', 'message': 'permission required'}]).encode()
        def readAllStandardError(self):
            return b''
    apps, mics, status = QComboBox(), QComboBox(), QLabel()
    _complete_devices(0, QProcess.NormalExit, Process(), QTimer(),
                      {'name': 'list', 'timed_out': False}, apps, mics, status, lambda _: None)
    assert apps.currentData() == 12 and mics.currentData() == 'synthetic-uid'
    assert status.text() == 'permission required'


def test_finalize_recording_runs_after_finalized_and_preserves_dict(app, tmp_path):
    class Store:
        calls = []
        def recording_directory(self, *_):
            return tmp_path / 'nonexistent'
        def cancel_empty_recording_session(self, project_id, session_id, expected_revision):
            self.worker_thread = threading.get_ident()
            self.calls.append((project_id, session_id, expected_revision))
            return {'id': 'p', 'revision': 2, 'status': 'review', 'transcript': []}
    window = Window()
    window.store = Store()
    original = window.project
    session = _CaptureSession(window, 'p', 'recording-id')
    session.dispatch({'type': 'status', 'state': 'stopped'})
    assert not window.store.calls and window.runtime is not None
    session.dispatch({'type': 'finalized', 'state': 'stopped'})
    window.pool.waitForDone(3000)
    app.processEvents()
    assert window.project is original and window.project['revision'] == 2
    assert window.store.calls == [('p', 'recording-id', 1)]
    assert window.store.worker_thread != threading.get_ident()
    assert not window.tasks


def test_stale_project_events_do_not_mutate_current(app):
    window = Window()
    session = _CaptureSession(window, 'another-project')
    session.dispatch({'type': 'status', 'state': 'recording'})
    assert window.project['status'] == 'ready' and not window.saved


def test_next_capture_offsets_project_time_but_retains_session_time(app):
    window = Window()
    window.project['transcript'] = [{'end': 35.2}]
    window.project['capture_elapsed_seconds'] = 40
    session = _CaptureSession(window, 'p', 'session-two')
    session.dispatch({'type': 'final', 'start_ms': 1200, 'end_ms': 2500,
                      'start_ns': 1_200_000_000, 'end_ns': 2_500_000_000})
    event = window.events[-1]
    assert event['start_ms'] == 41200 and event['end_ms'] == 42500
    assert event['session_start_ms'] == 1200 and event['session_end_ms'] == 2500
    assert event['start_ns'] == 41_200_000_000
    session.dispatch({'type': 'gap', 'start_ns': 3_000_000_000, 'end_ns': 4_000_000_000})
    assert window.events[-1]['start_ns'] == 43_000_000_000


def test_managed_merge_preserves_live_editor_objects():
    plan, resume, notes = {'questions': []}, {'text': 'synthetic'}, []
    project = {'plan': plan, 'resume': resume, 'notes': notes, 'revision': 1}
    _merge_managed_project(project, {'plan': {'questions': ['stale']}, 'resume': {}, 'notes': ['stale'],
                                     'revision': 2, 'attachments': [{'id': 'a'}]})
    assert project['plan'] is plan and project['resume'] is resume and project['notes'] is notes
    assert project['revision'] == 2 and project['attachments'] == [{'id': 'a'}]
