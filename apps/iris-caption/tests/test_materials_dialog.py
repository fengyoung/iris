"""Offscreen material dialog tests with no network or real credentials."""
import os
import threading
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from iris_caption import settings
from iris_caption.materials import MaterialIndex
from iris_caption.materials_dialog import MaterialsDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def settle(dialog):
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: loop.quit() if not dialog._busy else None)
    timer.start()
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    timer.stop()
    assert not dialog._busy


@pytest.fixture
def dialog(app, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "read_settings", lambda: {"feishu_app_id": "test-app"})
    instance = MaterialsDialog(MaterialIndex(), tmp_path)
    instance.show()
    settle(instance)
    yield instance
    instance.reject()
    if instance._busy:
        settle(instance)
    instance.deleteLater()
    app.processEvents()


def test_background_and_temporary_import(dialog, tmp_path):
    changes = []
    dialog.changed.connect(lambda: changes.append(threading.get_ident()))
    dialog.set_background("周会", "张三、李四", "Iris")
    assert dialog.background == "主题：周会\n参会人：张三、李四\n项目：Iris"
    dialog.name_edit.setText("会前参考")
    dialog.text_edit.setPlainText("项目预算和发布计划")
    dialog.add_pasted_text()
    settle(dialog)
    assert len(dialog.index.list_materials()) == 1
    assert dialog.retained_ids == set()
    assert not list(tmp_path.iterdir())
    assert all(thread == threading.get_ident() for thread in changes)
    assert "关键词检索可用" in dialog.status_label.text()
    assert dialog.app_id_edit.text() == "test-app"


def test_retention_toggle_remove_and_reopen(dialog, tmp_path):
    dialog.retain_checkbox.setChecked(True)
    dialog.text_edit.setPlainText("需要长期保留的术语")
    dialog.add_pasted_text()
    settle(dialog)
    ids = dialog.retained_ids
    assert len(ids) == 1
    reloaded = MaterialIndex()
    assert set(reloaded.load_retained(tmp_path)) == ids
    reopened = MaterialsDialog(reloaded, tmp_path, retained_ids=ids)
    settle(reopened)
    assert "长期保留" in reopened.material_list.item(0).text()
    reopened.reject()
    dialog.material_list.setCurrentRow(0)
    dialog.toggle_selected_retention()
    settle(dialog)
    assert dialog.retained_ids == set()
    assert MaterialIndex().load_retained(tmp_path) == []
    dialog.remove_selected()
    # The refreshed list has no selected item; select explicitly.
    dialog.material_list.setCurrentRow(0)
    dialog.remove_selected()
    settle(dialog)
    assert dialog.index.list_materials() == []


def test_file_read_worker_and_safe_close(dialog, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    worker_threads = []

    def add_file(path):
        worker_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return dialog.index.add_text("文件", "内容")

    monkeypatch.setattr(dialog.index, "add_file", add_file)
    dialog.add_file("example.txt")
    assert entered.wait(1)
    assert dialog._busy
    dialog.reject()
    assert dialog.isVisible()
    dialog.close()
    assert dialog.isVisible()
    release.set()
    settle(dialog)
    assert worker_threads[0] != threading.get_ident()
    assert dialog.material_list.count() == 1


def test_failure_does_not_display_secret(dialog, monkeypatch):
    def fail(path):
        raise RuntimeError("token=SUPER_SECRET source=PRIVATE_TEXT")

    monkeypatch.setattr(dialog.index, "add_file", fail)
    dialog.add_file("bad.pdf")
    settle(dialog)
    assert "SUPER_SECRET" not in dialog.status_label.text()
    assert "PRIVATE_TEXT" not in dialog.status_label.text()
    assert dialog.controls.isEnabled()


def test_retention_failure_rolls_back_import(dialog, monkeypatch):
    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(dialog.index, "save_retained", fail)
    dialog.retain_checkbox.setChecked(True)
    dialog.text_edit.setPlainText("不应在失败后静默加入的内容")
    dialog.add_pasted_text()
    settle(dialog)
    assert dialog.index.list_materials() == []
    assert not dialog.retained_ids


def test_feishu_flow_mocked(dialog, monkeypatch):
    import iris_caption.feishu as feishu
    from PySide6.QtGui import QDesktopServices

    secrets = {}
    saved = []
    calls = []

    class FakeClient:
        def __init__(self, app_id, secret):
            calls.append(("init", app_id, secret))

        def authorization_url(self, redirect):
            calls.append(("authorize", redirect))
            return "https://example.com/auth"

        def exchange_redirect(self, callback):
            calls.append(("exchange", callback))

        def fetch_document(self, url):
            return SimpleNamespace(title="飞书参考", text="项目正文", url=url, token="t")

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(feishu, "FeishuClient", FakeClient)
    monkeypatch.setattr(settings, "set_secret", lambda k, v: secrets.update({k: v}))
    monkeypatch.setattr(settings, "get_secret", lambda k: secrets.get(k, ""))
    monkeypatch.setattr(settings, "save_settings", saved.append)
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: True)
    dialog.secret_edit.setText("personal-secret")
    dialog.authorize_feishu()
    settle(dialog)
    assert dialog.secret_edit.text() == ""
    assert secrets == {"feishu_app_secret": "personal-secret"}
    assert saved == [{"feishu_app_id": "test-app"}]
    dialog.callback_edit.setText("http://127.0.0.1:8766/callback?code=c&state=s")
    dialog.exchange_callback()
    settle(dialog)
    assert dialog.callback_edit.text() == ""
    dialog.document_edit.setText("https://example.feishu.cn/docx/test")
    dialog.import_feishu()
    settle(dialog)
    entry = dialog.index.list_materials()[0]
    assert entry["source_url"] == "https://example.feishu.cn/docx/test"
    assert dialog.retained_ids == set()
    dialog.reject()
    settle(dialog)
    assert ("close",) in calls
    assert not dialog.isVisible()


