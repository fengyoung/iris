"""KnowledgeDialog(knowledge_store, rule_store, parent=None), changed() on GUI thread.

All import and rule persistence run on a single worker. Iris rows are immutable;
selecting one prefills an explicit local/session override. Changes affect future
segments only; caller refreshes bound knowledge snapshots on changed().
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .theme import apply_theme


class _Signals(QObject):
    finished = Signal(object, bool, bool)


class _Task(QRunnable):
    def __init__(self, operation, mutation):
        super().__init__()
        self.operation, self.mutation = operation, mutation
        self.signals = _Signals()

    def run(self):
        try:
            value = self.operation()
        except Exception:
            self.signals.finished.emit(None, False, self.mutation)
        else:
            self.signals.finished.emit(value, True, self.mutation)


class KnowledgeDialog(QDialog):
    """Manage imported bundles and confirmed local/session literal substitutions."""
    changed = Signal()

    def __init__(self, knowledge_store, rule_store, parent=None):
        super().__init__(parent)
        apply_theme(self)
        self.knowledge_store, self.rule_store = knowledge_store, rule_store
        self._busy = False
        self._task = None
        self._rows = []
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.setWindowTitle("知识与词典 · 声笺")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        title = QLabel("知识与词典")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        hint = QLabel("优先级：本场会议 > 本机长期 > Iris。更新知识包不会覆盖人工规则。\n"
                      "规则采用字面替换；同一轮替换不会级联。修改只影响后续句段。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.controls = QWidget()
        body = QVBoxLayout(self.controls)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        heading = QLabel("知识包")
        heading.setObjectName("sectionTitle")
        body.addWidget(heading)
        imports = QHBoxLayout()
        self.folder_button = QPushButton("导入知识包文件夹…")
        self.folder_button.clicked.connect(self.choose_folder)
        self.zip_button = QPushButton("导入知识包 ZIP…")
        self.zip_button.clicked.connect(self.choose_zip)
        imports.addWidget(self.folder_button)
        imports.addWidget(self.zip_button)
        body.addLayout(imports)
        self.bundle_label = QLabel()
        self.bundle_label.setObjectName("muted")
        self.bundle_label.setTextFormat(Qt.TextFormat.PlainText)
        self.bundle_label.setWordWrap(True)
        body.addWidget(self.bundle_label)
        self.table = QTableWidget(0, 4)
        self.table.setMinimumHeight(160)
        self.table.setHorizontalHeaderLabels(["原词", "替换为", "规则来源", "生效与冲突"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._select)
        body.addWidget(self.table)
        heading = QLabel("人工规则")
        heading.setObjectName("sectionTitle")
        body.addWidget(heading)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(8)
        self.source_edit, self.target_edit = QLineEdit(), QLineEdit()
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("本机长期（保存到本机）", "local")
        self.scope_combo.addItem("仅本场会议（不落盘）", "session")
        form.addRow("原词", self.source_edit)
        form.addRow("替换为", self.target_edit)
        form.addRow("保存范围", self.scope_combo)
        body.addLayout(form)
        actions = QGridLayout()
        actions.setSpacing(8)
        self.save_button = QPushButton("添加 / 保存人工规则")
        self.save_button.clicked.connect(self.save_rule)
        self.source_edit.textChanged.connect(self._update_save_button)
        self.target_edit.textChanged.connect(self._update_save_button)
        self._update_save_button()
        self.delete_button = QPushButton("删除所选人工规则")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self.delete_selected)
        self.new_button = QPushButton("新建规则")
        self.new_button.clicked.connect(self.clear_editor)
        actions.addWidget(self.save_button, 0, 0, 1, 2)
        actions.addWidget(self.delete_button, 1, 0)
        actions.addWidget(self.new_button, 1, 1)
        body.addLayout(actions)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.controls)
        layout.addWidget(scroll, 1)
        self.status_label = QLabel("正在读取知识与规则…")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self._submit(self._snapshot, False)

    def _snapshot(self):
        bundle = self.knowledge_store.active_bundle()
        return bundle, self.rule_store.list_rules(bundle["replacements"])

    def _submit(self, operation, mutation=True):
        if self._busy:
            return False
        self._busy = True
        self.controls.setEnabled(False)
        self.close_button.setEnabled(False)
        self.status_label.setText("正在校验并更新…")
        self._task = _Task(operation, mutation)
        self._task.signals.finished.connect(self._finished)
        self._pool.start(self._task)
        return True

    @Slot(object, bool, bool)
    def _finished(self, result, success, mutation):
        self._busy = False
        self._task = None
        self.controls.setEnabled(True)
        self.close_button.setEnabled(True)
        if not success:
            self.status_label.setText("操作失败。请检查知识包格式、校验和、文件大小或规则内容；未完成导入不会替换旧知识包。")
            return
        bundle, self._rows = result
        version = bundle["manifest"].get("package_version", "尚未导入")
        self.bundle_label.setText(f"知识包版本：{version} · 热词 {len(bundle['hotwords'])} · "
                                  f"保护词 {len(bundle['protected_terms'])} · 提示规则 {len(bundle['rules'])}")
        self.table.setRowCount(0)
        names = {"iris": "Iris（只读）", "local": "本机长期", "session": "本场会议"}
        for row, item in enumerate(self._rows):
            self.table.insertRow(row)
            status = "生效" if item["effective"] else "被高优先级覆盖"
            if item["conflict"]:
                status += " · 有冲突"
            for column, text in enumerate((item["source"], item["target"], names[item["scope"]], status)):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.clear_editor()
        conflicts = len({item["source"] for item in self._rows if item["conflict"]})
        self.status_label.setText(f"已更新。冲突词条：{conflicts}。Iris 规则只读，可添加人工规则覆盖。")
        if mutation:
            self.changed.emit()

    def import_path(self, path):
        def operation():
            self.knowledge_store.import_bundle(path)
            return self._snapshot()
        return self._submit(operation)

    @Slot()
    def choose_folder(self):
        if self._busy:
            return
        path = QFileDialog.getExistingDirectory(self, "选择包含 manifest.json 的知识包文件夹")
        if path:
            self.import_path(path)

    @Slot()
    def choose_zip(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择知识包 ZIP", "", "ZIP (*.zip)")
        if path:
            self.import_path(path)

    def _update_save_button(self):
        self.save_button.setEnabled(bool(self.source_edit.text().strip()
                                         and self.target_edit.text().strip()))

    def _selected_row(self):
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    @Slot()
    def _select(self):
        row = self._selected_row()
        if not 0 <= row < len(self._rows):
            self.delete_button.setEnabled(False)
            return
        item = self._rows[row]
        self.source_edit.setText(item["source"])
        self.target_edit.setText(item["target"])
        self.scope_combo.setCurrentIndex(1 if item["scope"] == "session" else 0)
        self.delete_button.setEnabled(item["scope"] != "iris")
        if item["scope"] == "iris":
            self.status_label.setText("Iris 规则只读。保存将创建所选范围的人工覆盖规则。")

    @Slot()
    def clear_editor(self):
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.source_edit.clear()
        self.target_edit.clear()
        self.delete_button.setEnabled(False)

    @Slot()
    def save_rule(self):
        source, target = self.source_edit.text(), self.target_edit.text()
        scope = self.scope_combo.currentData()
        if not source.strip() or not target.strip():
            self.status_label.setText("原词和替换词均不能为空。")
            return False
        def operation():
            self.rule_store.set_rule(source, target, scope)
            return self._snapshot()
        return self._submit(operation)

    @Slot()
    def delete_selected(self):
        row = self._selected_row()
        if not 0 <= row < len(self._rows) or self._rows[row]["scope"] == "iris":
            self.status_label.setText("请选择本机或本场规则；Iris 规则不可直接删除。")
            return False
        item = dict(self._rows[row])
        def operation():
            self.rule_store.delete_rule(item["source"], item["scope"])
            return self._snapshot()
        return self._submit(operation)

    def reject(self):
        if not self._busy:
            super().reject()

    def accept(self):
        self.reject()

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
        else:
            super().closeEvent(event)
