"""Synthetic fixtures only; never read user resumes or capture displays."""
import json
from pathlib import Path
import subprocess

import fitz
import pytest

from iris_interview import ocr
from iris_interview.exceptions import ValidationError


@pytest.fixture
def helper_result():
    return {'text': '合成项目\n\nSYNTHETIC 2026', 'confidence': 0.93,
            'paragraphs': [{'text': '合成项目', 'confidence': 0.95, 'bounds': [0.1, 0.5, 0.5, 0.1]},
                           {'text': 'SYNTHETIC 2026', 'confidence': 0.91, 'bounds': [0.1, 0.3, 0.5, 0.1]}]}


def test_image_schema_and_original_preserved(tmp_path, monkeypatch, helper_result):
    image = tmp_path / 'synthetic.png'
    image.write_bytes(b'synthetic input only')
    monkeypatch.setattr(ocr, '_recognize', lambda path: helper_result)
    result = ocr.extract_with_ocr(image)
    assert image.read_bytes() == b'synthetic input only'
    assert result['source_name'] == image.name and result['verified'] is False
    assert result['requires_review'] is True and result['confidence'] == 0.93
    assert ''.join(block['text'] for block in result['blocks']) == result['text']
    assert all(set(block) == {'id', 'text', 'page'} for block in result['blocks'])
    assert all(p['page'] == 1 for p in result['paragraphs'])
    assert any('校对' in warning for warning in result['warnings'])


def test_mixed_pdf_renders_only_scan_and_cleans_temporary(tmp_path, monkeypatch, helper_result):
    source = tmp_path / 'synthetic.pdf'
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 80), 'SYNTHETIC TEXT PAGE')
        document.new_page()
        document.save(source)
    original = source.read_bytes()
    rendered = []

    def recognize(path):
        assert path.is_file()
        assert path.read_bytes().startswith(b'\x89PNG')
        rendered.append(path)
        return helper_result

    monkeypatch.setattr(ocr, '_recognize', recognize)
    result = ocr.extract_with_ocr(source)
    assert len(rendered) == 1 and not rendered[0].exists()
    assert 'SYNTHETIC TEXT PAGE' in result['text'] and '合成项目' in result['text']
    assert {block['page'] for block in result['blocks']} == {1, 2}
    assert source.read_bytes() == original


def test_low_confidence_and_empty_text_are_visible(tmp_path, monkeypatch):
    image = tmp_path / 'empty.png'
    image.write_bytes(b'synthetic')
    monkeypatch.setattr(ocr, '_recognize', lambda path: {'text': '', 'confidence': 0.0, 'paragraphs': []})
    result = ocr.extract_with_ocr(image)
    assert result['verified'] is False
    assert any('未识别' in warning for warning in result['warnings'])
    assert any('置信度较低' in warning for warning in result['warnings'])


def test_temporary_files_removed_on_failure(tmp_path, monkeypatch):
    source = tmp_path / 'synthetic.pdf'
    with fitz.open() as document:
        document.new_page()
        document.save(source)
    rendered = []

    def fail(path):
        rendered.append(path)
        raise ValidationError('synthetic failure')

    monkeypatch.setattr(ocr, '_recognize', fail)
    with pytest.raises(ValidationError, match='synthetic failure'):
        ocr.extract_with_ocr(source)
    assert rendered and not rendered[0].exists()


def test_missing_helper_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv('IRIS_INTERVIEW_OCR_HELPER', str(tmp_path / 'missing-helper'))
    with pytest.raises(ValidationError, match='尚未编译'):
        ocr._recognize(tmp_path / 'synthetic.png')


@pytest.mark.parametrize('payload', [
    {'text': 'text', 'confidence': float('nan'), 'paragraphs': []},
    {'text': 'text', 'confidence': 0.9, 'paragraphs': [{}]},
    {'text': 'text', 'confidence': 2, 'paragraphs': []},
])
def test_invalid_helper_output_rejected(tmp_path, monkeypatch, payload):
    helper = tmp_path / 'helper'
    helper.touch()
    monkeypatch.setenv('IRIS_INTERVIEW_OCR_HELPER', str(helper))
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ''))
    with pytest.raises(ValidationError, match='数据无效'):
        ocr._recognize(tmp_path / 'synthetic.png')


def test_encrypted_pdf_rejected(tmp_path):
    source = tmp_path / 'encrypted.pdf'
    with fitz.open() as document:
        document.new_page()
        document.save(source, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='synthetic', user_pw='test')
    with pytest.raises(ValidationError, match='解密'):
        ocr.extract_with_ocr(source)


def test_native_offline_self_test():
    helper = Path(__file__).parents[1] / 'native-ocr/.build/iris-ocr-helper'
    if not helper.exists():
        pytest.skip('Compile local helper with bash native-ocr/build.sh for native verification')
    result = subprocess.run([str(helper), '--self-test'], capture_output=True, text=True, timeout=60, check=True)
    data = json.loads(result.stdout)
    assert 'SYNTHETIC' in data['text'] and '项目' in data['text']
    assert data['requiresReview'] and 0 <= data['confidence'] <= 1


def test_native_scanned_pdf_end_to_end(tmp_path):
    helper = Path(__file__).parents[1] / 'native-ocr/.build/iris-ocr-helper'
    if not helper.exists():
        pytest.skip('Compile local helper for native verification')
    with fitz.open() as text_document:
        page = text_document.new_page(width=600, height=300)
        page.insert_text((50, 100), 'SYNTHETIC PROJECT 2026', fontsize=26)
        pixels = page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes('png')
    scanned = tmp_path / 'synthetic-scan.pdf'
    with fitz.open() as document:
        page = document.new_page(width=600, height=300)
        page.insert_image(page.rect, stream=pixels)
        document.save(scanned)
    original = scanned.read_bytes()
    result = ocr.extract_with_ocr(scanned)
    assert 'SYNTHETIC' in result['text'] and result['paragraphs']
    assert result['verified'] is False and 0 <= result['confidence'] <= 1
    assert scanned.read_bytes() == original
