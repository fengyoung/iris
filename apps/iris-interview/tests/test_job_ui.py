"""Synthetic human-readable job form tests."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from iris_interview.exceptions import ValidationError
from iris_interview.job_ui import CatalogDraftDialog, JobDialog


@pytest.fixture(scope='module')
def application(qt_application_lifetime):
    return qt_application_lifetime


def test_job_form_has_explicit_unselected_tier_and_preserves_metadata(application):
    job = {'id': 'synthetic', 'name': '合成岗位', 'jd': '逐字原文\n12%。', 'version': 2,
           'source_text': '保留来源', 'dimensions': [{'name': '系统设计', 'description': '解释取舍', 'weight': 30}]}
    dialog = JobDialog(job)
    assert dialog.tier.currentData() == ''
    assert dialog.tier.currentText() == '未选择'
    result = dialog.value()
    assert result['jd'] == job['jd'] and result['source_text'] == '保留来源'
    assert result['id'] == 'synthetic' and result['version'] == 2
    assert result['dimensions'][0]['weight'] == 30
    assert result['reviewed'] is False
    dialog.name.setText('人工修改岗位')
    dialog.management.setChecked(True)
    dialog.reviewed.setChecked(True)
    dialog.requirements.setPlainText('要求一\n要求二')
    result = dialog.value()
    assert result['requirements'] == ['要求一', '要求二']
    assert result['management_required'] and result['reviewed']
    assert job['name'] == '合成岗位'
    dialog.close()


def test_empty_name_validation_visible(application):
    dialog = JobDialog({})
    with pytest.raises(ValidationError):
        dialog.value()
    dialog.accept()
    assert '名称' in dialog.error.text()
    assert dialog.result() != QDialog.Accepted
    dialog.close()


def test_catalog_selects_only_checked_jobs_without_auto_review(application):
    jobs = [{'name': '岗位一', 'jd': '合成一', 'reviewed': False},
            {'name': '岗位二', 'jd': '合成二', 'reviewed': False}]
    dialog = CatalogDraftDialog(jobs)
    assert dialog.preview.toPlainText() == '岗位一\n\n合成一'
    dialog.list.item(1).setCheckState(Qt.Unchecked)
    result = dialog.value()
    assert len(result) == 1 and result[0]['name'] == '岗位一'
    assert result[0]['reviewed'] is False
    result[0]['name'] = '外部修改'
    assert dialog.jobs[0]['name'] == '岗位一'
    dialog.list.item(0).setCheckState(Qt.Unchecked)
    dialog.accept()
    assert '至少' in dialog.error.text()
    dialog.close()
