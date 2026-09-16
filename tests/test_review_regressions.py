"""审查发现的跨模块边界回归：全部使用临时数据和模拟模型。"""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from io import BytesIO
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from iris.core.exceptions import IrisRuntimeError
from iris.games.replay_store import ReplayStore, ReplayStoreError
from iris.games.web_server import EventLog, StartRequest, _Handler, _validate_start_request
from iris.ingest.chunker import MarkdownChunker
from iris.ingest.scanner import MarkdownScanner
from iris.ingest.watcher import SourceWatcher
from iris.llm.cache import LLMResponseCache
from iris.llm.service import LLMService
from iris.retrieval.embedder import EmbedderError, TextEmbedder
from iris.retrieval.enhanced import _load_vector_indexes, _rrf_fuse
from iris.retrieval.searcher import RetrievalHit
from iris.retrieval.vector_index import VectorIndex, VectorIndexModelMismatchError


def source_config(root):
    source = root / 'source'
    source.mkdir(exist_ok=True)
    return SimpleNamespace(root=root, app={}, data_source={
        'default_source': 's', 'sources': {'s': {'path': str(source)}}, 'ingestion': {}})


@pytest.mark.parametrize('token', ['../../../marker.txt', '/etc/hosts', '../x', '', None])
def test_upload_rejects_external_paths(tmp_path, token):
    (tmp_path / 'marker.txt').write_text('public test marker')
    assert ReplayStore(tmp_path / 'data').upload_path(token) is None


def test_upload_rejects_symlink_escape(tmp_path):
    store = ReplayStore(tmp_path / 'data')
    marker = tmp_path / 'marker.png'
    marker.write_bytes(b'marker')
    token = 'a' * 32 + '.png'
    (tmp_path / 'data/games/uploads' / token).symlink_to(marker)
    assert store.upload_path(token) is None
    with pytest.raises(ReplayStoreError):
        store.game_dir('../outside')


def test_valid_image_roundtrip_and_invalid_upload(tmp_path):
    import fitz
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 2, 2), False)
    data = pix.tobytes('png')
    store = ReplayStore(tmp_path / 'data')
    token = store.save_upload(data, 'test.png')
    assert store.upload_path(token).read_bytes() == data
    with pytest.raises(ValueError):
        store.save_upload(b'not an image', 'test.png')
    with pytest.raises(ValueError):
        store.save_upload(data, 'test.html')


def test_incremental_add_delete_restart(tmp_path):
    config = source_config(tmp_path)
    source = tmp_path / 'source'
    (source / 'a.md').write_text('# A\nalpha')
    scanner = MarkdownScanner(config)
    scanner.write_summary(scanner.scan_source_by_name('s'))
    chunker = MarkdownChunker(config)
    chunker.write_summary(chunker.build_source_chunks('s'))
    (source / 'b.md').write_text('# B\nbeta')
    chunker.write_summary(chunker.build_source_chunks('s', incremental=True))
    (source / 'b.md').unlink()
    restarted = MarkdownChunker(config)
    result = restarted.build_source_chunks('s', incremental=True)
    assert {c.relative_path for c in result.chunks} == {'a.md'}


def test_chunk_publication_rejects_stale_writer(tmp_path):
    config = source_config(tmp_path)
    (tmp_path / 'source/a.md').write_text('# A\nalpha')
    chunker = MarkdownChunker(config)
    first = chunker.build_source_chunks('s')
    stale = chunker.build_source_chunks('s')
    chunker.write_summary(first)
    with pytest.raises(IrisRuntimeError, match='其他构建'):
        chunker.write_summary(stale)


def test_vector_publication_rejects_stale_writer(tmp_path):
    path = tmp_path / 'index.json'
    first = VectorIndex(path)
    first.upsert('a', [1., 0.])
    first.save()
    stale = VectorIndex(path)
    assert stale.load()
    first.upsert('b', [0., 1.])
    first.save()
    with pytest.raises(VectorIndexModelMismatchError, match='其他构建'):
        stale.save()
    loaded = VectorIndex(path)
    assert loaded.load() and loaded.exists('b')


