"""Synthetic timeline tests: no device capture or actual media playback."""
import json
import wave

import pytest

from iris_interview.exceptions import InterviewError
from iris_interview.playback import segment_slices
from iris_interview.storage import ProjectStore, StorageError


def make_recording(store, tmp_path, *, manifest=True):
    doc = store.create_project(recording_enabled=True)
    doc = store.begin_recording_session(doc['id'], doc['revision'])
    session = doc['recording_sessions'][-1]['id']
    directory = store.recording_directory(doc['id'], session)
    directory.mkdir()
    with wave.open(str(directory/'local.wav'), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 32000)
    timeline = {'version': 1, 'clock': 'session_relative_ns', 'chunks': [
        {'file': 'local.wav', 'source': 'local', 'timestamp_ns': 10_000_000_000,
         'file_frame': 0, 'frames': 16000, 'sample_rate': 16000, 'channels': 1},
        {'file': 'local.wav', 'source': 'local', 'timestamp_ns': 20_000_000_000,
         'file_frame': 16000, 'frames': 16000, 'sample_rate': 16000, 'channels': 1}], 'gaps': []}
    if manifest:
        (directory/'manifest.json').write_text(json.dumps(timeline))
        (directory/'.recording.lock').touch()
    return doc, session, directory


def test_finalize_manifest_and_backup_preserve_timeline(tmp_path):
    with ProjectStore(tmp_path/'store') as store:
        doc, session, directory = make_recording(store, tmp_path)
        doc = store.finalize_recording_session(doc['id'], session, doc['revision'])
        assert not directory.exists()
        assert {a['kind'] for a in doc['attachments']} == {'recording', 'recording_manifest'}
        slices = segment_slices(store, doc, {'source': 'local', 'start': 20.2, 'end': 20.6,
                                            'recording_session_id': session})
        assert len(slices) == 1
        assert slices[0].start_ms == 1200 and slices[0].end_ms == 1600
        shifted = segment_slices(store, doc, {'source': 'local', 'start': 120.2, 'end': 120.6,
                                             'session_start_ms': 20200, 'session_end_ms': 20600,
                                             'recording_session_id': session})
        assert shifted == slices
        backup = tmp_path/'backup.zip'
        store.export_project(doc['id'], backup)
    with ProjectStore(tmp_path/'other') as restored:
        doc = restored.import_project(backup)
        parts = segment_slices(restored, doc, {'source': 'local', 'start': 10.2, 'end': 10.5})
        assert parts[0].start_ms == 200 and parts[0].end_ms == 500


def test_gap_has_no_fake_audio_and_other_source_rejected(tmp_path):
    with ProjectStore(tmp_path/'store') as store:
        doc, session, _ = make_recording(store, tmp_path)
        doc = store.finalize_recording_session(doc['id'], session, doc['revision'])
        with pytest.raises(InterviewError):
            segment_slices(store, doc, {'source': 'local', 'start': 14, 'end': 15})
        with pytest.raises(InterviewError):
            segment_slices(store, doc, {'source': 'remote', 'start': 10, 'end': 11})


def test_invalid_manifest_preserves_pending_sources(tmp_path):
    with ProjectStore(tmp_path/'store') as store:
        doc, session, directory = make_recording(store, tmp_path)
        manifest = json.loads((directory/'manifest.json').read_text())
        manifest['chunks'][0]['file'] = '../escape.wav'
        (directory/'manifest.json').write_text(json.dumps(manifest))
        with pytest.raises(StorageError):
            store.finalize_recording_session(doc['id'], session, doc['revision'])
        assert (directory/'local.wav').exists()
        assert store.get_project(doc['id'])['recording_sessions'][0]['state'] == 'pending'


def test_legacy_wav_without_manifest_cannot_guess_time(tmp_path):
    with ProjectStore(tmp_path/'store') as store:
        doc, session, _ = make_recording(store, tmp_path, manifest=False)
        doc = store.finalize_recording_session(doc['id'], session, doc['revision'])
        with pytest.raises(InterviewError):
            segment_slices(store, doc, {'source': 'local', 'start': 0, 'end': 1})


def test_panel_never_autoplays(monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from iris_interview import playback
    class Event:
        def connect(self, _callback):
            pass
    class Player:
        def __init__(self, _parent):
            self.mediaStatusChanged, self.errorOccurred = Event(), Event()
            self.calls = []
        def setAudioOutput(self, _output):
            pass
        def stop(self):
            self.calls.append('stop')
    monkeypatch.setattr(playback, 'QMediaPlayer', Player)
    monkeypatch.setattr(playback, 'QAudioOutput', lambda _parent: object())
    app = QApplication.instance() or QApplication([])
    panel = playback.PlaybackPanel(None, {'attachments': []}, {'source': 'local', 'start': 0, 'end': 1})
    assert panel.player.calls == [] and panel.playing is False
    panel.play_segment()
    assert '录音时间轴' in panel.status.text()
    panel.close()
    app.processEvents()
