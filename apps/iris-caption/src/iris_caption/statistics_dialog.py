"""Asynchronous, counts-only history viewer."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from .theme import apply_theme


class _Signals(QObject):
    finished = Signal(object)


class _Load(QRunnable):
    def __init__(self, store, current):
        super().__init__()
        self.store, self.current = store, current
        self.signals = _Signals()

    def run(self):
        try:
            result = self.store.summary(self.current)
        except Exception:
            result = None
        self.signals.finished.emit(result)


class StatsDialog(QDialog):
    """Capture current callback on GUI thread; load persistent history on a worker."""

    def __init__(self, store, current_snapshot=None, parent=None):
        super().__init__(parent)
        apply_theme(self)
        self.store, self.current_snapshot = store, current_snapshot
        self._task = None
        self.setWindowTitle('字数与历史 · 声笺')
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        title = QLabel('字数与历史')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)
        hint = QLabel('口径：汉字、字母和数字逐字计数，不含空格、标点与符号。\n'
                      '转写仅计定稿句段；校正字数是云端完成或人工编辑后的全文字数，并非改动字数。\n'
                      '校正不计待处理、本地词典及失败回退。\n'
                      '本机仅保存会话编号、时间、字数和定稿句段数，不保存正文、标题或资料。')
        hint.setObjectName('muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.current_label = QLabel()
        self.total_label = QLabel()
        layout.addWidget(self.current_label)
        layout.addWidget(self.total_label)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['开始时间', '转写字数', '校正字数', '定稿句段'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 230)
        layout.addWidget(self.table, 1)
        self.status_label = QLabel()
        self.status_label.setObjectName('muted')
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton('刷新')
        self.refresh_button.clicked.connect(self.refresh)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        close = QPushButton('关闭')
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.refresh()

    @Slot()
    def refresh(self):
        if self._task is not None:
            return
        current = self.current_snapshot() if callable(self.current_snapshot) else self.current_snapshot
        current = dict(current) if current else None
        self.current_label.setText('当前任务：转写 {:,} 字 · 校正 {:,} 字 · 定稿 {} 句段'.format(
            (current or {}).get('raw_chars', 0), (current or {}).get('corrected_chars', 0),
            (current or {}).get('finalized_segments', 0)))
        self.refresh_button.setEnabled(False)
        self.status_label.setText('正在读取历史…')
        self._task = _Load(self.store, current)
        self._task.signals.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self._task)

    @Slot(object)
    def _finished(self, result):
        self._task = None
        self.refresh_button.setEnabled(True)
        if result is None:
            self.status_label.setText('历史读取失败，请检查本机统计文件与访问权限后重试。')
            return
        totals = result['totals']
        self.total_label.setText('本机累计（含当前任务）：转写 {:,} 字 · 校正 {:,} 字 · {} 场'.format(
            totals['raw_chars'], totals['corrected_chars'], totals['sessions']))
        self.table.setRowCount(len(result['rows']))
        for index, row in enumerate(result['rows']):
            stamp = datetime.fromisoformat(row['started_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            values = (stamp, f"{row['raw_chars']:,}", f"{row['corrected_chars']:,}",
                      str(row['finalized_segments']))
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.status_label.setText('按开始时间倒序；仅包含启用统计后记录的任务。' if result['rows'] else
                                  '暂无历史记录。定稿后会自动记录字数。')


StatisticsDialog = StatsDialog
