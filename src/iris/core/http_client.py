"""共享 HTTP 客户端：LLM provider 与 embedder 共用的 POST + 重试逻辑。"""

from __future__ import annotations

import json
import random
import socket
import time
from typing import Any, Callable, Dict, Optional, TypeVar
from urllib import error, request

_MAX_BACKOFF_SECONDS = 60

E = TypeVar("E", bound=Exception)


def http_post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    *,
    timeout: int = 60,
    max_retries: int = 0,
    error_factory: Callable[[str], Exception] = lambda msg: RuntimeError(msg),
) -> Dict[str, Any]:
    """以 JSON POST 请求并解析响应，含指数退避重试。

    Args:
        url: 请求地址
        payload: JSON 请求体
        headers: 额外 HTTP 头（Content-Type 自动添加）
        timeout: 单次请求超时（秒）
        max_retries: 最大重试次数
        error_factory: 错误消息 → 自定义异常类型的工厂函数

    Returns:
        解析后的 JSON 响应字典

    Raises:
        指定类型的异常（当重试耗尽时）
    """
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url=url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            backoff = min(2 ** attempt + random.uniform(0, 1), _MAX_BACKOFF_SECONDS)
            time.sleep(backoff)

        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_exc = error_factory(f"HTTP {exc.code}: {detail}")
            if exc.code != 429 and exc.code < 500:
                raise last_exc
            continue
        except error.URLError as exc:
            last_exc = error_factory(f"网络请求失败: {exc}")
            continue
        except socket.timeout:
            last_exc = error_factory("请求超时")
            continue

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            last_exc = error_factory(f"返回非 JSON: {exc}")
            continue

    raise last_exc or error_factory("已达最大重试次数")
