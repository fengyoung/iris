"""Console runner contracts, using real short-lived isolated child processes."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from iris.core.exceptions import IrisRuntimeError
from iris.taskpanel.runner import JobConflict, JobNotFound, JobRunner, JobRunnerError
from iris.taskpanel.store import TaskStatus, write_current


@pytest.fixture
def make_runner(tmp_path):
    runners = []

    def make(source="import time; time.sleep(30)", **kwargs):
        script = tmp_path / f"cli{len(runners)}.py"
        script.write_text(source, encoding="utf-8")
        runner = JobRunner(
            tmp_path,
            tmp_path / "data",
            cli_script=script,
            python=sys.executable,
            cancel_timeout=0.1,
            **kwargs,
        )
        runners.append(runner)
        return runner

    yield make
    for runner in runners:
        for job_id in list(runner._children):
            runner.cancel(job_id)


def wait_done(runner, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.01)
    pytest.fail("child did not finish")


def test_start_real_process_and_persist_separate_logs(make_runner):
    runner = make_runner(
        "import os,sys,json\n"
        "print(json.dumps({'cwd':os.getcwd(),'stdin':sys.stdin.read(),'argv':sys.argv[1:],"
        "'session':os.getsid(0),'pid':os.getpid()}),flush=True)\n"
        "print('error output',file=sys.stderr,flush=True)\n"
    )
    params = {"query": "hello; $(touch should-not-exist)"}
    job = runner.start("search", params)
    assert wait_done(runner, job["id"])["status"] == "success"
    saved = json.loads((runner.jobs_dir / f"{job['id']}.json").read_text())
    assert saved["returncode"] == 0
    assert saved["params"] == params
    output = json.loads(runner.read_log(job["id"])["text"])
    assert output["cwd"] == str(runner.project_root)
    assert output["stdin"] == ""
    assert output["session"] == output["pid"] == job["pid"]
    assert "--query=hello; $(touch should-not-exist)" in output["argv"]
    assert runner.read_log(job["id"], "stderr")["text"] == "error output\n"
    assert not (runner.project_root / "should-not-exist").exists()
    assert runner.list_jobs()[0]["id"] == job["id"]


@pytest.mark.parametrize(
    "command,params",
    [
        ("unknown-command", {}),
        ("task-panel", {}),
        ("status", {"unknown": True}),
        ("status", []),
        ("search", {"top_k": "not-a-number"}),
    ],
)
def test_validation_before_spawn(make_runner, command, params):
    runner = make_runner()
    with pytest.raises(JobRunnerError):
        runner.start(command, params)
    assert runner.list_jobs() == []


def test_completion_persisted_without_api_polling(make_runner):
    runner = make_runner("import os; print(os.environ['PYTHONUNBUFFERED'])")
    job = runner.start("status")
    path = runner.jobs_dir / f"{job['id']}.json"
    deadline = time.monotonic() + 5
    while json.loads(path.read_text())["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    record = json.loads(path.read_text())
    assert record["status"] == "success"
    assert record["returncode"] == 0
    assert runner.read_log(job["id"])["text"] == "1\n"


def test_nonzero_and_rerun(make_runner):
    runner = make_runner("import sys; print('failed'); sys.exit(7)")
    first = runner.start("status")
    result = wait_done(runner, first["id"])
    assert result["status"] == "failed"
    assert result["returncode"] == 7
    second = runner.rerun(first["id"])
    assert second["id"] != first["id"]
    assert second["params"] == first["params"]
    assert wait_done(runner, second["id"])["status"] == "failed"


def test_cancel_and_running_rerun(make_runner):
    runner = make_runner()
    job = runner.start("status")
    with pytest.raises(JobConflict):
        runner.rerun(job["id"])
    cancelled = runner.cancel(job["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["returncode"] == -signal.SIGTERM
    assert runner.cancel(job["id"])["status"] == "cancelled"


def test_cancel_escalates_to_kill(make_runner):
    runner = make_runner(
        "import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "print('ready',flush=True)\ntime.sleep(30)"
    )
    job = runner.start("status")
    deadline = time.monotonic() + 5
    while not runner.read_log(job["id"])["text"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    result = runner.cancel(job["id"])
    assert result["returncode"] == -signal.SIGKILL


def test_cancel_kills_descendant_after_leader_exits(make_runner, monkeypatch):
    runner = make_runner(
        "import os,signal,time\n"
        "pid=os.fork()\n"
        "if pid == 0:\n"
        " signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        " print('ready',flush=True)\n"
        " while True:\n"
        "  with open('heartbeat','a') as f: f.write('.')\n"
        "  time.sleep(.01)\n"
        "else:\n"
        " time.sleep(30)\n"
    )
    job = runner.start("status")
    deadline = time.monotonic() + 5
    while not runner.read_log(job["id"])["text"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    child = runner._children[job["id"]]
    real_killpg = os.killpg
    signals = []

    def killpg(pid, sig):
        assert child.returncode is None  # leader is unreaped, so PID cannot be reused
        signals.append(sig)
        return real_killpg(pid, sig)

    monkeypatch.setattr("iris.taskpanel.runner.os.killpg", killpg)
    assert runner.cancel(job["id"])["status"] == "cancelled"
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    heartbeat = runner.project_root / "heartbeat"
    time.sleep(0.1)
    size = heartbeat.stat().st_size
    time.sleep(0.1)
    assert heartbeat.stat().st_size == size
    assert child.returncode == -signal.SIGTERM
    assert job["id"] not in runner._children


def test_capacity_is_shared_and_concurrent(make_runner):
    first, second = make_runner(), make_runner()

    def start(index):
        try:
            return (first if index % 2 else second).start("status")
        except JobConflict:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(start, range(8)))
    assert sum(result is not None for result in results) == 4
    assert len(first.list_jobs()) == 4


def test_exclusive_group_and_live_reporter(make_runner):
    runner = make_runner()
    job = runner.start("build-graph")
    with pytest.raises(JobConflict, match="互斥组"):
        runner.start("build-chunks")
    runner.cancel(job["id"])
    write_current(
        runner.data_root, TaskStatus("external", "daily-start", pid=os.getpid())
    )
    with pytest.raises(JobConflict, match="Reporter"):
        runner.start("build-graph")
    # Nonexclusive commands remain available alongside a Reporter task.
    runner.start("status")


def test_dead_reporter_does_not_block(make_runner, monkeypatch):
    runner = make_runner()
    write_current(runner.data_root, TaskStatus("dead", "daily-start", pid=12345))
    monkeypatch.setattr("iris.taskpanel.runner.is_pid_alive", lambda pid: False)
    runner.start("build-graph")


def test_restart_never_signals_unknown_pid(make_runner, monkeypatch):
    owner = make_runner()
    job = owner.start("status")
    observer = make_runner()
    assert observer.get(job["id"])["can_cancel"] is False
    with monkeypatch.context() as patch:
        patch.setattr(
            "iris.taskpanel.runner.os.killpg",
            lambda *args: pytest.fail("unknown process must not receive a signal"),
        )
        with pytest.raises(JobConflict):
            observer.cancel(job["id"])
    owner.cancel(job["id"])


def test_stale_job_is_interrupted_not_success(make_runner):
    runner = make_runner()
    job = runner.start("status")
    runner.cancel(job["id"])
    path = runner.jobs_dir / f"{job['id']}.json"
    record = json.loads(path.read_text())
    record.update(status="running", pid=None, returncode=None)
    path.write_text(json.dumps(record))
    restored = make_runner().get(job["id"])
    assert restored["status"] == "interrupted"
    assert restored["returncode"] is None


def test_log_offsets_validation_and_traversal(make_runner):
    runner = make_runner("print('abcdef',end='')")
    job = runner.start("status")
    wait_done(runner, job["id"])
    first = runner.read_log(job["id"], limit=3)
    assert first["text"] == "abc"
    assert first["offset"] == first["next_offset"] == 3
    second = runner.logs(job["id"], offset=first["offset"])
    assert second["text"] == "def"
    assert second["eof"]
    assert runner.read_log(job["id"], offset=99)["text"] == ""
    with pytest.raises(JobRunnerError):
        runner.read_log(job["id"], stream="../../evil")
    with pytest.raises(JobRunnerError):
        runner.read_log(job["id"], offset=-1)
    with pytest.raises(JobNotFound):
        runner.get("../../evil")
    with pytest.raises(JobNotFound):
        runner.get("a" * 32)


def test_unicode_log_pages_make_progress(make_runner):
    runner = make_runner(
        "import sys; sys.stdout.buffer.write('中文'.encode()+b'\\xe4')"
    )
    job = runner.start("status")
    wait_done(runner, job["id"])
    first = runner.read_log(job["id"], limit=1)
    assert first["text"] == "中"
    assert first["offset"] == 3
    second = runner.read_log(job["id"], offset=3, limit=3)
    assert second["text"] == "文"
    last = runner.read_log(job["id"], offset=6)
    assert last["text"] == "\ufffd"
    assert last["eof"]


def test_spawn_failure_is_persisted(make_runner):
    runner = make_runner()
    runner.python = "/definitely/missing/python"
    with pytest.raises(JobRunnerError, match="启动任务失败"):
        runner.start("status")
    assert runner.list_jobs()[0]["status"] == "failed"
    assert issubclass(JobRunnerError, IrisRuntimeError)
    assert issubclass(JobConflict, IrisRuntimeError)
    assert issubclass(JobNotFound, IrisRuntimeError)
