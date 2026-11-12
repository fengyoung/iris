"""Offline VAD discovery: no model downloads or changes to user preferences."""
import hashlib
import json

import pytest

from iris_caption import settings
from iris_caption import models


@pytest.fixture
def local_vad(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(settings.Path, 'home', lambda: home)
    monkeypatch.setattr(settings, 'data_dir', lambda: tmp_path / 'app')
    manifest = {'schema_version': 1, 'model_id': 'iic/test-vad', 'revision': 'test',
                'files': [{'path': name, 'size': len(name),
                           'sha256': hashlib.sha256(name.encode()).hexdigest()}
                          for name in ('config.yaml', 'am.mvn', 'model_quant.onnx')]}
    monkeypatch.setattr(models, 'builtin_fsmn_manifest', lambda: manifest)
    directory = home / '.cache/modelscope/hub/models/iic/test-vad'
    directory.mkdir(parents=True)
    for item in manifest['files']:
        (directory / item['path']).write_text(item['path'])
    return directory, manifest


def test_dialog_displays_discovered_vad_without_saving(local_vad, monkeypatch):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication, QLabel
    from iris_caption import dialogs
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(dialogs, 'read_settings', lambda: {})
    monkeypatch.setattr(dialogs, 'save_settings', lambda _: pytest.fail('must not save on discovery'))
    dialog = dialogs.SettingsDialog()
    assert dialog.vad.text() == str(local_vad[0])
    assert any('收音必需' in label.text() for label in dialog.findChildren(QLabel))
    dialog.close()
    assert app is not None


def test_cache_discovery_is_verified_and_read_only(local_vad):
    directory, _ = local_vad
    assert settings.resolve_vad_model_path() == str(directory)
    assert not settings.data_dir().exists()
    (directory / 'model_quant.onnx').write_text('x' * len('model_quant.onnx'))
    assert settings.resolve_vad_model_path() == ''


def test_explicit_selection_is_not_silently_replaced(local_vad, tmp_path):
    assert settings.resolve_vad_model_path(str(tmp_path)) == str(tmp_path)
    assert settings.resolve_vad_model_path(str(tmp_path / 'missing')) == ''


def test_active_app_install_has_priority_and_checks_integrity(local_vad):
    cache, manifest = local_vad
    root = settings.data_dir() / 'models/vad'
    active = models.ModelManager(root).import_model(cache, manifest)
    assert settings.resolve_vad_model_path() == active['directory']
    from pathlib import Path
    (Path(active['directory']) / 'am.mvn').write_text('broken')
    assert settings.resolve_vad_model_path() == str(cache)
    (root / 'active.json').write_text(json.dumps({'generation': '../unsafe'}))
    assert settings.resolve_vad_model_path() == str(cache)
