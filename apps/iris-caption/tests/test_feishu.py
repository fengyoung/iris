"""All HTTP is intercepted by MockTransport; never performs OAuth login."""
import base64
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from iris_caption.feishu import AUTHORIZE_URL, TOKEN_URL, FeishuClient

CALLBACK = "http://127.0.0.1:8765/callback"


@pytest.fixture
def factory():
    clients = []

    def create(handler, *, now=None, stored=None):
        store = {} if stored is None else stored
        client = FeishuClient("personal-app", "personal-secret",
                              credential_getter=lambda key: store.get(key, ""),
                              credential_setter=lambda key, value: store.__setitem__(key, value),
                              transport=httpx.MockTransport(handler), clock=lambda: (now or [1000])[0])
        clients.append(client)
        return client, store

    yield create
    for client in clients:
        client.close()


def tokens():
    return {"code": 0, "access_token": "access-secret", "expires_in": 7200,
            "refresh_token": "refresh-secret", "refresh_token_expires_in": 86400}


def authorized_store(expired=False):
    return {"feishu:personal-app:oauth": json.dumps({
        "access_token": "old-access", "expires_at": 999 if expired else 9999,
        "refresh_token": "old-refresh", "refresh_expires_at": 9999})}


def test_pkce_exchange_and_replay(factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=tokens())

    client, store = factory(handler)
    url = client.authorization_url(CALLBACK)
    assert url.startswith(AUTHORIZE_URL + "?")
    params = parse_qs(urlsplit(url).query)
    assert params["code_challenge_method"] == ["S256"]
    state = params["state"][0]
    client.exchange_redirect(CALLBACK + "?code=example-code&state=" + state)
    body = parse_qs(requests[0].content.decode())
    challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest())
    assert challenge.decode().rstrip("=") == params["code_challenge"][0]
    assert str(requests[0].url) == TOKEN_URL
    assert body["client_secret"] == ["personal-secret"]
    assert body["redirect_uri"] == [CALLBACK]
    assert json.loads(next(iter(store.values())))["refresh_token"] == "refresh-secret"
    with pytest.raises(ValueError):
        client.exchange("example-code", state)
    assert len(requests) == 1


@pytest.mark.parametrize("suffix", ["?code=x&state=bad", "?code=x", "?code=x&state=a&state=b"])
def test_bad_state_never_sends(factory, suffix):
    client, _ = factory(lambda _: pytest.fail("Network must not run"))
    client.authorization_url(CALLBACK)
    with pytest.raises(ValueError):
        client.exchange_redirect(CALLBACK + suffix)


def test_expiry_callback_target_denial_and_replaced_flow(factory):
    now = [1000]
    client, _ = factory(lambda _: pytest.fail("Network must not run"), now=now)
    state = parse_qs(urlsplit(client.authorization_url(CALLBACK)).query)["state"][0]
    with pytest.raises(ValueError):
        client.exchange_redirect("https://evil.example/callback?code=x&state=" + state)
    with pytest.raises(ValueError):
        client.exchange("x", state, redirect_uri="https://other.example/callback")
    client.authorization_url(CALLBACK)
    with pytest.raises(ValueError):
        client.exchange("x", state)
    state = parse_qs(urlsplit(client.authorization_url(CALLBACK)).query)["state"][0]
    with pytest.raises(RuntimeError, match="denied"):
        client.exchange_redirect(CALLBACK + "?error=access_denied&state=" + state)
    state = parse_qs(urlsplit(client.authorization_url(CALLBACK)).query)["state"][0]
    now[0] += 301
    with pytest.raises(ValueError, match="expired"):
        client.exchange("x", state)


