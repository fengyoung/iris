"""复杂输入三阶段处理模块。"""
from .detector import (
    ComplexityResult,
    EncodedImage,
    InputDetector,
    extract_file_paths_from_text,
)
from .docx_adapter import DocxAdapter, DocxAdapterError, DocxContent
from .pdf_adapter import PdfAdapter, PdfAdapterError, PdfContent
from .pipeline import ComplexInputPipeline, PipelineResult
