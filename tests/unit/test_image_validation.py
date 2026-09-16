"""上传图片边界校验的直接测试。

本模块此前无直接测试，只有一个「超限就报错」的合并式断言——正因如此，
2000 万像素上限把用户 2008 万像素的正常照片拒掉时没有任何测试拦得住。
"""
from __future__ import annotations

import struct

import pytest

from iris.core.exceptions import IrisValueError
from iris.games import image_validation
from iris.games.image_validation import downscale_for_upload, image_dimensions, validate_image


def _png_header(width: int, height: int) -> bytes:
    """只造 PNG 头部：像素检查发生在解码之前，超大尺寸无需真实图像数据。"""
    return (b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR'
            + struct.pack('>II', width, height) + b'\x08\x06\x00\x00\x00')


def _jpeg_header(width: int, height: int) -> bytes:
    """最小 JPEG 头部：SOF0 段承载尺寸。"""
    return (b'\xff\xd8' + b'\xff\xc0' + struct.pack('>H', 11) + b'\x08'
            + struct.pack('>HH', height, width) + b'\x00' * 4)


def _gif_header(width: int, height: int) -> bytes:
    return b'GIF89a' + struct.pack('<HH', width, height) + b'\x00\x00\x00'


def _bmp_header(width: int, height: int) -> bytes:
    return b'BM' + b'\x00' * 12 + struct.pack('<I', 40) + struct.pack('<ii', width, height)


def _bmp_bytes(width: int, height: int) -> bytes:
    """完整可解码的 24 位 BMP（含像素数据），用于走通解码路径。"""
    row_size = (width * 3 + 3) & ~3
    pixels = (b'\x10\x80\x40' * width + b'\x00' * (row_size - width * 3)) * height
    header = b'BM' + struct.pack('<IHHI', 14 + 40 + len(pixels), 0, 0, 14 + 40)
    dib = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0,
                      len(pixels), 2835, 2835, 0, 0)
    return header + dib + pixels


def _webp_header(width: int, height: int) -> bytes:
    return (b'RIFF' + b'\x00' * 4 + b'WEBP' + b'VP8X' + b'\x00' * 8
            + (width - 1).to_bytes(3, 'little') + (height - 1).to_bytes(3, 'little'))


def _real_image(fmt: str = 'png', width: int = 3, height: int = 2) -> bytes:
    fitz = pytest.importorskip('fitz')
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    return pix.tobytes(fmt)


class TestDimensions:
    """从文件头识别尺寸——解码之前的唯一防线。"""

    @pytest.mark.parametrize('header, expected', [
        (_png_header(640, 480), ('png', 640, 480)),
        (_jpeg_header(640, 480), ('jpg', 640, 480)),
        (_gif_header(640, 480), ('gif', 640, 480)),
        (_bmp_header(640, 480), ('bmp', 640, 480)),
        (_webp_header(640, 480), ('webp', 640, 480)),
    ])
    def test_recognizes_each_format(self, header, expected):
        assert image_dimensions(header) == expected

    def test_bmp_negative_height_is_absolutized(self):
        """BMP 用负高度表示自上而下存储，不是真的负尺寸。"""
        assert image_dimensions(_bmp_header(640, -480)) == ('bmp', 640, 480)

    def test_unknown_container_rejected(self):
        with pytest.raises(IrisValueError, match='无法识别图片格式'):
            image_dimensions(b'\x00\x01\x02\x03' * 8)

    def test_truncated_header_rejected(self):
        """认得出是 PNG（IHDR 到位）但尺寸字段被截断——与「认不出格式」是两回事。"""
        truncated = b'\x89PNG\r\n\x1a\n' + b'\x00' * 4 + b'IHDR' + b'\x00' * 2
        with pytest.raises(IrisValueError, match='图片头不完整'):
            image_dimensions(truncated)

    def test_header_shorter_than_signature_is_unknown(self):
        """连 IHDR 都没有，属于「认不出」而非「不完整」。"""
        with pytest.raises(IrisValueError, match='无法识别图片格式'):
            image_dimensions(b'\x89PNG\r\n\x1a\n\x00\x00')


