"""feishu/doc_convert.py 单元测试。"""

from __future__ import annotations



class TestGuessImageExt:
    """_guess_image_ext: 从 URL 推断图片扩展名。"""

    def test_png(self):
        from iris.feishu.doc_convert import _guess_image_ext
        assert _guess_image_ext("https://example.com/img.png") == ".png"

    def test_jpg(self):
        from iris.feishu.doc_convert import _guess_image_ext
        assert _guess_image_ext("https://example.com/photo.jpg") == ".jpg"

    def test_unknown_defaults_to_png(self):
        from iris.feishu.doc_convert import _guess_image_ext
        assert _guess_image_ext("https://example.com/img?token=abc") == ".png"

    def test_uppercase_ext(self):
        from iris.feishu.doc_convert import _guess_image_ext
        result = _guess_image_ext("https://example.com/img.PNG")
        # .PNG lowercase = .png, may or may not be in allowed set
        assert result in (".png", ".PNG", ".png")


class TestRemoteImageSafety:
    def test_only_https_public_hosts_allowed(self, monkeypatch):
        from iris.feishu.doc_convert import _validate_remote_image_url

        monkeypatch.setattr(
            "iris.feishu.doc_convert.socket.getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
        )
        _validate_remote_image_url("https://example.com/image.png")

    def test_rejects_http_and_private_address(self, monkeypatch):
        import pytest
        from urllib.error import URLError
        from iris.feishu.doc_convert import _validate_remote_image_url

        with pytest.raises(URLError):
            _validate_remote_image_url("http://example.com/image.png")
        monkeypatch.setattr(
            "iris.feishu.doc_convert.socket.getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
        )
        with pytest.raises(URLError):
            _validate_remote_image_url("https://example.com/image.png")

    def test_download_uses_limited_atomic_writer(self, config_bundle, tmp_path, monkeypatch):
        from unittest.mock import MagicMock
        from iris.feishu.doc_convert import FeishuDocConverter

        converter = FeishuDocConverter.__new__(FeishuDocConverter)
        converter._bundle = config_bundle
        target = config_bundle.root / "data" / "download.png"
        response = MagicMock(status=200)
        response.getheader.side_effect = lambda name: {
            "Content-Type": "image/png", "Content-Length": "4",
        }.get(name)
        response.read.return_value = b"\x89PNG"
        conn = MagicMock()
        conn.getresponse.return_value = response
        monkeypatch.setattr("iris.feishu.doc_convert._PinnedHTTPSConnection", lambda *args, **kwargs: conn)
        monkeypatch.setattr(
            "iris.feishu.doc_convert.socket.getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
        )
        converter._download_image_to("https://example.com/image.png", str(target))
        assert target.read_bytes() == b"\x89PNG"
        conn.request.assert_called_once_with(
            "GET", "/image.png", headers={"Host": "example.com", "User-Agent": "Iris/3.2"}
        )
        conn.close.assert_called_once()

    def test_download_rejects_missing_content_type(self, config_bundle, monkeypatch):
        import pytest
        from unittest.mock import MagicMock
        from iris.feishu.doc_convert import FeishuDocConverter

        converter = FeishuDocConverter.__new__(FeishuDocConverter)
        converter._bundle = config_bundle
        response = MagicMock(status=200)
        response.getheader.return_value = None
        conn = MagicMock()
        conn.getresponse.return_value = response
        monkeypatch.setattr("iris.feishu.doc_convert._PinnedHTTPSConnection", lambda *args, **kwargs: conn)
        monkeypatch.setattr(
            "iris.feishu.doc_convert.socket.getaddrinfo",
            lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
        )
        with pytest.raises(OSError, match="不是图片"):
            converter._download_image_to("https://example.com/image.png", str(config_bundle.root / "data" / "x.png"))
        conn.close.assert_called_once()


class TestInsertAfterTitle:
    """_insert_after_title: 在标题后插入元信息块。"""

    def test_title_at_start(self):
        from iris.feishu.doc_convert import FeishuDocConverter
        body = "# 测试文档\n\n正文内容"
        result = FeishuDocConverter._insert_after_title(body, "## 元信息\n\n")
        assert result.index("## 元信息") < result.index("正文内容")
        assert "# 测试文档" in result

    def test_no_title_prepends(self):
        from iris.feishu.doc_convert import FeishuDocConverter
        body = "没有标题的内容"
        result = FeishuDocConverter._insert_after_title(body, "## 元信息\n\n")
        assert result.startswith("## 元信息")

    def test_title_with_blank_lines(self):
        from iris.feishu.doc_convert import FeishuDocConverter
        body = "# 标题\n\n\n正文"
        result = FeishuDocConverter._insert_after_title(body, "INSERT\n")
        # INSERT 应在 "正文" 前
        assert result.index("INSERT") < result.index("正文")


class TestClassify:
    """_classify: 基于关键词的路由评分。"""

    def _converter(self, config_bundle, tmp_path):
        with __import__("unittest.mock", fromlist=["patch"]).patch("iris.feishu.doc_convert.FeishuClient"):
            from iris.feishu.doc_convert import FeishuDocConverter
            c = FeishuDocConverter.__new__(FeishuDocConverter)
            c._bundle = config_bundle
            c._pic_dir = tmp_path / "pic"
            c._dedup_path = tmp_path / "dedup.json"
            return c

    def test_meeting_minutes_route(self, config_bundle, tmp_path):
        c = self._converter(config_bundle, tmp_path)
        content = "这是一份会议纪要，记录了周会的结论"
        title = "周会纪要"
        result = c._classify(content, title)
        assert result == "05-会议纪要"

    def test_discussion_route(self, config_bundle, tmp_path):
        c = self._converter(config_bundle, tmp_path)
        content = "内部讨论和分析"
        title = "讨论文档"
        result = c._classify(content, title)
        assert result == "04-讨论思考"

    def test_reference_route(self, config_bundle, tmp_path):
        c = self._converter(config_bundle, tmp_path)
        content = "参考资料和学习材料"
        title = "行业分享"
        result = c._classify(content, title)
        assert result == "08-参考资料"

    def test_default_to_scheme_report(self, config_bundle, tmp_path):
        c = self._converter(config_bundle, tmp_path)
        # 无匹配词时 fallback 到 03-方案报告
        content = "随机内容 xyz"
        title = "未知文档"
        result = c._classify(content, title)
        assert result == "03-方案报告"
