"""Synthetic resume fidelity tests; no real candidate fixtures."""
import fitz
import pytest
from docx import Document

from iris_interview.exceptions import ValidationError
from iris_interview.resume import extract_resume, make_blocks, validate_blocks


@pytest.mark.parametrize('text', ['', '合成项目\n\n结果 12%。\n',
                                 '<b>原文不是HTML</b>\n\n  保留缩进、标点\t与数字0.12'])
def test_blocks_preserve_every_character(text):
    blocks = make_blocks(text, 2)
    assert ''.join(block['text'] for block in blocks) == text
    assert all(block['page'] == 2 for block in blocks)
    validate_blocks(blocks)


def test_utf8_resume_preserves_original_and_unverified(tmp_path):
    path = tmp_path / 'synthetic.txt'
    text = '合成简历\n\n负责项目；结果12%。\n'
    path.write_text(text, encoding='utf-8')
    original = path.read_bytes()
    value = extract_resume(path)
    assert value['text'] == text
    assert ''.join(block['text'] for block in value['blocks']) == text
    assert value['verified'] is False and value['source_name'] == path.name
    assert path.read_bytes() == original


def test_pdf_preserves_extracted_text_and_page_numbers(tmp_path):
    path = tmp_path / 'synthetic.pdf'
    with fitz.open() as document:
        document.new_page().insert_text((60, 80), 'SYNTHETIC PAGE ONE 12%')
        document.new_page().insert_text((60, 80), 'SYNTHETIC PAGE TWO')
        document.save(path)
    original = path.read_bytes()
    with fitz.open(path) as document:
        expected = ''.join(page.get_text('text', sort=True) for page in document)
    value = extract_resume(path)
    assert value['text'] == expected
    assert {block['page'] for block in value['blocks']} == {1, 2}
    assert any('校对' in warning for warning in value['warnings'])
    assert path.read_bytes() == original


def test_docx_paragraph_table_document_order(tmp_path):
    path = tmp_path / 'synthetic.docx'
    document = Document()
    document.add_paragraph('项目开始')
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = '原文12%'
    table.cell(0, 1).text = '本人负责'
    document.add_paragraph('项目结束')
    document.save(path)
    value = extract_resume(path)
    assert value['text'] == '项目开始\n原文12%\t本人负责\n项目结束\n'
    assert value['verified'] is False


def test_duplicate_block_ids_rejected():
    with pytest.raises(ValidationError):
        validate_blocks([{'id': 'same', 'text': 'a'}, {'id': 'same', 'text': 'b'}])
