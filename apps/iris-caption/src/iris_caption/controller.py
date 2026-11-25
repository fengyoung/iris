"""GUI orchestration. Audio and network work never execute in the Qt thread."""
from __future__ import annotations

import multiprocessing as mp
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from .capture_attempt import CaptureAttempt
from .correction import CorrectionService
from .dialogs import SettingsDialog, available_key
from .export import export_session
from .knowledge import KnowledgeStore, RuleStore, extract_rule_candidates
from .materials import MaterialIndex
from .persistence import PersistenceError
from .session import Session
from .settings import (VAD_REQUIRED_MESSAGE, data_dir, read_settings,
                       resolve_speaker_model_path, resolve_vad_model_path)
from .statistics import SessionCounts, StatisticsWriter, StatsStore


def _pump_audio(pipe, inbox, stop, done):
    """A killed writer can leave a partial pickle frame; only this daemon waits."""
    try:
        while not stop.is_set():
            try:
                event = pipe.get(timeout=.05)
            except queue.Empty:
                continue
            except (ValueError, OSError, EOFError):
                return
            while not stop.is_set():
                try:
                    inbox.put(event, timeout=.05)
                    break
                except queue.Full:
                    continue
    finally:
        done.set()


def _reap_audio(process, events, commands):
    """Run outside Qt; never join a queue feeder with an absent consumer."""
    try:
        if process is not None:
            # No GUI consumer remains. Stop the child immediately rather than
            # joining it while its event feeder is blocked on a full pipe.
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
            if not process.is_alive():
                process.close()
    except (ValueError, OSError, AssertionError):
        pass
    finally:
        for stream in (events, commands):
            if stream is not None:
                try:
                    stream.cancel_join_thread()
                    stream.close()
                except (AttributeError, ValueError, OSError):
                    pass


