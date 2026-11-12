"""Migration contract; every write is confined to pytest temporary storage."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from iris_caption import persistence, settings
from iris_caption.knowledge import RuleStore
from iris_caption.materials import MaterialIndex
from iris_caption.statistics import StatsStore


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', lambda: tmp_path)
    return tmp_path


@pytest.mark.parametrize('filename,read,save,legacy', [
    ('settings.json', settings.read_settings, settings.save_settings,
     {'model_path': '/old', 'extension': {'keep': True}}),
    ('view_preferences.json', settings.read_preferences, settings.save_preferences,
     {'font_size': 32, 'extension': {'keep': True}}),
])
def test_legacy_backup_roundtrip(root, filename, read, save, legacy):
    path = root / filename
    raw = json.dumps(legacy, ensure_ascii=False).encode() + b'  \n'
    path.write_bytes(raw)
    read()
    assert json.loads(path.read_bytes()) == {**legacy, 'schema_version': 1}
    backups = list((root / 'backups').iterdir())
    assert len(backups) == 1 and backups[0].read_bytes() == raw
    read()
    save({})
    assert json.loads(path.read_bytes())['extension'] == {'keep': True}
    assert list((root / 'backups').iterdir()) == backups


@pytest.mark.parametrize('raw', [b'{oops', b'[]', b'{"schema_version":2}',
                                b'{"schema_version":true}', b'{"schema_version":-1}',
                                b'{"a":1,"a":2}', b'{"x":NaN}'])
@pytest.mark.parametrize('filename,read,save', [
    ('settings.json', settings.read_settings, settings.save_settings),
    ('view_preferences.json', settings.read_preferences, settings.save_preferences),
])
def test_reject_without_overwrite(root, raw, filename, read, save):
    path = root / filename
    path.write_bytes(raw)
    for operation in (read, lambda: save({})):
        with pytest.raises(persistence.PersistenceError):
            operation()
        assert path.read_bytes() == raw
    assert not (root / 'backups').exists()


def test_rules_legacy_reserved_names_and_extensions(root):
    store = RuleStore(root / 'rules')
    store.root.mkdir()
    path = store.root / 'local_rules.json'
    legacy = {'schema_version': 'literal replacement', 'rules': 'literal rules', '错词': '正词'}
    raw = json.dumps(legacy).encode()
    path.write_bytes(raw)
    assert store.effective_rules() == legacy
    assert next((root / 'backups').iterdir()).read_bytes() == raw
    document = json.loads(path.read_bytes())
    document['extension'] = [1, 2]
    path.write_text(json.dumps(document))
    store.set_rule('另一个', '新规则')
    store.delete_rule('错词')
    assert json.loads(path.read_bytes())['extension'] == [1, 2]
    assert store.effective_rules()['rules'] == 'literal rules'


@pytest.mark.parametrize('document', [{'schema_version': 2, 'rules': {}},
                                     {'schema_version': 1, 'rules': []}, {'x': 9}])
def test_rules_reject_without_write(root, document):
    store = RuleStore(root / 'rules')
    store.root.mkdir()
    path = store.root / 'local_rules.json'
    raw = json.dumps(document).encode()
    path.write_bytes(raw)
    for operation in (store.effective_rules, lambda: store.set_rule('a', 'b'),
                      lambda: store.delete_rule('a')):
        with pytest.raises(ValueError):
            operation()
        assert path.read_bytes() == raw


def test_backup_failure_preserves_original(root, monkeypatch):
    path = root / 'settings.json'
    raw = b'{"model_path":"original"}'
    path.write_bytes(raw)
    def fail(*args):
        raise OSError('backup unavailable')
    monkeypatch.setattr(persistence, 'atomic_write_bytes', fail)
    with pytest.raises(OSError):
        settings.save_settings({'model_path': 'new'})
    assert path.read_bytes() == raw


def test_atomic_migration_failure_can_retry(root, monkeypatch):
    path = root / 'settings.json'
    raw = b'{"model_path":"original"}'
    path.write_bytes(raw)
    original = persistence.atomic_write_json
    def fail(*args):
        raise OSError('replace unavailable')
    monkeypatch.setattr(persistence, 'atomic_write_json', fail)
    with pytest.raises(OSError):
        settings.read_settings()
    assert path.read_bytes() == raw
    assert next((root / 'backups').iterdir()).read_bytes() == raw
    monkeypatch.setattr(persistence, 'atomic_write_json', original)
    settings.read_settings()
    assert len(list((root / 'backups').iterdir())) == 1


def test_concurrent_rule_migration_and_updates(root):
    store = RuleStore(root / 'rules')
    store.root.mkdir()
    path = store.root / 'local_rules.json'
    path.write_bytes(b'{"old":"value"}')
    def update(index):
        RuleStore(store.root).set_rule(str(index), 'value')
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(update, range(20)))
    assert len(store.effective_rules()) == 21
    assert len(list((root / 'backups').iterdir())) == 1


@pytest.mark.parametrize('raw', [b'{oops', b'[]', b'{"version":2,"documents":[]}',
                                b'{"version":true,"documents":[]}',
                                b'{"version":1,"documents":[{}]}'])
def test_retained_save_never_overwrites_invalid(root, raw):
    path = root / 'materials.json'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        MaterialIndex().save_retained(root, [])
    assert path.read_bytes() == raw



def test_newer_model_activation_is_preserved(root):
    import hashlib
    import io
    from iris_caption.models import ModelManager
    manifest = {'schema_version': 1, 'model_id': 'example/model', 'revision': 'pinned',
                'files': [{'path': 'model.bin', 'size': 5,
                           'sha256': hashlib.sha256(b'model').hexdigest(),
                           'url': 'https://huggingface.co/example/model/resolve/pinned/model.bin'}]}
    manager = ModelManager(root, opener=lambda *_: io.BytesIO(b'model'))
    installed = manager.download(manifest)
    pointer = (root / 'active.json').read_bytes()
    stored_manifest = root / 'versions' / installed['generation'] / 'manifest.json'
    value = json.loads(stored_manifest.read_bytes())
    value['schema_version'] = 2
    stored_manifest.write_text(json.dumps(value))
    before = stored_manifest.read_bytes()
    with pytest.raises(ValueError):
        manager.download(manifest)
    assert (root / 'active.json').read_bytes() == pointer
    assert stored_manifest.read_bytes() == before
    assert len(list((root / 'versions').iterdir())) == 1




@pytest.mark.parametrize('version', [2, True])
def test_statistics_newer_schema_rejects_record(root, version):
    path = root / 'statistics.json'
    raw = json.dumps({'version': version, 'sessions': {}}).encode()
    path.write_bytes(raw)
    row = dict(session_id='s', started_at='2026-01-01T00:00:00+00:00',
               updated_at='2026-01-01T00:00:00+00:00', raw_chars=1,
               corrected_chars=1, finalized_segments=1)
    with pytest.raises(ValueError):
        StatsStore(root).record(row)
    assert path.read_bytes() == raw


def test_missing_files_are_not_created_by_reads(root):
    settings.read_settings()
    settings.read_preferences()
    RuleStore(root / 'rules').effective_rules()
    assert not list(root.rglob('*.json'))
    assert not (root / 'backups').exists()


def test_statistics_invalid_row_not_dropped(root):
    path = root / 'statistics.json'
    raw = b'{"version":1,"sessions":{"bad":{}}}'
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        StatsStore(root).rows()
    assert path.read_bytes() == raw
