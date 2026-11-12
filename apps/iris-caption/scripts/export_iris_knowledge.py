#!/usr/bin/env python3
"""Export ONLY explicitly selected, already-filtered Iris ASR artifacts.

Example: python scripts/export_iris_knowledge.py --hotwords words.txt
  --replacements replacements.json --output caption-bundle --version 2026-11-05
Optional --rules selects plain-text rules, split into paragraphs (never executed).
No Iris imports, environment/config discovery, source/Wiki scans, or original metadata.
Use existing Iris ASR exports that passed its sensitive-term filter; this converter
cannot establish provenance of arbitrary user files. Review the selected input first.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import os

# Allows direct execution from a source checkout without installing Iris or Caption.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from iris_caption.knowledge import validate_bundle  # noqa: E402

LIMIT = 1024 * 1024


def read_selected(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError("Selected artifact must be a bounded regular file")
    with path.open("rb") as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise ValueError("Artifact too large")
    return data.decode("utf-8")


def reject_private(value):
    """Fail closed on recognizable credentials/absolute paths, without echoing data."""
    text = json.dumps(value, ensure_ascii=False)
    if re.search(r"(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+\S+|(?:api[_ -]?key|secret|password)\s*[=:]|"
                 r"/(?:Users|home|private|Volumes)/|[A-Za-z]:\\\\)", text, re.I):
        raise ValueError("Selected artifact contains possible private metadata")


def export_bundle(*, hotwords, replacements, output, version, rules=None, protected_terms=None):
    """Create a new compatible folder atomically; refuse to overwrite a bundle."""
    text = read_selected(hotwords)
    words = json.loads(text) if Path(hotwords).suffix.lower() == ".json" else text.splitlines()
    if not isinstance(words, list) or any(not isinstance(word, str) for word in words):
        raise ValueError("Hotwords must be a string list or line-based text")
    words = list(dict.fromkeys(word.strip() for word in words if word.strip()))
    mappings = json.loads(read_selected(replacements))
    if isinstance(mappings, dict) and "replace_map" in mappings:
        mappings = mappings["replace_map"]
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", read_selected(rules)) if item.strip()] if rules else []
    protected = json.loads(read_selected(protected_terms)) if protected_terms else []
    payload = {"hotwords": words, "replacements": mappings, "rules": paragraphs,
               "protected_terms": protected}
    reject_private(payload)
    reject_private(version)
    destination = Path(output)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Output must be a new directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".caption-export-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        checksums = {}
        for name, value in payload.items():
            data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
            (stage / (name + ".json")).write_bytes(data)
            checksums[name + ".json"] = hashlib.sha256(data).hexdigest()
        manifest = {"schema_version": 1, "package_version": version,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "iris-asr-export", "checksums": checksums}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        validate_bundle(stage)
        os.rename(stage, destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hotwords", required=True)
    parser.add_argument("--replacements", required=True)
    parser.add_argument("--rules")
    parser.add_argument("--protected-terms")
    parser.add_argument("--output", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        export_bundle(**vars(args))
    except (ValueError, OSError) as exc:
        # Never print arbitrary exception text that might contain source data/paths.
        print(f"Export failed ({type(exc).__name__}); review selected artifact formats.", file=sys.stderr)
        return 1
    print("Knowledge bundle exported and validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
