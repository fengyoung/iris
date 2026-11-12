"""Bounded, local-only meeting material retrieval.

Limits: 20 MiB/file, 500,000 characters/document, 200 PDF pages, 40 MiB
expanded DOCX, 100 documents and 2,000,000 characters/index. UTF-8 text and
Markdown, PDF text layers, DOCX paragraphs/tables. No OCR: image-only PDFs
raise an error; mixed PDFs use text layers only. Chunks: 900 chars/100 overlap.
Only save_retained persists data. HTTP/Feishu sources loaded from disk are
quarantined until the caller verifies permission and supplies refreshed text.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
import math
from pathlib import Path
import re
from threading import RLock
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from .persistence import FileLock, atomic_write_json, check_schema, read_json_object

MATERIALS_SCHEMA_VERSION = 1

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
MAX_PDF_PAGES = 200
MAX_DOCX_EXPANDED_BYTES = 40 * 1024 * 1024
MAX_DOCUMENTS = 100
MAX_INDEX_CHARS = 2_000_000
MAX_RETAINED_BYTES = 16 * 1024 * 1024
CHUNK_CHARS = 900
CHUNK_OVERLAP = 100


def _tokens(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(run)
        words.update(run[i:i + 2] for i in range(len(run) - 1))
    return words


def _source_url(source: str) -> str | None:
    match = re.search(r"https?://\S+", source, re.IGNORECASE)
    return match.group() if match else None


def _restricted(source: str, source_url: str | None) -> bool:
    return bool(source_url) or "feishu" in source.lower() or "lark" in source.lower()


def _synchronized(method):
    """Serialize public operations, including nested add_file -> add_text."""
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return locked


@dataclass(frozen=True)
class _Document:
    id: str
    name: str
    text: str
    source: str
    source_url: str | None
    verified: bool = True


class MaterialIndex:
    """Thread-safe memory-only index; never downloads models.

    semantic_available/status expose semantic+lexical or lexical-only operation.
    Embedding load/inference failures degrade to lexical retrieval.
    """

    def __init__(self, embedding_model_path: str | None = None):
        self._lock = RLock()
        self._model_lock = RLock()
        self._documents: dict[str, _Document] = {}
        self._vectors: dict[tuple[str, int, str], tuple[float, ...]] = {}
        self._model: Any = None
        self.semantic_available = False
        self.status = "lexical-only: no local embedding model configured"
        self._reload_generation = 0
        if embedding_model_path is not None:
            self.reload_embeddings(embedding_model_path)

    def reload_embeddings(self, model_path: str | None) -> None:
        """Load a local model on the caller's background worker, then swap it.

        Documents and quarantine flags are unchanged. None disables semantic
        retrieval; missing dependencies, invalid paths, or load failures expose
        lexical-only status. Existing snapshots retain their model and lock.
        Loading does not block live retrieval. If reloads overlap, only the most
        recently requested one may publish its result. No downloads occur.
        """
        with self._lock:
            self._reload_generation += 1
            generation = self._reload_generation
        model = None
        status = "lexical-only: no local embedding model configured"
        if model_path is not None:
            path = Path(model_path).expanduser()
            if not path.is_dir():
                status = "lexical-only: local embedding model directory does not exist"
            else:
                try:
                    from sentence_transformers import SentenceTransformer
                    model = SentenceTransformer(str(path.resolve()), local_files_only=True)
                    status = "semantic+lexical"
                except ImportError:
                    status = "lexical-only: install sentence-transformers to use local embeddings"
                except Exception:
                    status = "lexical-only: local embedding model could not be loaded"
        with self._lock:
            if generation != self._reload_generation:
                return
            self._model = model
            self._model_lock = RLock()
            self._vectors = {}
            self.semantic_available = model is not None
            self.status = status

    @_synchronized
    def snapshot(self) -> MaterialIndex:
        """Capture documents/verification for a controller background version.

        Later live mutations cannot affect this index. Document records are
        immutable; status is independent. A bounded content-keyed vector cache and
        model inference lock are shared for the same model generation. Inference is
        serialized across snapshots without holding the live document lock.
        Capture at enqueue time and retain this snapshot for retries.
        """
        captured = MaterialIndex()
        captured._documents = self._documents.copy()
        captured._vectors = self._vectors
        captured._model = self._model
        captured._model_lock = self._model_lock
        captured.semantic_available = self.semantic_available
        captured.status = self.status
        return captured

    def _validate(self, name: str, text: str, source: str) -> str:
        if not isinstance(name, str) or not name.strip() or len(name) > 1000:
            raise ValueError("Material name must contain 1–1000 characters")
        if not isinstance(source, str) or len(source) > 4096:
            raise ValueError("Material source must be a string of at most 4096 characters")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Material has no readable text; OCR is not supported")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"Material exceeds {MAX_TEXT_CHARS:,} extracted characters")
        return text.strip()

    def _check_capacity(self, documents: dict[str, _Document]) -> None:
        if len(documents) > MAX_DOCUMENTS:
            raise ValueError(f"Index supports at most {MAX_DOCUMENTS} documents")
        if sum(len(doc.text) for doc in documents.values()) > MAX_INDEX_CHARS:
            raise ValueError(f"Index exceeds {MAX_INDEX_CHARS:,} total characters")

    @_synchronized
    def add_text(self, name: str, text: str, source: str = "text") -> str:
        text = self._validate(name, text, source)
        identifier = uuid4().hex
        doc = _Document(identifier, name.strip(), text, source, _source_url(source))
        candidate = {**self._documents, identifier: doc}
        self._check_capacity(candidate)
        self._documents = candidate
        return identifier

    @_synchronized
    def add_file(self, path: str | Path) -> str:
        path = Path(path)
        if not path.is_file():
            raise ValueError(f"Material file does not exist: {path.name}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("Material file exceeds 20 MiB")
        suffix = path.suffix.lower()
        try:
            if suffix in {".txt", ".md", ".markdown"}:
                with path.open("rb") as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("Material file exceeds 20 MiB")
                text = data.decode("utf-8-sig")
            elif suffix == ".pdf":
                text = self._read_pdf(path)
            elif suffix == ".docx":
                text = self._read_docx(path)
            else:
                raise ValueError("Unsupported material format; use TXT, Markdown, PDF, or DOCX")
        except ImportError as exc:
            raise ValueError(f"Install iris-caption[materials] to read {suffix} files") from exc
        except UnicodeDecodeError as exc:
            raise ValueError("Text material must be UTF-8; convert its encoding and retry") from exc
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Could not parse {suffix} material; check it is valid and unencrypted") from exc
        return self.add_text(path.name, text, source="file")

    @staticmethod
    def _read_pdf(path: Path) -> str:
        import fitz
        parts: list[str] = []
        size = 0
        with fitz.open(path) as document:
            if document.needs_pass:
                raise ValueError("Encrypted PDF: provide an unencrypted copy")
            if len(document) > MAX_PDF_PAGES:
                raise ValueError(f"PDF exceeds {MAX_PDF_PAGES} pages")
            for page in document:
                text = page.get_text()
                size += len(text) + 1
                if size > MAX_TEXT_CHARS:
                    raise ValueError("PDF exceeds extracted text limit")
                parts.append(text)
        return "\n".join(parts)

    @staticmethod
    def _read_docx(path: Path) -> str:
        try:
            with ZipFile(path) as archive:
                if sum(info.file_size for info in archive.infolist()) > MAX_DOCX_EXPANDED_BYTES:
                    raise ValueError("DOCX exceeds 40 MiB expanded size")
        except BadZipFile as exc:
            raise ValueError("Invalid DOCX archive") from exc
        from docx import Document
        document = Document(path)
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            parts.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
        return "\n".join(parts)

    @_synchronized
    def remove(self, id: str) -> None:
        self._documents.pop(id, None)
        with self._model_lock:
            for key in list(self._vectors):
                if key[0] == id:
                    del self._vectors[key]

    @_synchronized
    def clear(self) -> None:
        self._documents.clear()
        self._vectors = {}

    @_synchronized
    def list_materials(self) -> list[dict]:
        return [{"id": doc.id, "name": doc.name, "source": doc.source,
                 "source_url": doc.source_url, "chars": len(doc.text),
                 "retrievable": doc.verified,
                 "requires_verification": not doc.verified}
                for doc in self._documents.values()]

    def _embedding(self, text: str) -> list[float]:
        with self._model_lock:
            vector = [float(value) for value in self._model.encode([text])[0]]
        norm = math.sqrt(sum(value * value for value in vector))
        if not vector or not math.isfinite(norm) or not norm:
            raise ValueError("Invalid embedding")
        return [value / norm for value in vector]

    @_synchronized
    def search(self, query: str, limit: int = 3, max_chars: int = 2500) -> list[str]:
        if limit <= 0 or max_chars <= 0 or not query.strip():
            return []
        query_tokens = _tokens(query)
        candidates = []
        query_vector = None
        if self.semantic_available:
            try:
                query_vector = self._embedding(query)
            except Exception:
                self._degrade()
        for doc in self._documents.values():
            if not doc.verified:
                continue
            for start in range(0, len(doc.text), CHUNK_CHARS - CHUNK_OVERLAP):
                chunk = doc.text[start:start + CHUNK_CHARS]
                tokens = _tokens(doc.name + " " + chunk)
                lexical = len(query_tokens & tokens) / max(1, len(query_tokens))
                semantic = 0.0
                if self.semantic_available and query_vector is not None:
                    try:
                        key = (doc.id, start, chunk)
                        with self._model_lock:
                            if key not in self._vectors:
                                # Bound retained old-version vectors across snapshots.
                                if len(self._vectors) >= 4096:
                                    self._vectors.pop(next(iter(self._vectors)))
                                self._vectors[key] = tuple(self._embedding(chunk))
                            vector = self._vectors[key]
                        if len(vector) != len(query_vector):
                            raise ValueError("Embedding dimensions changed")
                        semantic = max(0.0, sum(a * b for a, b in zip(vector, query_vector)))
                    except Exception:
                        self._degrade()
                candidates.append((lexical, semantic, f"[{doc.name}] {chunk}"))
        # An inference failure discards semantic scores for ALL candidates.
        ranked = sorted(candidates, key=lambda row: row[0] + (row[1] if self.semantic_available else 0),
                        reverse=True)
        results: list[str] = []
        remaining = max_chars
        for lexical, semantic, snippet in ranked:
            if lexical <= 0 and (not self.semantic_available or semantic <= 0):
                continue
            results.append(snippet[:remaining])
            remaining -= len(results[-1])
            if len(results) >= limit or remaining <= 0:
                break
        return results

    def _degrade(self) -> None:
        self.semantic_available = False
        self.status = "lexical-only: local embedding inference failed"
        self._vectors = {}

    @_synchronized
    def save_retained(self, root: str | Path, ids: list[str]) -> None:
        """Atomically replace the retained set with explicitly selected ids."""
        selected = list(dict.fromkeys(ids))
        if any(identifier not in self._documents for identifier in selected):
            raise ValueError("Cannot retain an unknown material id")
        records = [{"id": doc.id, "name": doc.name, "text": doc.text,
                    "source": doc.source, "source_url": doc.source_url}
                   for doc in (self._documents[identifier] for identifier in selected)]
        root = Path(root)
        with FileLock(root / ".materials.lock"):
            document = self._retained_document(root)
            previous = {row["id"]: row for row in document["documents"]}
            document["documents"] = [{**previous.get(row["id"], {}), **row} for row in records]
            atomic_write_json(root / "materials.json", document)

    def _retained_document(self, root: Path) -> dict:
        path = root / "materials.json"
        if not path.exists():
            return {"version": MATERIALS_SCHEMA_VERSION, "documents": []}
        if path.stat().st_size > MAX_RETAINED_BYTES:
            raise ValueError("Retained materials exceed size limit")
        document = read_json_object(path)
        try:
            self._decode_retained(document)
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid retained materials file") from exc
        return document

    @_synchronized
    def load_retained(self, root: str | Path) -> list[str]:
        """Merge retained ids atomically in memory; remote sources are quarantined."""
        root = Path(root)
        if not (root / "materials.json").exists():
            return []
        with FileLock(root / ".materials.lock"):
            loaded = self._decode_retained(self._retained_document(root))
        candidate = {**self._documents, **loaded}
        self._check_capacity(candidate)
        self._documents = candidate
        self._vectors = {}
        return list(loaded)

    def _decode_retained(self, payload: dict) -> dict[str, _Document]:
        if (not isinstance(payload, dict)
                or check_schema(payload, MATERIALS_SCHEMA_VERSION, key="version") != MATERIALS_SCHEMA_VERSION):
            raise ValueError("Unsupported retained materials schema")
        rows = payload["documents"]
        if not isinstance(rows, list) or len(rows) > MAX_DOCUMENTS:
            raise ValueError("Invalid retained document list")
        loaded = {}
        for row in rows:
            identifier, name, source = row["id"], row["name"], row["source"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise ValueError("Invalid retained material id")
            if identifier in loaded:
                raise ValueError("Duplicate retained material id")
            text = self._validate(name, row["text"], source)
            url = row.get("source_url") or _source_url(source)
            if url is not None and (not isinstance(url, str) or len(url) > 4096):
                raise ValueError("Invalid retained source URL")
            loaded[identifier] = _Document(identifier, name, text, source, url,
                                           verified=not _restricted(source, url))
        return loaded

    @_synchronized
    def verify_retained(self, id: str, source_url: str, text: str) -> None:
        """Caller asserts current-session permission and supplies fresh remote text.

        Call only after successful external access/update checks. Exact source
        URL is mandatory. Verification is never persisted.
        """
        doc = self._documents[id]
        if not doc.source_url or source_url != doc.source_url:
            raise ValueError("Verification requires the retained material's exact source URL")
        text = self._validate(doc.name, text, doc.source)
        replacement = _Document(doc.id, doc.name, text, doc.source, doc.source_url)
        candidate = {**self._documents, id: replacement}
        self._check_capacity(candidate)
        self._documents = candidate
        with self._model_lock:
            for key in list(self._vectors):
                if key[0] == id:
                    del self._vectors[key]
