"""PDF 文件适配器 — 提取文字 + 逐页渲染，供多模态流水线使用。

设计：
  - 文字提取：调用 PyMuPDF 提取全部页面纯文本
  - 页面渲染：将前 N 页渲染为 base64 图片，供多模态模型视觉理解
  - 分页限制：默认最多渲染 5 页（避免 token 爆炸），可配置
  - 容错：PyMuPDF 未安装时抛出明确错误，调用方可优雅降级
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from iris.complex_input.detector import EncodedImage

logger = logging.getLogger(__name__)

# 默认最大渲染页数
_DEFAULT_MAX_RENDER_PAGES = 5
# 提取文本最大字符数（超长 PDF 截断以避免超出 LLM 上下文窗口）
_DEFAULT_MAX_TEXT_CHARS = 8000
# 页面渲染缩放因子（2.0 ≈ 150 DPI）
_DEFAULT_RENDER_SCALE = 2.0


class PdfAdapterError(RuntimeError):
    """PDF 适配器相关错误。"""


@dataclass
class PdfContent:
    """单个 PDF 文件的处理结果。"""

    path: str
    text: str                       # 提取的全部文字（已截断）
    page_images: List[EncodedImage] = field(default_factory=list)
    total_pages: int = 0
    rendered_pages: int = 0
    error: Optional[str] = None     # 处理过程中的非致命错误


class PdfAdapter:
    """PDF 文件处理器。

    用法:
        adapter = PdfAdapter()
        content = adapter.process(pdf_path, max_render_pages=5)
        # content.text       → 提取的文字
        # content.page_images → 渲染的页面图片（EncodedImage 列表）
    """

    def __init__(self):
        self._check_dependency()

    @staticmethod
    def _check_dependency() -> None:
        """检查 PyMuPDF 是否可用。"""
        try:
            import fitz  # noqa: F401
        except ImportError:
            raise PdfAdapterError(
                "PyMuPDF (fitz) 未安装，请运行: pip install PyMuPDF"
            )

    # ── 公开 API ──────────────────────────────────────────────

    def process(
        self,
        pdf_path: str | Path,
        *,
        max_render_pages: int = _DEFAULT_MAX_RENDER_PAGES,
        max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS,
        render_scale: float = _DEFAULT_RENDER_SCALE,
    ) -> PdfContent:
        """处理 PDF 文件：提取文字 + 渲染前 N 页为图片。

        Args:
            pdf_path: PDF 文件路径
            max_render_pages: 最多渲染的页数（图片）
            max_text_chars: 提取文字的最大字符数
            render_scale: 页面渲染缩放因子（1.0=72DPI, 2.0≈150DPI）

        Returns:
            PdfContent 包含文字和页面图片
        """
        import fitz

        path = Path(pdf_path).resolve()
        if not path.exists():
            raise PdfAdapterError(f"PDF 文件不存在: {path}")

        doc = fitz.open(str(path))
        total_pages = len(doc)
        errors: List[str] = []

        try:
            # ── 1. 提取全部页面文字 ──────────────────────────
            text_parts: List[str] = []
            for page_idx in range(total_pages):
                try:
                    page = doc[page_idx]
                    page_text = page.get_text()
                    if page_text and page_text.strip():
                        text_parts.append(page_text.strip())
                except Exception as exc:
                    errors.append(f"第 {page_idx + 1} 页文字提取失败: {exc}")

            full_text = "\n\n".join(text_parts)
            if len(full_text) > max_text_chars:
                full_text = full_text[:max_text_chars] + "\n\n...（文字已截断）"

            # ── 2. 渲染前 N 页为图片 ─────────────────────────
            page_images: List[EncodedImage] = []
            render_count = min(max_render_pages, total_pages)
            for page_idx in range(render_count):
                try:
                    page = doc[page_idx]
                    # 使用缩放矩阵提高渲染清晰度
                    mat = fitz.Matrix(render_scale, render_scale)
                    pix = page.get_pixmap(matrix=mat)
                    img_bytes = pix.tobytes("png")
                    b64 = base64.b64encode(img_bytes).decode("ascii")
                    data_url = f"data:image/png;base64,{b64}"
                    page_images.append(
                        EncodedImage(
                            path=f"{path}#page={page_idx + 1}",
                            mime_type="image/png",
                            data_url=data_url,
                        )
                    )
                except Exception as exc:
                    errors.append(f"第 {page_idx + 1} 页渲染失败: {exc}")

            error_msg = "; ".join(errors) if errors else None
            return PdfContent(
                path=str(path),
                text=full_text,
                page_images=page_images,
                total_pages=total_pages,
                rendered_pages=len(page_images),
                error=error_msg,
            )
        finally:
            doc.close()

    def extract_text_only(self, pdf_path: str | Path, max_chars: int = 8000) -> str:
        """仅提取 PDF 文字（不渲染图片），用于纯文本场景。

        Args:
            pdf_path: PDF 文件路径
            max_chars: 最大字符数

        Returns:
            提取的文字内容
        """
        import fitz

        path = Path(pdf_path).resolve()
        doc = fitz.open(str(path))
        try:
            parts = []
            for page_idx in range(len(doc)):
                try:
                    page_text = doc[page_idx].get_text()
                    if page_text and page_text.strip():
                        parts.append(page_text.strip())
                except Exception:
                    continue
            text = "\n\n".join(parts)
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n...（文字已截断）"
            return text
        finally:
            doc.close()
