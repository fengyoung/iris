"""Wizard contracts; no CLI, network or model calls are performed."""

import json
from pathlib import Path

import pytest

from iris.taskpanel.wizards import WizardError, WizardService


class FakeRunner:
    def __init__(self):
        self.jobs = {}
        self.output = {}

    def start(self, command, params):
        job = {
            "id": str(len(self.jobs)),
            "command": command,
            "params": dict(params),
            "status": "running",
        }
        self.jobs[job["id"]] = job
        return dict(job)

    def get(self, job_id):
        return dict(self.jobs[job_id])

    def list_jobs(self):
        return list(reversed(list(self.jobs.values())))

    def read_log(self, job_id, stream, **kwargs):
        return {"text": self.output[job_id], "eof": True}

    def success(self, job, output=None):
        self.jobs[job["id"]]["status"] = "success"
        if output is not None:
            self.output[job["id"]] = json.dumps(output)


@pytest.fixture
def service(tmp_path):
    return WizardService(tmp_path, tmp_path / "data", FakeRunner())


def discovery(service):
    job = service.action("wiki-discover", {"limit": 10})
    service.runner.success(
        job,
        {
            "items": [
                {
                    "title": "主题",
                    "query": "检索词",
                    "page_type": "concept",
                    "sample_paths": ["正常.md"],
                },
                {"title": "绩效评估", "query": "x", "page_type": "person"},
                {
                    "title": "安全标题",
                    "query": "x",
                    "page_type": "domain",
                    "sample_paths": ["调薪.md"],
                },
            ]
        },
    )
    return job


def test_wiki_selection_preview_write_and_lint(service):
    job = discovery(service)
    view = service.read("wiki")
    assert [item["index"] for item in view["items"]] == [0]
    selected = service.action("wiki-select", {"job_id": job["id"], "selected": [0]})
    preview = service.action("wiki-preview", {"review_id": selected["review_id"]})
    path = Path(preview["params"]["review_file"])
    assert path.is_relative_to(service.data_root / "tasks" / "console")
    assert json.loads(path.read_text()) == {
        "title": "主题",
        "query": "检索词",
        "page_type": "concept",
        "selected": True,
    }
    with pytest.raises(WizardError):
        service.action(
            "wiki-write",
            {"review_id": selected["review_id"], "preview_job_id": preview["id"]},
        )
    service.runner.success(
        preview,
        {
            "items": [
                {
                    "title": "主题",
                    "query": "检索词",
                    "page_type": "concept",
                    "markdown": "# 主题\n内容",
                    "output_path": "/wiki/主题.md",
                    "slug": "主题",
                }
            ]
        },
    )
    written = service.action(
        "wiki-write",
        {"review_id": selected["review_id"], "preview_job_id": preview["id"]},
    )
    assert written["params"] == {"review_file": str(path), "write": True}
    assert service.action("wiki-lint")["command"] == "wiki-lint"


@pytest.mark.parametrize("selected", [[1], [2], [99], [], [True], [0, 0], "0"])
def test_wiki_rejects_invalid_selection(service, selected):
    job = discovery(service)
    with pytest.raises(WizardError):
        service.action("wiki-select", {"job_id": job["id"], "selected": selected})


@pytest.mark.parametrize(
    "output",
    [
        {
            "items": [
                {
                    "title": "主题",
                    "query": "检索词",
                    "page_type": "concept",
                    "status": "refused_sensitive",
                }
            ]
        },
        {"items": []},
        {
            "items": [
                {
                    "title": "主题",
                    "query": "检索词",
                    "page_type": "concept",
                    "error": "failed",
                }
            ]
        },
        {
            "items": [
                {
                    "title": "different",
                    "query": "检索词",
                    "page_type": "concept",
                    "markdown": "draft",
                    "output_path": "/wiki/x",
                }
            ]
        },
    ],
)
def test_wiki_rejects_exit_zero_business_failures(service, output):
    job = discovery(service)
    selected = service.action("wiki-select", {"job_id": job["id"], "selected": [0]})
    preview = service.action("wiki-preview", {"review_id": selected["review_id"]})
    service.runner.success(preview, output)
    before = len(service.runner.jobs)
    with pytest.raises(WizardError):
        service.action(
            "wiki-write",
            {"review_id": selected["review_id"], "preview_job_id": preview["id"]},
        )
    assert len(service.runner.jobs) == before


