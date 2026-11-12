"""About entry points and non-destructive persistence error handling."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog

from iris_caption import about_dialog, dialogs, settings
from iris_caption.persistence import PersistenceError
from iris_caption.ui import MainWindow
from iris_caption.version import __version__, version_text


def test_about_build_details_and_copy_without_system_clipboard(app, monkeypatch):
    copied = []
    monkeypatch.setattr(QApplication, "clipboard", lambda: SimpleNamespace(setText=copied.append))
    dialog = about_dialog.AboutDialog()
    assert dialog.details.text() == version_text()
    assert __version__ in dialog.details.text()
    assert "Build" in dialog.details.text() and "构建日期：" in dialog.details.text()
    assert dialog.details.textFormat() == Qt.TextFormat.PlainText
    assert not dialog.logo.pixmap().isNull()
    assert dialog.copy_button.accessibleName()
    dialog.copy_button.click()
    assert copied == [version_text()]
    assert "已复制" in dialog.notice.text()
    dialog.close()


def test_about_entry_points(app, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    opened = []
    monkeypatch.setattr(about_dialog.AboutDialog, "exec", lambda self: opened.append(self.parent()))
    window = MainWindow()
    assert window.about_action.menuRole() == QAction.MenuRole.AboutRole
    window.about_action.trigger()
    dialog = dialogs.SettingsDialog(window)
    dialog.about_button.click()
    assert opened == [window, dialog]
    window.resize(720, 640)
    window.show()
    app.processEvents()
    assert window.width() == 720
    dialog.close()
    window.close()


def test_bad_preferences_remain_unchanged_after_ui_changes(app, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    path = tmp_path / "view_preferences.json"
    original = '{"schema_version":999,"font_size":33}'
    path.write_text(original)
    window = MainWindow(persist_preferences=True)
    assert not window._persist_preferences
    assert "原文件" in window.status_label.text()
    window.font_slider.setValue(40)
    window.save_preferences()
    window.close()
    assert path.read_text() == original


def test_settings_error_keeps_dialog_open_and_does_not_write_secret(app, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    dialog = dialogs.SettingsDialog()
    writes = []
    monkeypatch.setattr(dialogs, "set_secret", lambda *args: writes.append(args))
    def fail():
        raise PersistenceError("invalid")
    monkeypatch.setattr(dialogs, "read_settings", fail)
    dialog.key.setText("do-not-save")
    dialog._save()
    assert writes == []
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "原文件" in dialog.notice.text()
    dialog.close()


def test_controller_settings_read_failure_keeps_current_state(app, monkeypatch):
    from iris_caption import controller
    window = MainWindow()
    previous = {"embedding_model_path": "original"}
    messages = []
    def fail(*args):
        raise PersistenceError("invalid")
    monkeypatch.setattr(controller, "SettingsDialog", fail)
    monkeypatch.setattr(controller.QMessageBox, "warning", lambda *args: messages.append(args))
    instance = SimpleNamespace(window=window, settings=previous)
    controller.Controller.show_settings(instance)
    assert instance.settings is previous
    assert messages and "原文件" in messages[0][2]
    window.close()