class TestPixelLimit:
    """上限只挡解码炸弹，不该卡正常照片。"""

    def test_default_limit_covers_the_reported_photo(self):
        """回归：用户上传的 5489×3659 照片（2008 万像素）曾被 2000 万上限拒绝。

        该尺寸是常见手机/相机出图，上限必须高于它，否则正常照片会被判为非法。
        """
        assert 5489 * 3659 > 20_000_000, '前提：该照片确实超过旧的 2000 万上限'
        assert 5489 * 3659 < image_validation._MAX_PIXELS

    def test_image_within_limit_accepted(self, monkeypatch):
        monkeypatch.setattr(image_validation, '_MAX_PIXELS', 6)  # 3×2 正好 6 像素
        validate_image(_real_image('png', 3, 2), '.png')

    def test_image_over_limit_rejected(self, monkeypatch):
        monkeypatch.setattr(image_validation, '_MAX_PIXELS', 5)
        with pytest.raises(IrisValueError, match='超过上限'):
            validate_image(_real_image('png', 3, 2), '.png')

    def test_oversize_message_states_actual_and_limit(self, monkeypatch):
        """报错要同时给出实际像素与上限，用户才知道差多少。"""
        monkeypatch.setattr(image_validation, '_MAX_PIXELS', 5)
        with pytest.raises(IrisValueError) as excinfo:
            validate_image(_real_image('png', 3, 2), '.png')
        message = str(excinfo.value)
        assert '图片为 3×2' in message
        assert '超过上限' in message
        assert message.count('万像素') == 2  # 实际值与上限各一次


class TestErrorMessagesAreDistinct:
    """三类失败必须分开报：合并成一句用户无法判断该换图还是该改名。"""

    def test_format_mismatch_names_both_sides(self):
        with pytest.raises(IrisValueError) as excinfo:
            validate_image(_real_image('png', 3, 2), '.jpg')
        message = str(excinfo.value)
        assert 'PNG' in message and '.jpg' in message

    def test_size_and_format_errors_differ(self):
        """同一张图，改名与超限必须得到不同措辞。"""
        data = _real_image('png', 3, 2)
        with pytest.raises(IrisValueError) as mismatch:
            validate_image(data, '.jpg')
        with pytest.raises(IrisValueError) as oversize:
            validate_image(_png_header(9000, 9000), '.png')
        assert str(mismatch.value) != str(oversize.value)

    def test_oversize_is_reported_before_decoding(self):
        """超大尺寸在解码前就被拦下——这正是头部检查存在的意义。"""
        with pytest.raises(IrisValueError, match='超过上限'):
            validate_image(_png_header(20000, 20000), '.png')

    def test_library_failure_maps_to_damaged(self, monkeypatch):
        """图片库拒收时报「图片数据损坏」，与格式/尺寸问题区分开。

        实测 fitz 对本模块会用到的格式相当宽容（截断的 PNG/JPEG 照收），
        故这里直接令其抛错来钉住映射，而非假装某段残缺字节必然触发。
        """
        def _boom(**kwargs):
            raise RuntimeError('cannot open')

        monkeypatch.setattr('fitz.open', _boom)
        with pytest.raises(IrisValueError, match='图片数据损坏'):
            validate_image(_real_image('png', 3, 2), '.png')


class TestSingleFrame:
    """多帧分支的报错不能被 except 改写。"""

    def test_multiframe_keeps_its_own_message(self, monkeypatch):
        """IrisValueError 多继承了 ValueError，若把帧数检查写在 try 内，
        会被 `except (RuntimeError, ValueError)` 捕获并改写为「图片数据损坏」——
        错误信息就此失真。本测试钉住它不被改写。
        """
        class _Doc:
            page_count = 3

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr('fitz.open', lambda **kwargs: _Doc())
        with pytest.raises(IrisValueError, match='图片包含 3 帧'):
            validate_image(_real_image('png', 3, 2), '.png')


