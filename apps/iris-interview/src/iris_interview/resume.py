"""Lossless text extraction and explicit, editable block alignment."""

from __future__ import annotations

from pathlib import Path
import uuid

from .exceptions import ValidationError

MAX_BYTES = 40 * 1024 * 1024


def make_blocks(text: str, page: int | None = None) -> list[dict]:
    """Keep all extracted characters; only paragraph boundaries define rows."""
    parts = text.splitlines(keepends=True)
    blocks, current = [], []
    for line in parts:
        current.append(line)
        if not line.strip():
            blocks.append({"id": uuid.uuid4().hex, "text": "".join(current), "page": page})
            current = []
    if current:
        blocks.append({"id": uuid.uuid4().hex, "text": "".join(current), "page": page})
    return blocks


def extract_resume(path: str | Path) -> dict:
    source = Path(path)
    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValidationError("简历文件不存在或超过 40 MB")
    suffix = source.suffix.lower()
    warnings = []
    pages = []
    if suffix in {".txt", ".md"}:
        pages = [source.read_text(encoding="utf-8-sig")]
    elif suffix == ".pdf":
        import fitz

        with fitz.open(source) as document:
            if document.needs_pass:
                raise ValidationError("请先解密 PDF 后再导入")
            for page in document:
                text = page.get_text("text", sort=True)
                if not text.strip():
                    warnings.append(f"第 {page.number + 1} 页没有文字层，需要 OCR 或粘贴文字")
                pages.append(text)
        warnings.append("PDF 阅读顺序及字符需对照原件校对，尤其多栏和表格")
    elif suffix == ".docx":
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        document = Document(source)
        parts = []
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                parts.append(Paragraph(child, document).text + "\n")
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                parts.append(
                    "\n".join("\t".join(cell.text for cell in row.cells) for row in table.rows) + "\n"
                )
        pages = ["".join(parts)]
        warnings.append("表格转为按行阅读；页眉、文本框和图片中的文字请核对补充")
    elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".tiff"}:
        raise ValidationError("图片简历需要 OCR；请使用文字提取入口或粘贴校对文本")
    else:
        raise ValidationError("支持 PDF、DOCX、UTF-8 TXT/Markdown；旧 DOC 请另存为 DOCX")
    blocks = []
    for number, text in enumerate(pages, 1):
        blocks.extend(make_blocks(text, number))
    return {
        "text": "".join(pages),
        "blocks": blocks,
        "warnings": warnings,
        "verified": False,
        "source_name": source.name,
    }


def validate_blocks(blocks: list[dict]) -> None:
    seen = set()
    for block in blocks:
        if (
            not isinstance(block, dict)
            or not isinstance(block.get("text"), str)
            or not block.get("id")
            or block["id"] in seen
        ):
            raise ValidationError("简历段落标识或文字无效")
        seen.add(block["id"])
