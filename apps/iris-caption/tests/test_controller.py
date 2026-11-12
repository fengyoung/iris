"""Offline controller orchestration: no processes, microphone, keychain or HTTP."""
import os
import queue
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from iris_caption import controller as module
from iris_caption.controller import Controller
from iris_caption.materials import MaterialIndex
from iris_caption.session import Session


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def control(app, monkeypatch, tmp_path):
    monkeypatch.setattr(module, 'data_dir', lambda: tmp_path)
    c = Controller.__new__(Controller)
    QObject.__init__(c)
    c.window = Mock()
    c.window.closing_after_save = False
    c.session = Session()
    c.settings = {}
    c.knowledge = Mock()
    c.knowledge.active_bundle.return_value = {}
    c.rules = Mock()
    c.rules.apply.side_effect = lambda text, replacements: text
    c.index = MaterialIndex()
    c.retained_ids = set()
    c.background = '主题：original'
    c.results = queue.Queue()
    c._prepare_queue = queue.Queue(maxsize=1)
    c.tasks, c.retry_tasks = {}, {}
    c.service = None
    c.audio_events = c.commands = c.process = None
    c._closing = c.paused = False
    c.capture_state = "idle"
    c._capture_pending = None
    c._index_ready = True
    c._material_dialog = None
    return c


def final(c, sid='one'):
    return c.session.apply_audio(dict(type='final', session_id=c.session.id,
        segment_id=sid, text='apple', start_ms=0, end_ms=1000))


def task(c, segment):
    return dict(session_id=c.session.id, segment_id=segment.id, revision=segment.revision,
                text=segment.raw, context=[], references=[], rules_prompt='')


def test_speaker_results_and_stop_pending_are_revision_guarded(control):
    c = control
    c.session.set_speakers_enabled(True)
    s = final(c)
    c._event(dict(type='speaker', session_id=c.session.id, segment_id=s.id,
                  audio_revision=s.audio_revision + 1, speaker_state='confirmed', speaker_id='A'))
    assert s.speaker_state == 'pending'
    c._event(dict(type='speaker', session_id=c.session.id, segment_id=s.id,
                  audio_revision=s.audio_revision, speaker_state='confirmed', speaker_id='A'))
    assert s.speaker_id == 'A'
    pending = final(c, 'two')
    c._event(dict(type='stopped', session_id=c.session.id))
    assert pending.speaker_state == 'unknown'
    assert s.speaker_id == 'A'


def test_speaker_failure_keeps_transcription_state(control):
    c = control
    c.capture_state = 'recording'
    c._event(dict(type='speaker_status', session_id=c.session.id, text='不可用'))
    assert c.capture_state == 'recording'
    c.window.set_speaker_status.assert_called_once_with('不可用')


def test_snapshot_finalization_and_bounded_fallback(control):
    c = control
    identifier = c.index.add_text('material', 'apple original')
    c._schedule(final(c))
    base, snapshot = c._prepare_queue.queue[0]
    c.index.remove(identifier)
    c.background = 'changed'
    assert snapshot.search('apple') == ['[material] apple original']
    assert base['background'] == '主题：original'
    assert base['rules_prompt'] == ''
    assert base['protected_terms'] == []
    c._schedule(final(c, 'two'))
    assert c._prepare_queue.qsize() == 1
    assert c.retry_tasks['two']['references'] == []


def test_stale_errors_and_audio_error_classification(control):
    c = control
    c._event(dict(type='error', revision=0, session_id='old', text='stale'))
    c.window.set_status.assert_not_called()
    c._event(dict(type='error', revision=0, session_id=c.session.id, text='microphone failed'))
    c.window.set_status.assert_called_once_with('microphone failed')


def test_no_service_preserves_backlog_without_retrying(control):
    c = control
    t = task(c, final(c))
    c._event({'type': 'task_ready', 'task': t})
    c.retry_pending()
    assert c.retry_tasks == {'one': t}
    c.service = Mock()
    c.service.submit.return_value = False
    c.retry_pending()
    assert 'one' in c.retry_tasks
    c.service.submit.return_value = True
    c.retry_pending()
    assert not c.retry_tasks


def test_manual_or_old_revision_cannot_resubmit(control):
    c = control
    segment = final(c)
    c.retry_tasks['one'] = task(c, segment)
    c.session.edit('one', 'manual')
    c.service = Mock()
    c.retry_pending()
    c.service.submit.assert_not_called()
    assert not c.retry_tasks


