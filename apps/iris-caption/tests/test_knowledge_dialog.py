"""Offline layered knowledge editor and folder/ZIP import tests."""
import hashlib
import json
import os
import threading
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from iris_caption.knowledge import KnowledgeStore, RuleStore
from iris_caption.knowledge_dialog import KnowledgeDialog


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


def make_bundle(path):
    path.mkdir()
    hashes = {}
    for name, value in {"hotwords": ["Iris"], "replacements": {"爱瑞斯": "Iris"},
                        "rules": [], "protected_terms": []}.items():
        data = json.dumps(value).encode()
        (path / f"{name}.json").write_bytes(data)
        hashes[f"{name}.json"] = hashlib.sha256(data).hexdigest()
    (path / "manifest.json").write_text(json.dumps({"schema_version": 1,
        "package_version": "v1", "generated_at": "2026-11-05", "source": "test", "checksums": hashes}))
    return path


def test_rule_deletion_and_layer_listing(tmp_path):
    store = RuleStore(tmp_path)
    store.set_rule("word", "local")
    store.set_rule("word", "session", "session")
    rows = store.list_rules({"word": "iris"})
    assert [row["effective"] for row in rows] == [False, False, True]
    assert all(row["conflict"] for row in rows)
    assert store.delete_rule("word", "session")
    assert store.effective_rules()["word"] == "local"
    assert store.delete_rule("word", "local")
    assert not store.delete_rule("word", "local")
    with pytest.raises(ValueError):
        store.delete_rule("word", "iris")
    assert store.effective_rules({"word": "iris"})["word"] == "iris"


def test_folder_zip_import_and_readonly_iris(app, tmp_path):
    knowledge, rules = KnowledgeStore(tmp_path / "knowledge"), RuleStore(tmp_path / "rules")
    dialog = KnowledgeDialog(knowledge, rules)
    settle(dialog)
    changes = []
    dialog.changed.connect(lambda: changes.append(threading.get_ident()))
    folder = make_bundle(tmp_path / "bundle")
    dialog.import_path(folder)
    settle(dialog)
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    assert not dialog.delete_button.isEnabled()
    assert not dialog.delete_selected()
    assert knowledge.active_bundle()["replacements"] == {"爱瑞斯": "Iris"}
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for path in folder.iterdir():
            stream.write(path, path.name)
    dialog.import_path(archive)
    settle(dialog)
    assert changes == [threading.get_ident()] * 2
    dialog.reject()


def test_override_conflict_edit_delete(app, tmp_path):
    knowledge, rules = KnowledgeStore(tmp_path / "knowledge"), RuleStore(tmp_path / "rules")
    knowledge.import_bundle(make_bundle(tmp_path / "bundle"))
    dialog = KnowledgeDialog(knowledge, rules)
    settle(dialog)
    dialog.table.selectRow(0)
    dialog.target_edit.setText("人工词")
    dialog.scope_combo.setCurrentIndex(1)
    dialog.save_rule()
    settle(dialog)
    assert rules.effective_rules(knowledge.active_bundle()["replacements"])["爱瑞斯"] == "人工词"
    assert "冲突词条：1" in dialog.status_label.text()
    row = next(i for i, value in enumerate(dialog._rows) if value["scope"] == "session")
    dialog.table.selectRow(row)
    dialog.target_edit.setText("修改后的词")
    dialog.save_rule()
    settle(dialog)
    assert rules.session["爱瑞斯"] == "修改后的词"
    row = next(i for i, value in enumerate(dialog._rows) if value["scope"] == "session")
    dialog.table.selectRow(row)
    dialog.delete_selected()
    settle(dialog)
    assert not rules.session
    assert knowledge.active_bundle()["replacements"]["爱瑞斯"] == "Iris"
    dialog.reject()


def test_invalid_bundle_does_not_signal_change(app, tmp_path):
    dialog = KnowledgeDialog(KnowledgeStore(tmp_path / "knowledge"), RuleStore(tmp_path / "rules"))
    settle(dialog)
    changes = []
    dialog.changed.connect(lambda: changes.append(True))
    dialog.import_path(tmp_path / "missing-secret")
    settle(dialog)
    assert not changes
    assert "失败" in dialog.status_label.text()
    assert "missing-secret" not in dialog.status_label.text()
    dialog.reject()


def test_editor_prerequisites_and_cleared_selection(app, tmp_path):
    rules = RuleStore(tmp_path / "rules")
    rules.set_rule("source", "target", "session")
    dialog = KnowledgeDialog(KnowledgeStore(tmp_path / "knowledge"), rules)
    settle(dialog)
    assert not dialog.save_button.isEnabled()
    dialog.source_edit.setText("word")
    assert not dialog.save_button.isEnabled()
    dialog.target_edit.setText("  ")
    assert not dialog.save_button.isEnabled()
    dialog.target_edit.setText("replacement")
    assert dialog.save_button.isEnabled()
    dialog.table.selectRow(0)
    assert dialog.delete_button.isEnabled()
    dialog.table.clearSelection()
    assert not dialog.delete_button.isEnabled()
    assert not dialog.delete_selected()
    assert rules.session == {"source": "target"}
    dialog.new_button.click()
    assert not dialog.save_button.isEnabled()
    dialog.reject()


def test_busy_rule_save_close_and_failure_recovery(app, tmp_path, monkeypatch):
    rules = RuleStore(tmp_path / "rules")
    dialog = KnowledgeDialog(KnowledgeStore(tmp_path / "knowledge"), rules)
    settle(dialog)
    dialog.show()
    release = threading.Event()
    calls = []

    def fail(*args):
        calls.append(args)
        assert release.wait(3)
        raise OSError("fake failure")

    from PySide6.QtWidgets import QFileDialog

    def unexpected_picker(*args):
        pytest.fail("Busy dialogs must not open a file picker")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", unexpected_picker)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", unexpected_picker)

    monkeypatch.setattr(rules, "set_rule", fail)
    dialog.source_edit.setText("word")
    dialog.target_edit.setText("replacement")
    try:
        assert dialog.save_rule()
        assert not dialog.save_rule()
        dialog.choose_folder()
        dialog.choose_zip()
        assert not dialog.save_button.isEnabled()
        assert not dialog.close_button.isEnabled()
        dialog.reject()
        dialog.close()
        assert dialog.isVisible()
    finally:
        release.set()
        settle(dialog)
    assert len(calls) == 1
    assert dialog.save_button.isEnabled()
    assert dialog.close_button.isEnabled()
    dialog.reject()
