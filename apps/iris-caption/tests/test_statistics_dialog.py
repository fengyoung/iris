"""Offline statistics dialog worker/button transitions."""
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from iris_caption.statistics_dialog import StatsDialog


def settle(dialog):
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: loop.quit() if dialog._task is None else None)
    timer.start()
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()
    assert dialog._task is None


def test_refresh_duplicate_failure_retry_and_close():
    app = QApplication.instance() or QApplication([])
    release = threading.Event()
    calls = []

    class Store:
        def summary(self, current):
            calls.append(current)
            assert release.wait(3)
            if len(calls) == 1:
                raise OSError("fake failure")
            return {"totals": {"raw_chars": 0, "corrected_chars": 0, "sessions": 0}, "rows": []}

    dialog = StatsDialog(Store())
    dialog.show()
    try:
        assert not dialog.refresh_button.isEnabled()
        dialog.refresh()
        dialog.close()
        assert not dialog.isVisible()
    finally:
        release.set()
        settle(dialog)
    assert len(calls) == 1
    assert dialog.refresh_button.isEnabled()
    assert "失败" in dialog.status_label.text()
    dialog.refresh_button.click()
    settle(dialog)
    assert len(calls) == 2
    assert dialog.refresh_button.isEnabled()
    assert "暂无历史" in dialog.status_label.text()
    dialog.deleteLater()
    app.processEvents()
