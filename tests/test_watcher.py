"""测试文件系统监听器 — ingest/watcher.py。"""

from __future__ import annotations

import time


from iris.ingest.watcher import FileEvent, SourceWatcher


class TestFileEvent:
    """FileEvent 数据类。"""

    def test_creation(self):
        event = FileEvent(path="/a/b.md", relative_path="b.md", event_type="created")
        assert event.path == "/a/b.md"
        assert event.relative_path == "b.md"
        assert event.event_type == "created"
        assert event.detected_at

    def test_default_detected_at(self):
        e1 = FileEvent(path="a", relative_path="a", event_type="modified")
        e2 = FileEvent(path="a", relative_path="a", event_type="modified")
        assert e1.detected_at


def _make_minimal_bundle(temp_project, source_dir):
    """创建最小配置 bundle，避免 Pydantic 校验。"""
    from iris.config.models import ConfigBundleV2

    return ConfigBundleV2.from_dicts(
        root=temp_project,
        app_dict={
            "version": "3.0",
            "paths": {
                "output_dir": "./output", "temp_dir": "./temp",
                "memory_dir": "./memory", "data_dir": "./data", "log_dir": "./logs",
            },
        },
        data_source_dict={
            "version": "1.0",
            "default_source": "test",
            "sources": {"test": {"enabled": True, "path": str(source_dir)}},
        },
        llm_dict={},
    )


class TestSourceWatcher:
    def test_poll_initial_no_events(self, temp_project):
        """首次 poll 应返回空列表（建立快照，无变更事件）。"""
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        bundle = _make_minimal_bundle(temp_project, source_dir)

        watcher = SourceWatcher(bundle)
        events = watcher.poll()
        assert events == []

    def test_poll_detects_new_file(self, temp_project):
        """新文件应被检测为 created。"""
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        bundle = _make_minimal_bundle(temp_project, source_dir)

        watcher = SourceWatcher(bundle)
        watcher.poll()  # 首次建立快照

        new_file = source_dir / "new_doc.md"
        new_file.write_text("# 新文档", encoding="utf-8")

        events = watcher.poll()
        assert len(events) == 1
        assert events[0].event_type == "created"
        assert "new_doc.md" in events[0].path

    def test_poll_detects_modified_file(self, temp_project):
        """已有文件修改应被检测为 modified。"""
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        bundle = _make_minimal_bundle(temp_project, source_dir)

        watcher = SourceWatcher(bundle)

        existing = source_dir / "existing.md"
        existing.write_text("# 原始", encoding="utf-8")
        watcher.poll()  # 建立快照

        time.sleep(0.15)  # 确保 mtime 变化超过 0.1s 容差
        existing.write_text("# 修改后", encoding="utf-8")

        events = watcher.poll()
        modified = [e for e in events if e.event_type == "modified"]
        assert len(modified) >= 1
        assert "existing.md" in modified[0].path

    def test_poll_detects_deleted_file(self, temp_project):
        """文件删除应被检测为 deleted。"""
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        bundle = _make_minimal_bundle(temp_project, source_dir)

        watcher = SourceWatcher(bundle)

        doomed = source_dir / "delete_me.md"
        doomed.write_text("# 将被删除", encoding="utf-8")
        watcher.poll()  # 建立快照

        doomed.unlink()
        events = watcher.poll()
        deleted = [e for e in events if e.event_type == "deleted"]
        assert len(deleted) >= 1
        assert "delete_me.md" in deleted[0].path

    def test_poll_after_no_changes(self, temp_project):
        """无变更时返回空列表。"""
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        bundle = _make_minimal_bundle(temp_project, source_dir)

        watcher = SourceWatcher(bundle)
        watcher.poll()  # 首次
        events = watcher.poll()  # 无变更
        assert events == []


class TestSourceWatcherGeneratedArtifacts:
    """Iris 自己生成的 SOURCE/_INDEX.md 不应被当作「用户文档变更」。"""

    def test_snapshot_excludes_generated_index(self, temp_project):
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        (source_dir / "_INDEX.md").write_text("# SOURCE 文档索引", encoding="utf-8")
        (source_dir / "doc.md").write_text("# 文档", encoding="utf-8")
        bundle = _make_minimal_bundle(temp_project, source_dir)

        snap = SourceWatcher(bundle).snapshot()

        assert "doc.md" in snap["test"]
        assert "_INDEX.md" not in snap["test"]

    def test_generated_index_rewrite_produces_no_event(self, temp_project):
        """每次维护都会重写 _INDEX.md，这不应产生任何事件。"""
        source_dir = temp_project / "SOURCE"
        source_dir.mkdir()
        index = source_dir / "_INDEX.md"
        index.write_text("# SOURCE 文档索引\n> 自动生成于 A", encoding="utf-8")
        bundle = _make_minimal_bundle(temp_project, source_dir)

        watcher = SourceWatcher(bundle)
        watcher.poll()  # 建立快照

        time.sleep(0.15)  # 确保 mtime 变化超过 0.1s 容差
        index.write_text("# SOURCE 文档索引\n> 自动生成于 B", encoding="utf-8")

        assert watcher.poll() == []

    def test_nested_index_like_file_still_watched(self, temp_project):
        """子目录中的同名文件属于用户文档，仍应被监听。"""
        source_dir = temp_project / "SOURCE"
        (source_dir / "08-参考资料").mkdir(parents=True)
        (source_dir / "08-参考资料" / "_INDEX.md").write_text("# 用户自己的索引", encoding="utf-8")
        bundle = _make_minimal_bundle(temp_project, source_dir)

        snap = SourceWatcher(bundle).snapshot()

        assert "08-参考资料/_INDEX.md" in snap["test"]
