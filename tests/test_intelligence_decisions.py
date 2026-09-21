"""决策隔离、证据、并发和发布故障的行为测试。"""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from iris.core.exceptions import IrisValueError
from iris.decisions.schema import Decision
from iris.decisions.store import DecisionStore
from iris.decisions.extractor import DecisionExtractor


def decision(**changes):
    fields = {'title': '采用双路方案', 'outcome': '决定采用规则和模型双路', 'decided_at': '2026-09-21',
              'sources': [{'path': '05-会议纪要/策略.md', 'title': '策略', 'quote': '决定采用规则和模型双路'}],
              'review_status': 'approved'}
    fields.update(changes)
    return Decision(**fields)


@pytest.fixture
def store(tmp_path):
    return DecisionStore(tmp_path / 'decisions')


def test_pending_invisible(store):
    candidate = store.stage(decision(review_status='pending'), '日期待确认')
    assert store.list() == []
    result = store.review(candidate['candidate_id'], approve=True)
    assert result['decision_id'].endswith('0001')
    assert len(store.list()) == 1
    assert store.pending() == []


def test_reject_stays_invisible(store):
    candidate = store.stage(decision(), '歧义')
    store.review(candidate['candidate_id'], approve=False, reason='仍在讨论')
    assert not store.list() and not store.pending()


def test_repeat_review_rejected(store):
    candidate = store.stage(decision(), '歧义')
    store.review(candidate['candidate_id'], approve=True)
    with pytest.raises(IrisValueError):
        store.review(candidate['candidate_id'], approve=True)


def test_merge_sources_idempotent(store):
    first = store.save(decision())
    other = decision(sources=[{'path': '另一份纪要.md', 'quote': '决定采用规则和模型双路'}])
    assert store.save(other)['decision_id'] == first['decision_id']
    store.save(other)
    assert len(store.list()[0]['sources']) == 2


def test_changed_conclusion_new_record(store):
    store.save(decision())
    store.save(decision(outcome='决定取消双路方案'))
    assert len(store.list()) == 2


def test_concurrent_ids(store):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda n: store.save(decision(outcome=f'决策{n}')), range(16)))
    assert len({r['decision_id'] for r in results}) == 16
    assert len(store.list()) == 16


@pytest.mark.parametrize('changes', [{'title': ''}, {'status': 'bad'}, {'decided_at': '2026-99-21'},
    {'sources': [{'path': '../escape.md'}]}, {'sources': [{'path': '调薪方案.md'}]},
    {'title': '绩效评价'}, {'review_at': 'yesterday'}])
def test_invalid_rejected(store, changes):
    with pytest.raises(IrisValueError):
        store.save(decision(**changes))


def test_unreviewed_save_rejected(store):
    with pytest.raises(IrisValueError):
        store.save(decision(review_status='pending'))


def test_atomic_publish_failure(store, monkeypatch):
    store.save(decision())
    from iris.intelligence import storage
    monkeypatch.setattr(storage, 'atomic_write_text', Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        store.save(decision(outcome='另一决策'))
    assert len(store.list()) == 1


def test_unknown_version_read_write_refused(store):
    store.root.mkdir()
    (store.root / 'index.json').write_text('{"schema_version": 99}')
    with pytest.raises(IrisValueError):
        store.save(decision())


def test_migration_backup_and_repeat(store):
    store.root.mkdir()
    row = decision().to_dict()
    row['decision_id'] = 'DEC-20260920-0001'
    (store.root / 'index.json').write_text(json.dumps({'decisions': [row]}))
    store.migrate()
    store.migrate()
    assert len(store.list()) == 1
    assert len(list((store.root / 'backups').glob('*.json'))) == 1


def llm_responses(*items):
    return SimpleNamespace(generate=Mock(side_effect=[SimpleNamespace(text=json.dumps(i, ensure_ascii=False)) for i in items]))


def candidate():
    return {'title': '双路方案', 'outcome': '决定采用双路', 'quote': '决定采用双路', 'decided_at': '2026-09-21'}


def extract(store, llm, text='决定采用双路', path='纪要.md'):
    return DecisionExtractor(store, llm).extract(text, path=path, title='纪要', decided_at='2026-09-21')


def test_high_confidence_auto(store):
    llm = llm_responses([candidate()], [{'index': 0, 'verdict': 'approved', 'supported_fields': ['outcome', 'decided_at']}])
    assert len(extract(store, llm)['saved']) == 1


def test_uncertain_pending(store):
    assert len(extract(store, llm_responses([candidate()], []))['pending']) == 1
    assert not store.list()


def test_hallucinated_quote_discarded(store):
    result = extract(store, llm_responses([candidate()], []), text='只是讨论')
    assert result['discarded'] == 1
    assert not store.pending()


def test_sensitive_no_llm(store):
    llm = Mock()
    assert extract(store, llm, path='调薪方案.md')['status'] == 'refused_sensitive'
    llm.generate.assert_not_called()


def test_retry_once(store):
    llm = SimpleNamespace(generate=Mock(side_effect=OSError('offline')))
    assert extract(store, llm)['status'] == 'skipped'
    assert llm.generate.call_count == 2


def test_review_failure_pending(store):
    llm = llm_responses([candidate()])
    llm.generate.side_effect = [SimpleNamespace(text=json.dumps([candidate()])), OSError('offline')]
    assert len(extract(store, llm)['pending']) == 1


def test_optional_fields_not_invented(store):
    item = {**candidate(), 'owners': ['张三'], 'review_at': '2026-10-01'}
    result = extract(store, llm_responses([item], []))
    assert result['pending'][0]['decision']['owners'] == []
    assert result['pending'][0]['decision']['review_at'] is None
