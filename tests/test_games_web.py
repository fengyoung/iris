"""本机 HTTP 集成契约：安全边界、广播、资源配额和复盘持久化。"""
import http.client
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import fitz
import pytest

from iris.games.undercover import GameResult
from iris.games.web_server import GameSession, UndercoverWebServer


@pytest.fixture
def web(tmp_path):
    config = SimpleNamespace(root=tmp_path)
    with patch('iris.games.web_server.LLMService') as llm:
        llm.return_value.get_provider.return_value.get_model_manager.return_value.list_models.return_value = []
        server = UndercoverWebServer(config, port=0)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)


def request(web, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection(*web.server_address, timeout=3)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def image_bytes():
    return fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False).tobytes('png')


def test_http_read_routes_and_cross_site_rejection(web):
    assert request(web, 'GET', '/')[0] == 200
    assert request(web, 'GET', '/api/models')[0] == 200
    assert request(web, 'GET', '/api/history') == (200, b'[]')
    assert request(web, 'GET', '/missing')[0] == 404
    assert request(web, 'GET', '/uploads/../../../etc/hosts')[0] == 404
    assert request(web, 'GET', '/', headers={'Host': 'evil.invalid'})[0] == 403
    assert request(web, 'POST', '/api/game/start', '{}', {'Origin': 'http://evil.invalid'})[0] == 403
    assert request(web, 'GET', '/api/history/missing')[0] == 404
    assert request(web, 'GET', '/api/history/id/image/invalid')[0] == 400
    assert request(web, 'GET', '/api/history/id/image/spy')[0] == 404
    assert request(web, 'GET', '/api/game/missing/events')[0] == 404
    assert request(web, 'POST', '/api/game/missing/advance', '')[0] == 404
    assert request(web, 'POST', '/api/game/missing/abort', '')[0] == 404
    assert request(web, 'POST', '/missing', '')[0] == 404


def test_upload_preview_and_reject_invalid_image(web):
    data = image_bytes()
    prefix = b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="test.png"\r\nContent-Type: image/png\r\n\r\n'
    status, body = request(web, 'POST', '/api/upload', prefix + data + b'\r\n--boundary--\r\n',
                           {'Content-Type': 'multipart/form-data; boundary=boundary'})
    assert status == 200
    token = json.loads(body)['token']
    assert request(web, 'GET', '/uploads/' + token) == (200, data)
    assert request(web, 'POST', '/api/upload', b'bad')[0] == 400
    assert request(web, 'POST', '/api/upload', b'bad', {'Content-Type': 'multipart/form-data'})[0] == 400
    assert request(web, 'POST', '/api/upload', prefix + b'bad\r\n--boundary--\r\n',
                   {'Content-Type': 'multipart/form-data; boundary=boundary'})[0] == 400


def test_sse_multiple_subscribers_and_resume(web):
    session = GameSession('test')
    session.event_queue.put({'type': 'game_start'})
    session.event_queue.put({'type': 'round_start'})
    session.event_queue.put(None)
    web.state.games['test'] = session
    first = request(web, 'GET', '/api/game/test/events')
    assert first[0] == 200 and b'id: 1' in first[1] and b'stream_end' in first[1]
    assert request(web, 'GET', '/api/game/test/events') == first
    resumed = request(web, 'GET', '/api/game/test/events', headers={'Last-Event-ID': '1'})
    assert b'game_start' not in resumed[1] and b'round_start' in resumed[1]
    assert request(web, 'POST', '/api/game/test/advance', '')[0] == 200
    assert session.advance_event.is_set()
    assert request(web, 'POST', '/api/game/test/abort', '')[0] == 200
    assert session.cancel_event.is_set()


def test_start_validation_quota_and_saved_result(web):
    token = web.state.replay_store.save_upload(image_bytes(), 'test.png')
    payload = {'players': [{'role': 'base_model', 'model_id': str(i)} for i in range(3)],
               'image_civilian': token, 'image_spy': token, 'seed': 0}
    result = GameResult(image_civilian='a', image_spy='b', players=['base_model/0'], spy_keys=[], winner='cancelled')
    with patch('iris.games.web_server.UndercoverGame') as game:
        game.return_value.run.return_value = result
        status, body = request(web, 'POST', '/api/game/start', json.dumps(payload))
        assert status == 200
        game_id = json.loads(body)['game_id']
        web.state.games[game_id].thread.join(3)
        status, body = request(web, 'GET', '/api/history/' + game_id)
        assert status == 200 and json.loads(body)['result']['winner'] == 'cancelled'
        assert request(web, 'GET', '/api/history/' + game_id + '/image/civilian')[0] == 200
        assert json.loads(request(web, 'GET', '/api/history')[1])[0]['id'] == game_id
        assert game.call_args.kwargs['seed'] == 0
    for _ in range(2):
        assert web.state.game_slots.acquire(blocking=False)
    assert request(web, 'POST', '/api/game/start', json.dumps(payload))[0] == 429
    for _ in range(2):
        web.state.game_slots.release()
    for invalid in ('[]', 'null', 'bad', '{}'):
        assert request(web, 'POST', '/api/game/start', invalid)[0] == 400
    payload['image_spy'] = 'a' * 32 + '.png'
    assert request(web, 'POST', '/api/game/start', json.dumps(payload))[0] == 400


def test_summary_updates_replay(web):
    store = web.state.replay_store
    result = GameResult(image_civilian='a', image_spy='b', players=[], spy_keys=[])
    store.save_result('summary', result, [], 'civilian.png', 'spy.png')
    llm = Mock()
    llm.generate_as.return_value = SimpleNamespace(text='复盘总结')
    store._generate_summary('summary', llm, 'base_model', 'judge')
    saved = store.load_game('summary')
    assert saved['summary_ready'] and saved['summary_text'] == '复盘总结'
    (store.game_dir('broken')).mkdir()
    (store.game_dir('broken') / 'replay.json').write_text('bad')
    assert len(store.list_games()) == 1


def test_nonlocal_bind_rejected(tmp_path):
    with pytest.raises(ValueError, match='本机'):
        UndercoverWebServer(SimpleNamespace(root=tmp_path), host='0.0.0.0')
