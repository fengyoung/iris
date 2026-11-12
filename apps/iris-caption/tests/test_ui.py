"""Offscreen UI contract tests; skip cleanly when Qt is unavailable."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QMessageBox, QScrollArea, QTextEdit

from iris_caption.ui import MainWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app):
    widget = MainWindow()
    widget.show()
    app.processEvents()
    yield widget
    widget.cancel_pending_close()
    widget.set_capture_active(False)
    widget.mark_saved()
    widget.close()
    widget.deleteLater()
    app.processEvents()


def segment(key="s1", **kwargs):
    return {"id": key, "start_ms": 1200, "end_ms": 3400, "raw": "原文 <b>文本</b>",
            "corrected": None, "status": "pending", "manual": False, **kwargs}


def test_upsert_single_scroll_and_layout(window, app):
    window.upsert_segment(segment())
    window.upsert_segment(segment(corrected="校正结果", status="corrected"))
    assert len(window._cards) == 1
    card = window._cards["s1"]
    assert card.raw.text() == "原文 <b>文本</b>"
    assert card.corrected.text() == "校正结果"
    assert "00:00:01" in card.meta.text()
    assert len(window.findChildren(QScrollArea)) == 1
    assert card.text_layout.direction() == QHBoxLayout.Direction.TopToBottom
    window.layout_combo.setCurrentIndex(1)
    assert card.text_layout.direction() == QHBoxLayout.Direction.LeftToRight
    window.font_slider.setValue(30)
    assert card.raw.font().pointSize() == 30
    assert window._dirty


def test_manual_edit_dialog_and_late_result(window, app):
    window.upsert_segment(segment())
    emitted = []
    window.edit_requested.connect(lambda *args: emitted.append(args))

    def accept_edit():
        dialog = window.findChild(QDialog)
        dialog.findChild(QTextEdit).setPlainText("人工确认")
        dialog.accept()

    QTimer.singleShot(0, accept_edit)
    window._edit_segment("s1")
    assert emitted == [("s1", "人工确认")]
    window.upsert_segment(segment(corrected="迟到的自动校正", status="corrected"))
    assert window._cards["s1"].corrected.text() == "人工确认"
    assert window._cards["s1"].raw.text() == "原文 <b>文本</b>"
    assert window._segments["s1"]["manual"] is True


def test_devices_and_action_signals(window):
    emitted = []
    window.microphone_changed.connect(emitted.append)
    window.set_devices([{"index": 3, "name": "内置麦克风"}, {"index": 8, "name": "USB"}])
    assert window.selected_device == 3
    assert emitted == []
    window.microphone_combo.setCurrentIndex(1)
    assert emitted == [8]
    window.set_devices([{"index": 8, "name": "USB"}, {"index": 3, "name": "内置麦克风"}])
    assert window.selected_device == 8
    window.set_devices([])
    assert window.selected_device is None
    actions = []
    for name in ("start", "pause", "stop"):
        getattr(window, name + "_requested").connect(lambda n=name: actions.append(n))
        getattr(window, name + "_button").click()
    assert actions == ["start"]
    window.set_capture_state("recording")
    window.pause_button.click()
    window.stop_button.click()
    assert actions == ["start", "pause", "stop"]
    window.set_status("正在转写")
    assert window.status_label.text() == "正在转写"


@pytest.mark.parametrize("status", ["partial", "transcribing"])
def test_projected_partial_falls_back_and_cannot_be_edited(window, app, status):
    window.upsert_segment(segment(status=status, corrected=""))
    card = window._cards["s1"]
    assert not card.edit_button.isEnabled()
    window._edit_segment("s1")
    assert window.findChild(QDialog) is None
    window.set_projector_mode(True)
    app.processEvents()
    assert card.corrected.isVisible()
    assert card.corrected.text() == "原文 <b>文本</b>"
    assert card.raw_box.isHidden()
    window.upsert_segment(segment(status="final", corrected="", raw="完整定稿原文"))
    window.set_projector_mode(False)
    assert card.edit_button.isEnabled()
    assert card.corrected.text() == "完整定稿原文"


def test_projector_single_text_fallback_and_comparison(window, app):
    window.upsert_segment(segment())
    window.set_projector_mode(True)
    app.processEvents()
    card = window._cards["s1"]
    assert window.isFullScreen()
    assert card.raw_box.isHidden()
    assert card.corrected.text() == card.raw.text()
    assert card.corrected.font().pointSize() >= 32
    window.upsert_segment(segment(corrected="更新校正"))
    assert card.corrected.text() == "更新校正"
    window.comparison_button.setChecked(True)
    assert not card.raw_box.isHidden()
    window.set_projector_mode(False)
    assert not window.isFullScreen()


def test_history_does_not_jump_and_reports_updates(window, app):
    for i in range(25):
        window.upsert_segment(segment(str(i), raw="测试字幕。" * 15))
    for _ in range(3):
        app.processEvents()
    bar = window.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(0)
    assert not window._follow
    window.upsert_segment(segment("new"))
    app.processEvents()
    assert bar.value() == 0
    assert "1 条更新" in window.follow_button.text()
    window.follow_live()
    assert bar.value() == bar.maximum()
    assert window._follow


@pytest.mark.parametrize("choice", [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard])
def test_close_cancel_and_discard(window, monkeypatch, choice):
    window.mark_dirty()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: choice)
    window.close()
    assert window.isVisible() == (choice == QMessageBox.StandardButton.Cancel)


def test_close_save_waits_for_success_and_cancel_is_safe(window, app, monkeypatch):
    window.mark_dirty()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Save)
    exports = []
    window.export_requested.connect(lambda *args: exports.append(args))
    window.close()
    app.processEvents()
    assert exports == [("markdown", "both")]
    assert window.isVisible()
    assert window._dirty
    window.cancel_pending_close()
    window.mark_saved()
    assert window.isVisible()  # unrelated later save must not close
    window.mark_dirty()
    window.close()
    app.processEvents()
    window.mark_saved()
    assert not window.isVisible()


def test_new_content_during_close_save_stays_dirty(window, app, monkeypatch):
    window.mark_dirty()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Save)
    window.close()
    app.processEvents()
    window.upsert_segment(segment())
    window.mark_saved()
    assert window.isVisible()
    assert window._dirty


def test_synchronous_export_completion_does_not_reenter_close(window, app, monkeypatch):
    window.mark_dirty()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Save)
    window.export_requested.connect(lambda *_: window.mark_saved())
    window.close()
    app.processEvents()
    assert not window.isVisible()


def test_clear_segments_resets_session_and_pending_export(window, app, monkeypatch):
    window.set_devices([{"index": 7, "name": "演示麦克风"}])
    window.upsert_segment(segment(corrected="人工内容", manual=True))
    window.set_projector_mode(True)
    window._follow = False
    window._unseen.add("s1")
    exports = []
    window.export_requested.connect(lambda *args: exports.append(args))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Save)
    window.close()
    window.clear_segments()
    app.processEvents()
    assert not window._segments and not window._cards and not window._unseen
    assert not window._dirty and window._close_generation is None
    assert window._follow and window.empty_label.isVisible()
    assert window.scroll.verticalScrollBar().value() == 0
    assert window.selected_device == 7
    assert exports == []
    window.upsert_segment(segment(corrected="新会话"))
    assert window._cards["s1"].corrected.text() == "新会话"
    assert not window._cards["s1"].raw_box.isVisible()
    assert window._dirty


@pytest.mark.parametrize("choice", [QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Discard])
def test_clean_active_capture_still_prompts(window, monkeypatch, choice):
    prompts = []
    def answer(*args):
        prompts.append(args)
        return choice
    monkeypatch.setattr(QMessageBox, "warning", answer)
    window.set_capture_active(True)
    assert not window._dirty
    window.close()
    assert len(prompts) == 1
    assert window.isVisible() == (choice == QMessageBox.StandardButton.Cancel)


def test_close_save_waits_for_capture_drain_and_snapshot(window, app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Save)
    exports = []
    window.export_requested.connect(lambda *args: exports.append(args))
    window.set_capture_active(True)
    assert not window.prepare_close_export()
    window.close()
    app.processEvents()
    assert window.closing_after_save
    assert exports == [("markdown", "both")]
    assert not window.prepare_close_export()
    window.mark_saved()
    assert window.isVisible() and window.closing_after_save
    window.upsert_segment(segment(raw="停止前最后一句"))
    window.set_capture_active(False)
    assert window.prepare_close_export()
    window.mark_saved()
    assert not window.isVisible()
    assert not window.closing_after_save


def test_changes_after_drained_snapshot_prevent_close(window, app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Save)
    window.set_capture_active(True)
    window.close()
    app.processEvents()
    window.set_capture_active(False)
    assert window.prepare_close_export()
    window.upsert_segment(segment())
    window.mark_saved()
    assert window.isVisible() and window._dirty
    assert not window.closing_after_save


def test_export_menu_contract(window):
    window.upsert_segment(segment())
    emitted = []
    window.export_requested.connect(lambda *args: emitted.append(args))
    for action in window.export_button.menu().actions():
        action.trigger()
    assert emitted == [("markdown", "both"), ("srt", "corrected"), ("srt", "raw"), ("srt", "both")]


@pytest.mark.parametrize('state,enabled,label', [
    ('idle', (True, False, False, True), '开始'),
    ('loading', (False, False, True, False), '加载中…'),
    ('recording', (False, True, True, False), '正在收音'),
    ('paused', (True, True, True, False), '继续'),
    ('stopping', (False, False, False, False), '结束中…'),
    ('error', (True, False, False, True), '重试'),
])
def test_capture_state_controls(window, state, enabled, label):
    window.set_devices([{'index': 1, 'name': 'Mic'}])
    window.set_capture_state(state)
    # A late enumeration must not re-enable microphone during capture.
    window.set_devices([{'index': 1, 'name': 'Mic'}])
    controls = (window.start_button, window.pause_button, window.stop_button, window.microphone_combo)
    assert tuple(w.isEnabled() for w in controls) == enabled
    assert window.start_button.text() == label
    assert not window.export_button.isEnabled()
    window.upsert_segment(segment())
    assert window.export_button.isEnabled()
    window.clear_segments()
    assert not window.export_button.isEnabled()


def test_pending_ack_and_close_disable_actions(window, app, monkeypatch):
    window.set_capture_state('recording', pending='pause')
    assert not window.start_button.isEnabled() and not window.pause_button.isEnabled()
    assert window.stop_button.isEnabled()
    window.upsert_segment(segment())
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: QMessageBox.StandardButton.Save)
    window.close()
    for button in (window.start_button, window.pause_button, window.stop_button, window.export_button):
        assert not button.isEnabled()
    window.cancel_pending_close()
    assert window.stop_button.isEnabled() and window.export_button.isEnabled()
