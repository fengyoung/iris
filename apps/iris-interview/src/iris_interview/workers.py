"""Threaded jobs with explicit UI result/error events."""

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class JobSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = JobSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.function())
        except Exception as exc:
            self.signals.error.emit(str(exc) or type(exc).__name__)
        finally:
            self.signals.finished.emit()
