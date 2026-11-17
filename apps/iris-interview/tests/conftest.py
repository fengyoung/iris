"""Own Qt application and widgets before extension-module interpreter teardown."""
import gc
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


def _dispose_widgets(application):
    from PySide6.QtCore import QCoreApplication, QEvent

    # Dirty close handlers can present modal questions; dispose directly instead.
    for widget in application.topLevelWidgets():
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope='session', autouse=True)
def qt_application_lifetime():
    from PySide6.QtWidgets import QApplication
    from shiboken6 import delete

    application = QApplication.instance() or QApplication([])
    yield application
    _dispose_widgets(application)
    # PySide 6.12 must destroy Qt while its Python wrappers/extensions are alive.
    delete(application)


@pytest.fixture(autouse=True)
def qt_widget_cleanup(qt_application_lifetime):
    yield
    _dispose_widgets(qt_application_lifetime)


@pytest.fixture(scope='session')
def application(qt_application_lifetime):
    return qt_application_lifetime


@pytest.fixture(scope='session')
def app(qt_application_lifetime):
    return qt_application_lifetime
