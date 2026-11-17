"""Explicit segment playback using immutable recording timeline attachments."""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .exceptions import InterviewError


@dataclass(frozen=True)
class PlaybackSlice:
    path: Path
    start_ms: int
    end_ms: int
    source: str


def segment_slices(store, project, segment):
    """Map a segment's time range to per-file frame ranges; never guess gaps."""
    if 'session_start_ms' in segment and 'session_end_ms' in segment:
        start, end = segment['session_start_ms'] / 1000, segment['session_end_ms'] / 1000
    else:
        start, end = segment.get('start'), segment.get('end')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (start, end)) or start < 0 or end <= start:
        raise InterviewError('片段时间无效')
    source = segment.get('source') or segment.get('track')
    if source not in {'local', 'remote'}:
        raise InterviewError('片段缺少可识别的音轨来源')
    session_id = segment.get('recording_session_id')
    attachments = project.get('attachments', [])
    manifests = [a for a in attachments if a.get('kind') == 'recording_manifest'
                 and (not session_id or a.get('session_id') == session_id)]
    if not session_id and len(manifests) > 1:
        raise InterviewError('存在多次录音，片段缺少录音会话关联，无法安全定位')
    if not manifests:
        raise InterviewError('本片段没有可回听的录音时间轴；可能未开启录音')
    manifest_attachment = manifests[0]
    path = store.attachment_path(project['id'], manifest_attachment['id'])
    if path.stat().st_size > 64 * 1024 * 1024:
        raise InterviewError('录音时间轴过大')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if manifest['version'] != 1 or manifest['clock'] != 'session_relative_ns':
            raise ValueError('version')
        files = {a['name']: a for a in attachments if a.get('kind') == 'recording'
                 and a.get('session_id') == manifest_attachment.get('session_id')}
        result = []
        for chunk in manifest['chunks']:
            if chunk['source'] != source:
                continue
            rate = chunk['sample_rate']
            if type(rate) is not int or rate <= 0:
                raise ValueError('rate')
            begin = chunk['timestamp_ns'] / 1e9
            finish = begin + chunk['frames'] / rate
            lo, hi = max(start, begin), min(end, finish)
            if hi <= lo:
                continue
            item = files[chunk['file']]
            audio_path = store.attachment_path(project['id'], item['id'])
            first = round((chunk['file_frame'] / rate + lo - begin) * 1000)
            last = round((chunk['file_frame'] / rate + hi - begin) * 1000)
            if last <= first:
                continue
            part = PlaybackSlice(audio_path, first, last, source)
            if result and result[-1].path == part.path and abs(result[-1].end_ms-part.start_ms) <= 1:
                prev = result[-1]
                result[-1] = PlaybackSlice(prev.path, prev.start_ms, part.end_ms, source)
            else:
                result.append(part)
        if not result:
            raise InterviewError('该片段未覆盖已保存的录音，无法回听')
        return result
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        raise InterviewError('录音时间轴无效或附件不完整') from exc


class PlaybackPanel(QDialog):
    """No autoplay: construction resolves nothing and starts no media."""
    def __init__(self, store, project, segment=None, parent=None):
        super().__init__(parent)
        self.store, self.project, self.segment = store, project, segment
        self.slices, self.index, self.playing = [], 0, False
        self.setWindowTitle('知遇 · 片段回听')
        self.resize(520, 180)
        layout = QVBoxLayout(self)
        self.status = QLabel('点击播放回听已保存的片段；不会开始音频采集。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.play_button, self.stop_button = QPushButton('播放片段'), QPushButton('停止')
        row.addWidget(self.play_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        self.player.mediaStatusChanged.connect(self._media_status)
        self.player.errorOccurred.connect(self._error)
        self.play_button.clicked.connect(self.play_segment)
        self.stop_button.clicked.connect(self.stop)

    def play_segment(self):
        self.stop()
        try:
            self.slices = segment_slices(self.store, self.project, self.segment or {})
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.index, self.playing = 0, True
        self.play_button.setEnabled(False)
        self._load()

    def _load(self):
        part = self.slices[self.index]
        self.status.setText(f'回听 {"我" if part.source == "local" else "候选人"} · {self.index+1}/{len(self.slices)}')
        url = QUrl.fromLocalFile(str(part.path))
        if self.player.source() == url:
            self._start_loaded()
        else:
            self.player.setSource(url)

    def _media_status(self, status):
        if self.playing and status == QMediaPlayer.LoadedMedia:
            self._start_loaded()
        elif self.playing and status == QMediaPlayer.EndOfMedia:
            self._advance()

    def _start_loaded(self):
        self.player.setPosition(self.slices[self.index].start_ms)
        self.player.play()
        self.timer.start()

    def _tick(self):
        if self.playing and self.player.position() >= self.slices[self.index].end_ms:
            self._advance()

    def _advance(self):
        self.timer.stop()
        self.player.pause()
        self.index += 1
        if self.index >= len(self.slices):
            self.stop()
            self.status.setText('片段回听结束')
        else:
            self._load()

    def _error(self, _error, message):
        self.stop()
        self.status.setText('无法播放录音：' + message)

    def stop(self):
        self.playing = False
        self.timer.stop()
        self.player.stop()
        self.play_button.setEnabled(True)

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
