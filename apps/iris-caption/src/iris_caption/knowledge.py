"""Validated standalone knowledge packages and explicit human replacement rules.

Bundle schema 1: manifest.json has schema_version=1, package_version, generated_at,
source (strings), and checksums mapping the four required JSON basenames to SHA256.
Payloads: hotwords/protected_terms are string lists, replacements is a string map,
rules is a string list. active_bundle() returns those four keys plus manifest.
RuleStore persists ONLY explicitly confirmed local rules; session rules stay in RAM.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import stat
import threading
import uuid
import zipfile

from .persistence import FileLock, PersistenceError, atomic_write_json, migrate_json, read_json_object

LOCAL_RULES_SCHEMA_VERSION = 1

MAX_FILE_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 4 * MAX_FILE_BYTES
PAYLOADS = ("hotwords", "replacements", "rules", "protected_terms")
FILES = {"manifest.json", *(name + ".json" for name in PAYLOADS)}


def _json(data: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid bundle JSON") from exc


def _read_bundle(path: Path) -> dict[str, bytes]:
    result = {}
    if path.is_symlink():
        raise ValueError("Symlink bundle is forbidden")
    if path.is_dir():
        entries = list(path.iterdir())
        if {item.name for item in entries} != FILES:
            raise ValueError("Bundle must contain exactly the schema files")
        for item in entries:
            if item.is_symlink() or not item.is_file() or item.stat().st_size > MAX_FILE_BYTES:
                raise ValueError("Invalid bundle file")
            with item.open("rb") as stream:
                result[item.name] = stream.read(MAX_FILE_BYTES + 1)
    else:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) != len(FILES) or {item.filename for item in entries} != FILES:
                raise ValueError("Invalid archive paths or duplicate files")
            for item in entries:
                mode = item.external_attr >> 16
                if (item.file_size > MAX_FILE_BYTES or item.flag_bits & 1
                        or stat.S_ISLNK(mode) or item.is_dir()
                        or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))):
                    raise ValueError("Invalid archive entry")
                with archive.open(item) as stream:
                    result[item.filename] = stream.read(MAX_FILE_BYTES + 1)
    if any(len(data) > MAX_FILE_BYTES for data in result.values()):
        raise ValueError("Bundle file too large")
    if sum(map(len, result.values())) > MAX_BUNDLE_BYTES:
        raise ValueError("Bundle too large")
    return result


def _strings(value, max_length=256):
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError("Expected bounded string list")
    if any(not isinstance(item, str) or not item.strip() or len(item) > max_length
           or "\x00" in item for item in value):
        raise ValueError("Invalid string entry")


def _rules(value):
    if not isinstance(value, dict) or len(value) > 10000:
        raise ValueError("Expected replacement object")
    for source, target in value.items():
        _strings([source, target], 128)


def validate_bundle(path: str | Path) -> dict:
    """Fully validate a directory/ZIP without extracting untrusted paths."""
    raw = _read_bundle(Path(path))
    manifest = _json(raw["manifest.json"])
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int:
        raise ValueError("Invalid manifest")
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported schema")
    for name in ("package_version", "generated_at", "source"):
        _strings([manifest.get(name)], 256)
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != FILES - {"manifest.json"}:
        raise ValueError("Invalid checksum manifest")
    result = {"manifest": manifest}
    for name in PAYLOADS:
        data = raw[name + ".json"]
        if checksums[name + ".json"] != hashlib.sha256(data).hexdigest():
            raise ValueError("Checksum mismatch")
        value = _json(data)
        if name == "replacements":
            _rules(value)
        else:
            _strings(value, 2000 if name == "rules" else 256)
        result[name] = value
    return result


class KnowledgeStore:
    """Import immutable generations; atomically activate only fully valid bundles."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def import_bundle(self, path: str | Path) -> dict:
        bundle = validate_bundle(path)
        with FileLock(self.root / ".knowledge.lock"):
            # Never replace a newer/corrupt active pointer with a seemingly valid import.
            self.active_bundle()
            generation = uuid.uuid4().hex
            folder = self.root / "versions" / generation
            folder.mkdir(parents=True)
            # A complete single JSON snapshot avoids reserialization checksum ambiguity.
            atomic_write_json(folder / "bundle.json", bundle)
            atomic_write_json(self.root / "active.json", {"generation": generation})
        return bundle["manifest"]

    def active_bundle(self) -> dict:
        """Return manifest, hotwords, replacements, rules, protected_terms; empty defaults."""
        pointer = self.root / "active.json"
        if not pointer.exists():
            return {"manifest": {}, "hotwords": [], "replacements": {},
                    "rules": [], "protected_terms": []}
        generation = read_json_object(pointer).get("generation")
        if not isinstance(generation, str) or not re.fullmatch(r"[a-f0-9]{32}", generation):
            raise PersistenceError("知识包索引损坏，已保留原文件。")
        bundle = read_json_object(self.root / "versions" / generation / "bundle.json")
        if (not isinstance(bundle, dict) or not isinstance(bundle.get("manifest"), dict)
                or type(bundle["manifest"].get("schema_version")) is not int
                or bundle["manifest"]["schema_version"] != 1):
            raise PersistenceError("知识包格式不受支持，请升级 Iris Caption；已保留原文件。")
        try:
            for name in PAYLOADS:
                if name == "replacements":
                    _rules(bundle[name])
                else:
                    _strings(bundle[name], 2000 if name == "rules" else 256)
        except (KeyError, ValueError, TypeError) as exc:
            raise PersistenceError("知识包数据损坏，已保留原文件。") from exc
        return bundle


