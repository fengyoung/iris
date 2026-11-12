"""Speaker UI contracts use temporary storage and never access keychain."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest

from iris_caption import settings


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', lambda: tmp_path)
    return tmp_path


def test_speaker_settings_additive_defaults_and_strict_types(isolated_settings):
    assert settings.read_settings()['speakers_enabled'] is False
    assert settings.read_settings()['speaker_model_path'] == ''
    settings.save_settings({'speakers_enabled': 'true', 'speaker_model_path': 42})
    value = settings.read_settings()
    assert value['schema_version'] == 1
    assert value['speakers_enabled'] is False
    assert value['speaker_model_path'] == ''
    settings.save_settings({'speakers_enabled': True, 'speaker_model_path': '/local/models'})
    settings.save_settings({'deepseek_model': 'test'})
    assert settings.read_settings()['speakers_enabled'] is True
    assert settings.read_settings()['speaker_model_path'] == '/local/models'


def test_speaker_resolver_never_saves_settings(isolated_settings, tmp_path):
    directory = tmp_path / 'install'
    directory.mkdir()
    assert settings.resolve_speaker_model_path(str(directory)) == ''
    for name in ('campplus.onnx', 'segmentation.onnx'):
        (directory / name).write_bytes(b'fixture')
    assert settings.resolve_speaker_model_path(str(directory)) == str(directory)
    assert not (tmp_path / 'settings.json').exists()


@pytest.fixture
def app():
    pytest.importorskip('PySide6')
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_speaker_controls_off_state_and_signals(app):
    from iris_caption.ui import MainWindow
    window = MainWindow()
    window.upsert_segment({'id': 's1', 'raw': 'text', 'status': 'final',
                           'speaker_state': 'pending'})
    card = window._cards['s1']
    assert card.speaker_button.isHidden()
    assert card.speaker_label.isHidden()
    assert not window.speakers_action.isVisible()
    window.set_speakers_enabled(True)
    assert not card.speaker_button.isHidden()
    assert card.speaker_label.text() == '说话人待确认'
    emitted = []
    window.speaker_assign_requested.connect(emitted.append)
    card.speaker_button.click()
    assert emitted == ['s1']
    for state, name, expected in [('unknown', '', '未知说话人'),
                                  ('confirmed', '<b>A</b>', '<b>A</b>'),
                                  ('overlap', '', '多人同时讲话 · 可能不完整')]:
        window.upsert_segment({'id': 's1', 'speaker_state': state, 'speaker_name': name})
        assert card.speaker_label.text() == expected
    window.set_speaker_status('模型不可用')
    window.set_status('正在收音')
    assert window.speaker_status_label.text() == '模型不可用'
    window.upsert_segment({'id': 's1', 'speaker_state': 'confirmed', 'speaker_id': 'A'})
    window.set_reading_groups([['previous', 's1']])
    assert '接续发言' in card.speaker_label.text()
    window.upsert_segment({'id': 's1', 'overlap_intervals': [(0, 1)]})
    assert card.speaker_label.text() == '多人同时讲话 · 可能不完整'
    window.set_speakers_enabled(False)
    assert card.speaker_button.isHidden()
    assert window.speaker_status_label.isHidden()
    app.processEvents()
    window.mark_saved()
    window.close()
    app.processEvents()


def test_management_rename_validation_and_cancel(app):
    from iris_caption.speaker_dialog import SpeakerManagementDialog
    names = {'A': 'A'}
    dialog = SpeakerManagementDialog(names)
    emitted = []
    dialog.rename_requested.connect(lambda *args: emitted.append(args))
    for invalid in ('', 'a' * 81, 'bad\tname', 'bad\u202ename'):
        dialog.fields['A'].setText(invalid)
        dialog._save()
        assert emitted == []
    dialog.fields['A'].setText(' Alice ')
    dialog._save()
    assert emitted == [('A', 'Alice')]
    assert names == {'A': 'A'}
    cancelled = SpeakerManagementDialog(names)
    cancelled.rename_requested.connect(lambda *args: emitted.append(args))
    cancelled.fields['A'].setText('Bob')
    cancelled.reject()
    assert emitted == [('A', 'Alice')]


def test_model_dialog_speaker_kind_no_auto_install(app, tmp_path):
    from iris_caption.model_dialog import ModelDialog
    dialog = ModelDialog(tmp_path)
    dialog.kind_combo.setCurrentIndex(dialog.kind_combo.findData('speaker'))
    assert '34 MB' in dialog.builtin_button.text()
    assert dialog.managers['speaker'].root == tmp_path / 'speaker'
    assert not dialog._busy
    assert not (tmp_path / 'speaker' / 'active.json').exists()
    dialog.reject()


def test_settings_speaker_path_and_checkbox_without_keychain(app, isolated_settings, monkeypatch):
    from iris_caption import dialogs
    monkeypatch.setattr(dialogs, 'resolve_vad_model_path', lambda _: '')
    monkeypatch.setattr(dialogs, 'set_secret', lambda *_: pytest.fail('keychain accessed'))
    dialog = dialogs.SettingsDialog()
    assert not dialog.speakers_enabled.isChecked()
    dialog.speakers_enabled.setChecked(True)
    dialog._save()
    assert settings.read_settings()['speakers_enabled'] is True
    assert settings.read_settings()['schema_version'] == 1
