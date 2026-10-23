"""chunk_hash_index 死条目清理 — 单元测试。

背景：索引键是「相对某个源根」的路径且只 upsert，历史上目录重排
（`01-目标管理/xxx.md` → `01-目标管理/2026/xxx.md`）留下的旧键永不消失。
清理判据只能是「文件在任何启用源根下都不存在」，不能按本次扫描路径判断
（索引跨数据源共享），也不能在源根不可见时动手（外接盘/未下载）。
"""

from __future__ import annotations

import json

from iris.ingest.chunker import ChunkSummary, MarkdownChunker


def _config(tmp_path, roots):
    """最小 ConfigBundle 鸭子类型：只需要 root 与 data_source。"""

    class _Cfg:
        pass

    cfg = _Cfg()
    cfg.root = tmp_path
    cfg.data_source = {
        "sources": {
            f"source{i}": {"enabled": True, "path": str(root)}
            for i, root in enumerate(roots)
        }
    }
    return cfg


def _write_index(cfg, data):
    path = cfg.root / "data" / "metadata" / "chunk_hash_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ── filter_dead_entries（纯计算）────────────────────────────────────


class TestFilterDeadEntries:
    def test_keeps_existing_prunes_missing(self, tmp_path):
        root = tmp_path / "src"
        (root / "2026").mkdir(parents=True)
        (root / "a.md").write_text("x", encoding="utf-8")
        (root / "2026" / "b.md").write_text("x", encoding="utf-8")
        index = {
            "a.md": {"hash": "h1"},
            "2026/b.md": {"hash": "h2"},
            "gone.md": {"hash": "h3"},
        }
        surviving, report = MarkdownChunker.filter_dead_entries(index, [root])
        assert set(surviving) == {"a.md", "2026/b.md"}
        assert surviving["a.md"] == {"hash": "h1"}
        assert report["status"] == "ok"
        assert report["pruned"] == 1
        assert report["kept"] == 2
        assert report["samples"] == ["gone.md"]

    def test_moved_file_old_key_pruned_new_key_kept(self, tmp_path):
        # 目录改为按年归档：旧键（无年份）成为死键，新键有效
        root = tmp_path / "src"
        (root / "2026").mkdir(parents=True)
        (root / "2026" / "b.md").write_text("x", encoding="utf-8")
        index = {"b.md": {"hash": "old"}, "2026/b.md": {"hash": "new"}}
        surviving, report = MarkdownChunker.filter_dead_entries(index, [root])
        assert set(surviving) == {"2026/b.md"}
        assert report["pruned"] == 1

    def test_icloud_placeholder_counts_as_existing(self, tmp_path):
        # iCloud 逐出文件的占位符 `.name.icloud` 不是删除
        root = tmp_path / "src"
        root.mkdir()
        (root / ".c.md.icloud").write_text("", encoding="utf-8")
        surviving, report = MarkdownChunker.filter_dead_entries(
            {"c.md": {"hash": "h"}}, [root])
        assert "c.md" in surviving
        assert report["evicted_kept"] == 1
        assert report["pruned"] == 0

    def test_missing_root_skips_entirely(self, tmp_path):
        # 源根不可见时整体跳过，绝不按「文件不存在」清库
        index = {"a.md": {"hash": "h"}}
        surviving, report = MarkdownChunker.filter_dead_entries(
            index, [tmp_path / "not-mounted"])
        assert surviving == index
        assert report["status"] == "skipped_missing_roots"
        assert report["pruned"] == 0
        assert report["missing_roots"]

    def test_any_of_multiple_roots_keeps_entry(self, tmp_path):
        # 索引跨数据源共享：任一根下存在即保留，避免误删另一数据源同路径条目
        r1 = tmp_path / "r1"
        r2 = tmp_path / "r2"
        r1.mkdir()
        r2.mkdir()
        (r2 / "shared.md").write_text("x", encoding="utf-8")
        surviving, report = MarkdownChunker.filter_dead_entries(
            {"shared.md": {"hash": "h"}}, [r1, r2])
        assert "shared.md" in surviving
        assert report["pruned"] == 0

    def test_empty_index(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        surviving, report = MarkdownChunker.filter_dead_entries({}, [root])
        assert surviving == {}
        assert report == {"status": "ok", "total": 0, "pruned": 0, "kept": 0,
                          "evicted_kept": 0, "samples": []}


# ── prune_dead_entries（落盘）──────────────────────────────────────


class TestPruneDeadEntries:
    def test_dry_run_does_not_write(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "keep.md").write_text("x", encoding="utf-8")
        cfg = _config(tmp_path, [root])
        path = _write_index(cfg, {"gone.md": {"hash": "h"}, "keep.md": {"hash": "k"}})
        before = path.read_text(encoding="utf-8")
        report = MarkdownChunker.prune_dead_entries(cfg, dry_run=True)
        assert report["pruned"] == 1
        assert report["dry_run"] is True
        assert path.read_text(encoding="utf-8") == before

    def test_real_run_writes(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "keep.md").write_text("x", encoding="utf-8")
        cfg = _config(tmp_path, [root])
        path = _write_index(cfg, {"gone.md": {"hash": "h"}, "keep.md": {"hash": "k"}})
        report = MarkdownChunker.prune_dead_entries(cfg, dry_run=False)
        assert report["pruned"] == 1
        assert report["kept"] == 1
        assert json.loads(path.read_text(encoding="utf-8")) == {"keep.md": {"hash": "k"}}

    def test_missing_root_root_guard_blocks_write(self, tmp_path):
        cfg = _config(tmp_path, [tmp_path / "not-mounted"])
        path = _write_index(cfg, {"a.md": {"hash": "h"}})
        report = MarkdownChunker.prune_dead_entries(cfg, dry_run=False)
        assert report["status"] == "skipped_missing_roots"
        assert json.loads(path.read_text(encoding="utf-8")) == {"a.md": {"hash": "h"}}

    def test_missing_index_reports(self, tmp_path):
        cfg = _config(tmp_path, [tmp_path / "src"])
        report = MarkdownChunker.prune_dead_entries(cfg)
        assert report["status"] == "missing_index"

    def test_corrupt_index_reports_error(self, tmp_path):
        cfg = _config(tmp_path, [tmp_path / "src"])
        path = _write_index(cfg, {})
        path.write_text("{not json", encoding="utf-8")
        report = MarkdownChunker.prune_dead_entries(cfg, dry_run=False)
        assert report["status"] == "error"


# ── 自愈：write_hash_index 内联清理 ─────────────────────────────────


class TestWriteHashIndexSelfHeal:
    @staticmethod
    def _chunker(tmp_path, cfg):
        chunker = MarkdownChunker.__new__(MarkdownChunker)
        chunker._config = cfg
        chunker._metadata_dir = tmp_path / "data" / "metadata"
        chunker._metadata_dir.mkdir(parents=True, exist_ok=True)
        return chunker

    def _summary(self):
        return ChunkSummary(source_name="main_source", scanned_at="2026-10-22T00:00:00",
                            document_count=0, chunk_count=0, chunks=[])

    def test_dead_keys_pruned_on_write(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "keep.md").write_text("x", encoding="utf-8")
        cfg = _config(tmp_path, [root])
        path = _write_index(cfg, {"gone.md": {"hash": "h"}, "keep.md": {"hash": "k"}})
        self._chunker(tmp_path, cfg).write_hash_index(self._summary())
        assert json.loads(path.read_text(encoding="utf-8")) == {"keep.md": {"hash": "k"}}

    def test_live_entries_untouched(self, tmp_path):
        root = tmp_path / "src"
        root.mkdir()
        (root / "keep.md").write_text("x", encoding="utf-8")
        cfg = _config(tmp_path, [root])
        path = _write_index(cfg, {"keep.md": {"hash": "k"}})
        self._chunker(tmp_path, cfg).write_hash_index(self._summary())
        assert json.loads(path.read_text(encoding="utf-8")) == {"keep.md": {"hash": "k"}}
