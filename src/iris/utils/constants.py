"""Iris 全局常量 —— 避免跨模块重复定义。"""

# 常见图片文件扩展名（用于文件类型检测）
IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"})

# 包含 SVG 的扩展名（用于文档图片下载等场景）
IMAGE_EXTENSIONS_WITH_SVG: frozenset[str] = IMAGE_EXTENSIONS | {".svg"}

# MIME 类型映射
IMAGE_MIME_MAP: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# ── 文件类型分类 ───────────────────────────────────────────────────

# 文件类型标识
FILE_TYPE_IMAGE: str = "image"
FILE_TYPE_PDF: str = "pdf"
FILE_TYPE_DOCUMENT: str = "document"
FILE_TYPE_VIDEO: str = "video"
FILE_TYPE_UNKNOWN: str = "unknown"

# PDF 扩展名
PDF_EXTENSIONS: frozenset[str] = frozenset({".pdf"})

# Office 文档扩展名（doc/docx）
DOCUMENT_EXTENSIONS: frozenset[str] = frozenset({".doc", ".docx"})

# 视频扩展名
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"}
)

# 全部非文本复杂输入扩展名
COMPLEX_EXTENSIONS: frozenset[str] = (
    IMAGE_EXTENSIONS | PDF_EXTENSIONS | DOCUMENT_EXTENSIONS | VIDEO_EXTENSIONS
)
