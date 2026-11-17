"""Synthetic-only safe cancellation checks."""
import json
import pytest
from iris_interview.storage import ProjectStore, StorageError


def test_cancel_empty_manifest_preserves_any_audio_or_unknown_content(tmp_path):
    with ProjectStore(tmp_path/'store') as store:
        doc = store.create_project(recording_enabled=True)
        doc = store.begin_recording_session(doc['id'], doc['revision'])
        session = doc['recording_sessions'][-1]['id']
        directory = store.recording_directory(doc['id'], session)
        directory.mkdir()
        (directory/'manifest.json').write_text(json.dumps({
            'version': 1, 'clock': 'session_relative_ns', 'chunks': [], 'gaps': []}))
        (directory/'.recording.lock').touch()
        extra = directory/'unexpected.bin'
        extra.write_bytes(b'preserve')
        with pytest.raises(StorageError):
            store.cancel_empty_recording_session(doc['id'], session, doc['revision'])
        assert extra.read_bytes() == b'preserve'
        extra.unlink()
        updated = store.cancel_empty_recording_session(doc['id'], session, doc['revision'])
        assert updated['recording_sessions'] == [] and not directory.exists()


def test_cancel_nonempty_manifest_refuses_and_preserves(tmp_path):
    with ProjectStore(tmp_path/'store') as store:
        doc = store.create_project(recording_enabled=True)
        doc = store.begin_recording_session(doc['id'], doc['revision'])
        session = doc['recording_sessions'][-1]['id']
        directory = store.recording_directory(doc['id'], session)
        directory.mkdir()
        (directory/'local.wav').write_bytes(b'synthetic-audio')
        (directory/'manifest.json').write_text(json.dumps({
            'version': 1, 'clock': 'session_relative_ns', 'chunks': [{'file': 'local.wav'}], 'gaps': []}))
        with pytest.raises(StorageError):
            store.cancel_empty_recording_session(doc['id'], session, doc['revision'])
        assert (directory/'local.wav').exists() and (directory/'manifest.json').exists()
