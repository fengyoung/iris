"""Verified model installation with no Iris runtime dependency.

ModelManager operations block: run them in a GUI worker. progress receives dictionaries
(stage/file/completed/total); cancel is a threading.Event. All installs validate every
byte before atomically switching active.json. Failed installs leave the old active
model intact. Downloads require pinned SHA256 hashes; there are no invented defaults.
Local trust is explicit: manifest_from_directory(..., trust_local=True) records hashes
of user-selected files, establishing integrity, not publisher authenticity.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import time
from urllib.parse import quote, urlsplit
import urllib.request
import uuid

from .persistence import FileLock, atomic_write_json, read_json_object

MAX_FILE_BYTES = 8 * 1024**3
MAX_TOTAL_BYTES = 16 * 1024**3
MAX_FILES = 256
ALLOWED_HOSTS = {"huggingface.co", "hf.co", "modelscope.cn", "www.modelscope.cn",
                 "cdn-lfs.huggingface.co", "cdn-lfs-us-1.huggingface.co",
                 "cas-bridge.xethub.hf.co", "cdn-lfs.hf.co"}


class ModelCancelled(RuntimeError):
    """Installation was cancelled; no active version changed."""


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ModelCancelled("Model installation cancelled")


def _path(value):
    if not isinstance(value, str) or not value or len(value) > 512 or "\\" in value or "\x00" in value:
        raise ValueError("Invalid model file path")
    parts = PurePosixPath(value).parts
    if value.startswith("/") or any(part in ("..", ".") for part in value.split("/")):
        raise ValueError("Unsafe model file path")
    if any(not part or ":" in part for part in value.split("/")) or parts[0] == "manifest.json":
        raise ValueError("Unsafe model file path")
    return value


def _url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("Model URL is outside approved HTTPS hosts")
    return value


def validate_manifest(manifest: dict, *, require_urls: bool = False) -> dict:
    """Validate a pinned schema-1 manifest, returning a detached normalized copy."""
    if (not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1):
        raise ValueError("Unsupported model manifest")
    for field in ("model_id", "revision"):
        if not isinstance(manifest.get(field), str) or not 1 <= len(manifest[field]) <= 256:
            raise ValueError("Missing model identity")
    files = manifest.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Invalid model file count")
    clean, seen, total = [], set(), 0
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Invalid model file")
        path = _path(item.get("path"))
        if path.casefold() in seen:
            raise ValueError("Duplicate model file path")
        seen.add(path.casefold())
        size, digest = item.get("size"), item.get("sha256")
        if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES:
            raise ValueError("Invalid model file size")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise ValueError("Model files require pinned SHA256")
        entry = {"path": path, "size": size, "sha256": digest.lower()}
        if require_urls or "url" in item:
            entry["url"] = _url(item.get("url", ""))
        clean.append(entry)
        total += size
    if total > MAX_TOTAL_BYTES:
        raise ValueError("Model size exceeds limit")
    for path in seen:
        if any(parent.as_posix() in seen for parent in PurePosixPath(path).parents if str(parent) != "."):
            raise ValueError("Conflicting model paths")
    return {"schema_version": 1, "model_id": manifest["model_id"],
            "revision": manifest["revision"], "files": clean}


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, timeout):
    _url(url)
    return urllib.request.build_opener(_SafeRedirect).open(url, timeout=timeout)


def manifest_from_directory(directory, *, model_id: str, trust_local: bool = False,
                            cancel=None, progress=None) -> dict:
    """Hash explicit trusted local regular files; never infer remote authenticity."""
    if not trust_local:
        raise ValueError("Local model import requires explicit trust_local=True")
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Model source must be a regular directory")
    files = []
    for folder, directories, filenames in os.walk(root, followlinks=False):
        if any((Path(folder) / name).is_symlink() for name in directories):
            raise ValueError("Symlink model directories are forbidden")
        for name in sorted(filenames):
            path = Path(folder) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Only regular model files are allowed")
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            if (len(files) >= MAX_FILES or size > MAX_FILE_BYTES
                    or sum(item["size"] for item in files) + size > MAX_TOTAL_BYTES):
                raise ValueError("Local model exceeds limits")
            digest = hashlib.sha256()
            completed = 0
            _check_cancel(cancel)
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    _check_cancel(cancel)
                    digest.update(chunk)
                    completed += len(chunk)
                    if progress:
                        progress({"stage": "hashing", "file": relative,
                                  "completed": completed, "total": size})
            files.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
    return validate_manifest({"schema_version": 1, "model_id": model_id,
                              "revision": "local-" + uuid.uuid4().hex, "files": files})


class ModelManager:
    """One active model generation; install directory paths returned for ASR setup."""

    def __init__(self, root, *, timeout: float = 20, opener=None):
        if not 0 < timeout <= 60:
            raise ValueError("Invalid network timeout")
        self.root = Path(root)
        self.timeout = timeout
        self._opener = opener or _open

    def active_model(self):
        pointer = self.root / "active.json"
        if not pointer.exists():
            return None
        value = read_json_object(pointer)
        generation = value.get("generation", "")
        if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation):
            raise ValueError("Invalid model activation")
        directory = self.root / "versions" / generation
        manifest = validate_manifest(json.loads((directory / "manifest.json").read_text("utf-8")))
        return {"generation": generation, "directory": str(directory), "manifest": manifest}

    def import_model(self, directory, manifest, *, progress=None, cancel=None):
        root = Path(directory)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Invalid local model directory")
        def source(item):
            path = root / item["path"]
            if any(part.is_symlink() for part in [path, *path.parents] if part != root.parent):
                raise ValueError("Symlink model files are forbidden")
            if not path.is_file() or path.stat().st_size != item["size"]:
                raise ValueError("Model file missing or size mismatch")
            return path.open("rb")
        return self._install(validate_manifest(manifest), source, progress, cancel)

    def download(self, manifest, *, progress=None, cancel=None):
        manifest = validate_manifest(manifest, require_urls=True)
        return self._install(manifest, lambda item: self._opener(item["url"], self.timeout), progress, cancel)

    def _install(self, manifest, source, progress, cancel):
        self.root.mkdir(parents=True, exist_ok=True)
        _check_cancel(cancel)
        with tempfile.TemporaryDirectory(prefix=".model-", dir=self.root) as temporary:
            stage = Path(temporary)
            total = sum(item["size"] for item in manifest["files"])
            completed = 0
            for item in manifest["files"]:
                _check_cancel(cancel)
                target = stage / item["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with source(item) as stream, target.open("wb") as output:
                    deadline = time.monotonic() + min(4 * 3600, max(60, item["size"] / (64 * 1024)))
                    while True:
                        _check_cancel(cancel)
                        if time.monotonic() > deadline:
                            raise TimeoutError("Model file transfer deadline exceeded")
                        reader = getattr(stream, "read1", stream.read)
                        chunk = reader(min(1024 * 1024, item["size"] - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > item["size"]:
                            raise ValueError("Model file exceeds declared size")
                        digest.update(chunk)
                        output.write(chunk)
                        completed += len(chunk)
                        if progress:
                            progress({"stage": "copying", "file": item["path"],
                                      "completed": completed, "total": total})
                    output.flush()
                    os.fsync(output.fileno())
                if size != item["size"] or digest.hexdigest() != item["sha256"]:
                    raise ValueError("Model integrity check failed")
            atomic_write_json(stage / "manifest.json", manifest)
            with FileLock(self.root / ".models.lock"):
                _check_cancel(cancel)
                self.active_model()
                generation = uuid.uuid4().hex
                target = self.root / "versions" / generation
                target.parent.mkdir(parents=True, exist_ok=True)
                # Stage and destination share a filesystem. Only verified files become visible.
                os.replace(stage, target)
                atomic_write_json(self.root / "active.json", {"generation": generation})
            result = {"generation": generation, "directory": str(target), "manifest": manifest}
            if progress:
                progress({"stage": "complete", "file": "", "completed": completed, "total": total})
            return result


def builtin_contextual_manifest() -> dict:
    """Official Modelscope metadata, verified 2026-11-05; weights not downloaded.

    Source: https://modelscope.cn/api/v1/models/iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx/repo/files?Revision=8f0881c891ceba7360e215b04e54cad564a68c41&Recursive=true
    Integrity pins do not establish runtime compatibility/performance on a given Mac.
    """
    model = "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx"
    revision = "8f0881c891ceba7360e215b04e54cad564a68c41"
    entries = [
        ("am.mvn", 11203, "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5"),
        ("config.yaml", 2532, "1d9057edeaba9e131cb98f26011606497cf3af187d8943525ddb5ee36c836b1b"),
        ("configuration.json", 790, "6e4e5234d4f657eb5530fbda198915b9f219aedbaa13a7b59b62819f43022af3"),
        ("model_quant.onnx", 871251660, "f404e6eb532b54fd95761e2b4be4ed1998e8cff3cb3b930a9bee1f2d556e5035"),
        ("model_eb.onnx", 25618359, "d31446a5af664291a2922cca253a4200a523f347d6fc3cb1bff356bf60a116b6"),
        ("seg_dict", 8287834, "59a2ef803a3f1648ad03a2e1480db1c1ee0c0d7dc4ef4dbd16cea33944329022"),
        ("tokens.json", 93676, "2b20c2b12572d682afff84ce1c8d560f67b8b32a4c1f21567411d141ed352127"),
    ]
    return validate_manifest({"schema_version": 1, "model_id": model, "revision": revision,
        "files": [{"path": path, "size": size, "sha256": digest,
                   "url": f"https://modelscope.cn/api/v1/models/{model}/repo?Revision={revision}&FilePath={path}"}
                  for path, size, digest in entries]}, require_urls=True)


def builtin_fsmn_manifest() -> dict:
    """Optional 16kHz FSMN ONNX, official metadata verified 2026-11-05.

    Source: https://modelscope.cn/api/v1/models/iic/speech_fsmn_vad_zh-cn-16k-common-onnx/repo/files?Revision=a158155ef9e81f405c052f40b6d1ad43b87e6215&Recursive=true
    """
    model = "iic/speech_fsmn_vad_zh-cn-16k-common-onnx"
    revision = "a158155ef9e81f405c052f40b6d1ad43b87e6215"
    entries = [
        ("am.mvn", 8040, "6820fef9687708c4fc3fab2530179c8fcea6262daa25514380056cd8f6eb1754"),
        ("config.yaml", 1215, "2ef334f2d7776edd86ed696296774f87550206c256bfabc597872ad861831589"),
        ("configuration.json", 598, "f62fcbcebbaade798714b34c521cad87d869ee6a327ceed948e01ff2336bbfc0"),
        ("model_quant.onnx", 506744, "5289eb2aa3c9af2d7a4284bcfa7c3ceb81d360814ed4203239b6c5d0569da8a1"),
    ]
    return validate_manifest({"schema_version": 1, "model_id": model, "revision": revision,
        "files": [{"path": path, "size": size, "sha256": digest,
                   "url": f"https://modelscope.cn/api/v1/models/{model}/repo?Revision={revision}&FilePath={path}"}
                  for path, size, digest in entries]}, require_urls=True)


def builtin_embedding_manifest() -> dict:
    """SentenceTransformer safetensors + tokenizer/pooling assets, pinned revision.

    LFS digests: official HF API metadata at the revision below. Small JSON digests
    computed from exact pinned HTTPS response bytes on 2026-11-05 (no weights fetched).
    Source: https://huggingface.co/api/models/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/revision/e8f8c211226b894fcb81acc59f3b34ba3efd5f42?blobs=true
    Each file's source URL is retained in its manifest entry.
    """
    model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    revision = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    entries = [
        ("1_Pooling/config.json", 190, "4be450dde3b0273bb9787637cfbd28fe04a7ba6ab9d36ac48e92b11e350ffc23"),
        ("config.json", 645, "6300193cb75e01cf80c96decef7187dfb33094d97cc1490b7ead6ff134476e4e"),
        ("config_sentence_transformers.json", 122, "b8c64b5cece00d8424b4896ea75b512b6008576088497609dfeb6bd63e6d36b8"),
        ("modules.json", 229, "8f4b264b80206c830bebbdcae377e137925650a433b689343a63bdc9b3145460"),
        ("sentence_bert_config.json", 53, "70f4448f31320443fe3557cacea5abf2dcc4915dda8c80646bec9f3bb0aa5a1f"),
        ("special_tokens_map.json", 239, "378eb3bf733eb16e65792d7e3fda5b8a4631387ca04d2015199c4d4f22ae554d"),
        ("tokenizer_config.json", 526, "5036ea374ffedd706e3bef33e2e0d6953cb868ef8a490e76e32ba0faa37a6b9b"),
        ("model.safetensors", 470641600, "eaa086f0ffee582aeb45b36e34cdd1fe2d6de2bef61f8a559a1bbc9bd955917b"),
        ("sentencepiece.bpe.model", 5069051, "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865"),
        ("tokenizer.json", 9081518, "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"),
    ]
    return validate_manifest({"schema_version": 1, "model_id": model, "revision": revision,
        "files": [{"path": path, "size": size, "sha256": digest,
                   "url": f"https://huggingface.co/{model}/resolve/{revision}/{path}"}
                  for path, size, digest in entries]}, require_urls=True)


def builtin_speaker_manifest() -> dict:
    """Optional local speaker models, pinned HF exports with audited checksums."""
    from .speaker import MODEL_MANIFEST
    return validate_manifest({
        "schema_version": 1, "model_id": "iris-caption-conference-speaker-onnx",
        "revision": "campplus-6265ff7_pyannote-9403a69",
        "files": [{"path": item.filename, "size": item.size,
                   "sha256": item.sha256, "url": item.url} for item in MODEL_MANIFEST],
    }, require_urls=True)


def candidate_manifest(repo_id: str, revision: str, *, files: list[str] | None = None,
                       opener=None) -> dict:
    """Query HF metadata only; fail closed unless immutable commit and SHA256 exist.

    Many small Git files have only SHA1 metadata: select files with publisher SHA256
    or provide a separately audited full manifest. This helper never downloads weights.
    Modelscope URLs are supported by download manifests; no unaudited metadata inference.
    """
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo_id) or not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        raise ValueError("Candidate requires repository and pinned commit SHA")
    url = f"https://huggingface.co/api/models/{repo_id}/revision/{quote(revision)}?blobs=true"
    with (opener or _open)(url, 20) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Model metadata exceeds limit")
    metadata = json.loads(data)
    if metadata.get("sha", "").lower() != revision.lower():
        raise ValueError("Model revision mismatch")
    selected = set(files) if files is not None else None
    entries = []
    for item in metadata.get("siblings", []):
        path = item.get("rfilename")
        if selected is not None and path not in selected:
            continue
        lfs = item.get("lfs") or {}
        entries.append({"path": path, "size": lfs.get("size", item.get("size")),
                        "sha256": lfs.get("sha256"),
                        "url": f"https://huggingface.co/{repo_id}/resolve/{revision}/{quote(path or '', safe='/')}"})
    if selected is not None and selected != {item["path"] for item in entries}:
        raise ValueError("Selected model files are absent")
    return validate_manifest({"schema_version": 1, "model_id": repo_id,
                              "revision": revision, "files": entries}, require_urls=True)
