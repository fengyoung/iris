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
