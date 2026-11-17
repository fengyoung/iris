"""Editable private interview notes and read-only version history widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
import json
from uuid import uuid4


class NotesPanel(QWidget):
    """Mutates the bound document, emits changed, never writes storage directly."""

    changed = Signal()
    evidence_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.loading = False
        self.selected_id = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("自由笔记"))
        self.free_notes = QPlainTextEdit()
        self.free_notes.setPlaceholderText("整场观察，自动保存；下方管理与回答关联的标记和笔记")
        self.free_notes.setMaximumHeight(160)
        self.free_notes.textChanged.connect(self.free_changed)
        layout.addWidget(self.free_notes)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "类型", "内容", "关联问题"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.select_note)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.add_button = QPushButton("新增笔记")
        self.mark_button = QPushButton("新增回答标记")
        self.remove_button = QPushButton("删除所选")
        self.jump_button = QPushButton("查看附近回答")
        for button in (self.add_button, self.mark_button, self.remove_button, self.jump_button):
            actions.addWidget(button)
        self.add_button.clicked.connect(lambda: self.add_note())
        self.mark_button.clicked.connect(lambda: self.add_note(kind="marker"))
        self.remove_button.clicked.connect(self.remove_note)
        self.jump_button.clicked.connect(self.jump_to_evidence)
        layout.addLayout(actions)
        editor = QFormLayout()
        self.time = QDoubleSpinBox()
        self.time.setRange(0, 7 * 24 * 3600)
        self.time.setDecimals(1)
        self.time.setSuffix(" 秒")
        self.kind = QComboBox()
        for label, value in [
            ("笔记", "note"),
            ("回答标记", "marker"),
            ("亮点", "highlight"),
            ("疑点", "concern"),
            ("待追问", "follow_up"),
        ]:
            self.kind.addItem(label, value)
        self.text = QPlainTextEdit()
        self.text.setMaximumHeight(110)
        self.question = QComboBox()
        self.question.setEditable(True)
        self.question.setToolTip("选择关联问题，也可填写问题 ID；留空表示整场观察")
        editor.addRow("时间位置", self.time)
        editor.addRow("类型", self.kind)
        editor.addRow("内容", self.text)
        editor.addRow("关联问题", self.question)
        layout.addLayout(editor)
        self.time.valueChanged.connect(self.commit_note)
        self.kind.currentIndexChanged.connect(self.commit_note)
        self.text.textChanged.connect(self.commit_note)
        self.question.currentTextChanged.connect(self.commit_note)
        self.refresh_editor_enabled()

    def bind(self, project):
        self.loading = True
        self.project = project
        self.free_notes.setPlainText(project.get("free_notes", ""))
        self.question.clear()
        self.question.addItem("不关联问题", "")
        for question in project.get("plan", {}).get("questions", []):
            self.question.addItem(question.get("text", question["id"]), question["id"])
        self.loading = False
        self.refresh()

    def setPlainText(self, text):
        """Compatibility for callers previously using a QPlainTextEdit."""
        self.free_notes.setPlainText(text)

    def toPlainText(self):
        return self.free_notes.toPlainText()

    def free_changed(self):
        if self.loading or self.project is None:
            return
        self.project["free_notes"] = self.free_notes.toPlainText()
        self.changed.emit()

    def refresh(self):
        self.loading = True
        notes = self.project.get("notes", []) if self.project else []
        previous = self.selected_id
        self.table.setRowCount(len(notes))
        selected_row = -1
        for row, note in enumerate(notes):
            # Historical records without IDs are normalized only when edited.
            key = note.get("id", f"legacy-{row}")
            values = [
                f"{note.get('time', 0):.1f}s",
                note.get("kind", "note"),
                note.get("text", ""),
                note.get("question_id", ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.UserRole, key)
                self.table.setItem(row, column, item)
            if key == previous:
                selected_row = row
        self.table.resizeColumnsToContents()
        self.loading = False
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif notes:
            self.table.selectRow(0)
        else:
            self.selected_id = None
        self.select_note()

    def current_note(self):
        if not self.project:
            return None
        row = self.table.currentRow()
        notes = self.project.get("notes", [])
        return notes[row] if 0 <= row < len(notes) else None

    def select_note(self):
        if self.loading:
            return
        note = self.current_note()
        self.loading = True
        self.selected_id = note.get("id", f"legacy-{self.table.currentRow()}") if note else None
        self.time.setValue(note.get("time", 0) if note else 0)
        value = note.get("kind", "note") if note else "note"
        if self.kind.findData(value) < 0:
            self.kind.addItem(value, value)
        self.kind.setCurrentIndex(max(0, self.kind.findData(value)))
        self.text.setPlainText(note.get("text", "") if note else "")
        qid = note.get("question_id", "") if note else ""
        index = self.question.findData(qid)
        if index >= 0:
            self.question.setCurrentIndex(index)
        else:
            self.question.setEditText(qid)
        self.loading = False
        self.refresh_editor_enabled()

    def refresh_editor_enabled(self):
        enabled = self.current_note() is not None
        for control in (self.time, self.kind, self.text, self.question, self.remove_button, self.jump_button):
            control.setEnabled(enabled)

    def commit_note(self, *_):
        if self.loading:
            return
        note = self.current_note()
        if note is None:
            return
        index = self.question.currentIndex()
        # Editable combo may retain its old index while holding manually entered ID.
        qid = (
            self.question.itemData(index)
            if index >= 0 and self.question.currentText() == self.question.itemText(index)
            else self.question.currentText()
        )
        note.update(
            id=note.get("id") or uuid4().hex,
            time=self.time.value(),
            kind=self.kind.currentData(),
            text=self.text.toPlainText(),
            question_id=qid or "",
        )
        self.selected_id = note["id"]
        row = self.table.currentRow()
        for column, value in enumerate(
            [f"{note['time']:.1f}s", note["kind"], note["text"], note["question_id"]]
        ):
            self.table.item(row, column).setText(value)
            self.table.item(row, column).setData(Qt.UserRole, note["id"])
        self.changed.emit()

    def add_note(self, *, kind="note", time=0, text="", question_id=""):
        if self.project is None:
            return
        note = dict(id=uuid4().hex, kind=kind, time=max(0, time), text=text, question_id=question_id)
        self.project.setdefault("notes", []).append(note)
        self.selected_id = note["id"]
        self.refresh()
        self.text.setFocus()
        self.changed.emit()

    def remove_note(self):
        note = self.current_note()
        if note is not None:
            self.project["notes"].remove(note)
            self.selected_id = None
            self.refresh()
            self.changed.emit()

    def jump_to_evidence(self):
        note = self.current_note()
        segments = self.project.get("transcript", []) if self.project else []
        if note is None or not segments:
            return
        at = note.get("time", 0)

        def distance(segment):
            start = segment.get("start", 0)
            end = segment.get("end", start)
            return 0 if start <= at <= end else min(abs(start - at), abs(end - at))

        segment = min(segments, key=distance)
        self.evidence_requested.emit(segment["id"])


class HistoryDialog(QDialog):
    """Read-only snapshots: opening a historical version never adopts its content."""

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle("计划与正式评价历史 · 只读")
        self.resize(850, 650)
        self.snapshots = project.get("snapshots", [])
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("历史版本仅供查看；不会替换当前计划、评价或人工建议职级。"))
        self.versions = QComboBox()
        for snapshot in self.snapshots:
            kind = "面试计划" if snapshot.get("kind") == "plan" else "正式评价"
            label = (
                f"{kind} · {snapshot.get('created_at', '')} · 来源修订 {snapshot.get('source_revision', '?')}"
            )
            self.versions.addItem(label)
        layout.addWidget(self.versions)
        self.content = QPlainTextEdit()
        self.content.setReadOnly(True)
        layout.addWidget(self.content, 1)
        self.versions.currentIndexChanged.connect(self.show_version)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.show_version(self.versions.currentIndex())

    def show_version(self, index):
        self.content.setPlainText(
            json.dumps(self.snapshots[index], ensure_ascii=False, indent=2)
            if 0 <= index < len(self.snapshots)
            else "尚无确认版本。"
        )
