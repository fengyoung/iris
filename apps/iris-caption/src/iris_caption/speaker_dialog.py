"""Session-only speaker name editor; controllers own persistence and assignment."""
from __future__ import annotations

from collections.abc import Mapping
import unicodedata

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QScrollArea,
    QVBoxLayout, QWidget,
)

from .theme import apply_theme


def valid_speaker_name(value: str) -> bool:
    return bool(value.strip()) and len(value) <= 80 and not any(
        unicodedata.category(char).startswith('C') for char in value)


class SpeakerManagementDialog(QDialog):
    """Emit validated changed names on Save; cancellation never emits operations."""
    rename_requested = Signal(str, str)

    def __init__(self, names: Mapping[str, str], parent=None):
        super().__init__(parent)
        apply_theme(self)
        self.setWindowTitle('本场会议说话人')
        self.resize(460, 360)
        self._names = dict(names)
        self.fields = {}
        layout = QVBoxLayout(self)
        hint = QLabel('名称仅用于本场会议，不代表身份识别。每个名称最多 80 字，不含控制字符。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QFormLayout(content)
        for speaker_id, name in self._names.items():
            field = QLineEdit(name or str(speaker_id))
            field.setAccessibleName(f'说话人 {speaker_id} 的显示名称')
            label = QLabel(str(speaker_id))
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setBuddy(field)
            form.addRow(label, field)
            self.fields[speaker_id] = field
        if not self.fields:
            form.addRow(QLabel('本场会议尚无已确认的说话人。'))
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.notice = QLabel('')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        if any(not valid_speaker_name(field.text()) for field in self.fields.values()):
            self.notice.setText('名称不能为空、超过 80 字或包含控制字符。')
            return
        for speaker_id, field in self.fields.items():
            name = field.text().strip()
            if name != self._names[speaker_id]:
                self.rename_requested.emit(str(speaker_id), name)
        self.accept()
