"""测试 PDF 文本提取器 — ingest/pdf_extractor.py。"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris.ingest.pdf_extractor import (
    PDFExtractor,
    PDFExtractorError,
    PDFSection,
    PDFDocument,
)


class TestPDFSection:
    def test_creation(self):
        section = PDFSection(title="介绍", level=1, content="正文内容", page_start=1, page_end=3)
        assert section.title == "介绍"
        assert section.level == 1
        assert section.page_start == 1
        assert section.page_end == 3

    def test_is_frozen(self):
        section = PDFSection(title="t", level=1, content="c", page_start=1, page_end=1)
        with pytest.raises(Exception):
            section.title = "new"


class TestPDFDocument:
    def test_defaults(self):
        doc = PDFDocument(title="测试文档")
        assert doc.title == "测试文档"
        assert doc.sections == []
        assert doc.total_pages == 0

    def test_with_sections(self):
        s = PDFSection(title="s", level=1, content="c", page_start=1, page_end=1)
        doc = PDFDocument(title="doc", sections=[s], total_pages=5)
        assert len(doc.sections) == 1
        assert doc.total_pages == 5


class TestPDFExtractor:
    def test_init_checks_dependency(self):
        """初始化时检查 PyMuPDF 依赖。"""
        try:
            import fitz  # noqa: F401
            has_fitz = True
        except ImportError:
            has_fitz = False

        if has_fitz:
            ext = PDFExtractor()
            assert ext is not None
        else:
            with pytest.raises(PDFExtractorError, match="PyMuPDF"):
                PDFExtractor()

    def test_extract_nonexistent_file(self, tmp_path):
        """不存在的文件应抛出错误。"""
        try:
            import fitz  # noqa: F401
        except ImportError:
            pytest.skip("PyMuPDF 未安装")

        ext = PDFExtractor()
        path = tmp_path / "nonexistent.pdf"
        with pytest.raises(Exception):
            ext.extract(path)

    def test_extract_text_from_simple_pdf(self, tmp_path):
        """从简单 PDF 提取文字。"""
        try:
            import fitz  # noqa: F401
        except ImportError:
            pytest.skip("PyMuPDF 未安装")

        doc = fitz.open()
        page = doc.new_page()
        # 使用 ASCII 文字（CJK 需要嵌入字体才能正确渲染）
        page.insert_text((50, 50), "Hello World Test")
        path = tmp_path / "test.pdf"
        doc.save(str(path))
        doc.close()

        ext = PDFExtractor()
        result = ext.extract(path)
        assert result.title == "test"
        assert "Hello" in result.raw_text


class TestPDFExtractorInterface:
    def test_extract_method_smoke(self, tmp_path):
        """没有 PyMuPDF 时，至少模块导入和基础类正常工作。"""
        # 这些应该在无依赖时也能工作
        section = PDFSection(title="t", level=1, content="c", page_start=1, page_end=1)
        doc = PDFDocument(title="test", sections=[section])
        assert doc.total_pages == 0
        assert len(doc.sections) == 1
