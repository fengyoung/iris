"""Background-only material imports and personal Feishu authorization UI."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from . import settings
from .theme import apply_theme

REDIRECT_URI = "http://127.0.0.1:8766/callback"


class _Result(QObject):
    finished = Signal(object, bool)


class _Task(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.result = _Result()

    def run(self):
        try:
            value = self.operation()
        except Exception:
            # Exceptions can contain source text, OAuth codes, or secrets.
            self.result.finished.emit(None, False)
        else:
            self.result.finished.emit(value, True)


class MaterialsDialog(QDialog):
    """Edit meeting background and manage a shared MaterialIndex.

    ``background`` returns formatted title/participants/project text. ``on_changed``
    is called without arguments on the GUI thread after a successful mutation.
    The controller loads retained materials before constructing this dialog.
    ``root`` is passed unchanged to save_retained. Close/Escape are refused while
    a worker is active; no GUI-thread joins or unsafe worker termination occur.
    """
    changed = Signal()

    def __init__(self, index, root: Path, on_changed=None, parent=None, retained_ids=None):
        super().__init__(parent)
        apply_theme(self)
        self.index = index
        self.root = Path(root)
        self._busy = False
        self._task = None
        self._completion = None
        self._client = None
        self._retained = set(retained_ids or ())
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        if on_changed is not None:
            self.changed.connect(on_changed)
        self.setWindowTitle("会议背景与资料 · 声笺")
        self.resize(780, 740)
        self._build()
        self._submit(lambda: (self._snapshot(), settings.read_settings()), self._initial_loaded, "正在读取资料列表…")

    @property
    def background(self):
        return "\n".join(f"{label}：{field.text().strip()}" for label, field in (
            ("主题", self.title_edit), ("参会人", self.participants_edit),
            ("项目", self.project_edit),
        ) if field.text().strip())

    def set_background(self, title="", participants="", project=""):
        self.title_edit.setText(title)
        self.participants_edit.setText(participants)
        self.project_edit.setText(project)

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        title = QLabel("会议背景与资料")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        description = QLabel("背景与资料只影响后续句段。未选择长期保留的正文仅在本场内存中使用。")
        description.setObjectName("muted")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.controls = QWidget()
        body = QVBoxLayout(self.controls)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)
        heading = QLabel("会议背景")
        heading.setObjectName("sectionTitle")
        body.addWidget(heading)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(8)
        self.title_edit = QLineEdit()
        self.participants_edit = QLineEdit()
        self.project_edit = QLineEdit()
        for name, field in (("会议主题", self.title_edit), ("参会人", self.participants_edit), ("项目", self.project_edit)):
            form.addRow(name, field)
            field.textChanged.connect(self.changed.emit)
        body.addLayout(form)
        tabs = QTabWidget()
        paste = QWidget()
        paste_layout = QVBoxLayout(paste)
        paste_layout.setContentsMargins(12, 12, 12, 12)
        paste_layout.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("资料名称")
        self.text_edit = QTextEdit()
        self.text_edit.setMinimumHeight(120)
        self.text_edit.setPlaceholderText("粘贴会议参考资料…")
        paste_layout.addWidget(self.name_edit)
        paste_layout.addWidget(self.text_edit)
        row = QHBoxLayout()
        self.add_text_button = QPushButton("添加文字")
        self.add_text_button.clicked.connect(self.add_pasted_text)
        row.addWidget(self.add_text_button)
        self.add_file_button = QPushButton("导入本地文件…")
        self.add_file_button.clicked.connect(self.choose_file)
        row.addWidget(self.add_file_button)
        row.addStretch()
        paste_layout.addLayout(row)
        tabs.addTab(paste, "文字与本地文件")
        tabs.addTab(self._feishu_tab(), "飞书文档")
        body.addWidget(tabs)
        self.retain_checkbox = QCheckBox("长期保留本次新增资料（正文将保存到本机）")
        body.addWidget(self.retain_checkbox)
        heading = QLabel("已添加资料")
        heading.setObjectName("sectionTitle")
        body.addWidget(heading)
        self.material_list = QListWidget()
        self.material_list.setWordWrap(True)
        self.material_list.setMinimumHeight(120)
        body.addWidget(self.material_list)
        actions = QGridLayout()
        actions.setSpacing(8)
        self.remove_button = QPushButton("删除所选资料")
        self.remove_button.clicked.connect(self.remove_selected)
        actions.addWidget(self.remove_button, 0, 0)
        self.retention_button = QPushButton("切换所选资料长期保留")
        self.retention_button.clicked.connect(self.toggle_selected_retention)
        actions.addWidget(self.retention_button, 0, 1)
        self.refresh_button = QPushButton("验证并更新飞书资料")
        self.refresh_button.clicked.connect(self.refresh_selected)
        actions.addWidget(self.refresh_button, 1, 0, 1, 2)
        body.addLayout(actions)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self.controls)
        layout.addWidget(scroll, 1)
        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status_label)
        self.done_button = QPushButton("完成")
        self.done_button.clicked.connect(self.accept)
        layout.addWidget(self.done_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.material_list.itemSelectionChanged.connect(self._update_actions)
        self.text_edit.textChanged.connect(self._update_actions)
        for field in (self.app_id_edit, self.callback_edit, self.document_edit):
            field.textChanged.connect(self._update_actions)
        self._update_actions()

    def _selected_item(self):
        items = self.material_list.selectedItems()
        return items[0] if items else None

    def _update_actions(self):
        idle = not self._busy
        item = self._selected_item()
        app_id = bool(self.app_id_edit.text().strip())
        self.remove_button.setEnabled(idle and item is not None)
        self.retention_button.setEnabled(idle and item is not None)
        self.refresh_button.setEnabled(bool(idle and item is not None
            and item.data(Qt.ItemDataRole.UserRole + 1) and app_id))
        self.add_text_button.setEnabled(idle and bool(self.text_edit.toPlainText().strip()))
        self.authorize_button.setEnabled(idle and app_id)
        self.exchange_button.setEnabled(idle and self._client is not None
                                        and bool(self.callback_edit.text().strip()))
        self.import_feishu_button.setEnabled(idle and app_id and bool(self.document_edit.text().strip()))

    def _feishu_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        hint = QLabel(
            "使用你自己创建的飞书应用，不内置应用密钥。请配置文档只读权限及用户授权，"
            f"并登记回调地址：{REDIRECT_URI}\n"
            "授权后浏览器可能显示无法连接；复制地址栏完整回调 URL，粘贴到下方完成授权。"
            "密钥与用户令牌存入系统钥匙串。读取文档需要你的当前访问权限。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(8)
        self.app_id_edit = QLineEdit()
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_edit.setPlaceholderText("留空使用已存入钥匙串的密钥")
        self.callback_edit = QLineEdit()
        self.callback_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.callback_edit.setPlaceholderText("完整回调 URL（含 code 与 state）")
        self.document_edit = QLineEdit()
        self.document_edit.setPlaceholderText("https://…feishu.cn/docx/…")
        for label, widget in (("App ID", self.app_id_edit), ("App Secret", self.secret_edit),
                              ("授权回调", self.callback_edit), ("文档地址", self.document_edit)):
            form.addRow(label, widget)
        layout.addLayout(form)
        row = QHBoxLayout()
        for attribute, label, handler in (("authorize_button", "打开浏览器授权", self.authorize_feishu),
                               ("exchange_button", "完成授权", self.exchange_callback),
                               ("import_feishu_button", "读取文档", self.import_feishu)):
            button = QPushButton(label)
            setattr(self, attribute, button)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        return tab

    def _snapshot(self):
        entries = self.index.list_materials()
        semantic = getattr(self.index, "semantic_available", False)
        if callable(semantic):
            semantic = semantic()
        return entries, bool(semantic)

    @property
    def retained_ids(self):
        return set(self._retained)

    def _initial_loaded(self, result):
        snapshot, config = result
        self.app_id_edit.setText(config.get("feishu_app_id", ""))
        self._display(snapshot)

    def _display(self, snapshot):
        entries, semantic = snapshot
        self.material_list.clear()
        for entry in entries:
            key = str(entry["id"])
            mode = "长期保留" if key in self._retained else "仅本场"
            name = str(entry.get("name") or entry.get("title") or "未命名资料")
            availability = " · 待读取验证权限（未参与检索）" if entry.get("requires_verification") else ""
            item = QListWidgetItem(f"{name}  ·  {mode}{availability}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.get("source_url"))
            item.setToolTip(str(entry.get("source", "")))
            self.material_list.addItem(item)
        availability = "语义检索可用" if semantic else "关键词检索可用；语义检索未启用或模型不可用"
        self.status_label.setText(f"{len(entries)} 份资料 · {availability}")

    def _submit(self, operation, completion, status):
        if self._busy:
            return False
        self._busy = True
        self._update_actions()
        self.controls.setEnabled(False)
        self.done_button.setEnabled(False)
        self.status_label.setText(status)
        self._completion = completion
        task = _Task(operation)
        self._task = task
        task.result.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self._pool.start(task)
        return True

    @Slot(object, bool)
    def _finished(self, result, success):
        completion = self._completion
        self._busy = False
        self._completion = None
        self._task = None
        self.controls.setEnabled(True)
        self.done_button.setEnabled(True)
        if success:
            completion(result)
        else:
            self.status_label.setText("操作未完成。请检查文件格式、网络、权限或钥匙串后重试；未记录正文或凭据。")

        self._update_actions()

    def _mutation(self, operation, retained=None):
        previous = set(self._retained)
        desired = previous if retained is None else set(retained)

        def work():
            if desired != previous:
                self.index.save_retained(self.root, desired)
            operation()
            return self._snapshot()

        def done(snapshot):
            self._retained = desired
            self._display(snapshot)
            self.changed.emit()

        self._submit(work, done, "正在更新资料与索引…")

    def _add(self, operation):
        retain = self.retain_checkbox.isChecked()
        previous = set(self._retained)

        def work():
            key = str(operation())
            desired = previous | {key} if retain else previous
            if retain:
                try:
                    self.index.save_retained(self.root, desired)
                except Exception:
                    self.index.remove(key)
                    raise
            return self._snapshot(), desired

        def done(result):
            snapshot, desired = result
            self._retained = desired
            self._display(snapshot)
            self.changed.emit()

        self._submit(work, done, "正在读取资料并建立索引…")

    def add_pasted_text(self):
        text = self.text_edit.toPlainText().strip()
        if not text:
            self.status_label.setText("请先粘贴资料正文。")
            return
        name = self.name_edit.text().strip() or "粘贴资料"
        self._add(lambda: self.index.add_text(name, text, source="text"))

    def choose_file(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入会议资料", "", "支持的文件 (*.md *.txt *.pdf *.docx)")
        if path:
            self.add_file(path)

    def add_file(self, path):
        self._add(lambda: self.index.add_file(Path(path)))

    def remove_selected(self):
        item = self._selected_item()
        if item is not None:
            key = item.data(Qt.ItemDataRole.UserRole)
            self._mutation(lambda: self.index.remove(key), self._retained - {key})

    def toggle_selected_retention(self):
        item = self._selected_item()
        if item is not None:
            key = item.data(Qt.ItemDataRole.UserRole)
            self._mutation(lambda: None, self._retained ^ {key})

    def authorize_feishu(self):
        if self._busy:
            return
        app_id = self.app_id_edit.text().strip()
        secret = self.secret_edit.text()
        if not app_id:
            self.status_label.setText("请填写你自己的飞书应用 App ID。")
            return
        self.secret_edit.clear()
        previous_client, self._client = self._client, None

        def work():
            from .feishu import FeishuClient
            if previous_client is not None:
                previous_client.close()
            value = secret or settings.get_secret("feishu_app_secret")
            if not value:
                raise ValueError("Missing personal app secret")
            if secret:
                settings.set_secret("feishu_app_secret", value)
            config = settings.read_settings()
            config["feishu_app_id"] = app_id
            settings.save_settings(config)
            client = FeishuClient(app_id, value)
            try:
                url = client.authorization_url(REDIRECT_URI)
            except Exception:
                client.close()
                raise
            return client, url

        def done(result):
            self._client, url = result
            opened = QDesktopServices.openUrl(QUrl(url))
            self.status_label.setText("请在浏览器授权，然后粘贴完整回调 URL。" if opened else "无法打开浏览器，请检查默认浏览器后重试授权。")

        self._submit(work, done, "正在准备个人应用授权…")

    def exchange_callback(self):
        if self._busy:
            return
        callback = self.callback_edit.text().strip()
        if self._client is None or not callback:
            self.status_label.setText("请先打开浏览器授权，再粘贴回调 URL。")
            return
        self.callback_edit.clear()
        self._submit(lambda: self._client.exchange_redirect(callback),
                     lambda _: self.status_label.setText("授权成功，可以读取有权访问的文档。"), "正在验证授权…")

    def import_feishu(self):
        url = self.document_edit.text().strip()
        app_id = self.app_id_edit.text().strip()
        if not url or not app_id:
            self.status_label.setText("请填写 App ID 和飞书文档地址，并先完成用户授权。")
            return
        client = self._client

        def work():
            from .feishu import FeishuClient
            active = client or FeishuClient(app_id, settings.get_secret("feishu_app_secret"))
            try:
                document = active.fetch_document(url)
            finally:
                if client is None:
                    active.close()
            return self.index.add_text(document.title, document.text, source=document.url)

        self._add(work)

    def refresh_selected(self):
        item = self._selected_item()
        app_id = self.app_id_edit.text().strip()
        if item is None or not item.data(Qt.ItemDataRole.UserRole + 1) or not app_id:
            self.status_label.setText("请选择飞书资料，并填写 App ID、完成用户授权。")
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        url = item.data(Qt.ItemDataRole.UserRole + 1)
        client = self._client
        retained = set(self._retained)

        def work():
            from .feishu import FeishuClient
            active = client or FeishuClient(app_id, settings.get_secret("feishu_app_secret"))
            try:
                document = active.fetch_document(url)
            finally:
                if client is None:
                    active.close()
            self.index.verify_retained(key, document.url, document.text)
            if key in retained:
                self.index.save_retained(self.root, retained)
            return self._snapshot()

        def done(snapshot):
            self._display(snapshot)
            self.changed.emit()

        self._submit(work, done, "正在验证文档权限并读取最新正文…")

    def done(self, result):
        if self._busy:
            self.status_label.setText("资料操作仍在进行，请等待完成后关闭。")
            return
        if self._client is not None:
            client, self._client = self._client, None
            self._submit(client.close, lambda _: self.done(result), "正在关闭授权连接…")
            return
        super().done(result)

    def closeEvent(self, event):
        event.ignore()
        self.reject()
