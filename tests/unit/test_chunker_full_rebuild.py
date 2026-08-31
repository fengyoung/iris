"""chunker 全量重建回归测试 — 全量模式不得追加已删除文档的旧 chunk（v3.22.3 修复）。

背景：`_build_chunks_from_scan` 原条件 `if deleted_paths or reused_documents > 0:`
在全量重建（deleted_paths 为空且 reused>0）时也会执行「保留 previous 中未变更路径」，
把已删除/已归档迁移文档的旧 chunk 全部加回，导致死数据随每次全量重建累积
（实测曾占 chunk 总量的 45%）。
"""

from __future__ import annotations

import json
from pathlib import Path

from iris.ingest.chunker import ChunkRecord, MarkdownChunker
from iris.ingest.scanner import DocumentRecord, ScanSummary

# 磁盘上真实存在的文档（相对路径）
ALIVE_A = "01-目标管理/2026/doc-a.md"
ALIVE_B = "01-目标管理/2026/doc-b.md"
# 已删除/已归档迁移的文档（仅存在于旧 chunk 缓存）
DEAD = "01-目标管理/doc-dead.md"  # 旧扁平路径（无 2026/ 子目录）


def _make_chunker(tmp_path: Path) -> MarkdownChunker:
    """最小化构造 MarkdownChunker（绕开 ConfigBundle）。"""
    chunker = object.__new__(MarkdownChunker)
    chunker._config = None
    chunker._max_chunk_chars = 1200
    chunker._max_preview_chars = 180
    chunker._chunk_overlap_chars = 150
    chunker._metadata_dir = tmp_path / "metadata"
    return chunker


def _chunk(relative_path: str, hash_: str, n: int) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=f"{relative_path}::chunk-{n}",
        source_name="work_docs_main",
        document_path=f"/src/{relative_path}",
        relative_path=relative_path,
        document_hash=hash_,
        title="t",
        section_path=["s"],
        level=1,
        content="内容",
        content_preview="内容",
        line_start=1,
        line_end=2,
        word_count=2,
        token_count=2,
    )


def _scan_summary(docs: list) -> ScanSummary:
    return ScanSummary(
        source_name="work_docs_main",
        source_path="/src",
        scanned_at="2026-08-06T00:00:00+00:00",
        document_count=len(docs),
        documents=docs,
    )


def _make_doc(tmp_path: Path, relative_path: str, hash_: str) -> DocumentRecord:
    """创建真实文件并返回 DocumentRecord（切块需要真实读取）。"""
    real = tmp_path / "src" / relative_path
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text("## 标题\n\n内容段落。" * 10, encoding="utf-8")
    return DocumentRecord(
        "work_docs_main", str(real), relative_path,
        real.stat().st_size, "2026-08-01", hash_, "t",
    )


def _write_old_summary(chunker: MarkdownChunker, chunks: list) -> None:
    path = chunker._metadata_dir / "work_docs_main_chunk_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "source_name": "work_docs_main",
        "chunks": [c.__dict__ for c in chunks],
    }, ensure_ascii=False), encoding="utf-8")


class TestFullRebuildDropsDeadChunks:
    """全量重建：已删除文档的旧 chunk 必须被清除。"""

    def test_full_rebuild_excludes_dead_path(self, tmp_path):
        chunker = _make_chunker(tmp_path)
        # 旧缓存：2 个现存文档 + 1 个死文档
        old = [
            _chunk(ALIVE_A, "h1", 1),
            _chunk(ALIVE_B, "h2", 1),
            _chunk(DEAD, "h3", 1),
            _chunk(DEAD, "h3", 2),
        ]
        _write_old_summary(chunker, old)

        docs = [
            _make_doc(tmp_path, ALIVE_A, "h1"),      # hash 相同 → 复用
            _make_doc(tmp_path, ALIVE_B, "h2-new"),  # hash 变化 → 重建
        ]
        summary = _scan_summary(docs)

        result = chunker._build_chunks_from_scan(summary, incremental=False)

        paths = {c.relative_path for c in result.chunks}
        assert ALIVE_A in paths          # hash 相同 → 复用
        assert ALIVE_B in paths          # hash 变化 → 重建
        assert DEAD not in paths         # 死路径不得出现（回归核心断言）
        assert result.build_stats["cleaned_documents"] == 0  # 全量 scan 无 deleted 概念

    def test_incremental_keeps_unchanged_and_cleans_deleted(self, tmp_path):
        chunker = _make_chunker(tmp_path)
        old = [
            _chunk(ALIVE_A, "h1", 1),
            _chunk(ALIVE_B, "h2", 1),
        ]
        _write_old_summary(chunker, old)

        docs = [
            _make_doc(tmp_path, ALIVE_A, "h1"),
        ]
        summary = _scan_summary(docs)
        object.__setattr__(summary, "_deleted_paths", [ALIVE_B])  # 增量 scan 检测到 B 被删

        result = chunker._build_chunks_from_scan(summary, incremental=True)

        paths = {c.relative_path for c in result.chunks}
        assert ALIVE_A in paths          # 未变更 → 复用保留
        assert ALIVE_B not in paths      # deleted_paths → 清理
        assert result.build_stats["cleaned_documents"] == 1

    def test_incremental_only_modified_no_deleted_keeps_unchanged(self, tmp_path):
        """v3.28.1 回归：增量「只有修改、无删除」不得丢弃未变更文档的 chunk。

        历史 bug：条件 `if incremental and (deleted_paths or reused_documents > 0)`
        在增量 scan 只含变更文档（reused 恒 0）且无删除时为 False，
        previous 中全部未变更文档的 chunk 被静默丢弃，summary 覆盖后即数据丢失。
        """
        chunker = _make_chunker(tmp_path)
        old = [
            _chunk(ALIVE_A, "h1", 1),   # 本次未变更（不在增量 scan 中）
            _chunk(ALIVE_B, "h2", 1),   # 本次被修改
        ]
        _write_old_summary(chunker, old)

        # 增量 scan 只含变更文档 B（hash 已变化），无删除
        docs = [_make_doc(tmp_path, ALIVE_B, "h2-new")]
        summary = _scan_summary(docs)

        result = chunker._build_chunks_from_scan(summary, incremental=True)

        paths = {c.relative_path for c in result.chunks}
        assert ALIVE_A in paths, "未变更文档的旧 chunk 不得丢失（回归核心断言）"
        assert ALIVE_B in paths
        # B 必须是重建后的新 chunk（hash 已更新）
        b_hashes = {c.document_hash for c in result.chunks if c.relative_path == ALIVE_B}
        assert b_hashes == {"h2-new"}
        assert result.build_stats["rebuilt_documents"] == 1
