#!/usr/bin/env python3
"""Build standalone Caption releases without installing dependencies or changing public dist."""
from __future__ import annotations

import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from filelock import FileLock

APP_ROOT = Path(__file__).resolve().parents[1]


def read_version(root: Path) -> str:
    tree = ast.parse((root / "src/iris_caption/version.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value):
                return value
    raise ValueError("Caption __version__ must be a literal major.minor.patch string")


def atomic_json(path: Path, value: dict) -> None:
    """Replace state atomically and durably; caller holds the lock."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def allocate_build_number(state: Path) -> int:
    state.mkdir(parents=True, exist_ok=True)
    with FileLock(str(state / "counter.lock")):
        counter = state / "counter.json"
        previous = json.loads(counter.read_text()) if counter.exists() else {"last_build_number": 0}
        number = previous["last_build_number"]
        if type(number) is not int or number < 0:
            raise ValueError("Invalid build counter; refusing to reset it")
        number += 1
        atomic_json(counter, {"last_build_number": number})
        return number


def publish_generation(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing any existing target."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = (os.fsencode(source), os.fsencode(destination), 4)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        arguments = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    else:
        raise RuntimeError("Exclusive atomic publication requires macOS or Linux")
    function.restype = ctypes.c_int
    if function(*arguments) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_revision(root: Path) -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def source_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    paths = [root / name for name in ("IrisCaption.spec", "launcher.py", "pyproject.toml",
                                      "CHANGELOG.md", "README.md", "RELEASE.md", "docs/data-compatibility.md")]
    for name in ("src", "scripts", "tests", "assets"):
        paths.extend(path for path in (root / name).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    for path in sorted(paths):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def validate_source(root: Path) -> list[dict]:
    results = []
    for arguments in (["pytest", "-q"], ["ruff", "check", "src", "tests", "scripts", "IrisCaption.spec"]):
        command = [sys.executable, "-m", *arguments]
        subprocess.run(command, cwd=root, check=True, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        results.append({"command": arguments, "passed": True})
    return results


def validate_bundle(app: Path, info: dict) -> list[dict]:
    import plistlib

    with (app / "Contents/Info.plist").open("rb") as stream:
        plist = plistlib.load(stream)
    if (plist.get("CFBundleShortVersionString") != info["version"]
            or plist.get("CFBundleVersion") != str(info["build_number"])):
        raise ValueError("Bundle plist version/build number mismatch")
    embedded = json.loads((app / "Contents/Resources/assets/build-info.json").read_text())
    if embedded != info:
        raise ValueError("Frozen build metadata mismatch")
    results = []
    for flag in ("--version", "--diagnostics", "--smoke-test"):
        command = [str(app / "Contents/MacOS/IrisCaption"), flag]
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=120,
                                env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
        if flag == "--version" and info["version"] not in result.stdout:
            raise ValueError("Frozen executable version mismatch")
        results.append({"command": ["IrisCaption", flag], "passed": True, "stdout": result.stdout})
    return results


def release(root: Path, output: Path, state: Path, *, dry_run: bool = False, channel: str = "preview") -> dict:
    root, output, state = root.resolve(), output.resolve(), state.resolve()
    if state == root / "dist" or root / "dist" in state.parents:
        raise ValueError("Release state must survive dist cleanup; choose a path outside dist")
    version = read_version(root)
    package = f"Iris-Caption-{version}-macos-arm64.zip"
    destination = output / version
    plan = {"version": version, "package": str(destination / package), "state": str(state),
            "channel": channel, "architecture": "arm64", "dry_run": dry_run}
    if destination.exists() or (output / "archive" / version).exists():
        raise FileExistsError(f"Version {version} already published: {destination}")
    if dry_run:
        return plan
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("Caption release builds require a native macOS arm64 host")
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / ".release.lock")):
        if destination.exists() or (output / "archive" / version).exists():
            raise FileExistsError(f"Version {version} already published: {destination}")
        number = allocate_build_number(state)
        validation = validate_source(root)
        fingerprint = source_sha256(root)
        info = {"version": version, "build_number": number,
                "built_at": datetime.now(timezone.utc).isoformat(), "source_revision": "sha256:" + fingerprint[:16],
                "channel": channel, "architecture": "arm64"}
        with tempfile.TemporaryDirectory(prefix=".caption-build-", dir=output) as temporary:
            staging = Path(temporary)
            metadata = staging / "build-info.json"
            metadata.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
            environment = dict(os.environ, IRIS_CAPTION_BUILD_INFO=str(metadata),
                               PYINSTALLER_CONFIG_DIR=str(staging / 'pyinstaller-cache'))
            subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                            "--distpath", str(staging / "dist"), "--workpath", str(staging / "work"),
                            str(root / "IrisCaption.spec")], cwd=root, env=environment, check=True)
            app = staging / "dist/声笺.app"
            if not app.is_dir():
                raise FileNotFoundError(f"PyInstaller did not produce {app}")
            validation.extend(validate_bundle(app, info))
            if source_sha256(root) != fingerprint:
                raise RuntimeError("Source changed during build; retry with a stable source tree")
            generation = staging / "release"
            generation.mkdir()
            archive = generation / package
            subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)],
                           check=True)
            documents = {}
            for name in ("CHANGELOG.md", "README.md", "RELEASE.md", "docs/data-compatibility.md"):
                document = root / name
                if document.is_file():
                    (generation / name).parent.mkdir(parents=True, exist_ok=True)
                    (generation / name).write_bytes(document.read_bytes())
                    documents[name] = sha256(generation / name)
            (generation / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
            documents["validation.json"] = sha256(generation / "validation.json")
            manifest = {"schema": 1, "build_info": info, "package": package,
                        "documents_sha256": documents,
                        "source_sha256": fingerprint, "git_revision": source_revision(root), "validation": validation,
                        "sha256": sha256(archive), "size_bytes": archive.stat().st_size}
            (generation / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            publish_generation(generation, destination)
        return manifest


def verify_release(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    package = manifest.get("package", "")
    if (not package or Path(package).name != package or not package.endswith(".zip")
            or manifest.get("build_info", {}).get("version") != directory.name):
        raise ValueError(f"Invalid release manifest: {directory}")
    archive = directory / package
    if archive.is_symlink() or sha256(archive) != manifest["sha256"]:
        raise ValueError(f"Release checksum mismatch: {archive}")
    for name, digest in manifest.get("documents_sha256", {}).items():
        relative = Path(name)
        document = directory / relative
        if (relative.is_absolute() or not relative.parts or ".." in relative.parts
                or any((directory / Path(*relative.parts[:index])).is_symlink()
                       for index in range(1, len(relative.parts) + 1))
                or not document.resolve().is_relative_to(directory.resolve())
                or sha256(document) != digest):
            raise ValueError(f"Release document checksum mismatch: {document}")
    return manifest


def retention_plan(root: Path, output: Path) -> dict:
    """Unknown artifacts are never candidates."""
    releases = []
    if output.exists():
        for directory in output.iterdir():
            if (directory.is_symlink() or not directory.is_dir()
                    or not re.fullmatch(r"\d+\.\d+\.\d+", directory.name)):
                continue
            if not (directory / "manifest.json").is_file():
                continue
            verify_release(directory)
            releases.append(directory)
    releases.sort(key=lambda path: tuple(map(int, path.name.split("."))), reverse=True)
    legacy = root / "dist/Iris-Caption-0.1.0-noise-filter-preview.zip"
    return {"keep": [str(path) for path in releases[:2]],
            "previous_legacy_0.1.0": str(legacy) if len(releases) < 2 and legacy.is_file() else None,
            "archive_candidates": [str(path) for path in releases[2:]],
            "note": "Plan only; no deletion. Unknown/legacy artifacts are untouched."}


def archive_old_releases(root: Path, output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / ".release.lock")):
        plan = retention_plan(root, output)
        archive = output / "archive"
        if archive.is_symlink():
            raise ValueError("Archive directory must not be a symlink")
        for entry in plan["archive_candidates"]:
            source = Path(entry).resolve()
            if source.parent != output or not re.fullmatch(r"\d+\.\d+\.\d+", source.name):
                raise ValueError(f"Unsafe archive target: {source}")
            verify_release(source)
            archive.mkdir(exist_ok=True)
            publish_generation(source, archive / source.name)
        return {**plan, "note": "Older completed releases archived, never deleted."}


def import_legacy(root: Path, output: Path, *, dry_run: bool = False) -> dict:
    """Copy the known last preview; preserve original bytes and honest unknown metadata."""
    import shutil

    source = root / "dist/Iris-Caption-0.1.0-noise-filter-preview.zip"
    destination = output / "0.1.0"
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"Missing regular legacy archive: {source}")
    if destination.exists() or (output / "archive/0.1.0").exists():
        raise FileExistsError("Legacy 0.1.0 already imported")
    if dry_run:
        return {"source": str(source), "destination": str(destination), "dry_run": True}
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(output / ".release.lock")):
        if destination.exists() or (output / "archive/0.1.0").exists():
            raise FileExistsError("Legacy 0.1.0 already imported")
        with tempfile.TemporaryDirectory(prefix=".caption-import-", dir=output) as temporary:
            generation = Path(temporary) / "release"
            generation.mkdir()
            package = source.name
            shutil.copy2(source, generation / package)
            manifest = {"schema": 1, "package": package, "sha256": sha256(generation / package),
                        "size_bytes": source.stat().st_size, "legacy_source": str(source),
                        "build_info": {"version": "0.1.0", "build_number": None, "built_at": None,
                                       "source_revision": None, "channel": "legacy", "architecture": "arm64"}}
            (generation / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            publish_generation(generation, destination)
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build", "retention", "import-legacy"])
    parser.add_argument("--output", type=Path, default=APP_ROOT / "releases")
    parser.add_argument("--state", type=Path, default=APP_ROOT / "release-state")
    parser.add_argument("--channel", default="preview", choices=["stable", "preview"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Archive old completed releases; never delete")
    args = parser.parse_args(argv)
    if args.command != "retention" and args.apply:
        parser.error("--apply is only valid for retention")
    try:
        if args.command == "build":
            result = release(APP_ROOT, args.output, args.state, dry_run=args.dry_run, channel=args.channel)
        elif args.command == "import-legacy":
            result = import_legacy(APP_ROOT, args.output.resolve(), dry_run=args.dry_run)
        elif args.apply and not args.dry_run:
            result = archive_old_releases(APP_ROOT, args.output.resolve())
        else:
            result = retention_plan(APP_ROOT, args.output.resolve())
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Release refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
