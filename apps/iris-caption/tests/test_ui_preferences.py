"""Preferences and one-step correction scope contracts, with isolated storage."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QMessageBox

from iris_caption import settings
from iris_caption.dialogs import choose_rule_scope
from iris_caption.ui import MainWindow


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", lambda: tmp_path)
    return tmp_path / "view_preferences.json"


@pytest.fixture
def window(app, storage):
    widget = MainWindow(persist_preferences=True)
    yield widget
    widget._dirty = False
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_preferences_allowlist_validation_and_merge(storage):
    settings.save_preferences({"font_size": 30, "layout": "columns", "comparison": True,
                               "microphone_name": "USB", "secret": "never", "transcript": "never",
                               "fullscreen": True, "auto_start": True})
    settings.save_preferences({"comparison": False, "font_size": True, "layout": []})
    assert json.loads(storage.read_text()) == {
        "schema_version": 1,
        "font_size": 30, "layout": "columns", "comparison": False, "microphone_name": "USB"}
    settings.save_settings({"model_path": "/model"})
    assert settings.read_settings()["model_path"] == "/model"
    assert settings.read_preferences()["font_size"] == 30


@pytest.mark.parametrize("content", ["{broken", "[]", '{"font_size": 900, "comparison": 1}',
                                      '{"font_size": 14, "microphone_name": false}'])
def test_corrupt_preferences_are_safe(app, storage, content):
    storage.write_text(content)
    widget = MainWindow(persist_preferences=True)
    assert not widget._preferences_timer.isActive()
    assert widget.font_slider.value() in (14, 21)
    assert not widget.comparison_button.isChecked()
    assert not widget._capture_active and not widget._projector
    if content in ("{broken", "[]"):
        assert not widget._persist_preferences
        widget.font_slider.setValue(40)
        widget.save_preferences()
        assert storage.read_text() == content
    widget.close()


def test_restore_and_async_device_name(app, storage):
    settings.save_preferences({"font_size": 32, "layout": "columns", "comparison": True,
                               "microphone_name": "USB"})
    widget = MainWindow(persist_preferences=True)
    assert widget.font_slider.value() == 32
    assert widget.layout_combo.currentIndex() == 1
    assert widget.comparison_button.isChecked()
    assert widget.selected_device is None
    changed = []
    widget.microphone_changed.connect(changed.append)
    widget.set_devices([{"index": 9, "name": "Built-in"}, {"index": 2, "name": "USB"}])
    assert widget.selected_device == 2
    widget.set_devices([{"index": 2, "name": "Built-in"}, {"index": 19, "name": "USB"}])
    assert widget.selected_device == 19
    widget.set_devices([{"index": 3, "name": "Available"}])
    assert widget.selected_device == 3
    widget.set_devices([])
    widget.set_devices([{"index": 20, "name": "USB"}])
    assert widget.selected_device == 20
    assert changed == []
    widget.close()


def test_debounce_and_close_flush(window, storage, monkeypatch):
    writes = []
    original = settings.save_preferences
    def save(value):
        writes.append(value)
        original(value)
    monkeypatch.setattr(settings, "save_preferences", save)
    for size in range(25, 35):
        window.font_slider.setValue(size)
    assert writes == []
    QTest.qWait(400)
    assert len(writes) == 1
    assert settings.read_preferences()["font_size"] == 34
    window.font_slider.setValue(40)
    window.close()
    assert len(writes) == 2
    assert settings.read_preferences()["font_size"] == 40
    assert not window._preferences_timer.isActive()


def test_quit_flush_and_cancelled_close(window, storage, monkeypatch):
    window.font_slider.setValue(37)
    window.flush_preferences()
    assert settings.read_preferences()["font_size"] == 37
    window.font_slider.setValue(38)
    window._dirty = True
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Cancel)
    window.close()
    assert window._preferences_timer.isActive()
    assert settings.read_preferences()["font_size"] == 37
    window._dirty = False
    window.close()
    assert settings.read_preferences()["font_size"] == 38


def test_default_window_does_not_touch_preferences(app, storage):
    widget = MainWindow()
    widget.font_slider.setValue(40)
    widget.close()
    assert not storage.exists()


def test_device_choice_and_write_failure_nonmodal(window, monkeypatch):
    window.set_devices([{"index": 1, "name": "Built-in"}, {"index": 8, "name": "USB"}])
    window.microphone_combo.setCurrentIndex(1)
    window.save_preferences()
    assert settings.read_preferences()["microphone_name"] == "USB"
    def fail(_):
        raise OSError("read-only")
    monkeypatch.setattr(settings, "save_preferences", fail)
    window.save_preferences()
    assert "暂未保存" in window.status_label.text()


@pytest.mark.parametrize("choice,expected", [("不记住", "none"), ("仅本场会议", "session"),
                                              ("本机长期", "local"), ("escape", "none"),
                                              ("close", "none")])
def test_rule_scope_three_buttons_only(app, choice, expected):
    observed = []
    def act():
        dialog = app.activeModalWidget()
        observed.append((len(dialog.buttons()), len(dialog.findChildren(QComboBox)),
                         len(dialog.findChildren(QCheckBox))))
        if choice == "escape":
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        elif choice == "close":
            dialog.close()
        else:
            next(button for button in dialog.buttons() if button.text() == choice).click()
    QTimer.singleShot(0, act)
    assert choose_rule_scope(None, "<source>", "target") == expected
    assert observed == [(3, 0, 0)]


def test_statistics_and_720_layout(window, app):
    requested = []
    window.statistics_requested.connect(lambda: requested.append(True))
    window.statistics_button.click()
    window.set_statistics(1200, 950)
    assert requested == [True]
    assert window.statistics_label.text() == "原文 1,200 字 · 校正 950 字"
    window.resize(720, 640)
    window.show()
    app.processEvents()
    assert window.width() == 720
    assert window.centralWidget().minimumSizeHint().width() <= 720