def test_wiki_rejects_client_contents_and_review_path(service):
    job = discovery(service)
    with pytest.raises(WizardError):
        service.action(
            "wiki-select", {"job_id": job["id"], "selected": [0], "items": []}
        )
    with pytest.raises(WizardError):
        service.action("wiki-preview", {"review_id": "../../evil"})
    with pytest.raises(WizardError):
        service.action("wiki-preview", {"review_file": "/tmp/arbitrary"})


def test_wiki_preview_bound_to_immutable_review(service):
    job = discovery(service)
    selected = service.action("wiki-select", {"job_id": job["id"], "selected": [0]})
    preview = service.action(
        "wiki-preview", selected_payload := {"review_id": selected["review_id"]}
    )
    service.runner.success(preview)
    other = service.action("wiki-select", {"job_id": job["id"], "selected": [0]})
    with pytest.raises(WizardError):
        service.action(
            "wiki-write",
            {"review_id": other["review_id"], "preview_job_id": preview["id"]},
        )
    Path(preview["params"]["review_file"]).write_text("{}")
    with pytest.raises(WizardError, match="变更"):
        service.action("wiki-preview", selected_payload)


def test_discovery_requires_success_and_valid_json(service):
    job = service.action("wiki-discover")
    with pytest.raises(WizardError):
        service.read("wiki", job["id"])
    service.runner.success(job, {"not_items": []})
    with pytest.raises(WizardError):
        service.read("wiki", job["id"])
    service.runner.output[job["id"]] = "not json"
    with pytest.raises(WizardError):
        service.read("wiki", job["id"])


def test_feishu_confirmation_bound_to_url_and_preview(service):
    url = "https://example.feishu.cn/docx/abc"
    preview = service.action("feishu-preview", {"url": url})
    assert preview["params"] == {"url": url, "dry_run": True}
    payload = {"url": url, "preview_job_id": preview["id"]}
    with pytest.raises(WizardError):
        service.action("feishu-confirm", payload)
    service.runner.success(preview)
    with pytest.raises(WizardError):
        service.action("feishu-confirm", {**payload, "url": url + "different"})
    assert service.action("feishu-confirm", payload)["params"] == {
        "url": url,
        "dry_run": False,
    }
    with pytest.raises(WizardError):
        service.action("feishu-preview", {"url": "file:///etc/passwd"})
    with pytest.raises(WizardError):
        service.action("feishu-preview", {"url": url, "force": True})


def test_meeting_configured_directory_and_sensitive_symlinks(
    service, monkeypatch, tmp_path
):
    root = tmp_path / "meetings"
    root.mkdir()
    good = root / "产品讨论.txt"
    good.write_text("正文出现绩效不会按正文过滤")
    (root / "调薪.txt").write_text("secret")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (root / "outside-link.txt").symlink_to(outside)
    (root / "safe-link.txt").symlink_to(root / "调薪.txt")
    monkeypatch.setenv("IRIS_MEETING_TRANS_DIR", str(root))
    items = service.read("meeting")["items"]
    assert len(items) == 1
    job = service.action(
        "meeting-transcribe", {"file_id": items[0]["id"], "to_source": True}
    )
    assert job["command"] == "transcribe-meeting"
    assert job["params"] == {"transcript_file": str(good), "to_source": True}
    with pytest.raises(WizardError):
        service.action("meeting-transcribe", {"file_id": str(outside)})
    with pytest.raises(WizardError):
        service.action(
            "meeting-transcribe", {"file_id": items[0]["id"], "output": "/tmp/evil"}
        )
    good.unlink()
    with pytest.raises(WizardError):
        service.action("meeting-transcribe", {"file_id": items[0]["id"]})


