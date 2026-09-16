"""上传图片边界：解码前从文件头检查格式和像素预算。"""
from __future__ import annotations

import struct

from iris.core.exceptions import IrisValueError


def image_dimensions(data: bytes) -> tuple[str, int, int]:
    """识别常用位图尺寸；不接受 SVG、PDF 或未知容器。"""
    try:
        if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR':
            width, height = struct.unpack('>II', data[16:24])
            return 'png', width, height
        if data[:6] in (b'GIF87a', b'GIF89a'):
            width, height = struct.unpack('<HH', data[6:10])
            return 'gif', width, height
        if data.startswith(b'BM'):
            dib_size = int.from_bytes(data[14:18], 'little')
            if dib_size == 12:
                width, height = struct.unpack('<HH', data[18:22])
            elif dib_size >= 40:
                width, height = struct.unpack('<ii', data[18:26])
            else:
                raise IrisValueError('不支持的 BMP 头')
            return 'bmp', width, abs(height)
        if data.startswith(b'\xff\xd8'):
            return _jpeg_dimensions(data)
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            kind = data[12:16]
            if kind == b'VP8X' and len(data) >= 30:
                return 'webp', 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
            if kind == b'VP8L' and len(data) >= 25 and data[20] == 0x2f:
                bits = int.from_bytes(data[21:25], 'little')
                return 'webp', (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
            if kind == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
                width, height = struct.unpack('<HH', data[26:30])
                return 'webp', width & 0x3fff, height & 0x3fff
    except (struct.error, IndexError) as exc:
        raise IrisValueError('图片头不完整') from exc
    raise IrisValueError('无法识别图片格式')


def _jpeg_dimensions(data: bytes) -> tuple[str, int, int]:
    offset = 2
    while offset < len(data):
        if data[offset] != 0xff:
            break
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker in (0xd8, 0xd9, 0x01) or 0xd0 <= marker <= 0xd7:
            continue
        length = int.from_bytes(data[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            break
        if marker in {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}:
            height, width = struct.unpack('>HH', data[offset + 3:offset + 7])
            return 'jpg', width, height
        if marker == 0xda:
            break
        offset += length
    raise IrisValueError('JPEG 缺少有效尺寸')


def validate_image(data: bytes, suffix: str) -> None:
    """检查后才交由图片库解析，限制原始像素数。"""
    import fitz

    kind, width, height = image_dimensions(data)
    expected = 'jpg' if suffix == '.jpeg' else suffix.lstrip('.')
    if kind != expected or width <= 0 or height <= 0 or width * height > 20_000_000:
        raise IrisValueError('图片格式不匹配、尺寸无效或超过 2000 万像素')
    try:
        with fitz.open(stream=data, filetype=kind) as document:
            if document.page_count != 1:
                raise IrisValueError('图片容器无效')
    except (RuntimeError, ValueError) as exc:
        raise IrisValueError('图片数据损坏') from exc
