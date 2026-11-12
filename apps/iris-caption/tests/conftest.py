"""Keep one Qt application alive until all test widgets have been destroyed."""
import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _dispose_widgets(app):
    from PySide6.QtCore import QCoreApplication, QEvent

    # Do not close(): dirty-window close handlers may open modal prompts.
    for widget in app.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session", autouse=True)
def qt_application_lifetime():
    try:
        from PySide6.QtWidgets import QApplication
        from shiboken6 import delete
    except ImportError:
        yield None
        return
    application = QApplication.instance() or QApplication([])
    yield application
    _dispose_widgets(application)
    # Destroy Qt while Python and all extension modules are still alive, rather
    # than letting PySide's atexit cleanup race Python wrapper finalization.
    delete(application)


@pytest.fixture(autouse=True)
def qt_widget_cleanup(qt_application_lifetime):
    yield
    if qt_application_lifetime is not None:
        _dispose_widgets(qt_application_lifetime)


@pytest.fixture(scope="session")
def app(qt_application_lifetime):
    if qt_application_lifetime is None:
        pytest.skip("PySide6 is unavailable")
    return qt_application_lifetime