def test_feed_reads_and_submits_only_existing_items(service):
    service.data_root.mkdir()
    path = service.data_root / "feed_pending.json"
    path.write_text(
        json.dumps(
            [
                {"topic_id": "ok", "title": "产品"},
                {"topic_id": "secret", "title": "薪资"},
            ]
        )
    )
    assert len(service.read("feed")["items"]) == 1
    for command in ("feed-confirm", "feed-ignore"):
        assert service.action(command, {"topic_id": "ok"})["command"] == command
    with pytest.raises(WizardError):
        service.action("feed-confirm", {"topic_id": "secret"})
    assert len(json.loads(path.read_text())) == 2


def test_memory_actions_use_runner_only(service, monkeypatch):
    original_read = service.read
    monkeypatch.setattr(
        service,
        "read",
        lambda name: (
            {"items": [{"concept": "术语"}]}
            if name == "memory"
            else original_read(name)
        ),
    )
    for command in ("memory-confirm", "memory-pin", "memory-delete"):
        assert service.action(command, {"concept": "术语"})["params"] == {
            "concept": "术语"
        }
    assert service.action("memory-pin", {"concept": "术语", "unpin": True})["params"][
        "unpin"
    ]
    with pytest.raises(WizardError):
        service.action("memory-pin", {"concept": "术语", "unpin": "false"})
    with pytest.raises(WizardError):
        service.action("memory-delete", {"concept": "unknown"})


def test_sensitive_source_identities_filtered_without_scanning_content(service):
    service.data_root.mkdir()
    (service.data_root / "feed_pending.json").write_text(
        json.dumps(
            [
                {
                    "topic_id": "hidden",
                    "title": "普通标题",
                    "sources": ["SOURCE/薪资.md"],
                },
                {
                    "topic_id": "string",
                    "title": "普通标题",
                    "sources": "SOURCE/调薪.md",
                },
                {
                    "topic_id": "visible",
                    "title": "业务讨论",
                    "sources": ["SOURCE/产品.md"],
                    "summary": "正文提及绩效不应触发过滤",
                },
            ]
        )
    )
    assert [item["topic_id"] for item in service.read("feed")["items"]] == ["visible"]
    with pytest.raises(WizardError):
        service.action("feed-confirm", {"topic_id": "hidden"})
    job = service.action("wiki-discover")
    service.runner.success(
        job,
        {
            "items": [
                {
                    "title": "普通主题",
                    "query": "内容",
                    "page_type": "concept",
                    "wiki_path": "Wiki/人才盘点.md",
                },
                {
                    "title": "产品主题",
                    "query": "正文提及薪资",
                    "page_type": "concept",
                    "wiki_path": "Wiki/产品.md",
                },
            ]
        },
    )
    assert [item["index"] for item in service.read("wiki")["items"]] == [1]
    with pytest.raises(WizardError):
        service.action("wiki-select", {"job_id": job["id"], "selected": [0]})


def test_missing_config_is_readable_wizard_error(service):
    from iris.config.loader import ConfigError

    with pytest.raises(WizardError, match="本地配置") as error:
        service.read("memory")
    assert isinstance(error.value.__cause__, ConfigError)


@pytest.mark.parametrize(
    "failure", [PermissionError("denied"), UnicodeError("invalid encoding")]
)
def test_known_read_failures_wrapped(service, monkeypatch, failure):
    def fail():
        raise failure

    monkeypatch.setattr(service, "_meetings", fail)
    with pytest.raises(WizardError, match="文件访问权限") as error:
        service.read("meeting")
    assert error.value.__cause__ is failure


def test_programming_errors_not_hidden(service, monkeypatch):
    def fail():
        raise TypeError("programming error")

    monkeypatch.setattr(service, "_meetings", fail)
    with pytest.raises(TypeError, match="programming error"):
        service.read("meeting")


def test_unknown_actions_and_payloads(service):
    with pytest.raises(WizardError):
        service.action("unknown")
    with pytest.raises(WizardError):
        service.read("unknown")
    with pytest.raises(WizardError):
        service.action("wiki-lint", {"fix": True})
    with pytest.raises(WizardError):
        service.action("wiki-discover", [])
