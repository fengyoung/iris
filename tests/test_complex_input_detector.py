"""测试复杂输入检测器 — complex_input/detector.py 纯函数和数据结构。"""

from __future__ import annotations

import pytest

from iris.complex_input.detector import (
    EncodedImage,
    ComplexityResult,
    InputDetector,
    extract_file_paths_from_text,
    _classify_file,
    _merge_detected_types,
    _MAX_IMAGE_BYTES,
)


class TestEncodedImage:
    def test_creation(self):
        img = EncodedImage(path="/tmp/test.png", mime_type="image/png", data_url="data:image/png;base64,xxx")
        assert img.mime_type == "image/png"
        assert "base64" in img.data_url


class TestComplexityResult:
    def test_simple_text(self):
        r = ComplexityResult(is_complex=False)
        assert r.is_complex is False
        assert r.file_type == "unknown"

    def test_image_detected(self):
        r = ComplexityResult(is_complex=True, file_type="image",
                             file_paths=["/tmp/a.png"], reason="found image")
        assert r.file_type == "image"
        assert len(r.file_paths) == 1
        assert r.reason == "found image"

    def test_defaults(self):
        r = ComplexityResult(is_complex=False)
        assert r.file_paths == []
        assert r.encoded_images == []
        assert r.file_count == 0


class TestExtractFilePathFromText:
    def test_no_path(self):
        assert extract_file_paths_from_text("hello world") == []

    def test_nonexistent_path(self):
        assert extract_file_paths_from_text("/nonexistent/img.jpg") == []

    def test_file_without_ext(self):
        assert extract_file_paths_from_text("/tmp/README") == []


class TestClassifyFile:
    def test_image(self):
        assert _classify_file(".png") == "image"
        assert _classify_file(".jpg") == "image"

    def test_pdf(self):
        assert _classify_file(".pdf") == "pdf"

    def test_document(self):
        assert _classify_file(".docx") == "document"
        assert _classify_file(".doc") == "document"

    def test_video(self):
        assert _classify_file(".mp4") == "video"

    def test_unknown(self):
        assert _classify_file(".xyz") == "unknown"
        assert _classify_file("") == "unknown"


class TestMergeDetectedTypes:
    def test_single_type(self):
        result = _merge_detected_types({"image"}, [])
        assert result == "image"

    def test_mixed_types(self):
        result = _merge_detected_types({"image", "pdf"}, [])
        assert result == "mixed"

    def test_empty(self):
        result = _merge_detected_types(set(), [])
        assert result == "unknown"


# ── InputDetector.detect() 集成测试（使用 tmp_path 创建真实文件）──


class TestInputDetectorDetect:
    def test_pure_text_not_complex(self):
        detector = InputDetector()
        result = detector.detect("这是一段纯文字查询")
        assert result.is_complex is False
        assert result.file_count == 0

    def test_nonexistent_file_not_complex(self):
        detector = InputDetector()
        result = detector.detect("查询", file_paths=["/nonexistent/path/img.png"])
        assert result.is_complex is False

    def test_png_file_is_complex_and_encoded(self, tmp_path):
        img_file = tmp_path / "test_image.png"
        # 写入最小有效 PNG（89 字节的 1×1 像素 PNG 头）
        img_file.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc"
            b"\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        detector = InputDetector()
        result = detector.detect("看下这张图", file_paths=[str(img_file)])
        assert result.is_complex is True
        assert result.file_type == "image"
        assert result.file_count == 1
        assert len(result.encoded_images) == 1
        assert result.encoded_images[0].mime_type == "image/png"
        assert "base64" in result.encoded_images[0].data_url

    def test_pdf_file_is_complex_not_encoded(self, tmp_path):
        pdf_file = tmp_path / "report.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 minimal")
        detector = InputDetector()
        result = detector.detect("分析这份报告", file_paths=[str(pdf_file)])
        assert result.is_complex is True
        assert result.file_type == "pdf"
        assert result.file_count == 1
        assert result.encoded_images == []

    def test_oversized_image_skipped(self, tmp_path):
        big_img = tmp_path / "huge.jpg"
        # 写入超过 20MB 的文件
        big_img.write_bytes(b"\xff\xd8\xff" + b"x" * (_MAX_IMAGE_BYTES + 1))
        detector = InputDetector()
        result = detector.detect("图片", file_paths=[str(big_img)])
        assert result.is_complex is False

    def test_mixed_image_and_pdf(self, tmp_path):
        img_file = tmp_path / "img.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 30)
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")
        detector = InputDetector()
        result = detector.detect("文件", file_paths=[str(img_file), str(pdf_file)])
        assert result.is_complex is True
        assert result.file_type == "mixed"
        assert result.file_count == 2