def test_invalid_task_not_retried_forever(control):
    c = control
    c.service = Mock()
    c.service.submit.side_effect = ValueError('invalid text')
    c._event({'type': 'task_ready', 'task': task(c, final(c))})
    assert not c.retry_tasks and not c.tasks


class Process:
    def __init__(self, fail=False):
        self.alive = False
        self.fail = fail
        self.closed = False
    def start(self):
        if self.fail:
            raise OSError('spawn failed')
        self.alive = True
    def is_alive(self):
        return self.alive
    def close(self):
        self.closed = True
    def join(self, *args, **kwargs):
        pytest.fail('GUI thread must not join')


def fake_context(monkeypatch, process):
    monkeypatch.setattr(module, 'resolve_vad_model_path', lambda configured=None: '/fake/validated-vad')
    context = SimpleNamespace(Queue=lambda **kwargs: queue.Queue(**kwargs),
                              Process=lambda **kwargs: process)
    monkeypatch.setattr(module.mp, 'get_context', lambda _: context)
    monkeypatch.setattr(module.QMessageBox, 'question', lambda *args: module.QMessageBox.StandardButton.Yes)


def test_missing_vad_blocks_capture_without_reset(control, monkeypatch, tmp_path):
    c = control
    c.settings['model_path'] = str(tmp_path)
    old = c.session
    monkeypatch.setattr(module, 'resolve_vad_model_path', lambda configured=None: '')
    spawn = Mock()
    monkeypatch.setattr(module.mp, 'get_context', spawn)
    c.show_settings = Mock()
    c.start()
    assert c.capture_state == 'error'
    assert c.session is old
    spawn.assert_not_called()
    c.show_settings.assert_called_once()
    c.window.set_status.assert_called_with(module.VAD_REQUIRED_MESSAGE)


def test_capture_passes_resolved_vad_and_requires_it(control, monkeypatch, tmp_path):
    c = control
    c.settings['model_path'] = str(tmp_path)
    process = Process()
    fake_context(monkeypatch, process)
    constructor = Mock(return_value=process)
    context = SimpleNamespace(Queue=lambda **kwargs: queue.Queue(**kwargs), Process=constructor)
    monkeypatch.setattr(module.mp, 'get_context', lambda _: context)
    c.start()
    config = constructor.call_args.kwargs['args'][0]
    assert config['vad_required'] is True
    assert config['vad_model_path'] == '/fake/validated-vad'
    assert 'vad_model_path' not in c.settings


def test_start_failure_keeps_session_and_cleanup(control, monkeypatch, tmp_path):
    c = control
    final(c)
    old = c.session
    c.settings['model_path'] = str(tmp_path)
    process = Process(fail=True)
    fake_context(monkeypatch, process)
    c.start()
    assert c.session is old
    assert c.process is c.audio_events is c.commands is None
    assert process.closed
    assert c.capture_state == 'error'
    c.window.clear_segments.assert_not_called()
    process.fail = False
    c.start()
    assert c.capture_state == 'loading'
    assert c.session is not old


def test_restart_cancels_old_work_and_clears_ui_rules(control, monkeypatch, tmp_path):
    c = control
    final(c)
    old_id = c.session.id
    c.service = Mock()
    c.settings['model_path'] = str(tmp_path)
    process = Process()
    fake_context(monkeypatch, process)
    c.start()
    assert c.session.id != old_id
    c.service.cancel.assert_called_once_with(old_id)
    c.rules.clear_session.assert_called_once()
    c.window.clear_segments.assert_called_once()
    assert c.background == ''
    c.stop()
    assert c.commands.get_nowait() == {'type': 'stop'}
    process.alive = False
    c.poll()
    assert process.closed and c.process is None


def test_full_command_queue_never_blocks_or_changes_pause(control):
    c = control
    c.process = Process()
    c.process.alive = True
    c.commands = queue.Queue(maxsize=1)
    c.commands.put_nowait({'type': 'stop'})
    c.capture_state = "recording"
    c.pause()
    assert not c.paused


def test_export_srt_preserves_dirty_markdown_clears(control, monkeypatch, tmp_path):
    c = control
    final(c)
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', lambda *args: (str(tmp_path / 'out'), ''))
    monkeypatch.setattr(module, 'export_session', Mock())
    c.export('srt', 'corrected')
    assert c.session.dirty
    c.window.mark_saved.assert_not_called()
    assert 'Markdown' in c.window.set_status.call_args.args[0]
    c.export('markdown', 'corrected')
    assert not c.session.dirty
    c.window.mark_saved.assert_called_once()


