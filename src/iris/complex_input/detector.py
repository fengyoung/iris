"""输入复杂度检测器 — 识别非文本内容。"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from iris.utils.constants import IMAGE_EXTENSIONS, IMAGE_MIME_MAP as MIME_MAP

logger = logging.getLogger(__name__)

# 单张图片最大大小（20 MB），超过则跳过并警告
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class EncodedImage:
    path: str
    mime_type: str
    data_url: str


@dataclass(frozen=True)
class ComplexityResult:
    is_complex: bool
    has_images: bool
    image_paths: List[str]
    image_count: int
    encoded_images: List[EncodedImage] = field(default_factory=list)
    reason: str = ""


class InputDetector:
    def detect(self, query: str, image_paths: List[str] | None = None) -> ComplexityResult:
        if not image_paths:
            return ComplexityResult(is_complex=False, has_images=False, image_paths=[], image_count=0, reason="纯文本输入")
        resolved: List[str] = []
        encoded: List[EncodedImage] = []
        for raw in image_paths:
            p = Path(raw).expanduser().resolve()
            if not p.exists() or p.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            if p.stat().st_size > _MAX_IMAGE_BYTES:
                logger.warning("跳过超大图片 %s (%.1f MB > %.0f MB)", p.name, p.stat().st_size / (1024*1024), _MAX_IMAGE_BYTES / (1024*1024))
                continue
            resolved.append(str(p))
            mime = MIME_MAP[p.suffix.lower()]
            data_url = _encode_image(p, mime)
            encoded.append(EncodedImage(path=str(p), mime_type=mime, data_url=data_url))
        if not encoded:
            return ComplexityResult(is_complex=False, has_images=False, image_paths=resolved, image_count=0, reason="未识别到有效图片文件")
        return ComplexityResult(is_complex=True, has_images=True, image_paths=resolved, image_count=len(encoded),
                                 encoded_images=encoded, reason=f"检测到 {len(encoded)} 张图片")


def _encode_image(path: Path, mime_type: str) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{b64}"