def test_reader_finishes_before_old_generation_cleanup(tmp_path):
    path = tmp_path / 'index.json'
    writer = VectorIndex(path)
    writer.upsert('a', [1., 0.])
    writer.save()
    reader = VectorIndex(path)
    entered, release, saving = Event(), Event(), Event()
    original = reader._load_binary

    def read(data_dir):
        entered.set()
        assert release.wait(3)
        return original(data_dir)

    def save():
        saving.set()
        writer.upsert('b', [0., 1.])
        writer.save()

    with patch.object(reader, '_load_binary', side_effect=read), ThreadPoolExecutor(2) as pool:
        future = pool.submit(reader.load)
        assert entered.wait(3)
        writing = pool.submit(save)
        assert saving.wait(3)
        release.set()
        assert future.result(3)
        writing.result(3)
    assert reader.exists('a')


@pytest.mark.parametrize('entries', [[], [{'index': 1, 'embedding': [1., 2.]}],
    [{'index': 0, 'embedding': [1., 2.]}, {'index': 0, 'embedding': [3., 4.]}],
    [{'index': 0, 'embedding': [float('nan')]}, {'index': 1, 'embedding': [2.]}]])
def test_embedding_incomplete_response_never_cached(entries):
    embedder = TextEmbedder('https://example.invalid', 'fake', 'model')
    embedder._post_json = Mock(return_value={'data': entries})
    with pytest.raises(EmbedderError):
        embedder.embed(['A', 'B'])
    assert embedder._get_cached('A') is None


def test_model_mismatch_is_disabled_at_query_load(tmp_path):
    index = VectorIndex(tmp_path / 'data/metadata/s_vector_index')
    index.set_embedder_model('old')
    index.upsert('a', [1., 0.])
    index.save()
    cfg = SimpleNamespace(root=tmp_path, data_source={'sources': {'s': {}}},
                          llm={'embedding': {'model': 'new'}})
    assert _load_vector_indexes(cfg) == {}
    with pytest.raises(VectorIndexModelMismatchError):
        index.search([1., 0., 0.])


def hit(cid, score=0):
    return RetrievalHit(cid, score, cid, cid + '.md', [], '正文证据', 1, 2)


def test_rrf_complete_evidence_and_symmetric_weights():
    results = _rrf_fuse([hit('lex', 1)], {'vec': .8}, top_k=2,
                        vector_hits={'vec': hit('vec')}, bm25_bonus=0)
    assert len(results) == 2
    assert results[0].score == results[1].score
    assert all(h.relative_path and h.content_preview for h in results)
    assert not _rrf_fuse([], {'missing': .9}, top_k=1)


def test_cache_respects_parameters_and_model_configuration(tmp_path):
    service = object.__new__(LLMService)
    service._config = SimpleNamespace(llm={'model': 'one'})
    service._source = 'test'
    service._cache = LLMResponseCache(tmp_path)
    service._provider = Mock()
    service._provider.generate.return_value = SimpleNamespace(
        text='result', model='one', provider='fake', selected_role='base_model',
        api_base_url='', matched_rule='', prompt_tokens=1, completion_tokens=1)
    service.generate('same', temperature=0, max_tokens=5)
    service.generate('same', temperature=0, max_tokens=500)
    service.generate('same', temperature=0, max_tokens=500, extra_body={'format': 'json'})
    service.generate('same', temperature=0, max_tokens=500, extra_body={'format': 'json'})
    assert service._provider.generate.call_count == 3
    service._config.llm['model'] = 'two'
    service.generate('same', temperature=0, max_tokens=500)
    assert service._provider.generate.call_count == 4


def test_watcher_delivers_last_edit(tmp_path):
    watcher = SourceWatcher(source_config(tmp_path))
    with patch.object(watcher, 'snapshot', side_effect=[{'s': {'a.md': x}} for x in [1., 2., 3., 3.]]), \
         patch('iris.ingest.watcher.time.monotonic', side_effect=[10., 10.5, 13.]):
        assert [len(watcher.poll()) for _ in range(4)] == [0, 1, 0, 1]