def test_material_background_updates_live(control):
    c = control
    c._material_dialog = SimpleNamespace(background='主题：new')
    c._background_changed()
    assert c.background == '主题：new'
    assert c.session.background_version == 1


def test_superseded_service_callback_is_ignored(control):
    c = control
    segment = final(c)
    c._service_generation = 2
    c._event(dict(type='correction', session_id=c.session.id, segment_id=segment.id,
                  revision=segment.revision, corrected_text='obsolete', service_generation=1))
    assert segment.corrected == ''


def test_keychain_failure_graceful(control, monkeypatch):
    c = control
    monkeypatch.setattr(module, 'available_key', Mock(side_effect=RuntimeError('keychain inaccessible')))
    c._init_service()
    assert c.service is None


def test_new_meeting_quarantines_retained_remote_and_drops_temp(control, monkeypatch, tmp_path):
    c = control
    retained = c.index.add_text('remote', 'apple secret', 'https://team.feishu.cn/docx/abc')
    c.index.save_retained(tmp_path / 'materials', [retained])
    c.index.add_text('temporary', 'apple temporary')
    final(c)
    c.settings['model_path'] = str(tmp_path)
    fake_context(monkeypatch, Process())
    # Run only this local reload synchronously for deterministic testing.
    monkeypatch.setattr(module.threading, 'Thread', lambda target, **kw: SimpleNamespace(start=target))
    c.start()
    assert c.index.list_materials() == []
    c.poll()
    rows = c.index.list_materials()
    assert len(rows) == 1 and rows[0]['id'] == retained
    assert rows[0]['requires_verification']
    assert c.index.search('apple') == []
    assert c.background == ''


def test_initial_start_preserves_prepared_materials(control, monkeypatch, tmp_path):
    c = control
    c.index.add_text('prepared', 'apple')
    c.settings['model_path'] = str(tmp_path)
    fake_context(monkeypatch, Process())
    c.start()
    assert c.background == '主题：original'
    assert len(c.index.list_materials()) == 1


def test_audio_error_matching_segment_never_becomes_correction(control):
    c = control
    segment = final(c)
    c._event(dict(type='error', session_id=c.session.id, segment_id=segment.id,
                  revision=segment.revision, code='device_failed', text='microphone failed'))
    assert segment.status == 'pending'
    c.window.set_status.assert_called_once_with('microphone failed')


def test_old_index_generation_cannot_replace_new_meeting(control):
    c = control
    current = c.index
    c._index_generation = 1
    c._event(dict(type='index_ready', index=MaterialIndex(), retained=[]))
    assert c.index is current


def test_no_key_final_segment_has_explicit_local_status(control):
    c = control
    segment = final(c)
    c._schedule(segment)
    assert segment.status == 'local'


def test_embedding_reload_coalesces_and_preserves_index(control, monkeypatch):
    c = control
    original = c.index
    identifier = original.add_text('prepared', 'apple')
    jobs = []
    monkeypatch.setattr(module.threading, 'Thread',
        lambda target, **kw: SimpleNamespace(start=lambda: jobs.append(target)))
    calls = []
    monkeypatch.setattr(original, 'reload_embeddings', lambda path: calls.append(path))
    c.settings['embedding_model_path'] = 'first'
    c._reload_embeddings()
    c.settings['embedding_model_path'] = 'latest'
    c._reload_embeddings()
    assert len(jobs) == 1
    jobs[0]()
    assert calls == ['latest']
    assert c.index is original and c.index.list_materials()[0]['id'] == identifier
    assert not c._embedding_loading


def test_embedding_settings_deferred_until_index_ready(control, monkeypatch):
    c = control
    c._index_ready = False
    c.settings['embedding_model_path'] = 'latest'
    c._reload_embeddings()
    replacement = MaterialIndex()
    calls = []
    monkeypatch.setattr(replacement, 'reload_embeddings', calls.append)
    monkeypatch.setattr(module.threading, 'Thread', lambda target, **kw: SimpleNamespace(start=target))
    c._event(dict(type='index_ready', index=replacement, retained=[]))
    assert calls == ['latest']


