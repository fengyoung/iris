"""测试共享 HTTP 客户端：http_post_json 的重试、错误处理与成功路径。"""

from __future__ import annotations

import json
import socket
from unittest.mock import MagicMock, patch
from urllib import error

import pytest

from iris.core.http_client import http_post_json


class FakeHTTPError(error.HTTPError):
    """可构造的 HTTPError 模拟。"""

    def __init__(self, url, code, msg, hdrs, fp):
        self.url = url
        self.code = code
        self.msg = msg
        self.hdrs = hdrs
        self.fp = fp
        super().__init__(url, code, msg, hdrs, fp)

    def read(self):
        return self.fp if isinstance(self.fp, bytes) else self.fp.encode()


def _mock_response(data: dict) -> MagicMock:
    """构造模拟 urllib 响应对象，支持上下文管理器协议。"""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode("utf-8")
    resp.__enter__.return_value = resp  # 支持 with ... as response
    return resp


def _mock_raw_response(raw_bytes: bytes) -> MagicMock:
    """构造返回任意 bytes 的响应。"""
    resp = MagicMock()
    resp.read.return_value = raw_bytes
    resp.__enter__.return_value = resp
    return resp


# ── 成功路径 ──────────────────────────────────────────────


def test_success_first_attempt():
    """首次请求成功返回 JSON。"""
    resp = _mock_response({"result": "ok"})
    with patch("urllib.request.urlopen", return_value=resp):
        result = http_post_json("http://test/api", {"key": "val"}, {})
    assert result == {"result": "ok"}


def test_custom_error_factory():
    """自定义异常工厂。"""

    class CustomError(Exception):
        pass

    resp = _mock_response({"ok": True})
    with patch("urllib.request.urlopen", return_value=resp):
        result = http_post_json("http://test/api", {}, {},
                                error_factory=lambda msg: CustomError(msg))
    assert result == {"ok": True}


# ── 重试路径 ──────────────────────────────────────────────


def test_retry_on_429_then_success():
    """HTTP 429 → 重试 → 成功。"""
    resp_ok = _mock_response({"data": "ok"})
    http_err = FakeHTTPError("url", 429, "Too Many Requests", {}, b'{"error":"rate_limited"}')
    with patch("urllib.request.urlopen", side_effect=[http_err, resp_ok]):
        with patch("time.sleep", return_value=None):
            result = http_post_json("http://test/api", {}, {}, max_retries=2)
    assert result == {"data": "ok"}


def test_retry_on_500_then_success():
    """HTTP 500 → 重试 → 成功。"""
    resp_ok = _mock_response({"data": "ok"})
    http_err = FakeHTTPError("url", 500, "Internal Error", {}, b"boom")
    with patch("urllib.request.urlopen", side_effect=[http_err, resp_ok]):
        with patch("time.sleep", return_value=None):
            result = http_post_json("http://test/api", {}, {}, max_retries=2)
    assert result == {"data": "ok"}


def test_retry_on_urlerror_then_success():
    """网络错误 → 重试 → 成功。"""
    resp_ok = _mock_response({"data": "ok"})
    with patch("urllib.request.urlopen", side_effect=[error.URLError("conn refused"), resp_ok]):
        with patch("time.sleep", return_value=None):
            result = http_post_json("http://test/api", {}, {}, max_retries=2)
    assert result == {"data": "ok"}


def test_retry_on_timeout_then_success():
    """超时 → 重试 → 成功。"""
    resp_ok = _mock_response({"data": "ok"})
    with patch("urllib.request.urlopen", side_effect=[socket.timeout("timed out"), resp_ok]):
        with patch("time.sleep", return_value=None):
            result = http_post_json("http://test/api", {}, {}, max_retries=2)
    assert result == {"data": "ok"}


def test_retry_on_json_decode_error_then_success():
    """JSON 解析失败 → 重试 → 成功。"""
    bad_resp = _mock_raw_response(b"not json!!!")
    resp_ok = _mock_response({"data": "ok"})
    with patch("urllib.request.urlopen", side_effect=[bad_resp, resp_ok]):
        with patch("time.sleep", return_value=None):
            result = http_post_json("http://test/api", {}, {}, max_retries=2)
    assert result == {"data": "ok"}


# ── 重试耗尽 ──────────────────────────────────────────────


def test_retry_exhausted():
    """重试耗尽 → 抛出最后一条错误。"""
    http_err = FakeHTTPError("url", 503, "Unavailable", {}, b"overloaded")
    with patch("urllib.request.urlopen", side_effect=[http_err] * 4):
        with patch("time.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="HTTP 503"):
                http_post_json("http://test/api", {}, {}, max_retries=3)


# ── 不可重试错误 ──────────────────────────────────────────


def test_no_retry_on_400():
    """HTTP 400 → 立即抛出，不重试。"""
    http_err = FakeHTTPError("url", 400, "Bad Request", {}, b'{"error":"bad"}')
    call_count = [0]

    def counting_side_effect(req, timeout):
        call_count[0] += 1
        raise http_err

    with patch("urllib.request.urlopen", side_effect=counting_side_effect):
        with pytest.raises(RuntimeError, match="HTTP 400"):
            http_post_json("http://test/api", {}, {}, max_retries=3)
    assert call_count[0] == 1  # 只调用一次，无重试


def test_no_retry_on_401():
    """HTTP 401 → 立即抛出。"""
    http_err = FakeHTTPError("url", 401, "Unauthorized", {}, b"nope")
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(RuntimeError, match="HTTP 401"):
            http_post_json("http://test/api", {}, {}, max_retries=2)


# ── 自定义异常类型 ────────────────────────────────────────


def test_custom_error_on_failure():
    """验证自定义 error_factory 产生的异常类型。"""

    class MyError(Exception):
        pass

    http_err = FakeHTTPError("url", 400, "Bad", {}, b"x")
    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(MyError, match="HTTP 400"):
            http_post_json("http://test/api", {}, {}, max_retries=0,
                           error_factory=lambda msg: MyError(msg))


def test_custom_error_on_exhaustion():
    """验证重试耗尽时也使用自定义异常类型。"""

    class MyError(Exception):
        pass

    http_err = FakeHTTPError("url", 503, "SvcUnavail", {}, b"x")
    with patch("urllib.request.urlopen", side_effect=[http_err] * 3):
        with patch("time.sleep", return_value=None):
            with pytest.raises(MyError, match="HTTP 503"):
                http_post_json("http://test/api", {}, {}, max_retries=2,
                               error_factory=lambda msg: MyError(msg))


# ── 边界条件 ──────────────────────────────────────────────


def test_default_max_retries_zero():
    """默认不重试。"""
    resp = _mock_response({"a": 1})
    with patch("urllib.request.urlopen", return_value=resp):
        result = http_post_json("http://test/api", {}, {})
    assert result == {"a": 1}


def test_content_type_header_auto_added():
    """Content-Type 自动添加。"""
    resp = _mock_response({})
    with patch("urllib.request.Request") as mock_req_cls:
        mock_req = MagicMock()
        mock_req_cls.return_value = mock_req
        with patch("urllib.request.urlopen", return_value=resp):
            http_post_json("http://test/api", {"k": "v"}, {"X-Custom": "yes"})
        # 验证 Request 构造正确
        mock_req.add_header.assert_any_call("Content-Type", "application/json")
        mock_req.add_header.assert_any_call("X-Custom", "yes")
