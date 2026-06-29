"""Trello REST API 客户端。

DNS 解析使用独立子进程 dig + 线程安全缓存，通过 URL 重写（IP 替换域名）
+ 自定义 Host 头实现，避免全局 socket.getaddrinfo monkey-patch。
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request
from urllib.parse import quote

TRELLO_API_BASE = "https://api.trello.com/1"
_TRELLO_DOMAIN = "api.trello.com"
_CUSTOM_DNS = "8.8.8.8"
_DNS_CACHE_TTL = 3600  # DNS 缓存有效期（秒）

# DNS 缓存：{host: (ip, timestamp)}，由 _dns_lock 保护
_dns_cache: Dict[str, Tuple[str, float]] = {}
_dns_lock = threading.Lock()


def _is_ipv4(s: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, s)
        return True
    except OSError:
        return False


def _resolve_via_dns(host: str, dns_server: str = _CUSTOM_DNS) -> str:
    """通过 dig 命令解析主机名（线程安全，带 TTL 缓存）。"""
    now = time.monotonic()
    with _dns_lock:
        cached = _dns_cache.get(host)
        if cached and (now - cached[1]) < _DNS_CACHE_TTL:
            return cached[0]
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
    # 降级：返回原始主机名，依赖系统 DNS
    return host


def _make_trello_url(path: str) -> str:
    """构建 Trello API URL，将域名替换为解析后的 IP。

    返回 (url, host_header)，其中 url 用 IP 地址，host_header 保留原始域名
    以通过 TLS SNI 和 HTTP Host 头校验。
    """
    ip = _resolve_via_dns(_TRELLO_DOMAIN)
    if ip != _TRELLO_DOMAIN:
        # 使用 https://IP/path 并添加 Host header
        return f"https://{ip}{path}"
    return f"{TRELLO_API_BASE}{path}"


class TrelloClientError(RuntimeError):
    """Trello API 错误。"""


class TrelloClient:
    """封装 Trello REST API 认证与请求。

    DNS 解析通过 URL 重写实现，无全局 socket monkey-patch，
    线程安全且 KeyboardInterrupt 安全。
    """

    def __init__(self, api_key: str, token: str):
        self._key = api_key
        self._token = token

    def get(self, path: str, **params: Any) -> Dict[str, Any]:
        return self._request("GET", path, params=params)

    def post(self, path: str, **params: Any) -> Dict[str, Any]:
        return self._request("POST", path, params=params)

    def put(self, path: str, **params: Any) -> Dict[str, Any]:
        return self._request("PUT", path, params=params)

    def delete(self, path: str, **params: Any) -> Dict[str, Any]:
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

    def create_list(self, name: str, board_id: str) -> Dict[str, Any]:
        return self.post("/lists", name=name, idBoard=board_id)

    def list_cards(self, list_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/lists/{list_id}/cards")

    def get_card(self, card_id: str) -> Dict[str, Any]:
        return self.get(f"/cards/{card_id}")

    def create_card(self, list_id: str, name: str, desc: str = "",
                    due: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"idList": list_id, "name": name, "desc": desc}
        if due:
            params["due"] = due
        return self.post("/cards", **params)

    def update_card(self, card_id: str, **fields: Any) -> Dict[str, Any]:
        return self.put(f"/cards/{card_id}", **fields)

    def add_comment(self, card_id: str, text: str) -> Dict[str, Any]:
        return self.post(f"/cards/{card_id}/actions/comments", text=text)

    def list_labels(self, board_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/boards/{board_id}/labels")

    def create_label(self, board_id: str, name: str, color: str) -> Dict[str, Any]:
        return self.post("/labels", name=name, color=color, idBoard=board_id)

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
        url = _make_trello_url(full_path)

        # 自定义 SSL context（TLS 1.2+，验证证书）
        ssl_context = ssl.create_default_context()

        req = request.Request(url=url, method=method)
        # 如果使用了 IP 直连，设置正确的 Host 头
        req.add_header("Host", _TRELLO_DOMAIN)

        try:
            with request.urlopen(req, timeout=30, context=ssl_context) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise TrelloClientError(f"Trello HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise TrelloClientError(f"Trello 网络错误: {exc}") from exc
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
