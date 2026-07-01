"""输入复杂度检测器 — 识别非文本内容并分类文件类型。"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

from iris.utils.constants import (
    COMPLEX_EXTENSIONS,
    DOCUMENT_EXTENSIONS,
    FILE_TYPE_DOCUMENT,
    FILE_TYPE_IMAGE,
    FILE_TYPE_PDF,
    FILE_TYPE_UNKNOWN,
    FILE_TYPE_VIDEO,
    IMAGE_EXTENSIONS,
    IMAGE_MIME_MAP as MIME_MAP,
    PDF_EXTENSIONS,
    VIDEO_EXTENSIONS,
)

logger = logging.getLogger(__name__)

# 单张图片最大大小（20 MB），超过则跳过并警告
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class EncodedImage:
    """已编码的图片数据。"""
    path: str
    mime_type: str
    data_url: str


@dataclass(frozen=True)
class ComplexityResult:
    """输入复杂度检测结果。

    - file_type: 检测到的文件类型分类
    - file_paths: 有效文件路径列表
    - encoded_images: 仅图片类文件会编码，非图片类型此列表为空
    """

    is_complex: bool
    file_type: str = FILE_TYPE_UNKNOWN
    file_paths: List[str] = field(default_factory=list)
    file_count: int = 0
    encoded_images: List[EncodedImage] = field(default_factory=list)
    reason: str = ""


def extract_file_paths_from_text(text: str) -> List[str]:
    """从文本中提取可能的文件路径。

    按空白分割 token，查找包含路径分隔符且扩展名在已知类型中的 token，
    验证文件存在后返回。用于让 query 中包含文件路径时自动触发多模态路由。

    Args:
        text: 用户查询文本

    Returns:
        存在的文件路径列表（绝对路径）
    """
    found: List[str] = []
    for token in text.split():
        token = token.strip('.,;:!?，。；：！？""''（）()[]【】「」')
        if not token:
            continue
        # 必须包含路径分隔符
        if "/" not in token and "\\" not in token:
            continue
        p = Path(token).expanduser().resolve()
        if p.exists() and p.suffix.lower() in COMPLEX_EXTENSIONS:
            found.append(str(p))
    return found


def _classify_file(ext: str) -> str:
    """根据扩展名返回文件类型。"""
    if ext in IMAGE_EXTENSIONS:
        return FILE_TYPE_IMAGE
    if ext in PDF_EXTENSIONS:
        return FILE_TYPE_PDF
    if ext in DOCUMENT_EXTENSIONS:
        return FILE_TYPE_DOCUMENT
    if ext in VIDEO_EXTENSIONS:
        return FILE_TYPE_VIDEO
    return FILE_TYPE_UNKNOWN


class InputDetector:
    """检测输入是否为复杂输入（包含非文本文件）。"""

    def detect(
        self,
        query: str,
        file_paths: Optional[List[str]] = None,
        force_type: Optional[str] = None,
    ) -> ComplexityResult:
        """检测输入复杂度。

        Args:
            query: 用户查询文本（query 中包含的文件路径会被自动提取）
            file_paths: 文件路径列表
            force_type: 强制指定所有文件类型，跳过逐文件扩展名检测。
                        用于调用方已知文件类型时。

        Returns:
            ComplexityResult
        """
        if not file_paths:
            # ── 尝试从 query 文本中提取文件路径 ──
            extracted = extract_file_paths_from_text(query)
            if extracted:
                logger.info("从 query 文本中提取到 %d 个文件路径", len(extracted))
                resolved, encoded, detected_types = self._resolve_files(extracted, force_type)
                if resolved:
                    final_type = _merge_detected_types(detected_types, encoded)
                    return ComplexityResult(
                        is_complex=True,
                        file_type=final_type,
                        file_paths=resolved,
                        file_count=len(resolved),
                        encoded_images=encoded,
                        reason=f"从 query 中检测到 {len(resolved)} 个文件（类型: {final_type}）",
                    )
            return ComplexityResult(
                is_complex=False,
                reason="纯文本输入，无附件",
            )

        resolved, encoded, detected_types = self._resolve_files(file_paths, force_type)

        if not resolved:
            return ComplexityResult(
                is_complex=False,
                reason="未识别到有效复杂输入文件",
            )

        final_type = _merge_detected_types(detected_types, encoded)
        type_msg = f"图片" if encoded else final_type
        return ComplexityResult(
            is_complex=True,
            file_type=final_type,
            file_paths=resolved,
            file_count=len(resolved),
            encoded_images=encoded,
            reason=f"检测到 {len(resolved)} 个文件（类型: {type_msg}）",
        )

    @staticmethod
    def _resolve_files(
        file_paths: List[str],
        force_type: Optional[str] = None,
    ) -> tuple:
        """解析文件路径列表，返回 (resolved, encoded, detected_types)。

        resolved: 通过检查的有效文件路径
        encoded: 已编码的图片数据
        detected_types: 检测到的文件类型集合
        """
        resolved: List[str] = []
        encoded: List[EncodedImage] = []
        detected_types: Set[str] = set()

        for raw in file_paths:
            p = Path(raw).expanduser().resolve()
            if not p.exists():
                logger.warning("文件不存在，跳过: %s", raw)
                continue

            ext = p.suffix.lower()
            if ext not in COMPLEX_EXTENSIONS:
                logger.debug("不支持的扩展名，跳过: %s", ext)
                continue

            ftype = force_type or _classify_file(ext)

            # 图片类型先做大小检查，超标则整条跳过（不进入 resolved）
            if ftype == FILE_TYPE_IMAGE:
                if p.stat().st_size > _MAX_IMAGE_BYTES:
                    logger.warning(
                        "跳过超大图片 %s (%.1f MB > %.0f MB)",
                        p.name,
                        p.stat().st_size / (1024 * 1024),
                        _MAX_IMAGE_BYTES / (1024 * 1024),
                    )
                    continue
                mime = MIME_MAP.get(ext, "application/octet-stream")
                data_url = _encode_image(p, mime)
                encoded.append(
                    EncodedImage(path=str(p), mime_type=mime, data_url=data_url)
                )
            else:
                # 非图片类型暂不入编码（留待后续扩展）
                logger.info("检测到非图片文件: %s (类型=%s)", p.name, ftype)

            resolved.append(str(p))
            detected_types.add(ftype)

        return resolved, encoded, detected_types


def _merge_detected_types(
    detected_types: Set[str], encoded: list
) -> str:
    """合并检测到的文件类型为单一类型标识。"""
    if not detected_types:
        return FILE_TYPE_UNKNOWN
    final_type = next(iter(detected_types)) if len(detected_types) == 1 else "mixed"
    return final_type


def _encode_image(path: Path, mime_type: str) -> str:
    """读取图片文件并编码为 base64 data URL。"""
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime_type};base64,{b64}"
