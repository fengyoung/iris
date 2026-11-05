"""真实 HTTP 请求覆盖控制台鉴权和参数边界。"""

import json
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from iris.taskpanel.server import create_server


@pytest.fixture
def console(tmp_path):
    srv = create_server(tmp_path, tmp_path, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def request(url, payload=None, headers=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_dns_rebinding_cannot_read_page_or_api(console):
    _, url = console
    for path in ("/", "/api/state", "/api/catalog", "/api/jobs"):
        assert request(url + path, headers={"Host": "attacker.example"})[0] == 403


def test_post_requires_token_origin_and_json(console):
    srv, url = console
    srv.runner.start = Mock(return_value={"id": "job"})
    payload = {"command": "status", "params": {}}
    headers = {"Content-Type": "application/json", "X-Iris-Token": srv.token}
    assert request(url + "/api/jobs", payload)[0] == 403
    assert (
        request(url + "/api/jobs", payload, {**headers, "Origin": "https://evil.test"})[
            0
        ]
        == 403
    )
    assert request(url + "/api/jobs", payload, {**headers, "Origin": "null"})[0] == 403
    assert (
        request(url + "/api/jobs", payload, {**headers, "Content-Type": "text/plain"})[
            0
        ]
        == 415
    )
    srv.runner.start.assert_not_called()
    assert request(url + "/api/jobs", payload, {**headers, "Origin": url})[0] == 200
    srv.runner.start.assert_called_once_with("status", {})


def test_invalid_payload_does_not_start_job(console):
    srv, url = console
    srv.runner.start = Mock()
    headers = {"Content-Type": "application/json", "X-Iris-Token": srv.token}
    for payload in ([], {"command": []}, {"command": "status", "params": []}):
        assert request(url + "/api/jobs", payload, headers)[0] == 400
    srv.runner.start.assert_not_called()


def test_page_token_and_static_assets(console):
    srv, url = console
    code, body = request(url + "/")
    assert code == 200
    assert srv.token.encode() in body
    assert b"__IRIS_TOKEN__" not in body
    for path in ("/static/app.js", "/static/app.css"):
        code, body = request(url + path)
        assert code == 200 and body
    assert request(url + "/static/../server.py")[0] == 404


def test_overview_and_wizard_routes(console):
    srv, url = console
    code, body = request(url + "/api/overview")
    assert code == 200
    assert json.loads(body)["tasks"]["status"] == "ok"
    code, body = request(url + "/api/wizards/feed")
    assert code == 200
    assert json.loads(body) == {"items": []}
    srv.wizards.action = Mock(return_value={"id": "preview"})
    payload = {"url": "https://example.feishu.cn/docx/token"}
    assert request(url + "/api/wizards/feishu-preview", payload)[0] == 403
    srv.wizards.action.assert_not_called()
    headers = {"Content-Type": "application/json", "X-Iris-Token": srv.token}
    assert request(url + "/api/wizards/feishu-preview", payload, headers)[0] == 200
    srv.wizards.action.assert_called_once_with("feishu-preview", payload)


def test_catalog_and_empty_jobs(console):
    _, url = console
    code, body = request(url + "/api/catalog")
    assert code == 200
    assert len(json.loads(body)["commands"]) == 77
    code, body = request(url + "/api/jobs")
    assert code == 200
    assert json.loads(body) == {"jobs": []}
