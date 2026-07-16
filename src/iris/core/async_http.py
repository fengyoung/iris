"""异步 HTTP 客户端 — 为 LLM API 调用提供 async/await 支持。

使用 httpx 作为异步 HTTP 后端，httpx 不可用时回退到同步调用（ThreadPoolExecutor）。

用法:
    from iris.core.async_http import async_post_json

    data = await async_post_json(url, payload, headers, timeout=60)
"""

from __future__ import annotations

import asyncio
import atexit
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    _HAS_HTTPX = False

# 共享线程池（用于 httpx 不可用时的 fallback）
_sync_pool: Optional[ThreadPoolExecutor] = None


def _get_sync_pool() -> ThreadPoolExecutor:
    global _sync_pool
    if _sync_pool is None:
        _sync_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="iris-sync-http")
        atexit.register(_shutdown_sync_pool)
    return _sync_pool


def _shutdown_sync_pool() -> None:
    global _sync_pool
    if _sync_pool is not None:
        _sync_pool.shutdown(wait=False)
        _sync_pool = None


async def async_post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    *,
    timeout: int = 60,
    max_retries: int = 0,
) -> Dict[str, Any]:
    """异步发送 JSON POST 请求，返回 JSON 响应。

    httpx 可用时直接异步调用，不可用时回退到同步调用（在 ThreadPoolExecutor 中运行）。

    Args:
        url: 请求 URL
        payload: JSON 请求体
        headers: 请求头（不含 Content-Type）
        timeout: 超时秒数
        max_retries: 重试次数

    Returns:
        JSON 响应 dict

    Raises:
        httpx.HTTPError / Exception: 请求失败时抛出
    """
    if _HAS_HTTPX and httpx is not None:
        return await _httpx_post(url, payload, headers, timeout=timeout, max_retries=max_retries)
    else:
        return await _sync_fallback_post(url, payload, headers, timeout=timeout, max_retries=max_retries)


async def _httpx_post(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]],
    *,
    timeout: int,
    max_retries: int,
) -> Dict[str, Any]:
    """使用 httpx 异步发送请求。"""
    _headers = {"Content-Type": "application/json"}
    if headers:
        _headers.update(headers)

    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.post(url, json=payload, headers=_headers)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt < max_retries:
                wait = min(2 ** attempt, 8)
                await asyncio.sleep(wait)
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("async_post_json: 全部重试耗尽但无错误记录")


async def _sync_fallback_post(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]],
    *,
    timeout: int,
    max_retries: int,
) -> Dict[str, Any]:
    """httpx 不可用时的同步 fallback（在 ThreadPoolExecutor 中运行）。"""
    from iris.core.http_client import http_post_json

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _get_sync_pool(),
        lambda: http_post_json(url, payload, headers or {}, timeout=timeout, max_retries=max_retries),
    )


def has_async_support() -> bool:
    """检查异步 HTTP 支持是否可用。"""
    return _HAS_HTTPX