class TestDownscale:
    """上传归一化：长边封顶，只缩不放。"""

    @staticmethod
    def _img(fmt: str, width: int, height: int) -> bytes:
        fitz = pytest.importorskip('fitz')
        return fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False).tobytes(fmt)

    def test_long_edge_capped(self):
        out, suffix = downscale_for_upload(self._img('png', 3000, 2000), '.png')
        assert image_dimensions(out) == ('png', 2048, 1366)

    def test_portrait_long_edge_capped(self):
        out, _ = downscale_for_upload(self._img('png', 1000, 4000), '.png')
        assert image_dimensions(out) == ('png', 512, 2048)

    def test_scale_uses_page_rect_not_pixel_size(self):
        """回归：fitz 按 96 DPI 折算页面尺寸，page.rect 与像素数可差 0.75 倍。

        曾按像素尺寸算缩放，导致 3000px 的图只缩到 1536px——既没到上限、
        又白白丢了画质。断言落在上限本身，才能挡住这类「缩了但没缩够」。
        """
        data = self._img('png', 3000, 2000)
        assert image_dimensions(data)[1] == 3000
        out, _ = downscale_for_upload(data, '.png')
        assert max(image_dimensions(out)[1:]) == 2048

    def test_at_limit_is_returned_untouched(self):
        """恰好等于上限不重新编码——无谓的重编码只会掉画质。"""
        data = self._img('png', 2048, 10)
        out, suffix = downscale_for_upload(data, '.png')
        assert out is data and suffix == '.png'

    def test_small_image_returned_untouched(self):
        data = self._img('jpg', 500, 506)
        out, suffix = downscale_for_upload(data, '.jpg')
        assert out is data and suffix == '.jpg'

    @pytest.mark.parametrize('suffix', ['.jpg', '.jpeg'])
    def test_jpeg_stays_jpeg(self, suffix):
        out, out_suffix = downscale_for_upload(self._img('jpg', 3000, 2000), suffix)
        assert out_suffix == suffix
        assert image_dimensions(out)[0] == 'jpg'

    def test_unencodable_formats_become_png(self):
        """fitz 只能编码 png/jpg，bmp/gif/webp 缩放时转 PNG（无损），
        扩展名必须跟着变——否则存下来的文件与后缀不符，MIME 也会错。"""
        bmp = _bmp_bytes(2100, 4)  # 长边 2100 > 2048，触发缩放
        assert image_dimensions(bmp) == ('bmp', 2100, 4)
        out, out_suffix = downscale_for_upload(bmp, '.bmp')
        assert out_suffix == '.png'
        assert image_dimensions(out) == ('png', 2048, 4)

    def test_bmp_within_limit_keeps_its_suffix(self):
        """未触发缩放时不做转换——bmp 保持 bmp。"""
        bmp = _bmp_bytes(100, 4)
        out, out_suffix = downscale_for_upload(bmp, '.bmp')
        assert out is bmp and out_suffix == '.bmp'

    def test_png_alpha_is_preserved(self):
        """PNG 透明背景不能在缩放中被压成黑底。"""
        fitz = pytest.importorskip('fitz')
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 3000, 100), False)
        src = fitz.open(stream=pix.tobytes('png'), filetype='png')[0]
        assert src.get_pixmap(alpha=True).alpha == 1  # 前提：这是张带 alpha 的 PNG
        out, _ = downscale_for_upload(pix.tobytes('png'), '.png')
        with fitz.open(stream=out, filetype='png') as doc:
            assert doc[0].get_pixmap(alpha=True).alpha == 1


class TestUploadBoundaryMessages:
    """ReplayStore 的拒绝理由同样要分开报——此前四种原因共用一句「仅允许…」。"""

    @staticmethod
    def _store(tmp_path):
        from iris.games.replay_store import ReplayStore
        return ReplayStore(tmp_path / 'data')

    def test_unsupported_extension(self, tmp_path):
        with pytest.raises(IrisValueError, match='不支持的扩展名'):
            self._store(tmp_path).save_upload(_real_image('png', 3, 2), 'x.tiff')

    def test_empty_upload(self, tmp_path):
        with pytest.raises(IrisValueError, match='上传内容为空'):
            self._store(tmp_path).save_upload(b'', 'x.png')

    def test_oversize_file(self, tmp_path):
        with pytest.raises(IrisValueError, match='超过 20 MB 上限'):
            self._store(tmp_path).save_upload(b'\x00' * (20 * 1024 * 1024 + 1), 'x.png')

    def test_valid_image_still_roundtrips(self, tmp_path):
        """拆分报错不能误伤正常路径。"""
        store = self._store(tmp_path)
        data = _real_image('png', 3, 2)
        assert store.upload_path(store.save_upload(data, 'x.png')).read_bytes() == data
