"""Release safety checks use small fake bundles; never invoke PyInstaller."""
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location("caption_release", Path(__file__).parents[1] / "scripts/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@pytest.fixture
def root(tmp_path):
    version = tmp_path / "src/iris_caption/version.py"
    version.parent.mkdir(parents=True)
    version.write_text("__version__ = '0.2.0'\nraise RuntimeError('must not import')\n")
    return tmp_path


def test_version_is_read_without_execution(root):
    assert release.read_version(root) == "0.2.0"


def test_dry_run_has_no_side_effects(root):
    result = release.release(root, root / "releases", root / "release-state", dry_run=True)
    assert result["package"].endswith("0.2.0/Iris-Caption-0.2.0-macos-arm64.zip")
    assert not (root / "release-state").exists()
    assert not (root / "releases").exists()


def test_counter_is_monotonic_under_concurrency(tmp_path):
    state = tmp_path / "release-state"
    with ThreadPoolExecutor(max_workers=8) as executor:
        numbers = list(executor.map(lambda _: release.allocate_build_number(state), range(24)))
    assert sorted(numbers) == list(range(1, 25))
    assert release.allocate_build_number(state) == 25


def test_corrupt_counter_never_resets(tmp_path):
    (tmp_path / "counter.json").write_text('{"last_build_number": -1}')
    with pytest.raises(ValueError):
        release.allocate_build_number(tmp_path)


def test_atomic_publication_never_overwrites(tmp_path):
    first, second, target = (tmp_path / name for name in ["first", "second", "target"])
    first.mkdir()
    second.mkdir()
    (first / "payload").write_text("original")
    release.publish_generation(first, target)
    with pytest.raises(FileExistsError):
        release.publish_generation(second, target)
    assert (target / "payload").read_text() == "original"
    assert second.exists()


@pytest.fixture
def fake_build(monkeypatch):
    calls = []
    monkeypatch.setattr(release.platform, "machine", lambda: "arm64")
    # Keep real atomic publication implementation on the test host.
    real_publish = release.publish_generation
    host = release.sys.platform

    def publish(source, destination):
        monkeypatch.setattr(release.sys, "platform", host)
        try:
            real_publish(source, destination)
        finally:
            monkeypatch.setattr(release.sys, "platform", "darwin")

    monkeypatch.setattr(release, "publish_generation", publish)
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setattr(release, "source_revision", lambda root: "abc123")
    monkeypatch.setattr(release, "validate_source", lambda root: [{"passed": True}])
    monkeypatch.setattr(release, "validate_bundle", lambda app, info: [{"passed": True}])

    def run(command, **kwargs):
        calls.append(command)
        if "PyInstaller" in command:
            dist = Path(command[command.index("--distpath") + 1])
            (dist / "声笺.app").mkdir(parents=True)
            metadata = json.loads(Path(kwargs["env"]["IRIS_CAPTION_BUILD_INFO"]).read_text())
            assert Path(kwargs['env']['PYINSTALLER_CONFIG_DIR']).parent == dist.parent
            assert metadata["build_number"] > 0
        else:
            assert command[0] == "ditto"
            Path(command[-1]).write_bytes(b"fake zip")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release.subprocess, "run", run)
    return calls


def test_build_private_staging_manifest_and_duplicate_refusal(root, fake_build):
    installed = root / "dist/声笺.app"
    installed.mkdir(parents=True)
    (installed / "sentinel").write_text("preserve")
    document = root / "docs/data-compatibility.md"
    document.parent.mkdir()
    document.write_text("Compatibility contract")
    manifest = release.release(root, root / "releases", root / "release-state")
    assert (root / "releases/0.2.0/docs/data-compatibility.md").read_text() == document.read_text()
    assert manifest["documents_sha256"]["docs/data-compatibility.md"] == release.sha256(document)
    assert manifest["build_info"]["version"] == "0.2.0"
    assert manifest["build_info"]["build_number"] == 1
    assert release.verify_release(root / "releases/0.2.0") == manifest
    assert (installed / "sentinel").read_text() == "preserve"
    with pytest.raises(FileExistsError):
        release.release(root, root / "releases", root / "release-state")
    assert len(fake_build) == 2


def test_failed_build_consumes_number_without_publication(root, fake_build, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "PyInstaller")
    monkeypatch.setattr(release.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        release.release(root, root / "releases", root / "release-state")
    assert not (root / "releases/0.2.0").exists()
    assert release.allocate_build_number(root / "release-state") == 2


def test_failed_validation_never_publishes(root, fake_build, monkeypatch):
    def fail(root):
        raise subprocess.CalledProcessError(1, "pytest")
    monkeypatch.setattr(release, "validate_source", fail)
    with pytest.raises(subprocess.CalledProcessError):
        release.release(root, root / "releases", root / "release-state")
    assert not (root / "releases/0.2.0").exists()
    assert not fake_build
    assert release.allocate_build_number(root / "release-state") == 2


def make_release(output, version):
    directory = output / version
    directory.mkdir(parents=True)
    package = directory / "package.zip"
    package.write_bytes(version.encode())
    (directory / "manifest.json").write_text(json.dumps({"package": package.name,
        "build_info": {"version": version}, "sha256": release.sha256(package)}))
    return directory


def test_retention_keeps_current_previous_and_archives_only_verified(root):
    output = root / "releases"
    for version in ["0.1.0", "0.2.0", "0.3.0"]:
        make_release(output, version)
    (output / "unknown.zip").write_text("keep")
    plan = release.retention_plan(root, output)
    assert [Path(path).name for path in plan["keep"]] == ["0.3.0", "0.2.0"]
    assert (output / "0.1.0").exists()
    release.archive_old_releases(root, output)
    assert (output / "archive/0.1.0/package.zip").exists()
    assert (output / "unknown.zip").exists()


def test_retention_refuses_corrupt_artifact(root):
    output = root / "releases"
    directory = make_release(output, "0.1.0")
    (directory / "package.zip").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        release.archive_old_releases(root, output)
    assert directory.exists()


def test_legacy_import_preserves_original_and_metadata(root):
    source = root / "dist/Iris-Caption-0.1.0-noise-filter-preview.zip"
    source.parent.mkdir()
    source.write_bytes(b"last noise-filter release")
    output = root / "releases"
    assert release.import_legacy(root, output, dry_run=True)["dry_run"]
    assert not output.exists()
    manifest = release.import_legacy(root, output)
    assert manifest["build_info"]["build_number"] is None
    assert source.read_bytes() == (output / "0.1.0" / manifest["package"]).read_bytes()
    with pytest.raises(FileExistsError):
        release.import_legacy(root, output)


@pytest.mark.parametrize("name", ["../outside.md", "/outside.md", "docs/../../outside.md"])
def test_document_manifest_rejects_traversal(root, name):
    directory = make_release(root / "releases", "0.1.0")
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["documents_sha256"] = {name: "irrelevant"}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="document"):
        release.verify_release(directory)


def test_document_manifest_rejects_symlink_parent(root):
    directory = make_release(root / "releases", "0.1.0")
    outside = root / "outside"
    outside.mkdir()
    document = outside / "data.md"
    document.write_text("external")
    (directory / "docs").symlink_to(outside, target_is_directory=True)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["documents_sha256"] = {"docs/data.md": release.sha256(document)}
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="document"):
        release.verify_release(directory)


def test_state_under_dist_rejected(root):
    with pytest.raises(ValueError, match="survive"):
        release.release(root, root / "releases", root / "dist/state", dry_run=True)
