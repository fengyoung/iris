"""In-app original material review; editing never calls a model or changes source."""

from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView

MAX_PREVIEW_BYTES = 16 * 1024 * 1024


def _original_text(path):
    """Bounded source preview independent of edited extracted text."""
    if path.stat().st_size > MAX_PREVIEW_BYTES:
        raise ValueError("原件过大，超过本页 16 MiB 文本预览限制")
    is_docx = path.suffix.lower() == ".docx" or zipfile.is_zipfile(path)
    if not is_docx:
        return path.read_text(encoding="utf-8-sig")
    with zipfile.ZipFile(path) as package:
        info = package.getinfo("word/document.xml")
        if info.file_size > MAX_PREVIEW_BYTES:
            raise ValueError("DOCX 正文超过预览限制")
        with package.open(info) as stream:
            data = stream.read(MAX_PREVIEW_BYTES + 1)
        if len(data) > MAX_PREVIEW_BYTES or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            raise ValueError("DOCX 正文格式不支持安全预览")
    root = ET.fromstring(data)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return "\n".join("".join(p.itertext()) for p in root.findall(".//w:p", ns))


class ResumeReviewDialog(QDialog):
    """Original on left, verbatim editable extraction on right.

    source_path may be None for pasted material. On Accepted, the caller retrieves
    text() and explicitly regenerates blocks. This dialog never writes any files.
    """

    def __init__(self, source_path, text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("原件对照校对 · 仅人工修改文字")
        self.resize(1180, 820)
        self.source_path = Path(source_path) if source_path else None
        self.pdf_document = None
        self.pdf_view = None
        self.preview_error = ""
        layout = QVBoxLayout(self)
        notice = QLabel(
            "左侧核对原件，右侧修正漏字、错字和阅读顺序。保存仅返回文字，不自动润色或修改原始文件。"
        )
        notice.setWordWrap(True)
        notice.setTextFormat(Qt.PlainText)
        layout.addWidget(notice)
        self.splitter = QSplitter(Qt.Horizontal)
        original = QWidget()
        original_layout = QVBoxLayout(original)
        self.source_label = QLabel(self.source_path.name if self.source_path else "粘贴材料 · 无原始附件")
        self.source_label.setTextFormat(Qt.PlainText)
        original_layout.addWidget(self.source_label)
        self.original = self._make_preview()
        original_layout.addWidget(self.original, 1)
        self.splitter.addWidget(original)
        extracted = QWidget()
        extracted_layout = QVBoxLayout(extracted)
        extracted_layout.addWidget(QLabel("提取文字（可编辑）"))
        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        extracted_layout.addWidget(self.editor)
        self.splitter.addWidget(extracted)
        self.splitter.setSizes([590, 590])
        layout.addWidget(self.splitter, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("保存校对文字")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _error_preview(self, message):
        self.preview_error = message
        label = QLabel(message)
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        return label

    def _make_preview(self):
        path = self.source_path
        if path is None:
            return self._error_preview("本材料来自粘贴，没有原文件可对照。右侧保留当前文字，仍可人工校对。")
        try:
            if not path.is_file():
                return self._error_preview("原始附件不可用。当前提取文字仍可编辑；请检查附件。")
            suffix = path.suffix.lower()
            with path.open("rb") as stream:
                header = stream.read(1024)
            if suffix == ".pdf" or b"%PDF-" in header:
                self.pdf_document = QPdfDocument(self)
                error = self.pdf_document.load(str(path))
                if error != QPdfDocument.Error.None_:
                    return self._error_preview(f"PDF 无法预览：{error.name}。当前文字仍可编辑。")
                self.pdf_view = QPdfView(self)
                self.pdf_view.setDocument(self.pdf_document)
                self.pdf_view.setPageMode(QPdfView.PageMode.MultiPage)
                self.pdf_view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
                return self.pdf_view
            pixmap = QPixmap(str(path))
            if not pixmap.isNull() or suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
                if pixmap.isNull():
                    return self._error_preview("图片无法解码。当前提取文字仍可编辑。")
                label = QLabel()
                label.setPixmap(pixmap)
                label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                area = QScrollArea()
                area.setWidget(label)
                area.setWidgetResizable(False)
                return area
            original = QPlainTextEdit()
            original.setReadOnly(True)
            original.setPlainText(_original_text(path))
            return original
        except (OSError, UnicodeError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            return self._error_preview(f"无法预览原件：{exc}。当前提取文字仍可编辑。")

    def text(self):
        return self.editor.toPlainText()