def test_close_save_waits_for_final_audio_and_process_exit(control, monkeypatch, tmp_path):
    c = control
    c.window.closing_after_save = True
    c.process = Process()
    c.process.alive = True
    c.audio_events, c.commands = queue.Queue(), queue.Queue()
    exported = []
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', lambda *args: (str(tmp_path / 'out.md'), ''))
    monkeypatch.setattr(module, 'export_session', lambda session, *args: exported.append([s.raw for s in session.ordered()]))
    c.export('markdown', 'both')
    assert not exported
    assert c.commands.get_nowait() == {'type': 'stop'}
    c.audio_events.put(dict(type='final', session_id=c.session.id, segment_id='last',
                            start_ms=0, end_ms=1000, text='final speech'))
    c.poll()
    assert not exported  # Flush is not complete while worker still lives.
    c.process.alive = False
    c.poll()
    assert exported == [['final speech']]
    c.window.set_capture_state.assert_called_with("idle", pending=None)
    c.window.prepare_close_export.assert_called_once()
    c.window.mark_saved.assert_called_once()


def test_manual_export_does_not_stop_active_capture(control, monkeypatch, tmp_path):
    c = control
    final(c)
    c.process = Process()
    c.process.alive = True
    c.commands = queue.Queue()
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', lambda *args: (str(tmp_path / 'out.md'), ''))
    export = Mock()
    monkeypatch.setattr(module, 'export_session', export)
    c.export('markdown', 'both')
    export.assert_called_once()
    assert c.commands.empty()


def test_close_save_timeout_cancels_without_export(control, monkeypatch):
    c = control
    c.window.closing_after_save = True
    c.process = Process()
    c.process.alive = True
    c.commands = queue.Queue()
    monkeypatch.setattr(module.time, 'monotonic', lambda: 100)
    c.export('markdown', 'both')
    monkeypatch.setattr(module.time, 'monotonic', lambda: 131)
    c.poll()
    assert c._deferred_export is None
    c.window.cancel_pending_close.assert_called_once()
    c.window.mark_saved.assert_not_called()


def test_fatal_audio_error_survives_stopped_and_poll(control):
    c = control
    c.process = Process()
    c.audio_events = queue.Queue()
    c.audio_events.put(dict(type='error', session_id=c.session.id, code='model_failed', text='Check model files'))
    c.audio_events.put(dict(type='stopped', session_id=c.session.id, text='stopped'))
    c.poll()
    assert c.window.set_status.call_args.args[0] == 'Check model files'


def test_abnormal_exit_without_error_is_actionable(control):
    c = control
    c.process = Process()
    c.process.exitcode = 1
    c.poll()
    assert '异常退出' in c.window.set_status.call_args.args[0]


def test_permanent_correction_failure_keeps_snapshot_until_settings_change(control, monkeypatch):
    c = control
    segment = final(c)
    snapshot = task(c, segment)
    c.tasks[segment.id] = snapshot
    c._service_generation = 1
    c.service = Mock()
    c._event(dict(type='error', service_generation=1, session_id=c.session.id,
                  segment_id=segment.id, revision=segment.revision, retryable=False, error='http_error'))
    assert segment.status == 'error'
    assert c.tasks[segment.id] == snapshot and not c.retry_tasks
    c.retry_pending()
    c.service.submit.assert_not_called()
    replacement = Mock()
    replacement.submit.return_value = True
    monkeypatch.setattr(module, 'available_key', lambda: 'replacement-key')
    monkeypatch.setattr(module, 'CorrectionService', lambda *args, **kwargs: replacement)
    c._init_service()
    c.retry_pending()
    replacement.submit.assert_called_once_with(snapshot)


def test_partial_edit_is_ignored_without_cancelling_work(control):
    c = control
    segment = c.session.apply_audio(dict(type='partial', session_id=c.session.id,
        segment_id='partial', start_ms=0, end_ms=1, text='unfinished'))
    c.edit(segment.id, 'manual')
    assert not segment.manual and segment.raw == 'unfinished'


def test_statistics_final_correction_and_manual_edit(control, monkeypatch):
    from iris_caption import dialogs
    c = control
    c._stats_queue = queue.Queue()
    c._stats_started_at = '2026-01-01T00:00:00+00:00'
    c._event(dict(type='final', session_id=c.session.id, segment_id='one',
                  text='中文 abc!', start_ms=0, end_ms=1))
    c.window.set_statistics.assert_called_with(5, 0)
    queued = c._stats_queue.get_nowait()
    assert queued['raw_chars'] == 5 and 'text' not in queued
    c._service_generation = 1
    c._event(dict(type='correction', service_generation=1, session_id=c.session.id,
                  segment_id='one', revision=1, corrected_text='校正完成'))
    c.window.set_statistics.assert_called_with(5, 4)
    monkeypatch.setattr(dialogs, 'choose_rule_scope', lambda *args: 'none')
    c.edit('one', '短')
    c.window.set_statistics.assert_called_with(5, 1)
    assert c._stats_queue.queue[-1]['corrected_chars'] == 1
    assert c._stats_queue.queue[-1]['started_at'] == c._stats_started_at


