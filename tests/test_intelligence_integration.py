"""新能力真实文件与现有入口的集成验证（外部服务使用确定性替身）。"""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from iris.config.loader import make_config_bundle
from iris.app._cli_main import build_parser
from iris.decisions.schema import Decision
from iris.decisions.store import DecisionStore
from iris.decisions.cli import execute
from iris.decisions.retrieval import decision_hits
from iris.intelligence.context import documents
from iris.retrieval.searcher import RetrievalHit


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / 'SOURCE'
    wiki = tmp_path / 'WIKI'
    source.mkdir()
    (wiki / '03-项目').mkdir(parents=True)
    (wiki / '04-人物').mkdir()
    (source / '20260921-巡检.md').write_text('---\ntitle: 巡检\ndate: 2026-09-21\n---\n决定采用双路巡检\n接口权责待定\n张三负责巡检')
    (wiki / '03-项目' / '项目-巡检.md').write_text('---\ntitle: 巡检\nupdated_at: 2026-08-01\n---\n巡检方案')
    (wiki / '04-人物' / '人物-张三.md').write_text('---\ntitle: 张三\n---\n参与巡检项目')
    return make_config_bundle(tmp_path, {'version': '1', 'intelligence': {'okr_source': 'OKR.md'}},
        {'default_source': 'main', 'sources': {'main': {'path': str(source)}}}, {}, {'wiki_root': str(wiki)})


def record(bundle):
    store = DecisionStore(bundle.root / 'data/decisions')
    row = store.save(Decision(title='巡检双路', outcome='决定采用双路巡检', decided_at='2026-09-21',
        sources=[{'path': '20260921-巡检.md', 'quote': '决定采用双路巡检', 'line_start': 1}],
        review_status='approved', owners=['张三'], tags=['巡检'], related_krs=['KR2.1']))
    return store, row


def args(*argv):
    return build_parser().parse_args(list(argv))


@pytest.mark.parametrize('command', ['decisions', 'briefing', 'signals', 'okr-evidence', 'okr-check'])
def test_cli_registration(command):
    from iris.app.cli.handlers import COMMAND_HANDLERS
    assert command in COMMAND_HANDLERS
    assert args(command).command == command


def test_decision_cli_roundtrip(bundle, tmp_path):
    store, row = record(bundle)
    assert execute(args('decisions', 'list', '--kr', 'KR2'), bundle)['decisions']
    assert execute(args('decisions', 'search', '双路'), bundle)['decisions']
    assert execute(args('decisions', 'show', row['decision_id']), bundle)['outcome'] == '决定采用双路巡检'
    execute(args('decisions', 'update', row['decision_id'], '--status', 'implemented'), bundle)
    output = tmp_path / 'report.md'
    execute(args('decisions', 'report', '--output', str(output)), bundle)
    assert 'implemented' in output.read_text()
    execute(args('decisions', 'export-wiki'), bundle)
    assert list((bundle.root / 'WIKI/05-决策').glob('*.md'))


def test_cli_manual_and_review(bundle, tmp_path):
    file = tmp_path / 'manual.json'
    file.write_text(json.dumps({'title': '人工决策', 'outcome': '采用新方案', 'decided_at': '2026-09-21'}))
    execute(args('decisions', 'add', '--input-file', str(file)), bundle)
    store = DecisionStore(bundle.root / 'data/decisions')
    candidate = store.stage(Decision(title='另一项', outcome='取消旧方案', decided_at='2026-09-21'), '待确认')
    assert execute(args('decisions', 'pending'), bundle)['candidates']
    execute(args('decisions', 'approve', candidate['candidate_id'], '--patch', '{"owners":["张三"]}'), bundle)
    assert store.list()[0]['review_status'] == 'approved'


def test_decisions_retrieval_current_source(bundle):
    store, row = record(bundle)
    assert decision_hits(bundle, '巡检')[0].chunk_id == row['decision_id']
    (bundle.root / 'SOURCE/20260921-巡检.md').write_text('已删除的原文不再存在')
    assert decision_hits(bundle, '巡检') == []


def test_graph_projection(bundle):
    from iris.wiki.graph import WikiGraph
    from iris.decisions.graph import add_decisions
    from iris.wiki._graph_engine import GraphNode
    store, row = record(bundle)
    graph = WikiGraph(bundle)
    graph._nodes = {'person': GraphNode(id='person', title='张三', page_type='person'),
                    'project': GraphNode(id='project', title='巡检', page_type='project')}
    add_decisions(graph, store.list())
    assert {e.relation for e in graph._edges} == {'owns_decision', 'relates_to'}
    assert graph.neighbors(row['decision_id'])
    add_decisions(graph, [])
    assert row['decision_id'] not in graph._nodes


def hits():
    return [RetrievalHit('1', 2., '巡检', '20260921-巡检.md', [], '决定采用双路巡检', 1, 1)]


