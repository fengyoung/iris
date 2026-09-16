"""上传图片边界：解码前从文件头检查格式和像素预算，并把长边归一化到上限内。"""
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


# 像素上限只为挡住「解码炸弹」（小文件谎报超大尺寸），不用于卡正常照片：
# 6400 万像素覆盖 48MP 手机与 61MP 全画幅相机，而解码炸弹动辄数亿像素。
_MAX_PIXELS = 64_000_000


def _wan(pixels: int) -> str:
    """像素数转「万像素」，便于与相机规格对照。"""
    return f'{pixels / 10_000:.0f} 万像素'


def validate_image(data: bytes, suffix: str) -> None:
    """检查后才交由图片库解析，限制原始像素数。

    三类失败分别报错：合并成一句会让用户无法判断该换图还是该改名。
    """
    import fitz

    kind, width, height = image_dimensions(data)
    expected = 'jpg' if suffix == '.jpeg' else suffix.lstrip('.')
    if kind != expected:
        raise IrisValueError(f'图片实际为 {kind.upper()}，与文件扩展名 {suffix} 不一致')
    if width <= 0 or height <= 0:
        raise IrisValueError(f'图片尺寸无效：{width}×{height}')
    if width * height > _MAX_PIXELS:
        raise IrisValueError(
            f'图片为 {width}×{height}（{_wan(width * height)}），超过上限 {_wan(_MAX_PIXELS)}'
        )
    try:
        with fitz.open(stream=data, filetype=kind) as document:
            page_count = document.page_count
    except (RuntimeError, ValueError) as exc:
        raise IrisValueError('图片数据损坏') from exc
    # 必须在 except 之外：IrisValueError 多继承了 ValueError，写在 try 内会被
    # 上面的 except 捕获并改写成「图片数据损坏」，使本分支永不可达。
    if page_count != 1:
        raise IrisValueError(f'图片包含 {page_count} 帧，仅支持单帧图片')


# 长边上限。视觉模型内部本就会把图缩到 1568～2048px 再计费——实测 qwen3.8-max-zz
# 在长边 2048px 触顶（2048 与 3072 同为 2538 token），再大不增加信息量。
# 归一化同时把上行流量压到约 1/6：20MP 图 base64 单次 3.76MB，48 次调用即 180MB/局。
_MAX_LONG_EDGE = 2048


def downscale_for_upload(data: bytes, suffix: str) -> tuple[bytes, str]:
    """把长边超限的图缩到上限内，返回（数据, 扩展名）。

    只缩不放：长边未超限的图原样返回、不重新编码，避免无谓的画质损失。
    fitz 只能编码 png/jpg，故 bmp/gif/webp 在需要缩放时转存为 PNG（无损）。
    """
    kind, width, height = image_dimensions(data)
    if max(width, height) <= _MAX_LONG_EDGE:
        return data, suffix
    import fitz

    out_suffix = suffix if suffix in ('.jpg', '.jpeg') else '.png'
    out_kind = 'jpg' if out_suffix in ('.jpg', '.jpeg') else 'png'
    with fitz.open(stream=data, filetype=kind) as document:
        page = document[0]
        # 用 page.rect 而不是像素尺寸算缩放：fitz 按 96 DPI 折算页面尺寸，
        # 二者比值随图片声明的 DPI 变化（常见 0.75 或 1.0），用像素算会缩错。
        long_edge = max(page.rect.width, page.rect.height)
        if long_edge <= 0:
            return data, suffix
        scale = _MAX_LONG_EDGE / long_edge
        # JPEG 无 alpha 通道，传 alpha=True 会直接编码失败；PNG 则要保住透明背景
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=(out_kind == 'png'))
        return pixmap.tobytes(out_kind, jpg_quality=90), out_suffix
