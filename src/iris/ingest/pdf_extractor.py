"""PDF 文档文本提取器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List


class PDFExtractorError(RuntimeError):
    """PDF 提取相关错误。"""


@dataclass(frozen=True)
class PDFSection:
    title: str
    level: int
    content: str
    page_start: int
    page_end: int


@dataclass
class PDFDocument:
    title: str
    sections: List[PDFSection] = field(default_factory=list)
    total_pages: int = 0
    raw_text: str = ""


class PDFExtractor:
    def __init__(self):
        self._check_dependency()

    @staticmethod
    def _check_dependency() -> None:
        try:
            import fitz  # noqa: F401
        except ImportError:
            raise PDFExtractorError("PyMuPDF (fitz) 未安装，请运行：pip install PyMuPDF")

    def extract(self, pdf_path: Path) -> PDFDocument:
        import fitz
        doc = fitz.open(str(pdf_path))
        title = pdf_path.stem
        sections: List[PDFSection] = []
        raw_lines: List[str] = []
        current_title = title
        current_level = 0
        current_content: List[str] = []
        page_start = 0

        for page_idx in range(len(doc)):
            page = doc[page_idx]
            blocks = page.get_text("dict").get("blocks", [])
            for block in blocks:
                if block.get("type") != 0:
                    continue
                block_text = ""
                font_info = {"size": 10, "bold": False}
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "").strip()
                        if text:
                            block_text += text + " "
                            font_info["size"] = max(font_info["size"], span.get("size", 10))
                            if span.get("flags", 0) & 2**3:
                                font_info["bold"] = True
                block_text = block_text.strip()
                if not block_text:
                    continue
                raw_lines.append(block_text)
                inferred_level = self._infer_level(block_text, font_info["size"], font_info["bold"])
                if inferred_level > 0 and len(block_text) < 200:
                    if current_content:
                        sections.append(PDFSection(title=current_title, level=current_level,
                                                    content="\n".join(current_content),
                                                    page_start=page_start, page_end=page_idx + 1))
                        current_content = []
                    current_title = block_text
                    current_level = inferred_level
                    page_start = page_idx
                    current_content.append(block_text)
                else:
                    if not current_content and inferred_level == 0:
                        current_title = title
                        current_level = 0
                        page_start = page_idx + 1
                    current_content.append(block_text)

        if current_content:
            sections.append(PDFSection(title=current_title, level=current_level,
                                        content="\n".join(current_content),
                                        page_start=page_start, page_end=len(doc)))
        if not sections:
            full_text = "\n".join(raw_lines)
            sections.append(PDFSection(title=title, level=0, content=full_text, page_start=1, page_end=len(doc)))

        doc.close()
        return PDFDocument(title=title, sections=sections, total_pages=len(doc) if hasattr(doc, '__len__') else 0,
                           raw_text="\n".join(raw_lines))

    def extract_as_markdown(self, pdf_path: Path) -> str:
        doc = self.extract(pdf_path)
        lines: List[str] = [f"# {doc.title}", ""]
        for section in doc.sections:
            if section.level == 1:
                lines.append(f"## {section.title}")
            elif section.level == 2:
                lines.append(f"### {section.title}")
            elif section.level == 3:
                lines.append(f"#### {section.title}")
            lines.append("")
            lines.append(section.content)
            lines.append("")
        return "\n".join(lines)

    def _infer_level(self, text: str, font_size: float, is_bold: bool) -> int:
        if len(text) > 150:
            return 0
        import re
        if re.match(r'^[\d\s\.\,\;\:\-\(\)\[\]\/]+$', text):
            return 0
        if font_size >= 16 and is_bold:
            return 1
        if font_size >= 13 and is_bold:
            return 2
        if font_size >= 12:
            return 3
        if font_size >= 11 and is_bold and len(text) < 60:
            return 3
        return 0
