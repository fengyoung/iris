"""本机 HTTP 集成契约：安全边界、广播、资源配额和复盘持久化。"""
import http.client
import json
import socket
import struct
import threading
import time
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
    assert request(web, 'POST', '/api/game/test/advance', '')[0] == 409
    session.phase = 'waiting'
    assert request(web, 'POST', '/api/game/test/advance', '')[0] == 200
    assert session.advance_event.is_set()
    assert request(web, 'POST', '/api/game/test/advance', '')[0] == 409
    assert request(web, 'POST', '/api/game/test/abort', '')[0] == 200
    assert session.cancel_event.is_set()


def test_game_status_and_finished_controls(web):
    session = GameSession('status', auto_advance=False, phase='waiting', round_no=2)
    web.state.games['status'] = session
    status, body = request(web, 'GET', '/api/game/status')
    data = json.loads(body)
    assert status == 200 and data['phase'] == 'waiting' and data['round_no'] == 2
    assert not data['auto_advance']
    session.finished = True
    session.replay_ready = True
    assert request(web, 'POST', '/api/game/status/abort', '')[0] == 409
    assert request(web, 'POST', '/api/game/status/advance', '')[0] == 409
    assert json.loads(request(web, 'GET', '/api/game/status')[1])['replay_ready']


def test_summary_failure_and_skipped_state(web):
    store = web.state.replay_store
    result = GameResult(image_civilian='a', image_spy='b', players=[], spy_keys=[])
    store.save_result('skipped', result, [], 'civilian.png', 'spy.png')
    assert store.load_game('skipped')['summary_status'] == 'skipped'
    store.save_result('failed', result, [], 'civilian.png', 'spy.png', 'base_model/judge')
    assert store.load_game('failed')['summary_status'] == 'pending'
    llm = Mock()
    llm.generate_as.side_effect = RuntimeError('timeout')
    store._generate_summary('failed', llm, 'base_model', 'judge')
    data = store.load_game('failed')
    assert data['summary_status'] == 'failed' and not data['summary_ready']


def test_cancelled_game_still_records_summary_pending(web):
    """取消的对局也要出总结：已跑完的轮次仍有分析价值，winner=cancelled 已标明是部分复盘。"""
    result = GameResult(image_civilian='a', image_spy='b', players=[], spy_keys=[], winner='cancelled')
    store = web.state.replay_store
    store.save_result('cancelled-sum', result, [], 'c.png', 's.png', 'base_model/judge')
    assert store.load_game('cancelled-sum')['summary_status'] == 'pending'


def test_summary_prompt_names_cancelled_as_partial():
    """裁判提示词要说明这是被终止的部分复盘，不能把 cancelled 原样丢给模型。"""
    from iris.games.replay_store import _build_summary_prompt
    prompt = _build_summary_prompt({
        'setup': {'players': [], 'spy_keys': []},
        'result': {'winner': 'cancelled', 'total_rounds': 1},
        'rounds': [],
    })
    assert '部分复盘' in prompt
    assert 'cancelled' not in prompt


def test_summary_model_falls_back_to_referee(web):
    """回归：前端只发 referee_model（裁判改成文本框后不再发 summary_model），
    而后端只读 req["summary_model"]——于是 summary_model_id 恒为空、复盘一律记
    summary_status=skipped，总结从未生成过。总结必须挂在裁判模型上。
    """
    token = web.state.replay_store.save_upload(image_bytes(), 'test.png')
    payload = {'players': [{'role': 'base_model', 'model_id': str(i)} for i in range(3)],
               'image_civilian': token, 'image_spy': token, 'referee_model': 'judge-x'}
    result = GameResult(image_civilian='a', image_spy='b', players=[], spy_keys=[], winner='cancelled')
    with patch('iris.games.web_server.UndercoverGame') as game:
        game.return_value.run.return_value = result
        status, body = request(web, 'POST', '/api/game/start', json.dumps(payload))
        assert status == 200
        session = web.state.games[json.loads(body)['game_id']]
        assert session.referee_model == 'judge-x'
        assert session.summary_model_id == 'judge-x'


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


