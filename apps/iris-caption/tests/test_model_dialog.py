"""Offscreen model installer tests; no downloads or publisher credentials."""
import json
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from iris_caption.model_dialog import ModelDialog
from iris_caption.models import ModelCancelled


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


class Manager:
    def __init__(self):
        self.calls = []
        self.worker_thread = None

    def download(self, manifest, *, progress, cancel):
        self.worker_thread = threading.get_ident()
        self.calls.append(manifest)
        progress({"completed": 5, "total": 5})
        return {"directory": "/installed/model"}

    def import_model(self, directory, manifest, *, progress, cancel):
        return self.download(manifest, progress=progress, cancel=cancel)


def test_download_worker_and_gui_signal(app, tmp_path):
    manager = Manager()
    dialog = ModelDialog(tmp_path, managers={"asr": manager, "embedding": manager})
    signals = []
    dialog.installed.connect(lambda kind, directory: signals.append((kind, directory, threading.get_ident())))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"test": True}))
    assert dialog.launch_download(manifest)
    assert not dialog.launch_download(manifest)
    assert not dialog.kind_combo.isEnabled()
    settle(dialog)
    assert signals == [("asr", "/installed/model", threading.get_ident())]
    assert manager.worker_thread != threading.get_ident()
    assert dialog.progress_bar.value() == 1000
    assert dialog.kind_combo.isEnabled()
    dialog.reject()


def test_local_import_requires_trust_and_embedding_kind(app, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "weights.bin").write_bytes(b"weights")
    manager = Manager()
    dialog = ModelDialog(tmp_path / "models", managers={"asr": manager, "embedding": manager})
    dialog.kind_combo.setCurrentIndex(1)
    signals = []
    dialog.installed.connect(lambda kind, directory: signals.append(kind))
    with pytest.raises(ValueError, match="trust"):
        dialog.launch_import(source)
    dialog.launch_import(source, trust_local=True)
    settle(dialog)
    assert signals == ["embedding"]
    assert manager.calls[0]["files"][0]["sha256"]
    dialog.reject()


def test_close_cancels_worker_without_destroying_dialog(app, tmp_path):
    class CancelManager:
        def download(self, manifest, *, progress, cancel):
            assert cancel.wait(3)
            raise ModelCancelled()
    dialog = ModelDialog(tmp_path, managers={"asr": CancelManager()})
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    dialog.show()
    dialog.launch_download(manifest)
    dialog.reject()
    assert dialog._busy
    assert dialog.isVisible()
    settle(dialog)
    assert not dialog.isVisible()
    assert "已取消" in dialog.status_label.text()


def test_builtin_manifest_offline_worker(app, tmp_path):
    manager = Manager()
    dialog = ModelDialog(tmp_path, managers={"asr": manager, "embedding": manager})
    assert dialog.launch_builtin()
    settle(dialog)
    manifest = manager.calls[0]
    assert len(manifest["files"]) == 7
    assert manifest["revision"] == "8f0881c891ceba7360e215b04e54cad564a68c41"
    assert next(item for item in manifest["files"] if item["path"] == "model_quant.onnx")["size"] == 871251660
    from iris_caption.audio import validate_model
    model_dir = tmp_path / "adapter-contract"
    model_dir.mkdir()
    for item in manifest["files"]:
        (model_dir / item["path"]).touch()
    _, acoustic, encoder = validate_model(model_dir)
    assert acoustic.name == "model_quant.onnx"
    assert encoder.name == "model_eb.onnx"
    assert not (model_dir / "model.pt").exists()
    result = []
    dialog.installed.connect(lambda kind, directory: result.append(kind))
    dialog.kind_combo.setCurrentIndex(1)
    assert dialog.builtin_button.isEnabled()
    assert "MiniLM" in dialog.builtin_button.text()
    assert dialog.launch_builtin()
    settle(dialog)
    embedding = manager.calls[1]
    assert embedding["revision"] == "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    assert len(embedding["files"]) == 10
    assert {item["path"] for item in embedding["files"]} >= {
        "model.safetensors", "modules.json", "1_Pooling/config.json", "tokenizer.json", "config.json"}
    assert result == ["embedding"]
    dialog.reject()


def test_builtin_vad_manifest_and_signal(app, tmp_path):
    manager = Manager()
    dialog = ModelDialog(tmp_path, managers={"asr": manager, "embedding": manager, "vad": manager})
    dialog.kind_combo.setCurrentIndex(2)
    result = []
    dialog.installed.connect(lambda kind, directory: result.append(kind))
    assert "FSMN" in dialog.builtin_button.text()
    assert dialog.launch_builtin()
    settle(dialog)
    assert result == ["vad"]
    manifest = manager.calls[0]
    assert manifest["model_id"].endswith("vad_zh-cn-16k-common-onnx")
    assert {item["path"] for item in manifest["files"]} >= {"config.yaml", "am.mvn", "model_quant.onnx"}
    dialog.reject()


def test_failure_sanitized_and_can_retry(app, tmp_path):
    dialog = ModelDialog(tmp_path)
    signals = []
    dialog.installed.connect(lambda *args: signals.append(args))
    manifest = tmp_path / "secret-name.json"
    manifest.write_text("secret malformed JSON")
    dialog.launch_download(manifest)
    settle(dialog)
    assert "失败" in dialog.status_label.text()
    assert "secret" not in dialog.status_label.text()
    assert not signals
    assert dialog.download_button.isEnabled()
    dialog.reject()


def test_cancellation_stays_busy_until_worker_returns_and_can_retry(app, tmp_path, monkeypatch):
    release = threading.Event()

    class WaitingManager(Manager):
        def download(self, manifest, *, progress, cancel):
            assert release.wait(3)
            if cancel.is_set():
                raise ModelCancelled()
            return super().download(manifest, progress=progress, cancel=cancel)

    from PySide6.QtWidgets import QFileDialog

    def unexpected_picker(*args):
        pytest.fail("Busy dialogs must not open a file picker")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", unexpected_picker)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", unexpected_picker)

    dialog = ModelDialog(tmp_path, managers={"asr": WaitingManager()})
    try:
        assert dialog.launch_builtin()
        dialog.cancel_button.click()
        dialog.choose_manifest()
        dialog.choose_directory()
        assert dialog._busy
        assert not dialog.cancel_button.isEnabled()
        assert not dialog.builtin_button.isEnabled()
        assert not dialog.download_button.isEnabled()
        assert not dialog.import_button.isEnabled()
        assert not dialog.launch_builtin()
        dialog._progress({"completed": 1, "total": 2})
        assert "正在取消" in dialog.status_label.text()
    finally:
        release.set()
        settle(dialog)
    assert dialog.builtin_button.isEnabled()
    assert dialog.download_button.isEnabled()
    assert not dialog.cancel_button.isEnabled()
    assert dialog.launch_builtin()
    settle(dialog)
    assert dialog.progress_bar.value() == 1000
    dialog.reject()
