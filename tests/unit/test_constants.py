"""iris.utils.constants 单元测试。"""

from __future__ import annotations

from iris.utils.constants import (
    IMAGE_EXTENSIONS,
    IMAGE_EXTENSIONS_WITH_SVG,
    IMAGE_MIME_MAP,
    FILE_TYPE_IMAGE,
    FILE_TYPE_PDF,
    FILE_TYPE_DOCUMENT,
    FILE_TYPE_VIDEO,
    FILE_TYPE_UNKNOWN,
    PDF_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    VIDEO_EXTENSIONS,
    COMPLEX_EXTENSIONS,
    InputType,
    TaskType,
    Complexity,
    UseCase,
)


class TestFileTypeConstants:
    """文件类型常量测试。"""

    def test_image_extensions_contains_common_formats(self):
        """IMAGE_EXTENSIONS 包含常见图片格式。"""
        assert ".png" in IMAGE_EXTENSIONS
        assert ".jpg" in IMAGE_EXTENSIONS
        assert ".jpeg" in IMAGE_EXTENSIONS
        assert ".webp" in IMAGE_EXTENSIONS

    def test_image_extensions_with_svg_includes_svg(self):
        """IMAGE_EXTENSIONS_WITH_SVG 额外包含 .svg。"""
        assert ".svg" in IMAGE_EXTENSIONS_WITH_SVG
        assert IMAGE_EXTENSIONS.issubset(IMAGE_EXTENSIONS_WITH_SVG)

    def test_mime_map_covers_all_image_extensions(self):
        """IMAGE_MIME_MAP 覆盖所有 IMAGE_EXTENSIONS。"""
        for ext in IMAGE_EXTENSIONS:
            assert ext in IMAGE_MIME_MAP, f"Missing MIME type for {ext}"

    def test_file_type_constants_are_distinct(self):
        """文件类型常量互不相同。"""
        types = {FILE_TYPE_IMAGE, FILE_TYPE_PDF, FILE_TYPE_DOCUMENT,
                 FILE_TYPE_VIDEO, FILE_TYPE_UNKNOWN}
        assert len(types) == 5

    def test_complex_extensions_is_union(self):
        """COMPLEX_EXTENSIONS 是各类扩展名的并集。"""
        union = IMAGE_EXTENSIONS | PDF_EXTENSIONS | DOCUMENT_EXTENSIONS | VIDEO_EXTENSIONS
        assert COMPLEX_EXTENSIONS == union

    def test_pdf_extensions(self):
        """PDF_EXTENSIONS 包含 .pdf。"""
        assert ".pdf" in PDF_EXTENSIONS

    def test_document_extensions(self):
        """DOCUMENT_EXTENSIONS 包含 .doc 和 .docx。"""
        assert ".doc" in DOCUMENT_EXTENSIONS
        assert ".docx" in DOCUMENT_EXTENSIONS

    def test_video_extensions_contains_common_formats(self):
        """VIDEO_EXTENSIONS 包含常见视频格式。"""
        assert ".mp4" in VIDEO_EXTENSIONS
        assert ".mov" in VIDEO_EXTENSIONS


class TestLLMRoutingConstants:
    """LLM 路由上下文常量测试。"""

    def test_input_type_values(self):
        """InputType 定义正确的字符串值。"""
        assert InputType.TEXT == "text"
        assert InputType.MULTIMODAL == "multimodal"

    def test_task_type_has_all_expected(self):
        """TaskType 包含所有预期的任务类型。"""
        expected = {"qa", "prompt_gen", "image_understanding", "wiki_generate",
                    "wiki_update", "asr_prompt_optimize", "analysis"}
        actual = {v for k, v in vars(TaskType).items() if not k.startswith("_")}
        assert expected == actual

    def test_complexity_values(self):
        """Complexity 定义正确的字符串值。"""
        assert Complexity.STANDARD == "standard"
        assert Complexity.COMPLEX == "complex"

    def test_use_case_has_all_expected(self):
        """UseCase 包含所有预期的用例类型。"""
        expected = {"qa", "retrieval_rerank", "analysis_basic",
                    "wiki_generate", "biweekly_report"}
        actual = {v for k, v in vars(UseCase).items() if not k.startswith("_")}
        assert expected == actual
