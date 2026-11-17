"""Human-readable job forms; no technical JSON editor in product workflows."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .exceptions import ValidationError


class JobDialog(QDialog):
    """Edit a job while retaining unexposed source metadata and identifiers."""
    def __init__(self, job=None, parent=None):
        super().__init__(parent)
        self.original = copy.deepcopy(job or {})
        self.setWindowTitle('岗位要求与评价标准')
        self.resize(700, 780)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        self.name = QLineEdit(self.original.get('name', ''))
        self.jd = QPlainTextEdit(self.original.get('jd', ''))
        self.jd.setMinimumHeight(180)
        self.tier = QComboBox()
        for label, value in [('未选择', ''), ('基层', '基层'), ('中层', '中层'), ('中高层', '中高层')]:
            self.tier.addItem(label, value)
        self.tier.setCurrentIndex(max(0, self.tier.findData(self.original.get('tier', ''))))
        self.management = QCheckBox('该岗位要求团队管理能力')
        self.management.setChecked(bool(self.original.get('management_required', False)))
        self.requirements = QPlainTextEdit('\n'.join(self.original.get('requirements', [])))
        self.requirements.setPlaceholderText('每行一项岗位要求')
        dimensions = self.original.get('dimensions', [])
        self.dimensions = QPlainTextEdit('\n'.join(
            row.get('name', '') + ('：' + row.get('description', '') if row.get('description') else '')
            if isinstance(row, dict) else str(row) for row in dimensions))
        self.dimensions.setPlaceholderText('每行一项，例如：系统设计：能够解释方案取舍')
        self.reviewed = QCheckBox('我已核对岗位原文与评价标准')
        self.reviewed.setChecked(bool(self.original.get('reviewed', False)))
        for label, widget in [('岗位名称', self.name), ('岗位原文 / JD', self.jd),
                              ('领导力适用层级', self.tier), ('管理要求', self.management),
                              ('关键任职要求', self.requirements), ('专业评价维度', self.dimensions),
                              ('审阅状态', self.reviewed)]:
            form.addRow(label, widget)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.PlainText)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        name = self.name.text().strip()
        if not name:
            raise ValidationError('请填写岗位名称')
        rows = []
        previous = {row.get('name'): row for row in self.original.get('dimensions', []) if isinstance(row, dict)}
        for line in self.dimensions.toPlainText().splitlines():
            if not line.strip():
                continue
            title, separator, description = line.partition('：')
            if not separator:
                title, separator, description = line.partition(':')
            title = title.strip()
            if not title:
                raise ValidationError('评价维度名称不能为空')
            rows.append({**previous.get(title, {}), 'name': title, 'description': description.strip()})
        return {**copy.deepcopy(self.original), 'name': name, 'jd': self.jd.toPlainText(),
                'tier': self.tier.currentData(), 'management_required': self.management.isChecked(),
                'requirements': [s.strip() for s in self.requirements.toPlainText().splitlines() if s.strip()],
                'dimensions': rows, 'reviewed': self.reviewed.isChecked()}

    def accept(self):
        try:
            self.value()
        except ValidationError as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class CatalogDraftDialog(QDialog):
    """Select and inspect multiple extracted jobs before adopting any of them."""
    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = copy.deepcopy(jobs)
        self.setWindowTitle('审阅拆分岗位')
        self.resize(850, 620)
        layout = QVBoxLayout(self)
        label = QLabel('勾选要采用的岗位，可逐项查看原文并编辑。未确认的草稿保持待审阅。')
        label.setWordWrap(True)
        layout.addWidget(label)
        columns = QHBoxLayout()
        self.list = QListWidget()
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        for index, job in enumerate(self.jobs):
            item = QListWidgetItem(job.get('name', '未命名岗位'))
            item.setData(Qt.UserRole, index)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self.show_job)
        columns.addWidget(self.list, 1)
        columns.addWidget(self.preview, 2)
        layout.addLayout(columns)
        edit = QPushButton('编辑当前岗位')
        edit.clicked.connect(self.edit_current)
        layout.addWidget(edit)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('采用勾选岗位')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if self.jobs:
            self.list.setCurrentRow(0)

    def show_job(self, index):
        if 0 <= index < len(self.jobs):
            job = self.jobs[index]
            self.preview.setPlainText(job.get('name', '') + '\n\n' + job.get('jd', ''))

    def edit_current(self):
        index = self.list.currentRow()
        if 0 <= index < len(self.jobs):
            dialog = JobDialog(self.jobs[index], self)
            if dialog.exec() == QDialog.Accepted:
                self.jobs[index] = dialog.value()
                self.list.item(index).setText(self.jobs[index]['name'])
                self.show_job(index)

    def value(self):
        return [copy.deepcopy(job) for index, job in enumerate(self.jobs)
                if self.list.item(index).checkState() == Qt.Checked]

    def accept(self):
        if not self.value():
            self.error.setText('请至少勾选一个岗位')
            return
        super().accept()
