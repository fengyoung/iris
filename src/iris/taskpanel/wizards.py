"""Guided console operations: read local state and submit validated CLI jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from iris.core.exceptions import IrisRuntimeError
from iris.core.locks import FileLock
from iris.utils.shared import atomic_write_json, atomic_write_text
from iris.wiki._sensitive import is_sensitive_path, is_sensitive_title


class WizardError(IrisRuntimeError):
    """Invalid wizard input or unavailable local state."""


class WizardService:
    """Read-only views plus CLI-only business mutations.

    read(name, job_id=None): memory/feed/meeting/wiki.
    action(name, payload): memory-confirm/pin/delete, feed-confirm/ignore,
    meeting-transcribe, feishu-preview/confirm, wiki-discover/select/preview/write/lint.
    Wiki selections contain indices, never client-authored candidate content.
    """

    def __init__(self, project_root: Path, data_root: Path, runner):
        self.project_root = Path(project_root).resolve()
        self.data_root = Path(data_root).resolve()
        self.runner = runner
        self.review_dir = self.data_root / "tasks" / "console" / "reviews"

    @staticmethod
    def _json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default
        except (OSError, ValueError) as exc:
            raise WizardError("本地数据无法读取") from exc

    @staticmethod
    def _fields(
        payload: dict, allowed: set[str], required: set[str] | None = None
    ) -> None:
        if set(payload) - allowed or (required or set()) - set(payload):
            raise WizardError("参数缺失或包含未允许的字段")

    @staticmethod
    def _text(value: Any) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\x00" in value
            or "\n" in value
        ):
            raise WizardError("需要非空单行文本")
        return value.strip()

    def read(self, name: str, job_id: str | None = None) -> dict:
        from iris.config.loader import ConfigError

        try:
            return self._read(name, job_id)
        except (ConfigError, OSError, UnicodeError) as exc:
            raise WizardError(
                f"无法读取向导数据（{name}）：请检查本地配置与文件访问权限"
            ) from exc

    def _read(self, name: str, job_id: str | None = None) -> dict:
        if name == "memory":
            from iris.config.loader import load_config_bundle
            from iris.memory.manager import LongTermMemoryManager

            return LongTermMemoryManager(
                load_config_bundle(self.project_root)
            ).list_memory("corrections")
        if name == "feed":
            items = self._json(self.data_root / "feed_pending.json", [])
            if not isinstance(items, list):
                raise WizardError("Feed 待确认队列格式错误")
            return {
                "items": [
                    item
                    for item in items
                    if isinstance(item, dict)
                    and not is_sensitive_title(str(item.get("title", "")))
                    and not self._sensitive_paths(item.get("sources", []))
                ]
            }
        if name == "meeting":
            return {"items": self._meetings()}
        if name == "wiki":
            jobs = [
                job
                for job in self.runner.list_jobs()
                if job.get("command") == "discover-wiki"
                and job.get("status") == "success"
            ]
            selected = job_id or (jobs[0]["id"] if jobs else None)
            return {
                "jobs": [
                    {"id": job["id"], "started_at": job.get("started_at", "")}
                    for job in jobs
                ],
                "job_id": selected,
                "items": self._candidates(selected) if selected else [],
            }
        raise WizardError("未知向导")

    @staticmethod
    def _sensitive_paths(paths: Any) -> bool:
        if isinstance(paths, str):
            paths = [paths]
        return any(is_sensitive_path(str(path)) for path in (paths or []))

    def _meetings(self) -> list[dict]:
        from iris.config.loader import load_env_file

        configured = os.environ.get("IRIS_MEETING_TRANS_DIR") or load_env_file(
            self.project_root / ".env"
        ).get("IRIS_MEETING_TRANS_DIR")
        if not configured:
            return []
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = self.project_root / root
        root = root.resolve()
        if not root.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in root.rglob("*"):
            resolved = path.resolve()
            if (
                not resolved.is_relative_to(root)
                or not resolved.is_file()
                or path.suffix.lower()
                not in {".txt", ".md", ".srt", ".vtt", ".mp3", ".wav", ".m4a", ".mp4"}
                or is_sensitive_path(str(path))
                or is_sensitive_path(str(resolved))
            ):
                continue
            items.append(
                {
                    "id": hashlib.sha256(str(resolved).encode()).hexdigest(),
                    "name": path.name,
                    "path": str(resolved),
                    "modified_at": resolved.stat().st_mtime,
                    "kind": "transcript"
                    if path.suffix.lower() in {".txt", ".md", ".srt", ".vtt"}
                    else "audio",
                }
            )
        return sorted(items, key=lambda item: item["modified_at"], reverse=True)[:100]

    def _success(self, job_id: str, command: str) -> dict:
        job = self.runner.get(self._text(job_id))
        if job.get("command") != command or job.get("status") != "success":
            raise WizardError("需要对应命令已成功完成的预览任务")
        return job

    def _candidates(self, job_id: str) -> list[dict]:
        self._success(job_id, "discover-wiki")
        log = self.runner.read_log(job_id, "stdout", offset=0, limit=1048576)
        if not log.get("eof"):
            raise WizardError("候选输出超过读取上限，请减少 discover limit")
        try:
            payload = json.loads(log["text"])
        except (ValueError, KeyError) as exc:
            raise WizardError("发现任务输出不是有效 JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise WizardError("发现任务输出缺少 items")
        result = []
        for index, item in enumerate(payload["items"]):
            if not isinstance(item, dict):
                continue
            title, query, page_type = (
                item.get(key) for key in ("title", "query", "page_type")
            )
            if (
                not isinstance(title, str)
                or not isinstance(query, str)
                or not title.strip()
                or not query.strip()
                or page_type not in {"domain", "concept", "project", "person"}
                or is_sensitive_title(title)
                or is_sensitive_path(str(item.get("wiki_path", "")))
                or self._sensitive_paths(item.get("sample_paths", []))
            ):
                continue
            result.append({**item, "index": index})
        return result

    def action(self, name: str, payload: dict | None = None) -> dict:
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise WizardError("payload 必须是对象")
        if name in {"memory-confirm", "memory-pin", "memory-delete"}:
            return self._memory(name, payload)
        if name in {"feed-confirm", "feed-ignore"}:
            self._fields(payload, {"topic_id"}, {"topic_id"})
            topic = self._text(payload["topic_id"])
            if not any(
                str(item.get("topic_id")) == topic
                for item in self.read("feed")["items"]
            ):
                raise WizardError("待确认话题不存在")
            return self.runner.start(name, {"topic_id": topic})
        if name == "meeting-transcribe":
            return self._meeting(payload)
        if name in {"feishu-preview", "feishu-confirm"}:
            return self._feishu(name, payload)
        if name.startswith("wiki-"):
            return self._wiki(name, payload)
        raise WizardError("未知向导操作")

    def _memory(self, name: str, payload: dict) -> dict:
        flag = {"memory-confirm": "unconfirm", "memory-pin": "unpin"}.get(name)
        self._fields(payload, {"concept"} | ({flag} if flag else set()), {"concept"})
        concept = self._text(payload["concept"])
        if not any(
            item.get("concept") == concept for item in self.read("memory")["items"]
        ):
            raise WizardError("纠正规则不存在")
        params = {"concept": concept}
        if flag and flag in payload:
            if type(payload[flag]) is not bool:
                raise WizardError("开关参数必须为布尔值")
            params[flag] = payload[flag]
        return self.runner.start(name, params)

    def _meeting(self, payload: dict) -> dict:
        self._fields(payload, {"file_id", "to_source"}, {"file_id"})
        selected = next(
            (item for item in self._meetings() if item["id"] == payload["file_id"]),
            None,
        )
        if selected is None:
            raise WizardError("文件不在当前允许的最近会议列表")
        to_source = payload.get("to_source", False)
        if type(to_source) is not bool:
            raise WizardError("to_source 必须为布尔值")
        key = "transcript_file" if selected["kind"] == "transcript" else "audio_file"
        return self.runner.start(
            "transcribe-meeting", {key: selected["path"], "to_source": to_source}
        )

    def _feishu(self, name: str, payload: dict) -> dict:
        self._fields(payload, {"url", "preview_job_id"}, {"url"})
        url = self._text(payload["url"])
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or not re.search(r"/(docx|wiki|docs)/[^/]+", parsed.path)
        ):
            raise WizardError("需要有效的 HTTPS 飞书文档 URL")
        if name == "feishu-preview":
            if "preview_job_id" in payload:
                raise WizardError("预览不接受 preview_job_id")
            return self.runner.start(
                "feishu-doc-convert", {"url": url, "dry_run": True}
            )
        job = self._success(
            self._text(payload.get("preview_job_id")), "feishu-doc-convert"
        )
        if job.get("params") != {"url": url, "dry_run": True}:
            raise WizardError("URL 已变更或任务不是相同参数的预览，请重新预览")
        return self.runner.start("feishu-doc-convert", {"url": url, "dry_run": False})

    def _review(self, review_id: str) -> tuple[Path, dict]:
        if not isinstance(review_id, str) or not re.fullmatch(
            r"[a-f0-9]{32}", review_id
        ):
            raise WizardError("审核记录不存在")
        path = self.review_dir / f"{review_id}.jsonl"
        meta = self._json(self.review_dir / f"{review_id}.json", None)
        if not isinstance(meta, dict) or not path.is_file():
            raise WizardError("审核记录不存在")
        if hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("sha256"):
            raise WizardError("审核记录已变更，请重新选择候选")
        return path, meta

    def _select(self, payload: dict) -> dict:
        self._fields(payload, {"job_id", "selected"}, {"job_id", "selected"})
        selected = payload["selected"]
        if (
            not isinstance(selected, list)
            or not selected
            or any(type(index) is not int for index in selected)
        ):
            raise WizardError("selected 必须是非空候选索引数组")
        candidates = {
            item["index"]: item for item in self._candidates(payload["job_id"])
        }
        if len(set(selected)) != len(selected) or any(
            index not in candidates for index in selected
        ):
            raise WizardError("候选索引不可用")
        rows = [
            {key: candidates[index][key] for key in ("title", "query", "page_type")}
            for index in selected
        ]
        content = "".join(
            json.dumps({**row, "selected": True}, ensure_ascii=False) + "\n"
            for row in rows
        )
        review_id = uuid.uuid4().hex
        with FileLock(self.review_dir / "reviews"):
            atomic_write_text(self.review_dir / f"{review_id}.jsonl", content)
            atomic_write_json(
                self.review_dir / f"{review_id}.json",
                {
                    "job_id": payload["job_id"],
                    "selected": selected,
                    "sha256": hashlib.sha256(content.encode()).hexdigest(),
                },
            )
        return {"review_id": review_id, "items": rows}

    def _validate_wiki_preview(self, job_id: str, path: Path) -> None:
        log = self.runner.read_log(job_id, "stdout", offset=0, limit=1048576)
        if not log.get("eof"):
            raise WizardError("预览输出超过校验上限，请减少所选页面")
        try:
            output = json.loads(log["text"])
            expected = [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.strip()
            ]
        except (ValueError, KeyError) as exc:
            raise WizardError("预览输出无效，请重新预览") from exc
        items = output.get("items") if isinstance(output, dict) else None
        if not isinstance(items, list) or len(items) != len(expected):
            raise WizardError("预览未完整生成所选页面")
        for item, selected in zip(items, expected):
            if (
                not isinstance(item, dict)
                or item.get("status") not in (None, "success")
                or item.get("error")
                or any(
                    item.get(key) != selected[key]
                    for key in ("query", "title", "page_type")
                )
                or not isinstance(item.get("markdown"), str)
                or not item["markdown"].strip()
                or not item.get("output_path")
            ):
                raise WizardError("预览包含拒绝、失败或缺失草稿，不能写入")

    def _wiki(self, name: str, payload: dict) -> dict:
        if name == "wiki-discover":
            self._fields(payload, {"limit", "incremental"})
            return self.runner.start("discover-wiki", payload)
        if name == "wiki-select":
            return self._select(payload)
        if name == "wiki-lint":
            self._fields(payload, set())
            return self.runner.start("wiki-lint", {})
        if name not in {"wiki-preview", "wiki-write"}:
            raise WizardError("未知 Wiki 操作")
        self._fields(payload, {"review_id", "preview_job_id"}, {"review_id"})
        with FileLock(self.review_dir / "reviews"):
            path, _ = self._review(payload["review_id"])
            params = {"review_file": str(path), "write": False}
            if name == "wiki-write":
                job = self._success(
                    self._text(payload.get("preview_job_id")), "build-wiki"
                )
                if job.get("params") != params:
                    raise WizardError("需要相同审核记录已成功完成的预览")
                self._validate_wiki_preview(job["id"], path)
                params["write"] = True
            elif "preview_job_id" in payload:
                raise WizardError("预览不接受 preview_job_id")
            return self.runner.start("build-wiki", params)
