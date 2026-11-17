"""Qt dialogs. All user/model text is plain text unless explicitly JSON."""

from __future__ import annotations
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QLabel,
    QScrollArea,
    QWidget,
    QSizePolicy,
)

# 横向留白与控件尺寸统一收口，避免表单在窄窗口下被压缩、文字被截断。
FORM_MARGIN = (28, 24, 28, 16)
FORM_HSPACING = 22
FORM_VSPACING = 12
FIELD_MIN_WIDTH = 260
DIRECTORY_BUTTON_WIDTH = 104
DIALOG_MIN_WIDTH = 640


def _section_label(text):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setStyleSheet("font-weight: 600; padding-top: 4px;")
    return label


def _field_row(*widgets):
    """把输入框与随行按钮放进同一行，输入框占满剩余宽度。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    for index, widget in enumerate(widgets):
        if isinstance(widget, QPushButton):
            widget.setFixedWidth(DIRECTORY_BUTTON_WIDTH)
            row.addWidget(widget)
        else:
            widget.setMinimumWidth(FIELD_MIN_WIDTH)
            row.addWidget(widget, 1 if index == 0 else 0)
    return row


class TextDialog(QDialog):
    def __init__(self, title, text="", parent=None, json_mode=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        self.json_mode = json_mode
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit(text)
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if self.json_mode:
            try:
                json.loads(self.editor.toPlainText())
            except ValueError as exc:
                QMessageBox.warning(self, "JSON 格式错误", str(exc))
                return
        super().accept()

    def value(self):
        return json.loads(self.editor.toPlainText()) if self.json_mode else self.editor.toPlainText()


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("知遇设置")
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setSizeGripEnabled(True)
        self.resize(860, 680)
        self.setMinimumHeight(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*FORM_MARGIN)
        layout.setSpacing(16)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 12, 8)
        content.setSpacing(16)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        form.setHorizontalSpacing(FORM_HSPACING)
        form.setVerticalSpacing(10)
        content.addLayout(form)

        self.theme = QComboBox()
        self.theme.addItem("浅色", "light")
        self.theme.addItem("深色", "dark")
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings["theme"])))
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        theme_row.addWidget(self.theme)
        theme_row.addStretch(1)
        self.theme.setFixedWidth(160)
        form.addRow("主题：浅色 / 深色", theme_row)

        form.addRow(_section_label("云端服务"))

        self.fields = {}
        for key, label, placeholder in [
            ("endpoint", "云端接口（OpenAI 兼容）", "https://api.deepseek.com/v1"),
            ("model", "模型名称", "deepseek-chat"),
        ]:
            field = QLineEdit(settings.get(key, ""))
            field.setPlaceholderText(placeholder)
            field.setClearButtonEnabled(True)
            self.fields[key] = field
            form.addRow(label, field)

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("留空沿用已保存密钥")
        self.key.setMinimumWidth(FIELD_MIN_WIDTH)
        form.addRow("API 密钥", self.key)

        form.addRow(_section_label("本地模型目录"))

        for key, label in [
            ("asr_model", "Paraformer 模型目录"),
            ("vad_model", "FSMN 模型目录"),
        ]:
            field = QLineEdit(settings.get(key, ""))
            field.setPlaceholderText("选择已安装的本地模型文件夹")
            field.setClearButtonEnabled(True)
            self.fields[key] = field
            button = QPushButton("选择目录")
            button.clicked.connect(lambda checked=False, f=field: self.choose_directory(f))
            form.addRow(label, _field_row(field, button))

        form.addRow(_section_label("提醒"))

        self.checks = {}
        for name, label in [
            ("live_suggestions", "启用可选追问建议"),
            ("coverage_reminders", "显示维度覆盖提醒"),
            ("time_reminders", "启用时间提醒"),
        ]:
            check = QCheckBox(label)
            check.setChecked(settings.get(name, False))
            self.checks[name] = check
            form.addRow("", check)

        hint = QLabel("音频在本地转写；只有主动生成或开启校正后发送文本。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)

        content.addStretch(1)
        for field in [*self.fields.values(), self.key]:
            field.setMinimumHeight(36)
            field.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            field.setCursorPosition(0)
            if field is not self.key:
                field.setToolTip(field.text())
                field.textChanged.connect(lambda text, widget=field: widget.setToolTip(text))
        self.theme.setMinimumHeight(36)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save_button = buttons.button(QDialogButtonBox.Save)
        save_button.setText("保存")
        save_button.setObjectName("primary")
        save_button.setDefault(True)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_directory(self, field):
        path = QFileDialog.getExistingDirectory(self, "选择本地模型目录")
        if path:
            field.setText(path)

    def values(self):
        return {
            "theme": self.theme.currentData(),
            **{k: v.text() for k, v in self.fields.items()},
            **{k: v.isChecked() for k, v in self.checks.items()},
        }


class ProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建面试 Project")
        self.resize(460, 250)
        form = QFormLayout(self)
        self.fields = {}
        for key, label in [
            ("name", "面试名称"),
            ("candidate", "候选人称呼"),
            ("job", "岗位"),
            ("round", "轮次"),
            ("interview_at", "面试时间（可选）"),
            ("focus", "本轮重点（可选）"),
        ]:
            field = QLineEdit()
            self.fields[key] = field
            form.addRow(label, field)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return {key: value.text() for key, value in self.fields.items()}
