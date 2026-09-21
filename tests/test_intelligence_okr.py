"""增量证据日志、周期和缓存失效行为。"""
from unittest.mock import Mock
import pytest
from iris.core.exceptions import IrisValueError
from iris.okr_evidence.store import OKREvidenceStore
from iris.okr_evidence.kr_vector_index import KRVectorIndex, cosine
from iris.okr_evidence.tagger import OKREvidenceTagger
from iris.intelligence.context import settings


class Embedder:
    model = 'test-v1'
    def __init__(self):
        self.calls = 0
    def embed(self, texts):
        self.calls += 1
        return [[1., 0.] if '巡检' in t else [0., 1.] for t in texts]


@pytest.fixture
def env(tmp_path):
    source = tmp_path / 'SOURCE'
    source.mkdir()
    (source / '20260921-周报.md').write_text('巡检召回率达到76%')
    store = OKREvidenceStore(tmp_path / 'evidence')
    vectors = KRVectorIndex(tmp_path / 'vectors')
    embedder = Embedder()
    return source, store, vectors, embedder


def run(env, cycle='Q3', krs=None, **kw):
    root, store, vectors, embedder = env
    return OKREvidenceTagger(store, vectors, embedder, **kw).run_incremental(root, cycle, krs or {'KR1': '巡检召回率80%'})


def test_incremental_reuses_embeddings(env):
    assert run(env)['processed'] == 1
    before = env[3].calls
    assert run(env)['processed'] == 0
    assert env[3].calls == before


def test_changed_document_replaces_evidence(env):
    run(env)
    (env[0] / '20260921-周报.md').write_text('与巡检无关的新版本，巡检召回率79%')
    run(env)
    records = env[1].get('Q3', 'KR1')
    assert len(records) == 1
    assert '79%' in records[0]['evidence_snippet']


def test_deleted_document_removed(env):
    run(env)
    (env[0] / '20260921-周报.md').unlink()
    run(env)
    assert env[1].get('Q3', 'KR1') == []


def test_sensitive_rename_removed(env):
    run(env)
    (env[0] / '20260921-周报.md').rename(env[0] / '调薪方案.md')
    run(env)
    assert env[1].get('Q3', 'KR1') == []


def test_cycle_isolation(env):
    run(env)
    run(env, cycle='Q4')
    assert len(env[1].get('Q3', 'KR1')) == len(env[1].get('Q4', 'KR1')) == 1


def test_kr_changed_invalidates_old(env):
    run(env)
    run(env, krs={'KR1': '客服满意度'})
    assert env[1].get('Q3', 'KR1') == []


def test_threshold_change_retags(env):
    run(env)
    assert run(env, threshold=1.0)['processed'] == 1
    assert env[1].get('Q3', 'KR1') == []


def test_failure_does_not_advance(env):
    env[3].embed = Mock(side_effect=OSError('offline'))
    with pytest.raises(OSError):
        run(env)
    assert not env[1].read().get('documents')


def test_incomplete_embedding_refused(env):
    env[3].embed = Mock(return_value=[])
    with pytest.raises(IrisValueError):
        run(env)


def test_jsonl_and_npz_published(env):
    run(env)
    root = env[1].root
    generation = (root / 'CURRENT').read_text()
    assert len(list((root / 'generations' / generation).glob('*.jsonl'))) == 1
    root = env[2].root
    generation = (root / 'CURRENT').read_text()
    assert (root / 'generations' / generation / 'kr_vectors.npz').exists()


def test_budget_leaves_remaining(env):
    (env[0] / 'other.md').write_text('巡检')
    result = run(env, max_documents=1)
    assert result['processed'] == result['remaining'] == 1
    assert run(env, max_documents=1)['remaining'] == 0


@pytest.mark.parametrize('left,right', [([], []), ([1.], [1.,2.]), ([float('nan')], [1.])])
def test_bad_vectors_rejected(left, right):
    with pytest.raises(IrisValueError):
        cosine(left, right)


@pytest.mark.parametrize('value', [-1, 2, float('nan'), True, '0.65'])
def test_bad_threshold(value):
    from types import SimpleNamespace
    with pytest.raises(IrisValueError):
        settings(SimpleNamespace(app={'intelligence': {'okr_threshold': value}}))
