import hashlib
import importlib.util
import io
import json
from pathlib import Path
import threading

import pytest

from iris_caption.models import (
    ModelCancelled, ModelManager, candidate_manifest, manifest_from_directory, validate_manifest,
)
from iris_caption.knowledge import validate_bundle


def manifest(data=b"model"):
    return {"schema_version": 1, "model_id": "example/model", "revision": "pinned",
            "files": [{"path": "weights/model.bin", "size": len(data),
                       "sha256": hashlib.sha256(data).hexdigest(),
                       "url": "https://huggingface.co/example/model/resolve/pinned/model.bin"}]}


def test_download_verify_and_atomic_activation(tmp_path):
    manager = ModelManager(tmp_path, opener=lambda url, timeout: io.BytesIO(b"model"))
    progress = []
    result = manager.download(manifest(), progress=progress.append)
    assert Path(result["directory"], "weights/model.bin").read_bytes() == b"model"
    assert manager.active_model() == result
    assert progress[-1]["stage"] == "complete"
    manager._opener = lambda url, timeout: io.BytesIO(b"wrong")
    with pytest.raises(ValueError, match="integrity"):
        manager.download(manifest())
    assert manager.active_model() == result
    assert not list(tmp_path.glob(".model-*"))


def test_manual_import_explicit_trust_and_hashing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "model.bin").write_bytes(b"local model")
    with pytest.raises(ValueError, match="explicit"):
        manifest_from_directory(source, model_id="local")
    value = manifest_from_directory(source, model_id="local", trust_local=True)
    manager = ModelManager(tmp_path / "installed")
    result = manager.import_model(source, value)
    assert result["manifest"]["files"][0]["sha256"] == hashlib.sha256(b"local model").hexdigest()
    (source / "model.bin").write_bytes(b"modified!!!")
    with pytest.raises(ValueError):
        manager.import_model(source, value)
    assert manager.active_model() == result


@pytest.mark.parametrize("path", ["../bad", "/absolute", "a/../bad", "a\\bad", "a//b", "manifest.json"])
def test_reject_paths(path):
    value = manifest()
    value["files"][0]["path"] = path
    with pytest.raises(ValueError):
        validate_manifest(value)


@pytest.mark.parametrize("url", ["http://huggingface.co/m", "https://evil.example/m",
    "https://huggingface.co.evil.example/m", "https://secret@huggingface.co/m"])
def test_reject_unapproved_hosts(url):
    value = manifest()
    value["files"][0]["url"] = url
    with pytest.raises(ValueError):
        validate_manifest(value, require_urls=True)


def test_cancel_does_not_activate_or_leave_staging(tmp_path):
    cancel = threading.Event()
    manager = ModelManager(tmp_path, opener=lambda url, timeout: io.BytesIO(b"model"))
    with pytest.raises(ModelCancelled):
        manager.download(manifest(), progress=lambda progress: cancel.set(), cancel=cancel)
    assert manager.active_model() is None
    assert not list(tmp_path.glob(".model-*"))


def test_oversize_and_missing_hash():
    value = manifest()
    value["files"][0]["sha256"] = ""
    with pytest.raises(ValueError, match="SHA256"):
        validate_manifest(value)
    value = manifest()
    value["files"][0]["size"] = 20 * 1024**3
    with pytest.raises(ValueError):
        validate_manifest(value)


def test_symlink_source_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "external").write_text("secret")
    (source / "link").symlink_to(tmp_path / "external")
    with pytest.raises(ValueError):
        manifest_from_directory(source, model_id="local", trust_local=True)


def test_candidate_metadata_requires_sha256_and_pinned_revision():
    revision = "a" * 40
    metadata = {"sha": revision, "siblings": [{"rfilename": "model.bin", "lfs": {
        "size": 5, "sha256": hashlib.sha256(b"model").hexdigest()}}]}
    def opener(url, timeout):
        return io.BytesIO(json.dumps(metadata).encode())
    value = candidate_manifest("example/model", revision, opener=opener)
    assert value["files"][0]["size"] == 5
    del metadata["siblings"][0]["lfs"]["sha256"]
    with pytest.raises(ValueError):
        candidate_manifest("example/model", revision, opener=opener)
    with pytest.raises(ValueError):
        candidate_manifest("example/model", "main", opener=opener)


def exporter():
    path = Path(__file__).parents[1] / "scripts/export_iris_knowledge.py"
    spec = importlib.util.spec_from_file_location("caption_export_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_export_selected_artifacts_compatible_and_no_source_metadata(tmp_path):
    words = tmp_path / "private-source-name.txt"
    words.write_text("Iris\nDeepSeek\nIris\n")
    replacements = tmp_path / "replacements.json"
    replacements.write_text(json.dumps({"replace_map": {"爱瑞斯": "Iris"}, "source": "/Users/private"}))
    rules = tmp_path / "rules.txt"
    rules.write_text("保留否定。\n\n保留数字。")
    output = tmp_path / "bundle"
    exporter().export_bundle(hotwords=words, replacements=replacements, output=output,
                             version="v1", rules=rules)
    value = validate_bundle(output)
    assert value["hotwords"] == ["Iris", "DeepSeek"]
    assert value["rules"] == ["保留否定。", "保留数字。"]
    assert "private-source-name" not in json.dumps(value)
    assert "/Users/private" not in json.dumps(value)


def test_export_rejects_possible_secrets(tmp_path):
    words = tmp_path / "words.txt"
    words.write_text("sk-abcdefghijklmnop")
    replacements = tmp_path / "replacements.json"
    replacements.write_text("{}")
    with pytest.raises(ValueError, match="private"):
        exporter().export_bundle(hotwords=words, replacements=replacements,
                                 output=tmp_path / "bundle", version="v1")
