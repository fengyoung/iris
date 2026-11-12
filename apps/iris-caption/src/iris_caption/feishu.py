"""Personal Feishu OAuth and read-only documents (no CLI runtime).

Developer apps are Confidential Clients: v3 requires a server secret even with
PKCE. Each user supplies their OWN app_id and secret from OS keychain. NEVER
bundle a shared secret. General distribution would need a server broker design.
Register the exact redirect URI in Feishu and enable docx:document:readonly,
wiki:wiki:readonly and offline_access. UI opens authorization_url on user action,
then accepts a FULL pasted redirect URL via exchange_redirect; no listener needed.
Run network work off the GUI thread. Tokens use injected keychain callbacks;
documents are not cached, so every fetch rechecks remote user permissions.

Verified 2026-11-05:
https://open.feishu.cn/document/common-capabilities/sso/api/obtain-oauth-code
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/get-user-access-token-v3
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .settings import get_secret, set_secret

AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
TOKEN_URL = "https://accounts.feishu.cn/oauth/v3/token"
API_ROOT = "https://open.feishu.cn/open-apis"
SCOPES = "docx:document:readonly wiki:wiki:readonly offline_access"
_TOKEN = re.compile(r"[A-Za-z0-9]{1,128}\Z")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)?feishu\.cn\Z")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class FeishuDocument:
    title: str
    text: str
    url: str
    token: str


class FeishuClient:
    """Thread-safe single-user client. Explicitly close or use a context manager.

    Process-local locks serialize clients for an app. Do not share its credential
    store between desktop processes. Construction performs no network requests.
    """

    def __init__(self, app_id: str, secret: str, *,
                 credential_getter: Callable[[str], str] = get_secret,
                 credential_setter: Callable[[str, str], None] = set_secret,
                 transport: httpx.BaseTransport | None = None, timeout: float = 20.0,
                 clock: Callable[[], float] = time.time) -> None:
        if not app_id.strip() or not secret.strip():
            raise ValueError("Provide your personal Feishu app ID and app secret from keychain.")
        if not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("Request timeout must be between 0 and 120 seconds.")
        self.app_id = app_id
        self._secret, self._get, self._set = secret, credential_getter, credential_setter
        self._clock = clock
        self._account = f"feishu:{app_id}:oauth"
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(app_id, threading.RLock())
        self._pending: tuple[str, str, str, float] | None = None
        self._http = httpx.Client(transport=transport, timeout=timeout,
                                  follow_redirects=False, trust_env=False)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FeishuClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def authorization_url(self, redirect_uri: str) -> str:
        parsed = urlsplit(redirect_uri)
        if (parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.hostname or not parsed.path
                or (parsed.scheme != "https" and not
                    (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}))):
            raise ValueError("Use a registered HTTPS or loopback HTTP callback without query or fragment.")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self._lock:
            self._pending = (state, verifier, redirect_uri, self._clock() + 300)
        return AUTHORIZE_URL + "?" + urlencode({
            "client_id": self.app_id, "response_type": "code", "redirect_uri": redirect_uri,
            "scope": SCOPES, "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "consent",
        })

    def exchange_redirect(self, callback_url: str) -> None:
        """Validate a pasted full redirect URL, including state and callback target."""
        with self._lock:
            if self._pending is None:
                raise ValueError("Start authorization before submitting a redirect URL.")
            parsed, expected = urlsplit(callback_url), urlsplit(self._pending[2])
            if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
                raise ValueError("OAuth callback address does not match.")
            query = parse_qs(parsed.query, keep_blank_values=True)
            if parsed.fragment or len(query.get("state", [])) != 1:
                raise ValueError("Invalid OAuth callback state.")
            self._check_state(query["state"][0])
            if "error" in query:
                self._pending = None
                raise RuntimeError("Feishu authorization was denied; start authorization again.")
            if len(query.get("code", [])) != 1:
                raise ValueError("OAuth callback must contain one authorization code.")
            self.exchange(query["code"][0], query["state"][0])

    def _check_state(self, state: str) -> None:
        if self._pending is None or self._clock() >= self._pending[3]:
            self._pending = None
            raise ValueError("OAuth authorization expired or was already used.")
        if not secrets.compare_digest(state.encode(), self._pending[0].encode()):
            raise ValueError("OAuth state does not match.")

    def exchange(self, code: str, state: str, *, redirect_uri: str | None = None) -> None:
        with self._lock:
            self._check_state(state)
            assert self._pending is not None
            _, verifier, callback, _ = self._pending
            if redirect_uri is not None and redirect_uri != callback:
                raise ValueError("OAuth callback address does not match.")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,2048}", code):
                raise ValueError("Invalid authorization code.")
            self._pending = None  # Never replay single-use codes, even after ambiguous failures.
            result = self._request("POST", TOKEN_URL, data={
                "grant_type": "authorization_code", "client_id": self.app_id,
                "client_secret": self._secret, "code": code,
                "redirect_uri": callback, "code_verifier": verifier,
            })
            self._save_tokens(result)

    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            response = self._http.request(method, url, **kwargs)
            if not 200 <= response.status_code < 300:
                raise RuntimeError("Feishu request failed; check authorization, permissions or connection.")
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise RuntimeError("Feishu request failed; check connection and retry.") from None
        if not isinstance(data, dict) or data.get("code", 0) != 0 or data.get("error"):
            raise RuntimeError("Feishu rejected the request; check user authorization and document permissions.")
        return data

    def _save_tokens(self, data: dict) -> None:
        try:
            access, expires = data["access_token"], float(data["expires_in"])
            refresh = data.get("refresh_token", "")
            refresh_expires = float(data.get("refresh_token_expires_in", 0))
            if (not isinstance(access, str) or not access or not isinstance(refresh, str)
                    or not math.isfinite(expires) or expires <= 0
                    or not math.isfinite(refresh_expires) or refresh_expires < 0):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("Feishu returned invalid credentials; authorize again.") from None
        now = self._clock()
        self._store({"access_token": access, "expires_at": now + expires,
                     "refresh_token": refresh, "refresh_expires_at": now + refresh_expires})

    def _store(self, data: dict) -> None:
        try:
            self._set(self._account, json.dumps(data) if data else "")
        except Exception:
            raise RuntimeError("Unable to save Feishu credentials in keychain.") from None

    def _access_token(self) -> str:
        with self._lock:
            try:
                data = json.loads(self._get(self._account) or "{}")
                if not isinstance(data, dict):
                    raise ValueError
                if data.get("access_token") and float(data.get("expires_at", 0)) > self._clock() + 30:
                    return str(data["access_token"])
                refresh = data.get("refresh_token")
                valid = float(data.get("refresh_expires_at", 0)) > self._clock()
            except Exception:
                raise RuntimeError("Unable to load Feishu credentials; authorize again.") from None
            if not refresh or not valid:
                raise RuntimeError("Feishu authorization required or expired.")
            # Timeout may mean the server consumed it. Erase before sending so no
            # retry or process restart can replay this single-use refresh token.
            self._store({})
            result = self._request("POST", TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": self.app_id,
                "client_secret": self._secret, "refresh_token": refresh,
            })
            self._save_tokens(result)
            return result["access_token"]

    @staticmethod
    def parse_document_url(url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not _HOST.fullmatch(parsed.hostname or "")
                or parsed.username or parsed.password or parsed.port not in {None, 443}):
            raise ValueError("Use an HTTPS document URL on feishu.cn or a Feishu tenant subdomain.")
        match = re.fullmatch(r"/(docx|wiki)/([A-Za-z0-9]{1,128})/?", parsed.path)
        if not match:
            raise ValueError("Only Feishu docx and wiki document links are supported.")
        return match[1], match[2]

    def fetch_document(self, url: str) -> FeishuDocument:
        """Read current title and raw text; every call checks remote permissions."""
        kind, token = self.parse_document_url(url)
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if kind == "wiki":
            result = self._request("GET", API_ROOT + "/wiki/v2/spaces/get_node",
                                   params={"token": token}, headers=headers)
            node = self._object(self._object(result.get("data")).get("node"))
            token = node.get("obj_token", "")
            if node.get("obj_type") != "docx" or not isinstance(token, str) or not _TOKEN.fullmatch(token):
                raise ValueError("The wiki node must resolve to a supported docx document.")
        endpoint = API_ROOT + f"/docx/v1/documents/{token}"
        meta = self._request("GET", endpoint, headers=headers)
        body = self._request("GET", endpoint + "/raw_content", headers=headers)
        title = self._object(self._object(meta.get("data")).get("document")).get("title")
        text = self._object(body.get("data")).get("content")
        if not isinstance(title, str) or not isinstance(text, str):
            raise RuntimeError("Feishu returned an invalid document response.")
        return FeishuDocument(title, text, url, token)

    @staticmethod
    def _object(value: object) -> dict:
        if not isinstance(value, dict):
            raise RuntimeError("Feishu returned an invalid document response.")
        return value

    def fetch_text(self, url: str) -> str:
        return self.fetch_document(url).text
