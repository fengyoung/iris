"""Model installer dialog; instantiate ModelDialog(data_root / 'models').

installed(kind, directory) is emitted on the GUI thread after verified activation.
Kinds are 'asr', 'embedding', 'vad', and 'speaker'. Operations run on one background worker. Closing
while active requests cancellation and defers destruction until the worker returns.
A pinned official contextual Paraformer ONNX manifest is bundled; other models require
a user-selected pinned manifest or an explicitly trusted local folder.
"""
from __future__ import annotations

import json
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout,
)

from .models import (
    ModelCancelled, ModelManager, builtin_contextual_manifest, builtin_embedding_manifest,
    builtin_fsmn_manifest, builtin_speaker_manifest, manifest_from_directory,
)

from .theme import apply_theme



class _Signals(QObject):
    progress = Signal(object)
    finished = Signal(object, str)


class _InstallTask(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = _Signals()

    def run(self):
        try:
            result = self.operation(self.signals.progress.emit)
        except ModelCancelled:
            self.signals.finished.emit(None, "cancelled")
        except Exception:
            # URLs and filesystem exceptions can carry sensitive source information.
            self.signals.finished.emit(None, "failed")
        else:
            self.signals.finished.emit(result, "complete")


class ModelDialog(QDialog):
    """Download pinned manifests or explicitly trust a local model folder.

    managers may inject {'asr': manager, 'embedding': manager} for offline tests.
    launch_download(path) and launch_import(path,trust_local=True) return False
    when busy; local import without explicit trust raises ValueError.
    """
    installed = Signal(str, str)

    def __init__(self, root: Path, parent=None, *, managers=None):
        super().__init__(parent)
        apply_theme(self)
        self.root = Path(root)
        self.managers = managers or {kind: ModelManager(self.root / kind) for kind in ("asr", "embedding", "vad", "speaker")}
        self._busy = False
        self._close_pending = False
        self._task = None
        self._kind = "asr"
        self._cancel = threading.Event()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.setWindowTitle("安装模型 · 声笺")
        self.resize(620, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        title = QLabel("安装模型")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        heading = QLabel("选择模型")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("语音识别模型（ASR）", "asr")
        self.kind_combo.addItem("语义检索模型（Embedding）", "embedding")
        self.kind_combo.addItem("语音活动检测模型（FSMN VAD）", "vad")
        self.kind_combo.addItem("会议说话人（实验性 · 约 34 MB）", "speaker")
        layout.addWidget(self.kind_combo)
        hint = QLabel(
            "从包含固定 SHA-256 校验值的 JSON 模型清单下载，或导入你信任的本地模型文件夹。\n"
            "内置 contextual Paraformer ONNX 语音模型清单（约 905 MB），校验值来自官方固定版本元数据。"
            "另提供多语言 MiniLM 语义检索模型（约 485 MB）和 FSMN VAD（约 517 KB）。"
            "文件完整性通过不代表模型与当前识别后端兼容；请使用对应后端支持的模型格式。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.builtin_button = QPushButton("下载 contextual Paraformer ONNX（约 905 MB）")
        self.builtin_button.clicked.connect(self.launch_builtin)
        self.kind_combo.currentIndexChanged.connect(self._update_builtin_button)
        layout.addWidget(self.builtin_button)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.download_button = QPushButton("从模型清单下载…")
        self.download_button.clicked.connect(self.choose_manifest)
        self.import_button = QPushButton("导入本地模型文件夹…")
        self.import_button.clicked.connect(self.choose_directory)
        actions.addWidget(self.download_button)
        actions.addWidget(self.import_button)
        layout.addLayout(actions)
        layout.addSpacing(12)
        heading = QLabel("安装进度")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("选择模型类型后开始安装。已有模型仅在新模型完整验证后切换。")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        layout.addStretch()
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("取消安装")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_install)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    @Slot()
    def choose_manifest(self):
        if self._busy:
            return
        filename, _ = QFileDialog.getOpenFileName(self, "选择固定版本模型清单", "", "JSON (*.json)")
        if filename:
            self.launch_download(filename)

    @Slot()
    def choose_directory(self):
        if self._busy:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择本地模型文件夹")
        if not directory:
            return
        answer = QMessageBox.question(
            self, "确认模型来源", "此操作会为所选文件生成本机校验值并复制到模型目录。\n"
            "自行生成的校验值只能发现后续文件变化，不能证明发布者身份、来源安全或模型兼容性。\n"
            "请仅导入你信任且适用于当前后端的模型。确认信任此文件夹？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.launch_import(directory, trust_local=True)

    def _update_builtin_button(self):
        kind = self.kind_combo.currentData()
        labels = {"asr": "下载 contextual Paraformer ONNX（约 905 MB）",
                  "vad": "下载 FSMN VAD ONNX（约 517 KB）",
                  "embedding": "下载多语言 MiniLM 语义检索模型（约 485 MB）",
                  "speaker": "下载会议说话人模型（约 34 MB）"}
        self.builtin_button.setText(labels[kind])
        self.builtin_button.setEnabled(not self._busy)

    @Slot()
    def launch_builtin(self):
        kind = self.kind_combo.currentData()
        factories = {"asr": builtin_contextual_manifest, "vad": builtin_fsmn_manifest,
                     "embedding": builtin_embedding_manifest, "speaker": builtin_speaker_manifest}
        manifest = factories[kind]()
        return self._launch(kind, lambda progress: self.managers[kind].download(
            manifest, progress=progress, cancel=self._cancel), "正在下载官方固定版本模型…")

    def launch_download(self, manifest_path):
        path = Path(manifest_path)
        kind = self.kind_combo.currentData()
        def operation(progress):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
                raise ValueError("Invalid model manifest file")
            with path.open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("Model manifest too large")
            manifest = json.loads(data)
            return self.managers[kind].download(manifest, progress=progress, cancel=self._cancel)
        return self._launch(kind, operation, "正在读取模型清单并下载…")

    def launch_import(self, directory, *, trust_local=False):
        if not trust_local:
            raise ValueError("Local import requires explicit user trust")
        kind = self.kind_combo.currentData()
        def operation(progress):
            # Hashing runs off the GUI thread. Cancellation is checked before copying.
            if self._cancel.is_set():
                raise ModelCancelled()
            manifest = manifest_from_directory(directory, model_id=f"local-{kind}", trust_local=True,
                                               cancel=self._cancel, progress=progress)
            if self._cancel.is_set():
                raise ModelCancelled()
            return self.managers[kind].import_model(directory, manifest, progress=progress, cancel=self._cancel)
        return self._launch(kind, operation, "正在为可信本地文件计算校验值…")

    def _launch(self, kind, operation, status):
        if self._busy:
            return False
        self._busy = True
        self._kind = kind
        self._cancel.clear()
        self.kind_combo.setEnabled(False)
        self.builtin_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(status)
        self._task = _InstallTask(operation)
        self._task.signals.progress.connect(self._progress)
        self._task.signals.finished.connect(self._finished)
        self._pool.start(self._task)
        return True

    @Slot(object)
    def _progress(self, value):
        total, completed = value.get("total", 0), value.get("completed", 0)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(min(1000, int(1000 * completed / total)) if total else 0)
        if not self._cancel.is_set():
            stage = "正在计算校验值" if value.get("stage") == "hashing" else "正在安装"
            self.status_label.setText(f"{stage}：{completed / 1024**2:.1f} / {total / 1024**2:.1f} MB")

    @Slot(object, str)
    def _finished(self, result, status):
        self._busy = False
        self._task = None
        self.kind_combo.setEnabled(True)
        self._update_builtin_button()
        self.download_button.setEnabled(True)
        self.import_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setRange(0, 1000)
        if status == "complete":
            self.progress_bar.setValue(1000)
            self.status_label.setText("模型已通过完整性验证并安装。")
            self.installed.emit(self._kind, result["directory"])
        elif status == "cancelled":
            self.progress_bar.setValue(0)
            self.status_label.setText("已取消安装，之前的模型未更改。")
        else:
            self.progress_bar.setValue(0)
            self.status_label.setText("安装失败。请检查清单校验值、文件格式、网络及可用磁盘空间。之前的模型未更改。")
        if self._close_pending:
            super().reject()

    @Slot()
    def cancel_install(self):
        if self._busy:
            self._cancel.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("正在取消，等待当前文件读取或网络请求结束…")

    def reject(self):
        if self._busy:
            self._close_pending = True
            self.cancel_install()
        else:
            super().reject()

    def accept(self):
        self.reject()

    def closeEvent(self, event):
        if self._busy:
            self._close_pending = True
            self.cancel_install()
            event.ignore()
        else:
            super().closeEvent(event)
