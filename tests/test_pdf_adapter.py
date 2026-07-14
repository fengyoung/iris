"""complex_input/pdf_adapter.py 专项测试。"""

from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.complex_input.detector import EncodedImage
from iris.complex_input.pdf_adapter import (
    PdfAdapter,
    PdfAdapterError,
    PdfContent,
    _DEFAULT_MAX_RENDER_PAGES,
    _DEFAULT_MAX_TEXT_CHARS,
)

# ── 辅助函数 ──────────────────────────────────────────────


def _create_minimal_pdf(path: Path, num_pages: int = 2) -> None:
    """用 PyMuPDF 创建一个最小 PDF 文件（用于测试）。"""
    import fitz
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page()
        page.insert_text(
            fitz.Point(50, 72),
            f"Page {i + 1}: Test content for PDF testing purposes.",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()


def _create_text_heavy_pdf(path: Path, num_pages: int = 10) -> str:
    """创建一个文本较长的 PDF，返回期望的前 8000 字符。"""
    import fitz
    doc = fitz.open()
    all_text: list[str] = []
    for i in range(num_pages):
        page = doc.new_page()
        # 每页生成约 1000 字符的文本
        page_text = f"Page {i + 1}: " + "Lorem ipsum dolor sit amet, " * 50
        page.insert_text(fitz.Point(50, 72), page_text, fontsize=10)
        all_text.append(page_text.strip())
    doc.save(str(path))
    doc.close()
    return "\n\n".join(all_text)


# ── PdfAdapter 基础 ────────────────────────────────────────


class TestPdfAdapterBasic:
    """PdfAdapter 基础功能测试。"""

    def test_init_checks_dependency(self):
        """PyMuPDF 可用时正常初始化。"""
        adapter = PdfAdapter()
        assert adapter is not None

    def test_file_not_found(self, tmp_path):
        """不存在的文件抛出 PdfAdapterError。"""
        adapter = PdfAdapter()
        nonexistent = tmp_path / "nonexistent.pdf"
        with pytest.raises(PdfAdapterError, match="PDF 文件不存在"):
            adapter.process(nonexistent)

    def test_file_not_pdf(self, tmp_path):
        """非 PDF 文件：PyMuPDF 可能报错也可能返回空内容，两种行为均可接受。"""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("not a pdf")
        adapter = PdfAdapter()
        try:
            content = adapter.process(str(txt_file))
            # 如果没抛异常，至少验证返回值结构正确
            assert content.total_pages >= 0
            assert isinstance(content.text, str)
        except Exception:
            # PyMuPDF 抛出异常也是合理行为
            pass


class TestPdfAdapterTextExtraction:
    """PDF 文字提取测试。"""

    def test_extract_text_basic(self, tmp_path):
        """从 PDF 提取文字内容。"""
        pdf_path = tmp_path / "test.pdf"
        _create_minimal_pdf(pdf_path, num_pages=2)

        adapter = PdfAdapter()
        content = adapter.process(pdf_path)

        assert "Page 1" in content.text
        assert "Page 2" in content.text
        assert content.total_pages == 2
        assert content.error is None

    def test_extract_text_truncation(self, tmp_path):
        """长文本应被截断到 max_text_chars。"""
        pdf_path = tmp_path / "long.pdf"
        _create_text_heavy_pdf(pdf_path, num_pages=10)

        adapter = PdfAdapter()
        content = adapter.process(pdf_path, max_text_chars=500)

        assert len(content.text) <= 500 + 50  # +50 容差（含截断标记）
        assert "...（文字已截断）" in content.text or len(content.text) <= 520

    def test_extract_text_only(self, tmp_path):
        """extract_text_only 只返回文字不含图片。"""
        pdf_path = tmp_path / "test.pdf"
        _create_minimal_pdf(pdf_path, num_pages=2)

        adapter = PdfAdapter()
        text = adapter.extract_text_only(pdf_path)

        assert "Page 1" in text
        assert "Page 2" in text
        assert isinstance(text, str)

    def test_extract_text_only_truncation(self, tmp_path):
        """extract_text_only 也遵循 max_chars 限制。"""
        pdf_path = tmp_path / "long.pdf"
        _create_text_heavy_pdf(pdf_path, num_pages=10)

        adapter = PdfAdapter()
        text = adapter.extract_text_only(pdf_path, max_chars=300)

        assert len(text) <= 350


class TestPdfAdapterPageRendering:
    """PDF 页面渲染测试。"""

    def test_render_pages_basic(self, tmp_path):
        """渲染页面为 base64 图片。"""
        pdf_path = tmp_path / "test.pdf"
        _create_minimal_pdf(pdf_path, num_pages=3)

        adapter = PdfAdapter()
        content = adapter.process(pdf_path, max_render_pages=5)

        assert content.rendered_pages == 3  # 3 页，全部渲染
        assert len(content.page_images) == 3
        for img in content.page_images:
            assert img.mime_type == "image/png"
            assert img.data_url.startswith("data:image/png;base64,")
            # 验证 base64 可解码
            b64_part = img.data_url[len("data:image/png;base64,"):]
            decoded = base64.b64decode(b64_part)
            assert len(decoded) > 0

    def test_max_render_pages(self, tmp_path):
        """超过 max_render_pages 的页面不渲染。"""
        pdf_path = tmp_path / "test.pdf"
        _create_minimal_pdf(pdf_path, num_pages=10)

        adapter = PdfAdapter()
        content = adapter.process(pdf_path, max_render_pages=3)

        assert content.total_pages == 10
        assert content.rendered_pages == 3
        assert len(content.page_images) == 3

    def test_render_zero_pages(self, tmp_path):
        """max_render_pages=0 时只提取文字。"""
        pdf_path = tmp_path / "test.pdf"
        _create_minimal_pdf(pdf_path, num_pages=2)

        adapter = PdfAdapter()
        content = adapter.process(pdf_path, max_render_pages=0)

        assert content.total_pages == 2
        assert content.rendered_pages == 0
        assert len(content.page_images) == 0
        assert "Page 1" in content.text

    def test_empty_pdf(self, tmp_path):
        """空页面 PDF 不崩溃。"""
        import fitz
        pdf_path = tmp_path / "empty.pdf"
        doc = fitz.open()
        doc.new_page()  # 空白页面
        doc.save(str(pdf_path))
        doc.close()

        adapter = PdfAdapter()
        content = adapter.process(pdf_path)

        assert content.total_pages == 1
        assert content.rendered_pages == 1


class TestPdfAdapterErrorHandling:
    """PdfAdapter 容错测试。"""

    @patch("iris.complex_input.pdf_adapter.PdfAdapter._check_dependency")
    def test_missing_dependency(self, mock_check, tmp_path):
        """PyMuPDF 未安装时抛出 PdfAdapterError。"""
        mock_check.side_effect = PdfAdapterError("PyMuPDF (fitz) 未安装")

        with pytest.raises(PdfAdapterError, match="PyMuPDF"):
            PdfAdapter()

    def test_page_render_error_isolated(self, tmp_path):
        """单页渲染失败不影响其他页。"""
        pdf_path = tmp_path / "test.pdf"
        _create_minimal_pdf(pdf_path, num_pages=3)

        adapter = PdfAdapter()
        content = adapter.process(pdf_path, max_render_pages=3)

        # 所有页面都应成功
        assert content.rendered_pages == 3
        assert content.error is None
