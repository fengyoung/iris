"""Trello REST API 客户端。"""

from __future__ import annotations

import contextlib
import json
import subprocess
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, request
from urllib.parse import quote

TRELLO_API_BASE = "https://api.trello.com/1"
_TRELLO_DOMAIN = "api.trello.com"
_CUSTOM_DNS = "8.8.8.8"
_DNS_CACHE_TTL = 3600  # DNS 缓存有效期（秒）

# DNS 缓存：{host: (ip, timestamp)}
_dns_cache: Dict[str, Tuple[str, float]] = {}
_dns_lock = threading.Lock()


def _is_ipv4(s: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, s)
        return True
    except OSError:
        return False


def _resolve_via_dns(host: str, dns_server: str = _CUSTOM_DNS) -> str:
    now = time.time()
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
    return host


@contextlib.contextmanager
def _patch_trello_dns():
    """临时替换 socket.getaddrinfo 以使用自定义 DNS 解析 Trello 域名。

    注意：由于修改全局 socket 函数，此上下文管理器不是线程安全的。
    仅在单线程场景下使用。多线程环境应使用 httpx 自定义 transport。
    """
    original = socket.getaddrinfo

    def _patched(host, port, family=0, type=0, proto=0, flags=0):
        resolved = _resolve_via_dns(host) if host == _TRELLO_DOMAIN else host
        return original(resolved, port, family, type, proto, flags)

    socket.getaddrinfo = _patched
    try:
        yield
    finally:
        socket.getaddrinfo = original


class TrelloClientError(RuntimeError):
    """Trello API 错误。"""


class TrelloClient:
    """封装 Trello REST API 认证与请求。"""

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
        return self.post(f"/boards/{board_id}/lists", name=name)

    def list_labels(self, board_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/boards/{board_id}/labels")

    def find_label_by_color(self, board_id: str, color: str) -> Optional[Dict[str, Any]]:
        labels = self.list_labels(board_id)
        for label in labels:
            if label.get("color") == color:
                return label
        return None

    def create_label(self, board_id: str, name: str, color: str) -> Dict[str, Any]:
        return self.post(f"/boards/{board_id}/labels", name=name, color=color)

    def list_cards(self, list_id: str) -> List[Dict[str, Any]]:
        return self.get(f"/lists/{list_id}/cards")

    def get_card(self, card_id: str) -> Dict[str, Any]:
        return self.get(f"/cards/{card_id}")

    def create_card(self, list_id: str, name: str, desc: str = "", due: Optional[str] = None,
                    id_labels: Optional[List[str]] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"idList": list_id, "name": name}
        if desc:
            params["desc"] = desc
        if due:
            params["due"] = due
        if id_labels:
            params["idLabels"] = ",".join(id_labels)
        return self.post("/cards", **params)

    def update_card(self, card_id: str, **fields: Any) -> Dict[str, Any]:
        return self.put(f"/cards/{card_id}", **fields)

    def move_card(self, card_id: str, to_list_id: str) -> Dict[str, Any]:
        return self.put(f"/cards/{card_id}", idList=to_list_id)

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

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        params = dict(params or {})
        params["key"] = self._key
        params["token"] = self._token
        qs = "&".join(f"{_quote(k)}={_quote(_serialize(v))}" for k, v in params.items() if v is not None)
        url = f"{TRELLO_API_BASE}{path}?{qs}"
        req = request.Request(url=url, method=method)
        try:
            with _patch_trello_dns():
                with request.urlopen(req, timeout=30) as resp:
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


def _quote(s: str) -> str:
    return quote(s, safe="")
