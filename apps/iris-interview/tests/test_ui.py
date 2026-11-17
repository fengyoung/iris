"""Offscreen synthetic UI tests. Never opens devices, network or Keychain."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QLabel

from iris_interview.resume import make_blocks
from iris_interview.ui import MainWindow


@pytest.fixture(scope='module')
def application(qt_application_lifetime):
    return qt_application_lifetime


def test_alignment_theme_and_save_preserve_synthetic_resume(application, tmp_path):
    root = tmp_path / 'workspace'
    window = MainWindow(root)
    project = window.store.create_project(name='合成面试', candidate='合成称呼')
    text = '<b>合成原文</b>\n\n本人负责12%，不确定是否持续。\n'
    blocks = make_blocks(text)
    project['resume'] = {'text': text, 'blocks': blocks, 'verified': False}
    project['plan'] = {'questions': [{'id': 'q1', 'block_id': blocks[1]['id'],
                                    'text': '如何验证12%？', 'followups': ['本人贡献？'],
                                    'status': 'unasked'}]}
    window.open_project(project)
    window.show()
    application.processEvents()
    assert window.runtime is None and not window.record.isChecked()
    assert len(window.aligned.rows) == len(blocks)
    body = window.aligned.scroll.widget()
    labels = body.findChildren(QLabel)
    original = next(label for label in labels if label.text() == blocks[0]['text'].rstrip('\r\n'))
    assert original.textFormat() == Qt.PlainText
    before = window.project['resume']['text']
    old_theme = window.settings['theme']
    window.toggle_theme()
    application.processEvents()
    assert window.settings['theme'] != old_theme
    assert window.project['resume']['text'] == before
    assert window.aligned.scroll.widget() is body
    window.aligned.toggle('q1')
    application.processEvents()
    assert window.project['plan']['questions'][0]['status'] == 'unasked'
    window.notes.setPlainText('合成笔记，不含个人资料')
    assert window.save()
    pid = window.project['id']
    window.close()
    restored = MainWindow(root)
    try:
        restored.open_project(restored.store.get_project(pid))
        assert restored.project['resume']['text'] == text
        assert restored.notes.toPlainText() == '合成笔记，不含个人资料'
        assert restored.settings['theme'] != old_theme
        assert not restored.project.get('suggested_level')
        assert restored.runtime is None and not restored.record.isChecked()
    finally:
        restored.close()


def test_question_callback_remains_live_across_consecutive_autosaves(application, tmp_path):
    window = MainWindow(tmp_path / 'autosave')
    project = window.store.create_project(name='合成连续保存')
    blocks = make_blocks('合成项目：本人负责实验。')
    question = {'id': 'q-live', 'block_id': blocks[0]['id'], 'text': '如何验证？',
                'followups': [], 'status': 'unasked'}
    project['resume'] = {'text': blocks[0]['text'], 'blocks': blocks, 'verified': False}
    project['plan'] = {'questions': [question]}
    window.open_project(project)
    window.show()
    application.processEvents()
    row = window.aligned.rows[blocks[0]['id']]
    combo = next(widget for widget in row.findChildren(QComboBox)
                 if widget.findData('follow_up') >= 0)
    try:
        combo.setCurrentIndex(combo.findData('asked'))
        QTest.qWait(500)
        application.processEvents()
        assert window.store.get_project(project['id'])['plan']['questions'][0]['status'] == 'asked'
        assert window.project['plan']['questions'][0] is question
        # Reuse the original widget callback after save: it must still own the live dict.
        combo.setCurrentIndex(combo.findData('follow_up'))
        QTest.qWait(500)
        application.processEvents()
        assert window.store.get_project(project['id'])['plan']['questions'][0]['status'] == 'follow_up'
        assert window.project['plan']['questions'][0] is question
    finally:
        window.close()
