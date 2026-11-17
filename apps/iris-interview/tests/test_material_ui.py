"""Synthetic original/extracted resume review tests; no cloud or real private files."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtGui import QImage, QPainter, QPdfWriter
from PySide6.QtWidgets import QPlainTextEdit, QScrollArea
from iris_interview.material_ui import ResumeReviewDialog


@pytest.fixture(scope="module")
def app(qt_application_lifetime):
    return qt_application_lifetime


def test_text_review_preserves_source_and_returns_manual_edits(app, tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("原始文字\n标点、数字12%")
    dialog = ResumeReviewDialog(path, "提取错字")
    assert isinstance(dialog.original, QPlainTextEdit)
    assert dialog.original.isReadOnly()
    assert dialog.original.toPlainText() == "原始文字\n标点、数字12%"
    dialog.editor.setPlainText("人工校对原文，不润色。")
    dialog.accept()
    assert dialog.text() == "人工校对原文，不润色。"
    assert path.read_text() == "原始文字\n标点、数字12%"
    dialog.close()


def test_extensionless_managed_pdf_preview(app, tmp_path):
    path = tmp_path / ("a" * 32)
    writer = QPdfWriter(str(path))
    painter = QPainter(writer)
    painter.drawText(100, 100, "SYNTHETIC RESUME")
    painter.end()
    dialog = ResumeReviewDialog(path, "保留提取内容")
    assert dialog.pdf_view is not None
    assert dialog.pdf_document.pageCount() == 1
    assert not dialog.preview_error
    assert dialog.text() == "保留提取内容"
    dialog.close()


def test_extensionless_image_preview(app, tmp_path):
    path = tmp_path / ("b" * 32)
    image = QImage(120, 160, QImage.Format_RGB32)
    image.fill(0xFFFFFFFF)
    assert image.save(str(path), "PNG")
    dialog = ResumeReviewDialog(path, "图片提取文字")
    assert isinstance(dialog.original, QScrollArea)
    assert not dialog.preview_error
    dialog.close()


def test_missing_or_pasted_source_remains_editable(app, tmp_path):
    for source in (None, tmp_path / "missing.pdf"):
        dialog = ResumeReviewDialog(source, "当前文字")
        assert dialog.preview_error
        dialog.editor.setPlainText("修正")
        assert dialog.text() == "修正"
        dialog.reject()
        dialog.close()


def test_docx_preview_uses_original_not_extraction(app, tmp_path):
    import zipfile

    path = tmp_path / ("c" * 32)
    xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>原始DOCX</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("word/document.xml", xml)
    dialog = ResumeReviewDialog(path, "待校对")
    assert isinstance(dialog.original, QPlainTextEdit)
    assert dialog.original.toPlainText() == "原始DOCX"
    dialog.close()
