"""TrelloClient 请求层逻辑测试 — curl 优先传输 + 写操作幂等感知（mock 网络层）。

覆盖：
  - 幂等读(GET)：首选 curl，curl 成功时不走 urllib 兜底
  - 幂等读(GET)：curl 网络失败 → 重试已定次数 → 回退 urllib
  - 写操作(POST/PUT/DELETE)：单次执行，失败不重试、不回退 urllib（防重复提交）
  - 写操作 curl 返回 HTTP 错误 → 直接抛 TrelloClientError
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from iris.trello.client import (
    TrelloClient,
    TrelloClientError,
    _TrelloNetworkError,
)


def _client() -> TrelloClient:
    return TrelloClient(api_key="k", token="t")


def test_get_prefers_curl_no_urllib_fallback():
    c = _client()
    with patch.object(type(c), "_request_via_curl", return_value=[{"id": "x"}]) as curl, \
         patch.object(type(c), "_request_urllib") as urllib:
        result = c._request("GET", "/members/me/organizations")
    assert result == [{"id": "x"}]
    curl.assert_called_once_with("GET", "/1/members/me/organizations?key=k&token=t")
    urllib.assert_not_called()


def test_curl_credentials_are_passed_via_stdin_not_argv():
    c = TrelloClient("secret-key", "secret-token")
    with patch("subprocess.run") as run:
        run.return_value = SimpleNamespace(returncode=0, stdout='{}\n__IRIS_TRELLO_CODE__200', stderr='')
        c._request_via_curl("GET", "/1/cards?key=secret-key&token=secret-token")
        argv = run.call_args.args[0]
        assert "secret-key" not in argv and "secret-token" not in argv
        assert "secret-key" in run.call_args.kwargs["input"]


def test_get_curl_fails_then_urllib_fallback():
    c = _client()
    with patch.object(type(c), "_request_via_curl",
                      side_effect=_TrelloNetworkError("网络错误")) as curl, \
         patch.object(type(c), "_request_urllib", return_value=[{"id": "y"}]) as urllib, \
         patch("iris.trello.client.time.sleep"):
        result = c._request("GET", "/members/me/organizations")
    assert result == [{"id": "y"}]
    assert curl.call_count == 2  # 幂等读重试 2 次
    urllib.assert_called_once()


def test_write_single_attempt_no_retry_no_fallback():
    c = _client()
    with patch.object(type(c), "_request_via_curl",
                      side_effect=_TrelloNetworkError("网络错误")) as curl, \
         patch.object(type(c), "_request_urllib") as urllib:
        try:
            c._request("POST", "/cards", params={"name": "n"})
            assert False, "应抛出 _TrelloNetworkError"
        except _TrelloNetworkError:
            pass
    # 写操作：curl 只试一次，不重试、不回退 urllib（防重复写）
    curl.assert_called_once_with("POST", "/1/cards?name=n&key=k&token=t")
    urllib.assert_not_called()


def test_write_curl_http_error_propagates():
    c = _client()
    with patch.object(type(c), "_request_via_curl",
                      side_effect=TrelloClientError("Trello HTTP 400: bad")) as curl:
        try:
            c._request("PUT", "/cards/abc", params={"name": "n"})
            assert False, "应抛出 TrelloClientError"
        except TrelloClientError as exc:
            assert "HTTP 400" in str(exc)
    curl.assert_called_once()
