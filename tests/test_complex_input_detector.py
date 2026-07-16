"""测试复杂输入检测器 — complex_input/detector.py 纯函数和数据结构。"""

from __future__ import annotations

import pytest

from iris.complex_input.detector import (
    EncodedImage,
    ComplexityResult,
    extract_file_paths_from_text,
    _classify_file,
    _merge_detected_types,
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