def test_event_log_broadcast_reconnect_and_end():
    events = EventLog()
    events.put({'type': 'game_start'})
    first, _ = events.read(0, 0)
    assert events.read(0, 0)[0] == first
    events.put({'type': 'round_start'})
    events.put(None)
    resumed, closed = events.read(1, 0)
    assert closed and [event['type'] for _, event in resumed] == ['round_start', 'stream_end']
    assert events.read(1, 0) == (resumed, closed)


def test_event_log_overflow_is_explicit():
    events = EventLog()
    for _ in range(4100):
        events.put({'type': 'event'})
    entries, closed = events.read(0, 0)
    assert closed and entries[0][1]['type'] == 'resync_required'


@pytest.mark.parametrize('value', [None, [], {}, {'players': 'bad'}])
def test_invalid_request_returns_validation_error(value):
    assert _validate_start_request(value)


def test_start_limits_and_seed_zero():
    request = dict(players=[{'role': 'base_model', 'model_id': str(i)} for i in range(3)],
                   image_civilian='a' * 32 + '.png', image_spy='b' * 32 + '.png', seed=0)
    assert StartRequest.model_validate(request).seed == 0
    assert _validate_start_request({**request, 'spy_count': '1'})
    assert _validate_start_request({**request, 'auto_advance': 'false'})
    assert _validate_start_request({**request, 'image_spy': '../x.png'})


def test_request_body_bounds_and_origin():
    handler = object.__new__(_Handler)
    handler.headers = Message()
    handler.headers['Content-Length'] = str(21 * 1024 * 1024)
    handler.rfile = BytesIO(b'')
    with pytest.raises(ValueError, match='20 MB'):
        handler._read_body()
    handler.server = SimpleNamespace(server_address=('127.0.0.1', 7862))
    handler._json = Mock()
    handler.headers['Host'] = 'attacker.invalid:7862'
    assert not handler._check_origin()
    assert handler._json.call_args.args[0] == 403


def test_game_cancel_stops_subsequent_calls_and_keeps_partial_round():
    from test_games_undercover import _make_game
    cancelled = Event()
    game, llm = _make_game(cancel_event=cancelled)

    def response(*args, **kwargs):
        cancelled.set()
        return '描述：第一段描述'

    llm.generate_multimodal_as.side_effect = response
    result = game.run()
    assert result.winner == 'cancelled'
    assert len(result.rounds) == 1
    assert len(result.rounds[0].speeches) == 1
    assert llm.generate_multimodal_as.call_count == 1
    llm.generate_as.assert_not_called()


def test_retrieval_golden_set_metrics(tmp_path):
    from iris.evaluation.retrieval_eval import evaluate_retrieval
    config = source_config(tmp_path)
    (tmp_path / 'source/knowledge.md').write_text('# 项目状态\n银河项目已经完成上线验收')
    chunker = MarkdownChunker(config)
    summary = chunker.build_source_chunks('s')
    chunker.write_summary(summary)
    cases = tmp_path / 'cases.json'
    cases.write_text(json.dumps({'cases': [{
        'query': '银河项目已经完成上线验收',
        'expected_chunk_ids': [summary.chunks[0].chunk_id],
    }]}, ensure_ascii=False))
    report = evaluate_retrieval(config, cases, top_k=3)
    assert report.recall_at_k == 1
    assert report.citation_completeness == 1
    assert report.stale_evidence_rate == 0
    assert not report.failures


def test_retrieval_golden_set_rejects_empty_cases(tmp_path):
    from iris.core.exceptions import IrisValueError
    from iris.evaluation.retrieval_eval import evaluate_retrieval
    cases = tmp_path / 'cases.json'
    cases.write_text('{"cases": []}')
    with pytest.raises(IrisValueError):
        evaluate_retrieval(source_config(tmp_path), cases)
