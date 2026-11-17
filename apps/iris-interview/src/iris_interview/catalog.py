"""Reusable local job library; never writes private catalog into source tree."""

from __future__ import annotations

import json
from pathlib import Path
import uuid
from filelock import FileLock

from .exceptions import ValidationError
from .preferences import app_root, atomic_json


class JobLibrary:
    def __init__(self, root: Path | None = None):
        self.root = root or app_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.json"
        self.lock = FileLock(str(self.root / "jobs.lock"))

    def list(self) -> list[dict]:
        with self.lock:
            return json.loads(self.path.read_text()).get("jobs", []) if self.path.exists() else []

    def save(self, job: dict) -> dict:
        if not isinstance(job.get("name"), str) or not job["name"].strip():
            raise ValidationError("岗位名称不能为空")
        job = dict(job)
        job.setdefault("id", uuid.uuid4().hex)
        with self.lock:
            jobs = self.list()
            previous = next((j for j in jobs if j["id"] == job["id"]), None)
            job["version"] = (previous or {}).get("version", 0) + 1
            jobs = [j for j in jobs if j["id"] != job["id"]] + [job]
            atomic_json(self.path, {"schema_version": 1, "jobs": jobs})
        return job

    def delete(self, job_id: str) -> None:
        with self.lock:
            atomic_json(
                self.path, {"schema_version": 1, "jobs": [j for j in self.list() if j["id"] != job_id]}
            )

    def import_file(self, path: Path) -> list[dict]:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValidationError("岗位文件超过4MB")
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
            jobs = data if isinstance(data, list) else data.get("jobs", [data])
            if not all(isinstance(j, dict) and isinstance(j.get("name"), str) for j in jobs):
                raise ValidationError("岗位JSON需要name字段")
        else:
            # Preserve private iris-hire source exactly; user splits/reviews before adopting.
            jobs = [
                {
                    "name": path.stem,
                    "jd": text,
                    "requirements": [],
                    "dimensions": [],
                    "reviewed": False,
                    "source_type": "iris-hire-markdown",
                    "import_notice": "已保留原始岗位库，请按岗位拆分并审阅后采用",
                }
            ]
        return [self.save({**j, "id": uuid.uuid4().hex}) for j in jobs]


def leadership_standard() -> dict:
    return json.loads((Path(__file__).parent / "config/leadership-v1.0.json").read_text())