def apply_replacements(text: str, rules: dict[str, str]) -> str:
    """Longest literal match at each position; emitted targets never cascade."""
    _rules(rules)
    if not rules:
        return text
    pattern = re.compile("|".join(re.escape(key) for key in sorted(rules, key=len, reverse=True)))
    return pattern.sub(lambda match: rules[match.group()], text)


class RuleStore:
    """Priority session > local > Iris; set_rule is an explicit user confirmation API."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.session: dict[str, str] = {}
        self._lock = threading.RLock()

    def _document(self):
        return migrate_json(
            self.root / "local_rules.json", self.root.parent / "backups",
            LOCAL_RULES_SCHEMA_VERSION,
            lambda value: {"schema_version": LOCAL_RULES_SCHEMA_VERSION, "rules": value},
            lambda value: _rules(value["rules"]),
            legacy=lambda value: all(isinstance(item, str) for item in value.values()))

    def _local(self):
        with FileLock(self.root / ".rules.lock"):
            return self._document()["rules"]

    def set_rule(self, source: str, target: str, scope: str = "local") -> None:
        _rules({source: target})
        with self._lock:
            if scope == "session":
                self.session[source] = target
            elif scope == "local":
                with FileLock(self.root / ".rules.lock"):
                    document = self._document()
                    document["rules"][source] = target
                    atomic_write_json(self.root / "local_rules.json", document)
            else:
                raise ValueError("Unknown rule scope")

    def delete_rule(self, source: str, scope: str = "local") -> bool:
        """Delete only an explicit human layer; return whether a rule existed."""
        with self._lock:
            if scope == "session":
                return self.session.pop(source, None) is not None
            if scope != "local":
                raise ValueError("Only local/session rules can be deleted")
            with FileLock(self.root / ".rules.lock"):
                document = self._document()
                rules = document["rules"]
                if source not in rules:
                    return False
                del rules[source]
                atomic_write_json(self.root / "local_rules.json", document)
                return True

    def list_rules(self, iris_rules: dict | None = None) -> list[dict]:
        """Detached rows: source, target, scope, effective, conflict."""
        with self._lock:
            layers = {"iris": iris_rules or {}, "local": self._local(), "session": dict(self.session)}
            rows = []
            for source in sorted(set().union(*(set(layer) for layer in layers.values()))):
                winner = next(scope for scope in ("session", "local", "iris") if source in layers[scope])
                conflict = len({layer[source] for layer in layers.values() if source in layer}) > 1
                for scope, layer in layers.items():
                    if source in layer:
                        rows.append({"source": source, "target": layer[source], "scope": scope,
                                     "effective": scope == winner, "conflict": conflict})
            return rows

    def clear_session(self) -> None:
        with self._lock:
            self.session.clear()

    def effective_rules(self, iris_rules: dict | None = None) -> dict:
        with self._lock:
            return {**(iris_rules or {}), **self._local(), **self.session}

    def conflicts(self, iris_rules: dict | None = None) -> list[dict]:
        with self._lock:
            layers = {"iris": iris_rules or {}, "local": self._local(), "session": self.session}
            result = []
            for source in sorted(set().union(*(set(layer) for layer in layers.values()))):
                values = {name: layer[source] for name, layer in layers.items() if source in layer}
                if len(set(values.values())) > 1:
                    winner = next(name for name in ("session", "local", "iris") if name in values)
                    result.append({"source": source, "values": values, "winner": winner})
            return result

    def apply(self, text: str, iris_rules: dict | None = None) -> str:
        return apply_replacements(text, self.effective_rules(iris_rules))


def extract_rule_candidates(before: str, after: str) -> list[dict[str, str]]:
    """Suggest only a single short literal substitution; never auto-approve it.

    Insertions, deletions, sentence rewrites, numbers and negations are excluded.
    A candidate is intentionally a changed span, not an inferred name or concept.
    """
    changes = [item for item in SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
               if item[0] != "equal"]
    if len(changes) != 1 or changes[0][0] != "replace":
        return []
    _, a, b, c, d = changes[0]
    source, target = before[a:b], after[c:d]
    if a == 0 and b == len(before):
        return []
    if not (2 <= len(source) <= 16 and 2 <= len(target) <= 16):
        return []
    if any(re.search(r"[\d\W]|[不没无否未]", value) for value in (source, target)):
        return []
    if len(before) > 16 and max(len(source), len(target)) > len(before) / 3:
        return []
    if before.count(source) != 1 or source == target:
        return []
    return [{"source": source, "target": target}]