def test_action_prerequisites_and_selection_clear(dialog):
    assert not dialog.add_text_button.isEnabled()
    assert not dialog.remove_button.isEnabled()
    assert not dialog.retention_button.isEnabled()
    assert not dialog.refresh_button.isEnabled()
    assert not dialog.exchange_button.isEnabled()
    assert not dialog.import_feishu_button.isEnabled()
    dialog.text_edit.setPlainText("  ")
    assert not dialog.add_text_button.isEnabled()
    dialog.text_edit.setPlainText("reference")
    assert dialog.add_text_button.isEnabled()
    dialog.add_text_button.click()
    assert not dialog.add_text_button.isEnabled()
    settle(dialog)
    dialog.material_list.setCurrentRow(0)
    assert dialog.remove_button.isEnabled()
    assert dialog.retention_button.isEnabled()
    assert not dialog.refresh_button.isEnabled()
    dialog.material_list.clearSelection()
    assert not dialog.remove_button.isEnabled()
    dialog.remove_selected()
    assert not dialog._busy
    assert len(dialog.index.list_materials()) == 1
    dialog.index.add_text("remote", "text", source="https://example.feishu.cn/docx/test")
    dialog._display(dialog._snapshot())
    dialog.material_list.setCurrentRow(1)
    assert dialog.refresh_button.isEnabled()
    dialog.app_id_edit.clear()
    assert not dialog.refresh_button.isEnabled()
    assert not dialog.authorize_button.isEnabled()
    dialog.document_edit.setText("https://example.feishu.cn/docx/test")
    assert not dialog.import_feishu_button.isEnabled()
    dialog.app_id_edit.setText("app")
    assert dialog.import_feishu_button.isEnabled()


def test_busy_authorization_does_not_discard_client_or_inputs(dialog, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    def unexpected_picker(*args):
        pytest.fail("Busy dialogs must not open a file picker")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", unexpected_picker)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", unexpected_picker)

    client = SimpleNamespace(close=lambda: None)
    dialog._client = client
    dialog.secret_edit.setText("fake-secret")
    dialog.callback_edit.setText("fake-callback")
    release = threading.Event()
    try:
        assert dialog._submit(lambda: release.wait(3), lambda _: None, "working")
        dialog.authorize_feishu()
        dialog.choose_file()
        dialog.exchange_callback()
        assert dialog._client is client
        assert dialog.secret_edit.text() == "fake-secret"
        assert dialog.callback_edit.text() == "fake-callback"
        assert not dialog._submit(lambda: None, lambda _: None, "duplicate")
        assert not dialog.exchange_button.isEnabled()
    finally:
        release.set()
        settle(dialog)
    assert dialog.exchange_button.isEnabled()
