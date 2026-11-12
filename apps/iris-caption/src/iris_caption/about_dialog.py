"""Accessible product/build information without runtime configuration or secrets."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout,
)

from .branding import icon_path
from .theme import apply_theme
from .version import version_text


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_theme(self)
        self.setWindowTitle("关于声笺")
        self.setAccessibleName("关于声笺：版本与构建信息")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        header = QHBoxLayout()
        self.logo = QLabel()
        self.logo.setAccessibleName("声笺应用图标")
        self.logo.setFixedSize(64, 64)
        icon = QIcon(str(icon_path()))
        self.setWindowIcon(icon)
        self.logo.setPixmap(icon.pixmap(QSize(64, 64), self.devicePixelRatioF()))
        header.addWidget(self.logo)
        title = QLabel("声笺 · Iris Caption")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        self.version_details = version_text()
        self.details = QLabel(self.version_details)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.details.setAccessibleName("版本与构建信息")
        layout.addWidget(self.details)
        self.notice = QLabel("复制版本信息可用于反馈问题。")
        self.notice.setObjectName("status")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        self.copy_button = QPushButton("复制版本信息")
        self.copy_button.setAccessibleName("复制版本与构建信息")
        self.copy_button.clicked.connect(self.copy_version)
        buttons.addButton(self.copy_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.copy_button.setFocus()

    def copy_version(self):
        QApplication.clipboard().setText(self.version_details)
        self.notice.setText("版本信息已复制。")