@pytest.mark.parametrize("url", [
    "https://evil.example/docx/abc", "https://feishu.cn.evil.example/docx/abc",
    "https://evilfeishu.cn/docx/abc", "http://team.feishu.cn/docx/abc",
    "https://user@team.feishu.cn/docx/abc", "https://team.feishu.cn:444/docx/abc",
    "https://team.feishu.cn/docx/../abc", "https://team.feishu.cn/docx/ab%2Fcd",
    "https://team.feishu.cn/docx/abc_def", "https://team.feishu.cn/sheets/abc",
    "https://a.b.feishu.cn/docx/abc", "https://team.feishu.cn/docx/abc/extra",
])
def test_reject_unsafe_urls_before_credentials_or_network(factory, url):
    client, _ = factory(lambda _: pytest.fail("Network must not run"))
    with pytest.raises(ValueError):
        client.fetch_document(url)


def test_wiki_fixed_endpoints_and_rechecks_permission(factory):
    requests = []
    revoked = [False]

    def handler(request):
        requests.append(request)
        assert request.url.host == "open.feishu.cn"
        assert request.headers["Authorization"] == "Bearer old-access"
        if revoked[0]:
            return httpx.Response(403, json={"msg": "secret body"})
        if request.url.path.endswith("get_node"):
            return httpx.Response(200, json={"data": {"node": {"obj_type": "docx", "obj_token": "Doc123"}}})
        if request.url.path.endswith("raw_content"):
            return httpx.Response(200, json={"data": {"content": "Current text"}})
        return httpx.Response(200, json={"data": {"document": {"title": "Current title"}}})

    client, _ = factory(handler, stored=authorized_store())
    doc = client.fetch_document("https://tenant.feishu.cn/wiki/Wiki123?from=share")
    assert (doc.title, doc.text, doc.token) == ("Current title", "Current text", "Doc123")
    assert requests[0].url.params["token"] == "Wiki123"
    revoked[0] = True
    with pytest.raises(RuntimeError):
        client.fetch_document(doc.url)
    assert len(requests) == 4


@pytest.mark.parametrize("token,kind", [("../evil", "docx"), ("valid", "sheet"), ("https://evil", "docx")])
def test_wiki_resolved_token_is_validated(factory, token, kind):
    client, _ = factory(lambda _: httpx.Response(200, json={"data": {"node": {
        "obj_type": kind, "obj_token": token}}}), stored=authorized_store())
    with pytest.raises(ValueError):
        client.fetch_document("https://team.feishu.cn/wiki/abc")


def test_refresh_once_across_threads_and_clients(factory):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == TOKEN_URL
        assert parse_qs(request.content.decode())["refresh_token"] == ["old-refresh"]
        assert request.extensions["timeout"]["read"] == 20.0
        return httpx.Response(200, json=tokens())

    store = authorized_store(expired=True)
    clients = [factory(handler, stored=store)[0] for _ in range(2)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: clients[i % 2]._access_token(), range(30)))
    assert results == ["access-secret"] * 30
    assert len(requests) == 1
    assert json.loads(next(iter(store.values())))["refresh_token"] == "refresh-secret"


def test_ambiguous_refresh_failure_cannot_replay_and_errors_redacted(factory):
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("old-refresh personal-secret access-secret", request=request)

    client, store = factory(handler, stored=authorized_store(expired=True))
    with pytest.raises(RuntimeError) as exc:
        client._access_token()
    assert "secret" not in str(exc.value)
    assert exc.value.__suppress_context__
    with pytest.raises(RuntimeError, match="authorization required"):
        client._access_token()
    assert len(requests) == 1
    assert not next(iter(store.values()))


def test_redirect_response_never_followed(factory):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    client, _ = factory(handler, stored=authorized_store())
    with pytest.raises(RuntimeError):
        client.fetch_document("https://team.feishu.cn/docx/abc")
    assert len(requests) == 1


@pytest.mark.parametrize("response", [{"code": 20002, "error_description": "personal-secret"},
                                       {"access_token": "access-secret", "expires_in": "nan"}, []])
def test_token_errors_do_not_disclose_payload(factory, response):
    client, _ = factory(lambda _: httpx.Response(200, json=response))
    state = parse_qs(urlsplit(client.authorization_url(CALLBACK)).query)["state"][0]
    with pytest.raises(RuntimeError) as exc:
        client.exchange("code", state)
    assert "secret" not in str(exc.value)