class Controller(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.session = Session()
        self.settings = read_settings()
        self.knowledge = KnowledgeStore(data_dir() / 'knowledge')
        self.rules = RuleStore(data_dir() / 'rules')
        self.index = MaterialIndex()
        self.retained_ids = set()
        self.background = ''
        self.results = queue.Queue()
        self._stats = StatsStore(data_dir() / 'statistics')
        self._stats_started_at = datetime.now(timezone.utc).isoformat()
        self._stats_counts = SessionCounts(self.session.id, self._stats_started_at)
        self._stats_writer = StatisticsWriter(self._stats, lambda: self.results.put(
            {'type': 'status', 'message': '字数统计未能及时保存，请检查本机存储。'}))
        self.audio_events = None
        self.commands = None
        self.process = None
        self.tasks = {}
        self.retry_tasks = {}
        self.service = None
        self.paused = False
        self.capture_state = 'idle'
        self._capture_pending = None
        self.window.set_capture_state('idle')
        self._closing = False
        self._deferred_export = None
        self._audio_failure = None
        self._index_ready = False
        self._index_generation = 0
        self._material_dialog = None
        self._prepare_queue = queue.Queue(maxsize=256)
        threading.Thread(target=self._prepare_tasks, daemon=True).start()
        self._connect()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(60)
        self.retry_timer = QTimer(self)
        self.retry_timer.timeout.connect(self.retry_pending)
        self.retry_timer.start(30000)
        threading.Thread(target=self._prepare, daemon=True).start()
        self._init_service()

    def _connect(self):
        for signal, slot in [(self.window.start_requested, self.start),
                             (self.window.pause_requested, self.pause),
                             (self.window.stop_requested, self.stop),
                             (self.window.settings_requested, self.show_settings),
                             (self.window.materials_requested, self.show_materials),
                             (self.window.knowledge_requested, self.import_knowledge),
                             (self.window.export_requested, self.export),
                             (self.window.edit_requested, self.edit)]:
            signal.connect(slot)
        if hasattr(self.window, 'retry_correction_requested'):
            self.window.retry_correction_requested.connect(self.retry_correction)
        if hasattr(self.window, 'new_meeting_requested'):
            self.window.new_meeting_requested.connect(self.start_new_meeting)
        if hasattr(self.window, 'boundary_stats_requested'):
            self.window.boundary_stats_requested.connect(self.show_boundary_statistics)
        if hasattr(self.window, 'statistics_requested'):
            self.window.statistics_requested.connect(self.show_statistics)
        if hasattr(self.window, 'speakers_requested'):
            self.window.speakers_requested.connect(self.show_speakers)
            self.window.speaker_assign_requested.connect(self.assign_speaker)

    def show_boundary_statistics(self):
        labels = {'silence': '停顿结束', 'soft_pause': '长段短停顿', 'limit': '达到时长上限',
                  'speaker': '检测到换人', 'pause': '手动暂停', 'stop': '结束收音', 'unknown': '原因未知'}
        counts = self.session.boundary_diagnostics()
        detail = '\n'.join(f'{label}：{counts.get(reason, 0)}' for reason, label in labels.items())
        QMessageBox.information(self.window, '本场分段诊断',
                                f'已定稿 {sum(counts.values())} 段（未计入初稿和中断稿）\n\n{detail}')

    def _refresh_reading_groups(self):
        if hasattr(self.window, 'set_reading_groups'):
            self.window.set_reading_groups([[segment.id for segment in group]
                                           for group in self.session.reading_groups()])

    def _refresh_speakers(self):
        for segment in self.session.ordered():
            self.window.upsert_segment(segment.view())
        self._refresh_reading_groups()

    def show_speakers(self):
        if not self.session.speakers_enabled:
            return
        from .speaker_dialog import SpeakerManagementDialog
        dialog = SpeakerManagementDialog(self.session.speaker_names, self.window)
        def rename(speaker_id, name):
            self.session.rename_speaker(speaker_id, name)
            self._refresh_speakers()
        dialog.rename_requested.connect(rename)
        dialog.exec()

    def assign_speaker(self, sid):
        if not self.session.speakers_enabled or sid not in self.session.segments:
            return
        ids = [None, *self.session.speaker_names]
        labels = ['未确定（人工确认）', *[
            f'{index}. {name}' for index, name in enumerate(self.session.speaker_names.values(), 1)]]
        label, accepted = QInputDialog.getItem(self.window, '修改本段说话人',
                                               '只修改本段，之后不会被自动判断覆盖：',
                                               labels, 0, False)
        if accepted:
            segment = self.session.assign_speaker(sid, ids[labels.index(label)])
            self.window.upsert_segment(segment.view())
            self._refresh_reading_groups()

    def _statistics_ledger(self):
        counts = getattr(self, '_stats_counts', None)
        if counts is None or counts.session_id != self.session.id:
            if counts is not None and (writer := getattr(self, '_stats_writer', None)):
                old = counts.snapshot()
                if old['finalized_segments']:
                    writer.submit(old, flush=True)
            started = getattr(self, '_stats_started_at', None) or datetime.now(timezone.utc).isoformat()
            counts = self._stats_counts = SessionCounts(self.session.id, started)
            for existing in self.session.segments.values():
                counts.update(existing)  # Bootstrap once if an existing session was adopted.
        return counts

    def _statistics_snapshot(self):
        return self._statistics_ledger().snapshot()

    def _update_statistics(self, segment=None, *, flush=False):
        counts = self._statistics_ledger()
        if segment is not None:
            counts.update(segment)
        snapshot = counts.snapshot()
        if hasattr(self.window, 'set_statistics'):
            self.window.set_statistics(snapshot['raw_chars'], snapshot['corrected_chars'])
        writer = getattr(self, '_stats_writer', None)
        if snapshot['finalized_segments'] and writer:
            writer.submit(snapshot, flush=flush)

    def show_statistics(self):
        from .statistics_dialog import StatsDialog
        dialog = StatsDialog(self._stats, self._statistics_snapshot, self.window)
        dialog.exec()

    def _prepare(self):
        try:
            import sounddevice as sd
            devices = [{'index': i, 'name': d['name']} for i, d in enumerate(sd.query_devices())
                       if d['max_input_channels'] > 0]
            self.results.put({'type': 'devices', 'devices': devices})
        except Exception:
            self.results.put({'type': 'status', 'message': '设备初始化未完成，请检查设置。'})
        try:
            index = MaterialIndex(self.settings.get('embedding_model_path') or None)
            retained = index.load_retained(data_dir() / 'materials')
            self.results.put({'type': 'index_ready', 'index': index, 'retained': retained})
        except Exception:
            self.results.put({'type': 'index_ready', 'index': MaterialIndex(), 'retained': []})
            self.results.put({'type': 'status', 'message': '长期资料加载失败，未覆盖原文件。'})

    def _reset_materials(self):
        """Drop prior meeting data immediately; retained remote text reloads quarantined."""
        self.background = ''
        self.index = MaterialIndex()
        self.retained_ids = set()
        self._index_ready = False
        self._index_generation = getattr(self, '_index_generation', 0) + 1
        generation, session_id = self._index_generation, self.session.id
        model_path = self.settings.get('embedding_model_path') or None
        def load():
            try:
                index = MaterialIndex(model_path)
                retained = index.load_retained(data_dir() / 'materials')
            except Exception:
                index, retained = MaterialIndex(), []
                self.results.put({'type': 'status', 'session_id': session_id,
                                  'message': '长期资料加载失败，原文件保持不变。'})
            self.results.put({'type': 'index_ready', 'session_id': session_id,
                              'index_generation': generation, 'index': index, 'retained': retained})
        threading.Thread(target=load, daemon=True).start()

    def _init_service(self):
        if self.service:
            self.service.stop(wait=False)
        self.service = None
        self._service_generation = getattr(self, '_service_generation', 0) + 1
        generation = self._service_generation
        # Only work that has never reached a service may be submitted automatically
        # after configuration changes. The previous worker may already have sent
        # an in-flight request, even if its callback hasn't reached the GUI.
        for sid in self.tasks.keys() - self.retry_tasks.keys():
            segment = self.session.segments.get(sid)
            if segment and segment.status == 'queued':
                self._correction_status(segment, 'retry_required')
        try:
            key = available_key()
            if key:
                self.service = CorrectionService(key,
                    lambda event: self.results.put(dict(event, service_generation=generation)),
                    model=self.settings.get('deepseek_model', 'deepseek-flash'))
                self.service.start()
        except Exception:
            self.service = None
        if not self.service:
            self.window.set_status('就绪 · 未配置可用 DeepSeek，仅本地词典校正')

    def _set_capture_state(self, state, *, pending=None):
        self.capture_state = state
        self._capture_pending = pending
        self.paused = state == 'paused'
        self.window.set_capture_state(state, pending=pending)

    def start(self):
        if self.capture_state == 'paused':
            self.pause()
            return
        self._begin_capture(resume=self.capture_state == 'error' and
                            (bool(self.session.segments) or getattr(self, '_meeting_started', False)))

    def start_new_meeting(self):
        self._begin_capture(resume=False)

    def _can_start(self):
        return (not self._closing and not self.window.closing_after_save
                and not getattr(self, '_deferred_export', None)
                and self.capture_state in {'idle', 'error'} and self.process is None)

    def _confirm_replacement(self):
        if not self.session.segments:
            return True
        button = QMessageBox.StandardButton
        answer = QMessageBox.question(self.window, '开始新会议',
            '新会议就绪后将替换本场记录。选择“保存”先导出 Markdown，'
            '或选择“丢弃”不导出；新会议启动失败时仍会保留本场记录。',
            button.Save | button.Discard | button.Cancel, button.Cancel)
        if answer == button.Save:
            return bool(self.export('markdown', 'both'))
        return answer == button.Discard

    def _begin_capture(self, *, resume):
        if not self._can_start():
            return
        path = self.settings.get('model_path', '')
        if not path or not Path(path).is_dir():
            self._set_capture_state('error')
            self.window.set_status('请先在设置中选择完整的语音模型目录')
            self.show_settings()
            return
        vad_path = resolve_vad_model_path(self.settings.get('vad_model_path'))
        if not vad_path:
            self._set_capture_state('error')
            self.show_settings()
            self.window.set_status(VAD_REQUIRED_MESSAGE)
            return
        previous = self.session
        if not resume and not self._confirm_replacement():
            return
        # A file/confirmation dialog runs its own Qt event loop.
        if previous is not self.session or not self._can_start():
            return
        session = previous if resume else Session(speakers_enabled=self.settings.get('speakers_enabled', False))
        replacing = bool(previous.segments) or getattr(self, '_meeting_started', False)
        attempt = CaptureAttempt(session, resume, replacing)
        if resume:
            attempt.clock_base = max((s.end_ms for s in session.segments.values()), default=0)
            attempt.clock_base = max(attempt.clock_base, getattr(self, '_capture_clock_end', 0))
            attempt.id_base = max((int(i) for i in session.segments if i.isdecimal()), default=0)
            attempt.id_base = max(attempt.id_base, getattr(self, '_capture_id_end', 0))
        self._attempt = attempt
        self._audio_failure = self._audio_deadline = None
        self._audio_terminated = False
        self._set_capture_state('loading')
        try:
            self._spawn_capture(attempt, path, vad_path)
        except Exception:
            if self.process and self.process.is_alive():
                self._audio_failure = '收音进程初始化失败，原会议仍保留。'
                self._arm_audio_deadline(1)
                self._set_capture_state('stopping')
            else:
                self._release_audio()
                self._set_capture_state('error')
            self.window.set_status('无法启动收音进程，请检查设备和模型设置后重试。')
            return
        self.window.set_status('正在加载语音模型，已有会议记录保持不变…')

    def _spawn_capture(self, attempt, path, vad_path):
        from .audio import run_audio_worker
        context = mp.get_context('spawn')
        self.audio_events = context.Queue(maxsize=256)
        self.commands = context.Queue(maxsize=32)
        speakers = attempt.session.speakers_enabled
        speaker_path = resolve_speaker_model_path(self.settings.get('speaker_model_path', '')) if speakers else ''
        config = dict(session_id=attempt.wire_id, device=self.window.selected_device,
                      model_path=path, hotwords=self.knowledge.active_bundle().get('hotwords', []),
                      vad_model_path=vad_path, vad_required=True,
                      speakers_enabled=speakers, speaker_model_path=str(speaker_path or ''))
        self.process = context.Process(target=run_audio_worker,
            args=(config, self.audio_events, self.commands), daemon=True)
        self.process.start()
        self._start_audio_pump()

    def _capture_ready(self, attempt):
        if not attempt.resume:
            if self.service:
                self.service.cancel(self.session.id)
            self._update_statistics(flush=True)
            self.session = attempt.session
            self._stats_started_at = datetime.now(timezone.utc).isoformat()
            self._update_statistics()
            if attempt.replacing:
                self._reset_materials()
            self.rules.clear_session()
            self.tasks.clear()
            self.retry_tasks.clear()
            self.window.clear_segments()
            self._capture_clock_end = self._capture_id_end = 0
        self._meeting_started = attempt.ready = True
        if hasattr(self.window, 'set_speakers_enabled'):
            self.window.set_speakers_enabled(self.session.speakers_enabled)
        if attempt.speaker_status:
            self._event(attempt.translate(attempt.speaker_status))
            attempt.speaker_status = None

    def _capture_event(self, event):
        attempt = getattr(self, '_attempt', None)
        if attempt is None or event.get('session_id') != attempt.wire_id:
            return
        if not attempt.ready:
            if event.get('type') == 'speaker_status':
                attempt.speaker_status = event
                return
            kind = event.get('type')
            if kind == 'status' and event.get('state') == 'recording':
                if self.capture_state != 'loading' or self._audio_failure or self._closing:
                    return
                self._capture_ready(attempt)
            elif kind in {'error', 'stopped', 'status'}:
                self._status_event(dict(event, session_id=self.session.id))
                return
            else:
                return
        event = attempt.translate(event)
        if event.get('type') in {'partial', 'final', 'discard'}:
            self._capture_clock_end = max(getattr(self, '_capture_clock_end', 0), event.get('end_ms', 0))
            sid = str(event.get('segment_id', ''))
            if sid.isdecimal():
                self._capture_id_end = max(getattr(self, '_capture_id_end', 0), int(sid))
        self._event(event)

    def _command(self, kind):
        try:
            self.commands.put_nowait({'type': kind})
            return True
        except (queue.Full, ValueError, OSError, AttributeError):
            self.window.set_status('收音进程暂未响应，请稍后重试。')
            return False

    def _start_audio_pump(self):
        self._audio_inbox = None
        self._audio_dead_since = None
        if isinstance(self.audio_events, queue.Queue):
            return
        self._audio_inbox = queue.Queue(maxsize=256)
        self._audio_pump_stop = threading.Event()
        self._audio_pump_done = threading.Event()
        threading.Thread(target=_pump_audio,
            args=(self.audio_events, self._audio_inbox, self._audio_pump_stop, self._audio_pump_done),
            name='caption-audio-events', daemon=True).start()

    def _interrupt_audio(self):
        attempt = getattr(self, '_attempt', None)
        if attempt is not None and not attempt.ready:
            return
        for segment in self.session.interrupt_partials():
            self.window.upsert_segment(segment.view())

    def _arm_audio_deadline(self, seconds):
        deadline = time.monotonic() + seconds
        self._audio_deadline = min(getattr(self, '_audio_deadline', None) or deadline, deadline)

    def _check_audio_deadline(self):
        deadline = getattr(self, '_audio_deadline', None)
        if not self.process or deadline is None or time.monotonic() < deadline:
            return
        if not self.process.is_alive():
            return
        self._audio_failure = getattr(self, '_audio_failure', None) or '收音处理超时，已中断；已有文字已保留，请确认未完成句段。'
        self._interrupt_audio()
        if getattr(self, '_audio_terminated', False):
            self.process.kill()
        else:
            self.process.terminate()
            self._audio_terminated = True
        self._audio_deadline = time.monotonic() + 1
        self._set_capture_state('stopping')
        self.window.set_status(self._audio_failure)

    def _record_exit_failure(self):
        if getattr(self, '_audio_failure', None):
            return
        attempt = getattr(self, '_attempt', None)
        if attempt and not attempt.ready and self.capture_state == 'loading':
            self._audio_failure = '收音未就绪，原会议仍保留；请检查设备和模型后重试。'
        elif getattr(self.process, 'exitcode', 0) not in (None, 0):
            self._audio_failure = '收音进程异常退出，请检查设备和模型设置后重试。'

    def _release_audio(self):
        """Release dead/unstarted processes without waiting on the GUI thread."""
        if self.process:
            try:
                if self.process.is_alive():
                    return
                self.process.close()
            except (ValueError, OSError):
                pass
        self.process = self._attempt = None
        if getattr(self, '_audio_pump_stop', None) is not None:
            self._audio_pump_stop.set()
        self._audio_inbox = None
        for stream in (self.audio_events, self.commands):
            if stream is not None:
                try:
                    stream.cancel_join_thread()
                    stream.close()
                except (AttributeError, ValueError, OSError):
                    pass
        self.audio_events = self.commands = None

    def pause(self):
        if (self._closing or self.window.closing_after_save or self._capture_pending
                or self.capture_state not in {'recording', 'paused'}):
            return
        if self.process and self.process.is_alive():
            command = 'resume' if self.capture_state == 'paused' else 'pause'
            if self._command(command):
                self._set_capture_state(self.capture_state, pending=command)
                self._arm_audio_deadline(10)
                self.window.set_status('正在继续收音…' if command == 'resume' else '正在暂停并处理当前句段…')

    def stop(self):
        if self._closing or self.window.closing_after_save:
            return
        self._request_stop()

    def _request_stop(self):
        """Also used by close-save/shutdown; a stop is queued at most once."""
        if self.capture_state == 'stopping':
            return True
        if self.process is None:
            return False
        if self.process.is_alive() and not self._command('stop'):
            return False
        self._update_statistics(flush=True)
        self._set_capture_state('stopping')
        self._arm_audio_deadline(10)
        self.window.set_status('正在结束收音并处理最后句段…')
        return True

    def poll(self):
        audio_source = getattr(self, '_audio_inbox', None)
        if audio_source is None:
            audio_source = self.audio_events
        audio_drained = audio_source is None
        for source in (audio_source, self.results):
            if source is None:
                continue
            for _ in range(100):
                try:
                    event = source.get_nowait()
                except (queue.Empty, ValueError, OSError):
                    if source is audio_source:
                        audio_drained = True
                    break
                if source is audio_source and getattr(self, '_attempt', None) is not None:
                    self._capture_event(event)
                else:
                    self._event(event)
        self._check_audio_deadline()
        if self.process and not self.process.is_alive() and getattr(self, '_audio_inbox', None) is not None:
            if getattr(self, '_audio_dead_since', None) is None:
                self._audio_dead_since = time.monotonic()
            if not self._audio_pump_done.is_set() and time.monotonic() - self._audio_dead_since < .25:
                audio_drained = False
        if audio_drained and self.process and not self.process.is_alive():
            self._record_exit_failure()
            self._interrupt_audio()
            self._release_audio()
            self._set_capture_state('error' if getattr(self, '_audio_failure', None) else 'idle')
            self.window.set_status(getattr(self, '_audio_failure', None) or
                                   '收音已结束，可导出记录或开始新会议。')
        deferred = getattr(self, '_deferred_export', None)
        if deferred:
            if not self.window.closing_after_save:
                self._deferred_export = None
            elif self.process is None and audio_drained:
                self._deferred_export = None
                self.export(deferred[0], deferred[1])
            elif time.monotonic() > deferred[2]:
                self._deferred_export = None
                self.window.cancel_pending_close()
                self.window.set_status('收音尚未结束，未执行保存退出。请稍后重试，或手动导出已有字幕。')

    def _transcript_event(self, event):
        if event['type'] == 'partial' and getattr(self, '_audio_failure', None):
            return
        segment = self.session.apply_audio(event)
        if segment:
            if event['type'] == 'final':
                self._schedule(segment)
                self._update_statistics(segment)
            self.window.upsert_segment(segment.view())

    def _event(self, event):
        if self._closing or event.get('session_id', self.session.id) != self.session.id:
            return
        if event.get('service_generation', getattr(self, '_service_generation', 0)) != getattr(self, '_service_generation', 0):
            return
        kind = event.get('type')
        if kind in {'partial', 'final'}:
            self._transcript_event(event)
        elif (kind in {'correction', 'error'} and 'service_generation' in event
              and event.get('segment_id') in self.session.segments):
            current = self.session.segments[event['segment_id']]
            if current.status not in {'queued', 'pending'}:
                return
            segment = self.session.apply_correction(event)
            if segment:
                self.retry_tasks.pop(segment.id, None)
                if kind == 'error':
                    segment.status = 'retry_exhausted' if event.get('retryable') else 'error'
                else:
                    self.tasks.pop(segment.id, None)
                    self._update_statistics(segment)
                self.window.upsert_segment(segment.view())
        elif kind == 'task_ready':
            self._task_ready(event['task'])
        elif kind == 'devices':
            self.window.set_devices(event['devices'])
        elif kind == 'index_ready':
            if event.get('index_generation', 0) != getattr(self, '_index_generation', 0):
                return
            self.index = event['index']
            self.retained_ids = set(event['retained'])
            self._index_ready = True
            if getattr(self, '_embedding_deferred', False):
                self._embedding_deferred = False
                self._reload_embeddings()
        elif kind in {'speaker', 'speaker_status'}:
            self._speaker_event(event)
        elif kind == 'discard':
            if self.session.discard_partial(event):
                self.window.remove_segment(str(event['segment_id']))
        elif kind in {'status', 'error', 'stopped'}:
            self._status_event(event)

    def _task_ready(self, task):
        segment = self.session.segments.get(task['segment_id'])
        if (task['session_id'] == self.session.id and segment and not segment.manual
                and segment.status in {'pending', 'local'} and segment.id not in self.tasks
                and segment.revision == task['revision']):
            self.tasks[segment.id] = task
            self._submit_task(task)

    def _speaker_event(self, event):
        if event['type'] == 'speaker':
            segment = self.session.apply_speaker(event)
            if segment:
                self.window.upsert_segment(segment.view())
                self._refresh_reading_groups()
            return
        message = event.get('text') or '说话人区分暂不可用，普通字幕继续。'
        if hasattr(self.window, 'set_speaker_status'):
            self.window.set_speaker_status(message)
        else:
            self.window.set_status(message)

    def _status_event(self, event):
        kind = event.get('type')
        message = event.get('message', event.get('text', '已结束收音'))
        if kind == 'error' and 'session_id' in event and 'service_generation' not in event:
            self._audio_failure = message
            self._interrupt_audio()
            if self.process is not None:
                self._command('stop')
                self._arm_audio_deadline(1)
            self._set_capture_state('stopping' if self.process is not None else 'error')
        state = event.get('state')
        if (kind == 'status' and 'session_id' in event
                and self.capture_state not in {'stopping', 'error', 'idle'}
                and state in {'loading', 'recording', 'paused'}):
            expected = {'pause': 'paused', 'resume': 'recording'}.get(self._capture_pending)
            if expected and state != expected:
                return
            self._audio_deadline = None
            self._set_capture_state(state)
            message = {'loading': '正在加载语音模型…', 'recording': '正在收音', 'paused': '已暂停'}[state]
        elif kind == 'status' and state in {'loading', 'recording', 'paused'}:
            return
        if kind == 'stopped' or (kind == 'status' and state == 'paused'):
            attempt = getattr(self, '_attempt', None)
            for segment in self.session.ordered() if attempt is None or attempt.ready else ():
                if segment.speaker_state == 'pending':
                    updated = self.session.apply_speaker({
                        'type': 'speaker', 'session_id': self.session.id,
                        'segment_id': segment.id, 'audio_revision': segment.audio_revision,
                        'speaker_state': 'unknown'})
                    if updated:
                        self.window.upsert_segment(updated.view())
            if kind == 'stopped' and self.process is not None:
                self._arm_audio_deadline(1)
                self._set_capture_state('stopping')
        if kind in {'status', 'stopped'} and getattr(self, '_audio_failure', None):
            return
        self.window.set_status(message)

    def _schedule(self, segment):
        bundle = self.knowledge.active_bundle()
        segment.dictionary_text = self.rules.apply(segment.raw, bundle.get('replacements', {}))
        segment.corrected = segment.dictionary_text
        if not self.service:
            segment.status = 'local'
        context = []
        for prior in reversed(self.session.segments.values()):
            if prior.id != segment.id and prior.status not in {'partial', 'transcribing', 'interrupted'}:
                context.append(prior.corrected or prior.raw)
                if len(context) == 5:
                    break
        context.reverse()
        # Snapshot background on the GUI thread; retrieval itself happens in a background thread.
        base = dict(session_id=self.session.id, segment_id=segment.id, revision=segment.revision,
                    text=segment.dictionary_text, context=context,
                    rules_prompt='\n'.join(bundle.get('rules', [])),
                    protected_terms=list(bundle.get('protected_terms', [])),
                    background=self.background)
        snapshot = self.index.snapshot()
        try:
            self._prepare_queue.put_nowait((base, snapshot))
        except queue.Full:
            # Bounded fallback keeps correction available without spawning work.
            self._event({'type': 'task_ready', 'task': dict(base, references=[])})
            self.window.set_status('资料检索繁忙，本句仅使用会议背景与词典。')

    def _prepare_tasks(self):
        while not self._closing:
            try:
                base, snapshot = self._prepare_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if base['session_id'] != self.session.id:
                continue
            try:
                references = snapshot.search(base['text'])
            except Exception:
                references = []
            if not self._closing:
                self.results.put({'type': 'task_ready', 'task': dict(base, references=references)})

    def _correction_status(self, segment, status):
        if segment.status != status:
            segment.status = status
            self.window.upsert_segment(segment.view())

    def _submit_task(self, task):
        """Admission retries never resend accepted work; network retries live in the worker."""
        sid = task['segment_id']
        segment = self.session.segments[sid]
        if not self.service:
            self.retry_tasks[sid] = task  # No network attempt has been made.
            self._correction_status(segment, 'local')
            return
        try:
            accepted = self.service.submit(task)
        except (ValueError, RuntimeError):
            self.retry_tasks.pop(sid, None)
            self._correction_status(segment, 'error')
            self.window.set_status('该句无法提交云端校正，已保留本地结果。')
            return
        if accepted:
            self.retry_tasks.pop(sid, None)
            self._correction_status(segment, 'queued')
            return
        misses = task.get('_admission_misses', 0) + 1
        if misses >= 3:
            self.retry_tasks.pop(sid, None)
            self._correction_status(segment, 'retry_exhausted')
            return
        self.retry_tasks[sid] = dict(task, _admission_misses=misses)
        self._correction_status(segment, 'queued')

    def retry_correction(self, sid):
        """One explicit click opens one fresh correction revision, not a repeating resubmit."""
        segment = self.session.segments.get(sid)
        if (self._closing or not segment or segment.manual
                or segment.status not in {'local', 'error', 'retry_required', 'retry_exhausted'}):
            return
        if not self.service:
            self.window.set_status('请先在设置中配置云端校正；本地文字已保留。')
            return
        task = self.tasks.get(sid)
        if (not task or task['session_id'] != self.session.id
                or task['revision'] != segment.revision):
            return
        segment.revision += 1
        fresh = dict(task, revision=segment.revision)
        self.tasks[sid] = fresh
        self.retry_tasks.pop(sid, None)
        self._submit_task(fresh)

    def retry_pending(self):
        if not self.service or self._closing:
            return
        for sid, task in list(self.retry_tasks.items()):
            segment = self.session.segments.get(sid)
            if (task and segment and not segment.manual and task['session_id'] == self.session.id
                    and segment.status in {'pending', 'queued', 'local'}
                    and task['revision'] == segment.revision):
                self._submit_task(task)
            else:
                self.retry_tasks.pop(sid, None)

    def _reload_embeddings(self):
        if not self._index_ready:
            self._embedding_deferred = True
            return
        self._embedding_target = (self.index, self.settings.get('embedding_model_path') or None)
        if getattr(self, '_embedding_loading', False):
            return
        self._embedding_loading = True
        def reload():
            try:
                while not self._closing:
                    target = self._embedding_target
                    index, path = target
                    try:
                        index.reload_embeddings(path)
                    except Exception:
                        pass  # MaterialIndex reports lexical fallback through its status.
                    if target == self._embedding_target:
                        break
            finally:
                self._embedding_loading = False
        threading.Thread(target=reload, daemon=True).start()

    def show_settings(self):
        previous_model = self.settings.get('embedding_model_path')
        try:
            if not SettingsDialog(self.window).exec():
                return
            settings = read_settings()
        except (PersistenceError, OSError):
            QMessageBox.warning(self.window, '设置无法读取',
                                '设置文件可能已损坏、不可访问或来自更新版本。原文件未被重置；'
                                '请检查读取权限、恢复备份或使用匹配版本。')
            return
        self.settings = settings
        if previous_model != self.settings.get('embedding_model_path'):
            self._reload_embeddings()
        self._init_service()
        self.retry_pending()

    def show_materials(self):
        if not self._index_ready:
            self.window.set_status('资料初始化中，请稍后打开。')
            return
        from .materials_dialog import MaterialsDialog
        dialog = MaterialsDialog(self.index, data_dir() / 'materials', self._background_changed,
                                 parent=self.window, retained_ids=self.retained_ids)
        fields = dict(line.split('：', 1) for line in self.background.splitlines() if '：' in line)
        dialog.set_background(fields.get('主题', ''), fields.get('参会人', ''), fields.get('项目', ''))
        self._material_dialog = dialog
        dialog.exec()
        self._material_dialog = None
        self.background = dialog.background
        self.retained_ids = set(dialog.retained_ids)
        self.session.background_version += 1

    def _background_changed(self, *args):
        if self._material_dialog is not None:
            self.background = self._material_dialog.background
        self.session.background_version += 1

    def import_knowledge(self):
        from .knowledge_dialog import KnowledgeDialog
        dialog = KnowledgeDialog(self.knowledge, self.rules, self.window)
        dialog.changed.connect(lambda: self.window.set_status('知识与词典已更新，用于后续句段。'))
        dialog.exec()

    def edit(self, sid, text):
        segment = self.session.segments.get(sid)
        if not segment or segment.status == 'partial':
            return
        self.tasks.pop(sid, None)
        self.retry_tasks.pop(sid, None)
        before = segment.corrected or segment.raw
        segment = self.session.edit(sid, text)
        if self.service:
            self.service.cancel(self.session.id, sid)
        self.window.upsert_segment(segment.view())
        self._update_statistics(segment)
        from .dialogs import choose_rule_scope
        for candidate in extract_rule_candidates(before, text):
            source, target = candidate['source'], candidate['target']
            scope = choose_rule_scope(self.window, source, target)
            if scope in {'session', 'local'}:
                self.rules.set_rule(source, target, scope=scope)

    def export(self, kind, mode):
        if self._closing:
            return
        if not self.window.closing_after_save and not any(s.raw or s.corrected for s in self.session.ordered()):
            return
        if self.window.closing_after_save and self.process is not None:
            if getattr(self, '_deferred_export', None):
                return
            self._deferred_export = (kind, mode, time.monotonic() + 30)
            if not self._request_stop():
                self._deferred_export = None
                self.window.cancel_pending_close()
                return
            self.window.set_status('正在结束收音并收集最后句段，完成后保存退出…')
            return
        extension = 'md' if kind == 'markdown' else 'srt'
        path, _ = QFileDialog.getSaveFileName(self.window, '保存会议文字', f'声笺会议.{extension}',
                                             f'会议文件 (*.{extension})')
        if not path:
            self.window.cancel_pending_close()
            return
        try:
            if self.window.closing_after_save and not self.window.prepare_close_export():
                self.window.cancel_pending_close()
                self.window.set_status('收音状态已变化，请重新保存退出。')
                return
            live = self.process is not None
            emergency = kind == 'srt' and mode == 'emergency'
            if live or emergency:
                export_session(self.session, Path(path), kind, 'raw' if emergency else mode,
                               live_snapshot=live, include_unfinalized=emergency)
            else:
                export_session(self.session, Path(path), kind, mode)
            if kind == 'markdown' and not live:
                self.session.dirty = False
                self.window.mark_saved()
            else:
                self.window.cancel_pending_close()
            if live:
                message = '已导出实时快照；后续内容不会自动写入，请在会议结束后再次导出。'
            elif emergency:
                message = '已导出应急字幕；未定稿内容已标记。'
            else:
                message = '已保存会议记录' if kind == 'markdown' else '已导出定稿 SRT；完整记录请另存 Markdown。'
            self.window.set_status(message)
            return True
        except ValueError as exc:
            self.window.cancel_pending_close()
            self.window.set_status(str(exc))
        except OSError:
            self.window.cancel_pending_close()
            QMessageBox.warning(self.window, '保存失败', '请检查目标文件夹权限或可用空间。')

    def shutdown(self):
        self._closing = True
        self.timer.stop()
        self.retry_timer.stop()
        if self.service:
            self.service.stop(wait=False)
        self._request_stop()
        if getattr(self, '_audio_pump_stop', None) is not None:
            self._audio_pump_stop.set()
        self._audio_inbox = None
        process, events, commands = self.process, self.audio_events, self.commands
        self.process = self.audio_events = self.commands = None
        if process is not None or events is not None or commands is not None:
            threading.Thread(target=_reap_audio, args=(process, events, commands),
                             name='caption-audio-cleanup').start()
        self.index.clear()
        if (writer := getattr(self, '_stats_writer', None)) is not None:
            self._update_statistics(flush=True)
            writer.close()