def test_statistics_worker_drains_in_order_and_reports_failure(control):
    c = control
    c._stats = Mock()
    c._stats.record.side_effect = [OSError('disk unavailable'), None]
    c._stats_queue = queue.Queue()
    c._stats_queue.put({'raw_chars': 1})
    c._stats_queue.put({'raw_chars': 2})
    c._stats_queue.put(None)
    c._write_statistics()
    assert c._stats.record.call_count == 2
    assert c.results.get_nowait()['type'] == 'status'



def test_worker_acknowledged_lifecycle_and_programmatic_guards(control, monkeypatch, tmp_path):
    c = control
    c.settings['model_path'] = str(tmp_path)
    process = Process()
    fake_context(monkeypatch, process)
    c.start()
    session_id = c.session.id
    assert c.capture_state == 'loading'
    c.start()
    c.pause()
    assert c.commands.empty() and c.session.id == session_id
    def status(state):
        c._event(dict(type='status', session_id=session_id, state=state))
    status('recording')
    assert c.capture_state == 'recording'
    c.pause()
    c.pause()
    c.start()
    assert c.commands.get_nowait() == {'type': 'pause'}
    assert c.commands.empty() and c._capture_pending == 'pause'
    status('recording')  # older readiness cannot clear pending pause
    assert c._capture_pending == 'pause'
    status('paused')
    assert c.paused and c._capture_pending is None
    c.start()
    c.start()
    c.pause()
    assert c.commands.get_nowait() == {'type': 'resume'}
    assert c.commands.empty()
    status('recording')
    assert not c.paused
    c.stop()
    c.stop()
    c.start()
    c.pause()
    assert c.commands.get_nowait() == {'type': 'stop'}
    assert c.commands.empty() and c.capture_state == 'stopping'
    status('paused')
    status('recording')
    assert c.capture_state == 'stopping'
    process.alive = False
    c.poll()
    assert c.capture_state == 'idle'
    c.start()
    assert c.capture_state == 'loading' and c.session.id != session_id


def test_worker_failure_drains_before_restart(control, monkeypatch, tmp_path):
    c = control
    c.settings['model_path'] = str(tmp_path)
    process = Process()
    fake_context(monkeypatch, process)
    c.start()
    old = c.session.id
    c._event(dict(type='error', session_id=old, text='Model failed'))
    c.start()
    assert c.capture_state == 'stopping' and c.session.id == old
    process.alive = False
    c.poll()
    assert c.capture_state == 'error'
    c.start()
    assert c.capture_state == 'loading' and c.session.id != old


def test_close_pending_rejects_capture_actions_without_duplicate_stop(control, monkeypatch, tmp_path):
    c = control
    c.settings['model_path'] = str(tmp_path)
    fake_context(monkeypatch, Process())
    c.start()
    c.stop()
    assert c.commands.get_nowait() == {'type': 'stop'}
    c.window.closing_after_save = True
    c.export('markdown', 'both')
    c.start()
    c.pause()
    c.stop()
    c.export('markdown', 'both')
    assert c.commands.empty() and c.capture_state == 'stopping'
    assert c._deferred_export is not None


def test_empty_export_is_rejected_even_programmatically(control, monkeypatch):
    choose = Mock()
    monkeypatch.setattr(module.QFileDialog, 'getSaveFileName', choose)
    control.export('markdown', 'both')
    choose.assert_not_called()


def test_stop_while_loading_ignores_late_readiness(control, monkeypatch, tmp_path):
    c = control
    c.settings['model_path'] = str(tmp_path)
    fake_context(monkeypatch, Process())
    c.start()
    c.stop()
    c._event(dict(type='status', session_id=c.session.id, state='recording', text='Recording'))
    c.start()
    c.pause()
    assert c.capture_state == 'stopping'
    assert c.commands.get_nowait() == {'type': 'stop'}
    assert c.commands.empty()


def test_failed_stop_queue_preserves_retryable_controls(control, monkeypatch, tmp_path):
    c = control
    c.settings['model_path'] = str(tmp_path)
    fake_context(monkeypatch, Process())
    c.start()
    c.commands = queue.Queue(maxsize=1)
    c.commands.put_nowait({'type': 'pause'})
    c.stop()
    assert c.capture_state == 'loading'
    c.commands.get_nowait()
    c.stop()
    assert c.capture_state == 'stopping'
