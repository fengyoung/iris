"""异步 HTTP 客户端的重试、fallback 和响应错误测试。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from iris.core import async_http


@pytest.mark.asyncio
async def test_sync_fallback_runs_in_shared_executor():
    with patch.object(async_http, "shared_pool") as pool:
        executor = ThreadPoolExecutor(max_workers=1)
        pool.get_executor.return_value = executor
        try:
            with patch("iris.core.http_client.http_post_json", return_value={"ok": True}) as post:
                result = await async_http._sync_fallback_post("https://example.test", {"x": 1}, {}, timeout=2, max_retries=1)
            assert result == {"ok": True}
            post.assert_called_once()
        finally:
            executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_httpx_post_retries_request_error():
    fake_httpx = MagicMock()
    fake_httpx.Timeout = lambda *args, **kwargs: object()
    fake_httpx.RequestError = type("RequestError", (Exception,), {})
    fake_httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    fake_httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    response = MagicMock()
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    client = AsyncMock()
    client.post.side_effect = [fake_httpx.RequestError("temporary"), response]
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=client)
    context.__aexit__ = AsyncMock(return_value=None)
    fake_httpx.AsyncClient.return_value = context
    with patch.object(async_http, "httpx", fake_httpx), patch.object(async_http, "asyncio") as aio:
        aio.sleep = AsyncMock()
        result = await async_http._httpx_post("https://example.test", {}, None, timeout=2, max_retries=1)
    assert result == {"ok": True}
    assert client.post.call_count == 2
