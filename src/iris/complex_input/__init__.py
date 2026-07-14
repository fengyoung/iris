"""复杂输入三阶段处理模块。"""
from iris.complex_input.detector import (
    ComplexityResult,
    EncodedImage,
    InputDetector,
    extract_file_paths_from_text,
)
from iris.complex_input.docx_adapter import DocxAdapter, DocxAdapterError, DocxContent
from iris.complex_input.pdf_adapter import PdfAdapter, PdfAdapterError, PdfContent
from iris.complex_input.pipeline import ComplexInputPipeline, PipelineResult
