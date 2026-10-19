"""同步内容不得覆盖人工 pin/确认元数据，且须与 store 共用读改写锁。"""

from __future__ import annotations

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from iris.core.locks import FileLock, FileLockError
from iris.utils.shared import atomic_write_json

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync_memory.py"
_spec = importlib.util.spec_from_file_location("sync_memory_pinning", _SCRIPT)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)

_MD = """---
name: signature-rule
description: 文档签名规则
type: feedback
sync_to_iris: true
iris_target: corrections
pinned: true
pinned_at: forged
confirmed: true
confirmed_at: forged
---
**Why:** 纠正规则必须统一，普通 CC 文本不能代替人工置顶。
**How to apply:** 输出文档时追加新的签名行。
"""


def _seed(tmp_path, items):
    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()
    (cc_dir / "rule.md").write_text(_MD, encoding="utf-8")
    iris_dir = tmp_path / "iris"
    corrections_path = iris_dir / "long_term" / "corrections.json"
    atomic_write_json(corrections_path, {"items": items, "updated_at": "old"})
    return cc_dir, iris_dir, corrections_path


def _items(path):
    return json.loads(path.read_text(encoding="utf-8"))["items"]


@pytest.mark.parametrize("cc_pin", ["true", "false"])
@pytest.mark.parametrize("match", ["exact", "source", "description"])
@pytest.mark.parametrize("metadata", [
    {"pinned": True, "pinned_at": "manual-pin", "confirmed": True,
     "confirmed_at": "manual-confirm"},
    {"pinned": False, "pinned_at": "", "confirmed": False, "confirmed_at": ""},
    {},
])
def test_content_update_preserves_manual_metadata(tmp_path, match, metadata, cc_pin):
    concept = "signature rule" if match == "exact" else "文档签名规则"
    old = {"preferred": "旧规则", "update_count": 8, "updated_at": "old",
           "last_source": "signature-rule" if match == "source" else "human",
           **metadata}
    cc_dir, iris_dir, path = _seed(tmp_path, {concept: old})
    (cc_dir / "rule.md").write_text(
        _MD.replace("pinned: true", f"pinned: {cc_pin}"), encoding="utf-8",
    )
    # description 分支不能被来源匹配抢先命中。
    stats = sm.run_sync(cc_dir, iris_dir)
    after = _items(path)
    assert list(after) == [concept]
    entry = after[concept]
    assert entry["preferred"] != old["preferred"]
    assert "新的签名行" in entry["preferred"]
    assert entry["update_count"] == 9
    assert entry["updated_at"] != "old"
    assert entry["last_source"] == "signature-rule"
    for key in ("pinned", "pinned_at", "confirmed", "confirmed_at"):
        if key in metadata:
            assert entry[key] == metadata[key]
        else:
            assert key not in entry
    assert stats["corrections_updated"] == 1
    assert stats["corrections_added"] == 0

    before = path.read_bytes()
    assert sm.run_sync(cc_dir, iris_dir)["synced"] is False
    assert path.read_bytes() == before


def test_new_cc_entry_cannot_set_pin_or_confirmation(tmp_path):
    cc_dir, iris_dir, path = _seed(tmp_path, {})
    stats = sm.run_sync(cc_dir, iris_dir)
    entry = _items(path)["signature rule"]
    assert stats["corrections_added"] == 1
    assert entry["update_count"] == 1
    assert not {"pinned", "pinned_at", "confirmed", "confirmed_at"} & entry.keys()


@pytest.mark.parametrize("reverse", [False, True])
def test_dry_run_leaves_existing_content_and_lock_files_untouched(tmp_path, reverse):
    old = {"preferred": "旧规则", "pinned": True, "pinned_at": "manual"}
    cc_dir, iris_dir, path = _seed(tmp_path, {"signature rule": old})
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    stats = sm.run_sync(cc_dir, iris_dir, dry_run=True, reverse=reverse)
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert stats["corrections_updated"] == 1
    assert stats["dry_run"] is True
    assert after == before


def test_dry_run_does_not_create_iris_directory(tmp_path):
    cc_dir = tmp_path / "cc"
    cc_dir.mkdir()
    (cc_dir / "rule.md").write_text(_MD, encoding="utf-8")
    iris_dir = tmp_path / "absent"
    assert sm.run_sync(cc_dir, iris_dir, dry_run=True)["corrections_added"] == 1
    assert not iris_dir.exists()


def test_locks_cover_reads_merge_and_both_atomic_writes(tmp_path, monkeypatch):
    cc_dir, iris_dir, path = _seed(tmp_path, {})
    profile_path = path.with_name("profile.json")
    atomic_write_json(profile_path, {"user_preferences": {"likes": ["已有偏好"]}})
    stages = []
    original_load = sm._load_json
    original_sync = sm._sync_one
    original_write = sm.atomic_write_json

    def assert_locked(stage):
        stages.append(stage)
        for target in (profile_path, path):
            with pytest.raises(FileLockError):
                with FileLock(target, blocking=False):
                    pass

    def load(target, default):
        assert_locked("read")
        return original_load(target, default)

    def sync(state, source):
        assert_locked("merge")
        return original_sync(state, source)

    def write(target, payload):
        assert_locked("write")
        # 原子替换前旧 inode 保持可读；替换后的路径指向新 inode。
        with target.open("rb") as old_file:
            before = old_file.read()
            original_write(target, payload)
            old_file.seek(0)
            assert old_file.read() == before
            assert json.loads(target.read_text(encoding="utf-8")) == payload

    monkeypatch.setattr(sm, "_load_json", load)
    monkeypatch.setattr(sm, "_sync_one", sync)
    monkeypatch.setattr(sm, "atomic_write_json", write)
    assert sm.run_sync(cc_dir, iris_dir)["synced"] is True
    assert stages == ["read", "read", "merge", "write", "write"]
    assert json.loads(profile_path.read_text())["user_preferences"]["likes"] == ["已有偏好"]
    for target in (profile_path, path):
        assert target.with_suffix(".json.lock").exists()
        with FileLock(target, blocking=False):
            pass  # 同步结束后均已释放，锁文件仍保留。


def test_concurrent_pin_waits_for_sync_then_preserves_new_content(tmp_path, monkeypatch):
    cc_dir, iris_dir, path = _seed(tmp_path, {
        "signature rule": {"preferred": "旧规则", "update_count": 1},
    })
    started = Event()
    checked = Event()
    blocked = []
    original_sync = sm._sync_one

    def pin():
        assert started.wait(5)
        try:
            with FileLock(path, blocking=False):
                blocked.append(False)
        except FileLockError:
            blocked.append(True)
        finally:
            checked.set()
        with FileLock(path):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["items"]["signature rule"].update(pinned=True, pinned_at="concurrent")
            atomic_write_json(path, payload)

    def sync(state, source):
        # Iris 已读取，暂停在同步合并前，制造旧实现会丢 pin 的窗口。
        started.set()
        assert checked.wait(5)
        assert blocked == [True]
        return original_sync(state, source)

    monkeypatch.setattr(sm, "_sync_one", sync)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pin)
        sm.run_sync(cc_dir, iris_dir)
        future.result(timeout=5)
    entry = _items(path)["signature rule"]
    assert entry["pinned"] is True
    assert entry["pinned_at"] == "concurrent"
    assert "新的签名行" in entry["preferred"]
    assert entry["update_count"] == 2
