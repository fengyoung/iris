"""Explicit capture preflight and main-thread ownership of a capture session."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout,
                               QLabel, QPushButton, QVBoxLayout)


def _merge_managed_project(project, updated):
    """Preserve editable nested objects captured by aligned/notes widgets."""
    keys = ('revision', 'updated_at', 'recording_sessions', 'attachments', 'snapshots', 'status')
    for key in keys:
        if key in updated:
            project[key] = updated[key]


def helper_path():
    override = os.environ.get('IRIS_INTERVIEW_AUDIO_HELPER')
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / 'native/.build/iris-audio-helper'


class _CaptureSession(QObject):
    """Runtime callbacks may originate in ASR threads; all window work is queued."""
    received = Signal(object)

    def __init__(self, window, project_id, recording_session=None):
        super().__init__(window)
        self.window = window
        self.project_id = project_id
        self.recording_session = recording_session
        self.runtime = None
        self.started = False
        self.finished = False
        previous = window.project.get('transcript', [])
        ends = [float(row.get('end', 0)) for row in previous]
        self.offset_ms = round(max([float(window.project.get('capture_elapsed_seconds', 0)), *ends]) * 1000)
        self.received.connect(self.dispatch)

    @Slot(object)
    def dispatch(self, event):
        window = self.window
        if self.finished or not window.project or window.project['id'] != self.project_id:
            return
        event = dict(event)
        kind = event.get('type')
        if kind in {'final', 'partial', 'discard'}:
            for field in ('start_ms', 'end_ms'):
                if field in event:
                    event['session_' + field] = event[field]
                    event[field] += self.offset_ms
            for field in ('start_ns', 'end_ns'):
                if field in event:
                    event[field] += self.offset_ms * 1_000_000
        elif kind == 'gap':
            for field in ('start_ns', 'end_ns'):
                if field in event:
                    event[field] += self.offset_ms * 1_000_000
        if kind == 'status' and event.get('state') == 'recording' and not self.started:
            self.started = True
            origin = getattr(self.runtime, 'origin', 0)
            elapsed = (time.monotonic_ns() - origin) / 1e9 if origin else 0
            window.started_at = time.monotonic() - self.offset_ms / 1000 - elapsed
            window.project['status'] = 'interviewing'
            window.save()
            window.tabs.setCurrentIndex(1)
        # Preserve regular transcript/error/status handling on the main thread.
        window.audio_event.emit(event)
        if kind == 'finalized':
            self.finished = True
            started_at = getattr(window, 'started_at', None)
            if started_at is not None:
                window.project['capture_elapsed_seconds'] = max(
                    self.offset_ms / 1000, time.monotonic() - started_at)
            window.started_at = None
            window.project['status'] = 'review'
            window.pause_button.setText('暂停')
            window.runtime = None
            if window.save() is False:
                window.error('采集已停止，但项目保存失败；录音会话保留，请恢复后重试整理。')
                return
            if self.recording_session:
                self._finalize_recording_async()
            else:
                window.statusBar().showMessage('采集已结束，材料已保存；请复核记录后主动生成评价。')

    def _finalize_recording_async(self):
        from .workers import Job
        window = self.window
        store, project_id, session_id = window.store, self.project_id, self.recording_session
        revision = window.project['revision']

        def finalize():
            directory = store.recording_directory(project_id, session_id)
            empty = not directory.exists() or all(
                p.name in {'manifest.json', '.recording.lock'} for p in directory.iterdir())
            method = store.cancel_empty_recording_session if empty else store.finalize_recording_session
            return method(project_id, session_id, expected_revision=revision)

        job = Job(finalize)
        self.finalize_job = job
        window.tasks.append(job)
        job.signals.result.connect(self._recording_ready)
        job.signals.error.connect(self._recording_error)
        job.signals.finished.connect(self._recording_done)
        window.statusBar().showMessage('正在整理录音与时间轴…')
        window.pool.start(job)

    @Slot(object)
    def _recording_ready(self, updated):
        window = self.window
        if not window.project or window.project['id'] != self.project_id:
            return
        _merge_managed_project(window.project, updated)
        window.statusBar().showMessage('采集已结束，录音与时间轴已保存。')

    @Slot(str)
    def _recording_error(self, message):
        self.window.error('录音整理未完成，源文件已保留：' + message)

    @Slot()
    def _recording_done(self):
        if self.finalize_job in self.window.tasks:
            self.window.tasks.remove(self.finalize_job)
        self.finalize_job = None


def start_capture_dialog(window):
    if window.runtime:
        window.error('本场采集正在进行或载入中')
        return
    path = helper_path()
    if not path.is_file():
        window.error('音频组件尚未构建，请运行 native/build.sh 或安装完整应用')
        return
    dialog = QDialog(window)
    dialog.setWindowTitle('双方收音检查')
    dialog.resize(620, 420)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    apps, mics, mode = QComboBox(), QComboBox(), QComboBox()
    mode.addItem('耳机（主要场景）', 'headphones')
    mode.addItem('Mac 扬声器（回声待核实）', 'speaker')
    form.addRow('会议应用', apps)
    form.addRow('麦克风', mics)
    form.addRow('听音方式', mode)
    layout.addLayout(form)
    status = QLabel('点击刷新列出应用与设备。未授权时请先授权。')
    status.setWordWrap(True)
    layout.addWidget(status)
    warning = QLabel('仅在点击开始后采集。会议软件静音不会自动停止知遇本地采集。\n'
                     '外放回声消除尚未验证，归属须复核；推荐耳机。')
    warning.setWordWrap(True)
    layout.addWidget(warning)
    correction = QCheckBox('开启云端文字校正（音频不上传）')
    correction.setChecked(bool(window.project.get('cloud_correction', False)))
    layout.addWidget(correction)
    refresh = QPushButton('刷新应用与设备')
    permission = QPushButton('请求系统音频与麦克风权限')
    start = QPushButton('开始本场采集')
    start.setObjectName('primary')
    start.setEnabled(False)
    for button in (refresh, permission, start):
        layout.addWidget(button)

    # QProcess delivers completion on the GUI thread, with no run_task lambda
    # touching widgets from a QRunnable. Only one short-lived helper at a time.
    process = QProcess(dialog)
    timer = QTimer(dialog)
    timer.setSingleShot(True)
    action = {'name': None, 'timed_out': False}

    def busy(value):
        refresh.setEnabled(not value)
        permission.setEnabled(not value)
        start.setEnabled(not value and apps.count() > 0 and mics.count() > 0)

    def command(name, args):
        if process.state() != QProcess.NotRunning:
            return
        action.update(name=name, timed_out=False)
        busy(True)
        status.setText('等待系统授权操作…' if name == 'permission' else '正在枚举应用与麦克风…')
        process.setProgram(str(path))
        process.setArguments(args)
        process.start()
        timer.start(120000 if name == 'permission' else 20000)

    def timed_out():
        action['timed_out'] = True
        process.kill()
        status.setText('音频组件操作超时；未开始采集，请重试。')

    timer.timeout.connect(timed_out)

    def completed(code, exit_status):
        _complete_devices(code, exit_status, process, timer, action, apps, mics, status, busy)

    def process_error(error):
        if error == QProcess.FailedToStart:
            timer.stop()
            busy(False)
            status.setText('无法启动音频组件：' + process.errorString())

    process.finished.connect(completed)
    process.errorOccurred.connect(process_error)
    refresh.clicked.connect(lambda: command('list', ['--list']))
    permission.clicked.connect(lambda: command('permission', ['--request-permission']))

    def begin():
        _begin_capture(window, path, dialog, apps, mics, mode, correction, status)

    start.clicked.connect(begin)

    def close_process():
        timer.stop()
        if process.state() != QProcess.NotRunning:
            process.kill()
            process.waitForFinished(1000)

    dialog.finished.connect(close_process)
    dialog.exec()


def _complete_devices(code, exit_status, process, timer, action, apps, mics, status, busy):
    timer.stop()
    raw = bytes(process.readAllStandardOutput()).decode('utf-8', errors='replace')
    stderr = bytes(process.readAllStandardError()).decode('utf-8', errors='replace')
    events, errors = [], []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get('protocol') != 1:
                raise ValueError('协议版本不匹配')
            events.append(event)
        except (ValueError, TypeError):
            errors.append('音频组件返回无效协议数据')
    if action['name'] == 'list':
        old_pid, old_mic = apps.currentData(), mics.currentData()
        apps.clear()
        mics.clear()
        for event in events:
            if event.get('event') != 'devices':
                continue
            for item in event.get('applications', []):
                pid = item.get('pid')
                if isinstance(pid, int) and pid > 0:
                    apps.addItem(str(item.get('name') or pid), pid)
            for item in event.get('microphones', []):
                uid = item.get('id')
                if isinstance(uid, str) and uid:
                    mics.addItem(str(item.get('name') or '麦克风'), uid)
        for combo, old in ((apps, old_pid), (mics, old_mic)):
            index = combo.findData(old)
            if index >= 0:
                combo.setCurrentIndex(index)
    for event in events:
        if event.get('event') == 'error':
            errors.append(str(event.get('message') or event.get('code')))
    busy(False)
    if action['timed_out']:
        return
    if errors or code != 0 or exit_status != QProcess.NormalExit:
        status.setText('\n'.join(errors) or stderr or '音频组件异常退出；请重试。')
    elif action['name'] == 'permission':
        granted = any(e.get('event') == 'permissions' and e.get('screen_audio_authorized')
                      and e.get('microphone_authorized') for e in events)
        status.setText('授权操作完成，请刷新设备。' if granted else '权限尚未全部允许，请在系统设置中检查。')
    elif apps.count() and mics.count():
        status.setText('请选择会议应用和麦克风。开始后观察双方转写确认收音。')
    else:
        status.setText('未找到可用会议应用或麦克风；请打开会议软件、检查设备和权限后刷新。')


def _begin_capture(window, path, dialog, apps, mics, mode, correction, status):
    if window.runtime:
        status.setText('采集已在启动或运行中')
        return
    if apps.currentData() is None or not mics.currentData():
        status.setText('请先刷新并选择可用会议应用与麦克风')
        return
    from .transcription import CaptureConfig, TranscriptionRuntime
    session_id = None
    try:
        window.project['cloud_correction'] = correction.isChecked()
        window.project['recording_enabled'] = window.record.isChecked()
        if window.save() is False:
            status.setText('项目尚未保存，未开始采集')
            return
        recording = None
        if window.record.isChecked():
            updated = window.store.begin_recording_session(
                window.project['id'], expected_revision=window.project['revision'])
            _merge_managed_project(window.project, updated)
            session_id = window.project['recording_sessions'][-1]['id']
            recording = str(window.store.recording_directory(window.project['id'], session_id))
        config = CaptureConfig(
            helper_path=str(path), app_pid=apps.currentData(), microphone_id=mics.currentData(),
            model_path=window.settings['asr_model'], vad_model_path=window.settings['vad_model'],
            speaker_mode=mode.currentData(), recording_dir=recording)
        session = _CaptureSession(window, window.project['id'], session_id)
        runtime = TranscriptionRuntime(config, session.received.emit)
        session.runtime = runtime
        window.capture_session = session
        window.runtime = runtime
        # start only launches worker/model loading. Real recording status is
        # handled through the queued native-start-confirmed callback above.
        runtime.start()
    except Exception as exc:
        window.runtime = None
        if session_id:
            try:
                updated = window.store.cancel_empty_recording_session(
                    window.project['id'], session_id, expected_revision=window.project['revision'])
                _merge_managed_project(window.project, updated)
            except Exception as cleanup_error:
                status.setText('启动失败，录音会话保留待恢复：' + str(cleanup_error))
                return
        status.setText('未开始采集：' + str(exc))
        return
    dialog.accept()