def _rst_close(sock):
    """SO_LINGER=0 让 close() 发 RST 而非 FIN。

    浏览器取消 SSE 时正是如此：接收缓冲里还有服务端推来的未读事件，
    进程一关内核就回 RST，而不是礼貌的 FIN。
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
    sock.close()


def test_connection_errors_are_silenced(web, capsys):
    """连接类异常必须静默——默认 handle_error 会 traceback.print_exc。

    直接构造异常调用，不依赖网络时序：这样测的正是分类逻辑本身。
    """
    for exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
        try:
            raise exc_type(54, 'reset by peer')
        except exc_type:
            web.handle_error(None, ('127.0.0.1', 1))
    assert 'Traceback' not in capsys.readouterr().err


def test_client_reset_does_not_dump_traceback(web, capsys):
    """端到端：客户端 RST 断开后，控制台不该出现整栈。"""
    host, port = web.server_address
    for _ in range(2):  # 连上即断
        _rst_close(socket.create_connection((host, port), timeout=3))
    s = socket.create_connection((host, port), timeout=3)  # 发一半就断
    s.sendall(b'GET / HTTP/1.1\r\nHost: x')
    _rst_close(s)
    s = socket.create_connection((host, port), timeout=3)  # 发完整请求但不等响应
    s.sendall(b'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n')
    _rst_close(s)

    err = ''
    deadline = time.time() + 3
    while time.time() < deadline and 'Traceback' not in err:
        err += capsys.readouterr().err
        time.sleep(0.05)
    assert 'Traceback' not in err, f'客户端断开不应打印 traceback：{err[:300]}'


def test_unexpected_error_still_reported(web, capsys):
    """只静默连接类异常——真故障仍要打出来，别跟着一起吞掉。"""
    try:
        raise RuntimeError('boom')
    except RuntimeError:
        web.handle_error(None, ('127.0.0.1', 1))
    assert 'RuntimeError' in capsys.readouterr().err


def test_referee_default_is_official_and_mutually_exclusive_with_players(web):
    """裁判默认走官方 deepseek-flash；裁判与玩家互斥由后端兜底，不依赖前端禁用。

    前端会把裁判占用的模型置灰，但那只是交互约定——直接构造请求仍须被拒。
    """
    from iris.games import web_server
    assert web_server._DEFAULT_REFEREE_MODEL == 'deepseek-flash'
    # 三个落点都要跟着常量走，改漏一处就会出现「前端显示 A、后端实跑 B」
    assert web_server.StartRequest.model_fields['referee_model'].default == 'deepseek-flash'
    assert web_server.GameSession.__dataclass_fields__['referee_model'].default == 'deepseek-flash'

    token = web.state.replay_store.save_upload(image_bytes(), 'test.png')
    payload = {'players': [{'role': 'base_model', 'model_id': str(i)} for i in range(3)],
               'image_civilian': token, 'image_spy': token, 'referee_model': '1'}
    status, body = request(web, 'POST', '/api/game/start', json.dumps(payload))
    assert status == 400 and '不能与参与玩家相同' in json.loads(body)['error']

    # 同一模型换到裁判没占用的角色则不冲突（裁判固定以 base_model 调用）
    payload['referee_model'] = '9'
    assert request(web, 'POST', '/api/game/start', json.dumps(payload))[0] != 400


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


def test_resume_preserves_mode_and_rejects_duplicate(web):
    store = web.state.replay_store
    game_id = store.new_game_id()
    players = [{"role": "base_model", "model_id": f"m{i}"} for i in range(3)]
    keys = [f"base_model/m{i}" for i in range(3)]
    store.save_checkpoint(game_id, {"players": players, "auto_advance": False}, [], keys, keys[:1], keys)
    with patch('iris.games.web_server._run_game') as worker:
        assert request(web, 'POST', f'/api/game/{game_id}/resume', '')[0] == 200
        assert web.state.games[game_id].auto_advance is False
        assert request(web, 'POST', f'/api/game/{game_id}/resume', '')[0] == 409
        assert worker.call_count == 1
    assert web.state.game_slots.acquire(blocking=False)
    assert not web.state.game_slots.acquire(blocking=False)
    web.state.game_slots.release()
    web.state.game_slots.release()


def test_resume_rejects_legacy_without_consuming_slot(web):
    store = web.state.replay_store
    game_id = store.new_game_id()
    directory = store.ensure_game_dir(game_id)
    (directory / 'checkpoint.json').write_text('{"completed_rounds": [{"round_no": 1}]}')
    assert request(web, 'POST', f'/api/game/{game_id}/resume', '')[0] == 409
    assert game_id not in web.state.games
    assert web.state.game_slots.acquire(blocking=False)
    assert web.state.game_slots.acquire(blocking=False)
    web.state.game_slots.release()
    web.state.game_slots.release()


def test_resume_runs_engine_with_saved_history_and_persists_result(web):
    from iris.games.undercover import RoundRecord
    store = web.state.replay_store
    game_id = store.new_game_id()
    players = [{"key": f"base_model/m{i}", "role": "base_model", "model_id": f"m{i}"} for i in range(3)]
    keys = [p["key"] for p in players]
    history = RoundRecord(1, descriptions={keys[0]: "恢复前的公开描述"})
    store.save_checkpoint(game_id, {"players": players, "auto_advance": False},
                          [history.to_dict()], keys, keys[:1], keys)
    with patch('iris.games.web_server.UndercoverGame') as engine, patch.object(store, 'trigger_summary'):
        engine.return_value.run.return_value = GameResult(
            image_civilian='', image_spy='', players=keys, spy_keys=keys[:1],
            rounds=[history], winner='stalemate')
        assert request(web, 'POST', f'/api/game/{game_id}/resume', '')[0] == 200
        session = web.state.games[game_id]
        session.thread.join(3)
        assert not session.thread.is_alive()
        assert engine.return_value.run.call_args.kwargs['checkpoint']['completed_rounds'] == [history.to_dict()]
        assert engine.call_args.kwargs['advance_event'] is session.advance_event
    assert store.load_game(game_id)['rounds'] == [history.to_dict()]
    assert request(web, 'POST', f'/api/game/{game_id}/resume', '')[0] == 400


def test_resume_rejects_versioned_incomplete_history(web):
    store = web.state.replay_store
    game_id = store.new_game_id()
    keys = [f"base_model/m{i}" for i in range(3)]
    players = [{"role": "base_model", "model_id": f"m{i}"} for i in range(3)]
    store.save_checkpoint(game_id, {"players": players}, [{"round_no": 1}], keys, keys[:1], keys)
    assert request(web, 'POST', f'/api/game/{game_id}/resume', '')[0] == 409
    assert game_id not in web.state.games
