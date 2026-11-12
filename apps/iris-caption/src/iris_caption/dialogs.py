"""Desktop settings dialogs; credentials never enter JSON preferences."""
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .settings import (
    VAD_REQUIRED_MESSAGE, get_secret, read_settings, resolve_speaker_model_path, resolve_vad_model_path,
    save_settings, set_secret,
)
from .persistence import PersistenceError
from .theme import apply_theme


def choose_rule_scope(parent, source, target) -> str:
    """Choose correction memory in one action; dismissal never creates a rule."""
    from PySide6.QtCore import Qt
    dialog = QMessageBox(parent)
    apply_theme(dialog)
    dialog.setWindowTitle("记住这次纠正？")
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(f"{source} → {target}")
    dialog.setInformativeText("选择这条纠正规则的使用范围。")
    none = dialog.addButton("不记住", QMessageBox.ButtonRole.RejectRole)
    session = dialog.addButton("仅本场会议", QMessageBox.ButtonRole.AcceptRole)
    local = dialog.addButton("本机长期", QMessageBox.ButtonRole.ActionRole)
    dialog.setDefaultButton(none)
    dialog.setEscapeButton(none)
    dialog.exec()
    clicked = dialog.clickedButton()
    if clicked is session:
        return "session"
    if clicked is local:
        return "local"
    return "none"


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        apply_theme(self)
        self.setWindowTitle("声笺设置")
        self.resize(680, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        title = QLabel("声笺设置")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        description = QLabel("音频留在本机；校正文字和相关资料片段发送至 DeepSeek。")
        description.setObjectName("muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 12, 0, 0)
        body.setSpacing(12)
        local_title = QLabel("本地模型")
        local_title.setObjectName("sectionTitle")
        body.addWidget(local_title)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setSpacing(8)
        body.addLayout(form)
        settings = read_settings()
        self.model = self._directory(form, "语音模型目录", settings.get("model_path", ""))
        self.embedding = self._directory(form, "语义模型目录", settings.get("embedding_model_path", ""))
        selected_vad = settings.get('vad_model_path', '')
        resolved_vad = resolve_vad_model_path(selected_vad)
        self.vad = self._directory(form, "语音检测模型目录（收音必需 · 过滤噪声）",
                                   selected_vad or resolved_vad)
        vad_help = QLabel('已发现并校验本地语音检测模型。' if resolved_vad and not selected_vad
                          else VAD_REQUIRED_MESSAGE)
        vad_help.setWordWrap(True)
        body.addWidget(vad_help)
        self.speakers_enabled = QCheckBox("会议说话人区分（实验性，默认关闭；下场会议生效）")
        self.speakers_enabled.setChecked(settings.get('speakers_enabled', False))
        self.speakers_enabled.setAccessibleName("启用会议说话人区分实验")
        body.addWidget(self.speakers_enabled)
        speaker_form = QFormLayout()
        speaker_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.speaker = self._directory(speaker_form, "说话人模型目录（可在模型管理中安装）",
                                       settings.get('speaker_model_path', '') or resolve_speaker_model_path())
        body.addLayout(speaker_form)
        speaker_help = QLabel("仅在本机处理，不建立跨会议声纹库。单麦克风下短句、远处人声及多人同时说话可能无法区分；标签不代表真实身份，可手动修正。不会自动下载模型。")
        speaker_help.setWordWrap(True)
        speaker_help.setObjectName("muted")
        body.addWidget(speaker_help)
        install = QPushButton("下载或导入模型…")
        install.clicked.connect(self._install_models)
        body.addWidget(install)
        body.addSpacing(12)
        cloud_title = QLabel("文字校正 · DeepSeek")
        cloud_title.setObjectName("sectionTitle")
        body.addWidget(cloud_title)
        cloud_form = QFormLayout()
        cloud_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        cloud_form.setSpacing(8)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("留空保留已保存的密钥")
        cloud_form.addRow("DeepSeek API Key", self.key)
        self.model_name = QLineEdit(settings.get("deepseek_model", "deepseek-flash"))
        cloud_form.addRow("DeepSeek 模型", self.model_name)
        body.addLayout(cloud_form)
        body.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.notice = QLabel("密钥存入 macOS 钥匙串；模型目录需包含完整的本地模型。")
        self.notice.setObjectName("muted")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        self.about_button = QPushButton("关于声笺…")
        self.about_button.setObjectName("quiet")
        self.about_button.setAccessibleName("关于声笺：查看并复制版本信息")
        self.about_button.clicked.connect(self._show_about)
        footer = QHBoxLayout()
        footer.addWidget(self.about_button)
        footer.addStretch()
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _show_about(self):
        from .about_dialog import AboutDialog
        AboutDialog(self).exec()

    def _directory(self, form, label, value):
        row = QHBoxLayout()
        row.setSpacing(8)
        field = QLineEdit(value)
        field.setMinimumHeight(36)
        field.setAccessibleName(label)
        button = QPushButton("选择…")
        button.setAccessibleName(f"选择{label}")
        def choose():
            value = QFileDialog.getExistingDirectory(self, label)
            if value:
                field.setText(value)
        button.clicked.connect(choose)
        row.addWidget(field, 1)
        row.addWidget(button)
        form.addRow(label, row)
        return field

    def _install_models(self):
        from .model_dialog import ModelDialog
        from .settings import data_dir
        dialog = ModelDialog(data_dir() / 'models', self)
        fields = {'asr': self.model, 'embedding': self.embedding, 'vad': self.vad, 'speaker': self.speaker}
        dialog.installed.connect(lambda kind, directory: fields[kind].setText(directory))
        dialog.exec()

    def _save(self):
        try:
            for field in (self.model, self.embedding, self.vad, self.speaker):
                if field.text().strip() and not Path(field.text().strip()).is_dir():
                    raise ValueError("请选择存在的模型目录")
            settings = read_settings()
            if self.key.text().strip():
                set_secret("deepseek", self.key.text().strip())
            settings.update(model_path=self.model.text().strip(),
                            vad_model_path=self.vad.text().strip(),
                            embedding_model_path=self.embedding.text().strip(),
                            speakers_enabled=self.speakers_enabled.isChecked(),
                            speaker_model_path=self.speaker.text().strip(),
                            deepseek_model=self.model_name.text().strip())
            save_settings(settings)
        except PersistenceError:
            self.notice.setText("设置无法读取或保存：文件可能已损坏或来自更新版本。原文件未被重置，请恢复备份或使用匹配版本后重试。")
            return
        except Exception:
            self.notice.setText("保存失败：请检查模型路径和系统钥匙串是否可用。")
            return
        self.accept()


def available_key():
    try:
        return get_secret("deepseek")
    except Exception:
        return ""
