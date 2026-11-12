import hashlib
import json
import zipfile

import pytest

from iris_caption.knowledge import (
    KnowledgeStore, RuleStore, apply_replacements, extract_rule_candidates, validate_bundle,
)
from iris_caption.persistence import FileLock, atomic_write_json


def bundle(tmp_path):
    folder = tmp_path / "input"
    folder.mkdir()
    checksums = {}
    for name, value in {"hotwords": ["Iris"], "replacements": {"爱瑞斯": "Iris"},
                        "rules": ["保留否定"], "protected_terms": ["DeepSeek"]}.items():
        data = json.dumps(value).encode()
        (folder / (name + ".json")).write_bytes(data)
        checksums[name + ".json"] = hashlib.sha256(data).hexdigest()
    (folder / "manifest.json").write_text(json.dumps({"schema_version": 1,
        "package_version": "v1", "generated_at": "2026-11-05", "source": "Iris",
        "checksums": checksums}))
    return folder


def test_folder_zip_activation_and_failure_preserves_old(tmp_path):
    folder = bundle(tmp_path)
    store = KnowledgeStore(tmp_path / "store")
    assert store.active_bundle()["replacements"] == {}
    store.import_bundle(folder)
    assert store.active_bundle()["hotwords"] == ["Iris"]
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for file in folder.iterdir():
            stream.write(file, file.name)
    store.import_bundle(archive)
    old = (store.root / "active.json").read_bytes()
    (folder / "hotwords.json").write_text('["tampered"]')
    with pytest.raises(ValueError, match="Checksum"):
        store.import_bundle(folder)
    assert (store.root / "active.json").read_bytes() == old


def test_archive_traversal_and_symlink_rejected(tmp_path):
    folder = bundle(tmp_path)
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        for file in folder.iterdir():
            stream.write(file, file.name)
        stream.writestr("../outside", "x")
    with pytest.raises(ValueError):
        validate_bundle(archive)
    link = tmp_path / "linked"
    link.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError):
        validate_bundle(link)


def test_duplicate_json_and_size_rejected(tmp_path):
    folder = bundle(tmp_path)
    (folder / "manifest.json").write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="Duplicate"):
        validate_bundle(folder)
    (folder / "manifest.json").write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError):
        validate_bundle(folder)


def test_rule_precedence_conflicts_no_cascade(tmp_path):
    store = RuleStore(tmp_path)
    iris = {"a": "b", "ab": "x", "b": "c"}
    store.set_rule("a", "local")
    store.set_rule("a", "session", scope="session")
    assert store.effective_rules(iris)["a"] == "session"
    assert store.conflicts(iris) == [{"source": "a", "values": {
        "iris": "b", "local": "local", "session": "session"}, "winner": "session"}]
    assert apply_replacements("ab a b", iris) == "x b c"
    assert RuleStore(tmp_path).effective_rules(iris)["a"] == "local"
    store.clear_session()
    assert store.effective_rules(iris)["a"] == "local"


@pytest.mark.parametrize("before,after", [("预算10万", "预算20万"), ("应该做", "不应该做"),
    ("我们不做", "我们要做"), ("一段话", ""), ("今天讨论", "今天讨论项目")])
def test_candidates_do_not_learn_facts_or_insertions(before, after):
    assert extract_rule_candidates(before, after) == []


def test_candidate_requires_user_confirmation(tmp_path):
    assert extract_rule_candidates("请找爱瑞斯确认项目", "请找Iris确认项目") == [{"source": "爱瑞斯", "target": "Iris"}]
    assert extract_rule_candidates("请找张山确认项目", "请找张杉确认项目") == []
    assert not list(tmp_path.iterdir())


def test_atomic_persistence(tmp_path):
    path = tmp_path / "state.json"
    with FileLock(tmp_path / "state.lock"):
        atomic_write_json(path, {"version": 1})
    with FileLock(tmp_path / "state.lock"):
        value = json.loads(path.read_text())
        atomic_write_json(path, {"version": value["version"] + 1})
    assert json.loads(path.read_text()) == {"version": 2}
    assert not list(tmp_path.glob(".state.json.*"))
