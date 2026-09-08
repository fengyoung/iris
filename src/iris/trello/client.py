"""Trello REST API 客户端。

DNS 解析使用独立子进程 dig + 线程安全缓存，通过 URL 重写（IP 替换域名）
+ 自定义 Host 头实现，避免全局 socket.getaddrinfo monkey-patch。
IP 直连时通过自定义 HTTPS 连接设置正确的 SNI hostname，确保证书校验通过。

v3.32.x：curl 作为首选传输（对本机间歇性网络更稳健），urllib 兜底；
仅幂等读(GET)自动重试 + DNS 负缓存；写操作(POST/PUT/DELETE)单次执行不重发，
避免网络抖动下重复提交。
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request
from urllib.parse import quote, urlparse

from iris.core.exceptions import IrisRuntimeError

TRELLO_API_BASE = "https://api.trello.com/1"
_TRELLO_DOMAIN = "api.trello.com"
_CUSTOM_DNS = "8.8.8.8"
_DNS_CACHE_TTL = 3600  # DNS 缓存有效期（秒）
_REQUEST_MAX_ATTEMPTS = 2    # 幂等(GET)网络层重试次数
_REQUEST_BACKOFF_BASE = 0.5  # 重试退避基数（秒）
_DEFAULT_TIMEOUT = 15        # 网络请求超时（秒）
# 幂等方法才允许自动重试（避免写操作在网络抖动下重复提交）
_IDEMPOTENT_METHODS = frozenset({"GET"})

# DNS 缓存：{host: (ip|None, timestamp)}，None 表示负缓存（dig 失败需用 hostname）
_dns_cache: Dict[str, Tuple[Optional[str], float]] = {}
_dns_lock = threading.Lock()


def _is_ipv4(s: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, s)
        return True
    except OSError:
        return False


def _resolve_via_dns(host: str, dns_server: str = _CUSTOM_DNS) -> str:
    """通过 dig 命令解析主机名（线程安全，带 TTL 缓存 / 负缓存）。"""
    now = time.monotonic()
    with _dns_lock:
        cached = _dns_cache.get(host)
        if cached and (now - cached[1]) < _DNS_CACHE_TTL:
            # 负缓存（None）表示 dig 失败，回退 hostname
            return cached[0] or host
    try:
        result = subprocess.run(
            ["dig", f"@{dns_server}", "+short", host],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            line = line.strip()
            if line and _is_ipv4(line):
                with _dns_lock:
                    _dns_cache[host] = (line, now)
                return line
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    # 负缓存：dig 失败/无 IPv4 结果，缓存失败状态避免反复阻塞
    with _dns_lock:
        _dns_cache[host] = (None, now)
    return host


def _make_trello_url(path: str) -> str:
    """构建 Trello API URL，将域名替换为解析后的 IP。

    返回 (url, host_header)，其中 url 用 IP 地址，host_header 保留原始域名
    以通过 TLS SNI 和 HTTP Host 头校验。
    """
    ip = _resolve_via_dns(_TRELLO_DOMAIN)
    if ip != _TRELLO_DOMAIN:
        return f"https://{ip}{path}"
    return f"https://{_TRELLO_DOMAIN}{path}"


class TrelloClientError(IrisRuntimeError):
    """Trello API 错误。"""


class _TrelloNetworkError(TrelloClientError):
    """Trello 网络层传输失败（可重试；重试后回退 curl）。"""


class _TrelloHTTPSConnection(http.client.HTTPSConnection):
    """支持 IP 直连 + 正确 SNI hostname 的 HTTPS 连接。

    Python 3.13 的 urllib 在 URL 为 IP 时会将 IP 作为 SNI hostname，
    导致证书校验失败。此连接类允许指定独立的 SNI hostname。
    """

    _sni_hostname: str = ""

    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        if self._tunnel_host:
            self._tunnel()
        # IP 直连时使用真实域名作为 SNI hostname，确保 TLS 证书校验通过
        sni = self._sni_hostname or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=sni)


class TrelloClient:
    """封装 Trello REST API 认证与请求。

    DNS 解析通过 URL 重写实现，无全局 socket monkey-patch，
    线程安全且 KeyboardInterrupt 安全。
    """

    def __init__(self, api_key: str, token: str):
        self._key = api_key
        self._token = token
        self._timeout = _DEFAULT_TIMEOUT

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, **params: Any) -> Any:
        return self._request("POST", path, params=params)

    def put(self, path: str, **params: Any) -> Any:
        return self._request("PUT", path, params=params)

    def delete(self, path: str, **params: Any) -> Any:
        return self._request("DELETE", path, params=params)

    # ── Trello API 封装 ──────────────────────────────────────

    def list_organizations(self) -> List[Dict[str, Any]]:
        return self.get("/members/me/organizations")

    def find_organization_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        orgs = self.list_organizations()
        for org in orgs:
            if org.get("displayName") == name or org.get("name") == name:
                return org
        return None

    def list_boards(self, organization_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/organizations/{organization_id}/boards")

    def find_board_by_name(self, organization_id: str, name: str) -> Optional[Dict[str, Any]]:
        boards = self.list_boards(organization_id)
        for board in boards:
            if board.get("name") == name:
                return board
        return None

    def create_board(self, name: str, organization_id: str) -> Dict[str, Any]:
        return self.post("/boards", name=name, idOrganization=organization_id)

    def list_lists(self, board_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/boards/{board_id}/lists")

    def find_list_by_name(self, board_id: str, name: str) -> Optional[Dict[str, Any]]:
        lists = self.list_lists(board_id)
        for lst in lists:
            if lst.get("name") == name:
                return lst
        return None

    def create_list(self, board_id: str, name: str) -> Dict[str, Any]:
        return self.post("/lists", name=name, idBoard=board_id)

    def list_cards(self, list_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/lists/{list_id}/cards")

    def get_card(self, card_id: str) -> Dict[str, Any]:
        return self.get(f"/cards/{card_id}")

    def create_card(self, list_id: str, name: str, desc: str = "",
                    due: Optional[str] = None,
                    id_labels: Optional[List[str]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"idList": list_id, "name": name, "desc": desc}
        if due:
            params["due"] = due
        if id_labels:
            params["idLabels"] = ",".join(id_labels)
        return self.post("/cards", **params)

    def update_card(self, card_id: str, **fields: Any) -> Dict[str, Any]:
        return self.put(f"/cards/{card_id}", **fields)

    def add_comment(self, card_id: str, text: str) -> Dict[str, Any]:
        return self.post(f"/cards/{card_id}/actions/comments", text=text)

    def list_labels(self, board_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/boards/{board_id}/labels")

    def create_label(self, board_id: str, name: str, color: str) -> Dict[str, Any]:
        return self.post("/labels", name=name, color=color, idBoard=board_id)

    def find_label_by_color(self, board_id: str, color: str) -> Optional[Dict[str, Any]]:
        """在指定看板中按颜色查找标签。"""
        labels = self.list_labels(board_id)
        for label in labels:
            if label.get("color") == color:
                return label
        return None

    def move_card(self, card_id: str, target_list_id: str) -> Dict[str, Any]:
        """将卡片移动到指定列表。"""
        return self.put(f"/cards/{card_id}", idList=target_list_id)

    def add_label_to_card(self, card_id: str, label_id: str) -> Dict[str, Any]:
        return self.post(f"/cards/{card_id}/idLabels", value=label_id)

    def set_due_complete(self, card_id: str) -> Dict[str, Any]:
        return self.put(f"/cards/{card_id}", dueComplete=True)

    def archive_card(self, card_id: str) -> Dict[str, Any]:
        return self.put(f"/cards/{card_id}", closed=True)

    def search(self, query: str, board_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"query": query, "modelTypes": "cards", "cards_limit": 20}
        if board_id:
            params["idBoards"] = board_id
        result = self.get("/search", **params)
        return result.get("cards", [])

    # ── 内部实现 ────────────────────────────────────────────

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        params = dict(params or {})
        params["key"] = self._key
        params["token"] = self._token
        qs = "&".join(f"{quote(k)}={quote(_serialize(v))}" for k, v in params.items()
                       if v is not None)
        full_path = f"/1{path}?{qs}"
        idempotent = method in _IDEMPOTENT_METHODS

        last_exc: Optional[Exception] = None
        # 首选 curl 传输（对本机间歇性网络更稳健；macOS/Linux 自带 curl）
        attempts = _REQUEST_MAX_ATTEMPTS if idempotent else 1
        for attempt in range(attempts):
            try:
                return self._request_via_curl(method, full_path)
            except _TrelloNetworkError as exc:
                last_exc = exc
                if idempotent and attempt < attempts - 1:
                    time.sleep(_REQUEST_BACKOFF_BASE * (2 ** attempt))

        # 写操作（POST/PUT/DELETE）不自动重试、不回退，单次失败直接抛，避免重复提交
        if not idempotent:
            if last_exc is not None:
                raise last_exc
            raise

        # 幂等读：curl 重试耗尽后回退 urllib（curl 不可用时兜底）
        try:
            return self._request_urllib(method, full_path)
        except _TrelloNetworkError:
            if last_exc is not None:
                raise last_exc
            raise

    def _request_urllib(self, method: str, full_path: str) -> Any:
        """urllib 主路径：URL 构建 + IP 直连 + urlopen。"""
        url = _make_trello_url(full_path)
        parsed = urlparse(url)

        req = request.Request(url=url, method=method)
        req.add_header("Host", _TRELLO_DOMAIN)

        # IP 直连时使用自定义 HTTPS 连接，确保 SNI hostname 为域名
        if parsed.hostname and _is_ipv4(parsed.hostname):
            return self._request_via_ip(method, req, parsed)

        ssl_context = ssl.create_default_context()
        try:
            with request.urlopen(req, timeout=self._timeout, context=ssl_context) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TrelloClientError(f"Trello HTTP {exc.code}: {detail}") from exc
        except (error.URLError, OSError) as exc:
            raise _TrelloNetworkError(f"Trello 网络错误: {exc}") from exc
        return self._parse_response(raw)

    def _request_via_curl(self, method: str, full_path: str) -> Any:
        """curl 兜底传输：urllib 网络层失败时回退（curl 对本机间歇性网络更稳健）。"""
        url = f"https://{_TRELLO_DOMAIN}{full_path}"
        marker = "\n__IRIS_TRELLO_CODE__"
        # URL 中包含 API 凭证，但通过 curl config stdin 传递，避免出现在 ps 输出。
        cmd = ["curl", "-s", "-g", "--config", "-", "-w", marker + "%{http_code}"]
        config = "\n".join([
            f'url = "{url}"',
            f'request = "{method}"',
            f'max-time = {self._timeout}',
        ]) + "\n"
        try:
            r = subprocess.run(cmd, input=config, capture_output=True, text=True,
                               timeout=self._timeout + 10)
        except (subprocess.SubprocessError, OSError) as exc:
            raise _TrelloNetworkError(f"Trello 网络错误: curl 不可用 {exc}") from exc
        if r.returncode != 0:
            raise _TrelloNetworkError(f"Trello 网络错误: curl {r.stderr.strip()[:120]}")

        code = ""
        body = r.stdout
        if marker in body:
            body, code = body.rsplit(marker, 1)
        code = code.strip()
        if code in ("200", "201", "204"):
            return self._parse_response(body)
        raise TrelloClientError(f"Trello HTTP {code}: {body[:200]}")

    def _request_via_ip(self, method: str, req: request.Request,
                        parsed) -> Any:
        """通过 IP 直连 + 自定义 SNI hostname 发起请求。"""
        hostname = parsed.hostname
        port = parsed.port or 443
        selector = parsed.path
        if parsed.query:
            selector += "?" + parsed.query

        ssl_context = ssl.create_default_context()
        conn = _TrelloHTTPSConnection(hostname, port, context=ssl_context, timeout=self._timeout)
        conn._sni_hostname = _TRELLO_DOMAIN

        try:
            conn.request(method, selector, body=req.data, headers=dict(req.headers))
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise TrelloClientError(f"Trello HTTP {resp.status}: {raw}")
        except (socket.timeout, OSError, ssl.SSLError) as exc:
            raise _TrelloNetworkError(f"Trello 网络错误: {exc}") from exc
        finally:
            conn.close()
        return self._parse_response(raw)

    def _parse_response(self, raw: str) -> Any:
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TrelloClientError(f"Trello 返回非 JSON: {raw[:200]}") from exc


def _serialize(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