def test_recent_docs_and_person(bundle):
    from iris.briefing.doc_retriever import RecentDocsRetriever
    from iris.briefing.person_retriever import PersonRetriever
    retriever = SimpleNamespace(search=Mock(return_value=SimpleNamespace(hits=hits())))
    recent = RecentDocsRetriever(bundle, retriever).retrieve('巡检', today=date(2026,9,21))
    assert recent[0]['path'] == '20260921-巡检.md'
    person = PersonRetriever(bundle, retriever).retrieve(['张三', '不存在'], '巡检', date(2026,9,21))
    assert person['张三']['person_pages']
    assert not person['不存在']['person_pages']
    assert not person['不存在']['recent_documents']


def test_stale_retrieval_snippet_dropped(bundle):
    from iris.briefing.doc_retriever import RecentDocsRetriever
    retriever = SimpleNamespace(search=Mock(return_value=SimpleNamespace(hits=[
        RetrievalHit('1', 2., '巡检', '20260921-巡检.md', [], '旧索引中才有的内容', 1, 1)])))
    assert RecentDocsRetriever(bundle, retriever).retrieve('巡检', today=date(2026,9,21)) == []


def test_freshness_checker(bundle):
    from iris.wiki.freshness import WikiFreshnessChecker
    source = bundle.root / 'SOURCE'
    for n in range(2):
        (source / f'20260921-巡检{n}.md').write_text('巡检进展')
    fakehits = hits() + [RetrievalHit(str(n), 1., '巡检', f'20260921-巡检{n}.md', [], '巡检进展', 1, 1) for n in range(2)]
    retriever = SimpleNamespace(search=Mock(return_value=SimpleNamespace(hits=fakehits)))
    result = WikiFreshnessChecker(bundle, retriever).run(documents(source), today=date(2026,9,21))
    assert result and result[0]['score'] == 0


def test_feed_cache_to_signal(bundle):
    from iris.signals.feed_cache import cache_topics, recent_topics
    from iris.signals.detector import SignalDetector
    from iris.intelligence.context import local_today
    topic = SimpleNamespace(topic_id='t1', title='新策略', source_chats=[SimpleNamespace(name='群1'), SimpleNamespace(name='群2')])
    cache_topics(bundle.root / 'data', [topic])
    recent = recent_topics(bundle.root / 'data', local_today())
    signals = SignalDetector().undocumented(recent, [])
    assert signals[0].kind == 'UNDOCUMENTED_TOPIC'


def test_signals_all_channels_no_network(bundle, monkeypatch):
    from iris.signals.service import run
    from iris.wiki.freshness import WikiFreshnessChecker
    record(bundle)
    monkeypatch.setattr(WikiFreshnessChecker, 'run', lambda *a, **k: [])
    monkeypatch.setattr('iris.okr_evidence.service.evidence_context', lambda b: ({'KR1':'巡检'}, {'KR1':[]}))
    result = run(bundle, dry_run=True)
    assert result['delivery']['status'] == 'dry_run'
    assert result['signals'][0]['kind'] in {'KR_EVIDENCE_GAP','OPEN_DECISION_STALE'}


def test_okr_source_load_and_check(bundle, monkeypatch):
    from iris.okr_evidence.service import load_krs, check
    (bundle.root / 'SOURCE/OKR.md').write_text('## O1：巡检\n### KR1：召回率80%')
    cycle, krs = load_krs(bundle)
    assert krs == {'O1-KR1': '召回率80%'}
    llm = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(text='证据不足')))
    retriever = SimpleNamespace(retrieve=Mock(return_value=[]))
    result = check(bundle, llm=llm, retriever=retriever)
    assert result['results'][0]['supplemented']
    retriever.retrieve.assert_called_once()


def test_okr_log_sufficient_skips_search(bundle, monkeypatch):
    from iris.okr_evidence.service import check
    from iris.intelligence.context import local_today
    rows = [{'doc_path':str(n), 'doc_date':local_today().isoformat(), 'evidence_snippet':'巡检'} for n in range(3)]
    monkeypatch.setattr('iris.okr_evidence.service.evidence_context', lambda b: ({'KR1':'巡检'}, {'KR1':rows}))
    retriever = Mock()
    llm = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(text='进展')))
    assert not check(bundle, llm=llm, retriever=retriever)['results'][0]['supplemented']
    retriever.retrieve.assert_not_called()


def test_daily_intelligence_order_and_failure(bundle, monkeypatch):
    from iris.app.cli._handlers._system import _daily_intelligence
    calls = []
    def tagging(b):
        calls.append('tag')
        raise OSError('offline')
    def signalling(b):
        calls.append('signal')
        return {'status':'ok'}
    monkeypatch.setattr('iris.okr_evidence.service.tag', tagging)
    monkeypatch.setattr('iris.signals.service.run', signalling)
    result = _daily_intelligence(bundle)
    assert calls == ['tag', 'signal']
    assert result['okr_evidence']['status'] == 'error'
    assert result['signals']['status'] == 'ok'
