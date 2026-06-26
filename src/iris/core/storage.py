"""SQLite 结构化存储层：替代大型 JSON 文件的按需读写。"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("iris.core.storage")


class StorageError(RuntimeError):
    """存储层相关错误。"""


class ChunkStore:
    """基于 SQLite 的 Chunk 存储（替代 chunk_summary.json）。

    特性：
    - FTS5 全文搜索
    - 按数据源隔离表
    - 增量更新（INSERT OR REPLACE）
    - 自动建表
    - 保留 JSON 加载回退
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS chunk_store (
        chunk_id TEXT PRIMARY KEY,
        source_name TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        document_hash TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        section_path TEXT NOT NULL DEFAULT '[]',
        level INTEGER NOT NULL DEFAULT 0,
        content TEXT NOT NULL DEFAULT '',
        content_preview TEXT NOT NULL DEFAULT '',
        line_start INTEGER NOT NULL DEFAULT 1,
        line_end INTEGER NOT NULL DEFAULT 1,
        chunk_type TEXT NOT NULL DEFAULT 'section',
        segment_index INTEGER NOT NULL DEFAULT 1,
        segment_count INTEGER NOT NULL DEFAULT 1,
        structural_tags TEXT NOT NULL DEFAULT '[]',
        extracted_fields TEXT NOT NULL DEFAULT '{}',
        token_count INTEGER NOT NULL DEFAULT 0,
        token_freq TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_chunk_source ON chunk_store(source_name);
    CREATE INDEX IF NOT EXISTS idx_chunk_path ON chunk_store(relative_path);
    CREATE INDEX IF NOT EXISTS idx_chunk_title ON chunk_store(title);
    """

    FTS_SCHEMA = """
    CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
        title, content_preview, content,
        content=chunk_store, content_rowid=rowid
    );
    """

    FTS_TRIGGERS = """
    CREATE TRIGGER IF NOT EXISTS chunk_fts_insert AFTER INSERT ON chunk_store BEGIN
        INSERT INTO chunk_fts(rowid, title, content_preview, content)
        VALUES (new.rowid, new.title, new.content_preview, new.content);
    END;
    CREATE TRIGGER IF NOT EXISTS chunk_fts_delete AFTER DELETE ON chunk_store BEGIN
        INSERT INTO chunk_fts(chunk_fts, rowid, title, content_preview, content)
        VALUES ('delete', old.rowid, old.title, old.content_preview, old.content);
    END;
    CREATE TRIGGER IF NOT EXISTS chunk_fts_update AFTER UPDATE ON chunk_store BEGIN
        INSERT INTO chunk_fts(chunk_fts, rowid, title, content_preview, content)
        VALUES ('delete', old.rowid, old.title, old.content_preview, old.content);
        INSERT INTO chunk_fts(rowid, title, content_preview, content)
        VALUES (new.rowid, new.title, new.content_preview, new.content);
    END;
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self):
        self._get_conn()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        conn = self._conn
        if conn is None:
            return
        conn.executescript(self.SCHEMA)
        try:
            conn.executescript(self.FTS_SCHEMA)
            conn.executescript(self.FTS_TRIGGERS)
        except sqlite3.OperationalError:
            pass
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def insert_chunks(self, chunks: List[Dict[str, Any]]) -> Tuple[int, int]:
        conn = self._get_conn()
        now = _now_iso()
        inserted = 0
        errors = 0
        with conn:
            for chunk in chunks:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO chunk_store
                        (chunk_id, source_name, relative_path, document_hash,
                         title, section_path, level, content, content_preview,
                         line_start, line_end, chunk_type, segment_index, segment_count,
                         structural_tags, extracted_fields, token_count, token_freq, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (chunk.get("chunk_id", ""), chunk.get("source_name", ""),
                         chunk.get("relative_path", ""), chunk.get("document_hash", ""),
                         chunk.get("title", ""), json.dumps(chunk.get("section_path", [])),
                         chunk.get("level", 0), chunk.get("content", ""),
                         chunk.get("content_preview", ""), chunk.get("line_start", 1),
                         chunk.get("line_end", 1), chunk.get("chunk_type", "section"),
                         chunk.get("segment_index", 1), chunk.get("segment_count", 1),
                         json.dumps(chunk.get("structural_tags", [])),
                         json.dumps(chunk.get("extracted_fields", {})),
                         chunk.get("token_count", 0), json.dumps(chunk.get("token_freq", {})), now))
                    inserted += 1
                except sqlite3.Error as exc:
                    errors += 1
                    logger.warning("ChunkStore insert 失败 chunk_id=%s: %s", chunk.get("chunk_id", "?"), exc)
        return inserted, errors

    def delete_by_source(self, source_name: str) -> int:
        conn = self._get_conn()
        with conn:
            cursor = conn.execute("DELETE FROM chunk_store WHERE source_name = ?", (source_name,))
            return cursor.rowcount

    def load_all(self) -> list:
        """加载全部 chunks 为 ChunkRecord 列表（供 LocalRetriever 使用）。"""
        try:
            from iris.ingest.chunker import ChunkRecord
        except ImportError:
            return []
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM chunk_store ORDER BY source_name, relative_path").fetchall()
        results = []
        for row in rows:
            d = _row_to_full_dict(row)
            try:
                results.append(ChunkRecord(
                    chunk_id=d["chunk_id"], source_name=d["source_name"],
                    document_path=d.get("document_path", d["relative_path"]),
                    relative_path=d["relative_path"], document_hash=d.get("document_hash", ""),
                    title=d["title"], section_path=_parse_json_array(d.get("section_path", "[]")),
                    level=d.get("level", 0), content=d.get("content", ""),
                    content_preview=d.get("content_preview", ""),
                    line_start=d.get("line_start", 1), line_end=d.get("line_end", 1),
                    word_count=d.get("word_count", 0), token_count=d.get("token_count", 0),
                    chunk_type=d.get("chunk_type", "section"),
                    segment_index=d.get("segment_index", 1), segment_count=d.get("segment_count", 1),
                    structural_tags=_parse_json_array(d.get("structural_tags", "[]")),
                    extracted_fields=_parse_json_dict(d.get("extracted_fields", "{}")),
                ))
            except (TypeError, ValueError):
                continue
        return results

    def get_chunks_by_source(self, source_name: str) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        cursor = conn.execute(
            """SELECT chunk_id, relative_path, title, section_path,
                      content_preview, chunk_type, structural_tags, token_count
               FROM chunk_store WHERE source_name = ?""", (source_name,))
        return [_row_to_slim_dict(row) for row in cursor.fetchall()]

    def get_chunks_by_ids(self, chunk_ids: List[str]) -> List[Dict[str, Any]]:
        if not chunk_ids:
            return []
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in chunk_ids)
        cursor = conn.execute(f"SELECT * FROM chunk_store WHERE chunk_id IN ({placeholders})", chunk_ids)
        return [_row_to_full_dict(row) for row in cursor.fetchall()]

    def search_fts(self, query: str, limit: int = 50) -> List[str]:
        conn = self._get_conn()
        try:
            cursor = conn.execute("""SELECT c.chunk_id FROM chunk_fts f
                   JOIN chunk_store c ON f.rowid = c.rowid
                   WHERE chunk_fts MATCH ? ORDER BY rank LIMIT ?""", (query, limit))
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            cursor = conn.execute("""SELECT chunk_id FROM chunk_store
                   WHERE title LIKE ? OR content_preview LIKE ? LIMIT ?""",
                                  (f"%{query}%", f"%{query}%", limit))
            return [row[0] for row in cursor.fetchall()]

    def count(self, source_name: Optional[str] = None) -> int:
        conn = self._get_conn()
        if source_name:
            cursor = conn.execute("SELECT COUNT(*) FROM chunk_store WHERE source_name = ?", (source_name,))
        else:
            cursor = conn.execute("SELECT COUNT(*) FROM chunk_store")
        return cursor.fetchone()[0]

    def stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        cursor = conn.execute("""SELECT source_name, COUNT(*) as cnt, MAX(updated_at) as latest
               FROM chunk_store GROUP BY source_name""")
        sources = {row[0]: {"count": row[1], "latest": row[2]} for row in cursor.fetchall()}
        return {"db_path": str(self._db_path), "total_chunks": sum(s["count"] for s in sources.values()),
                "sources": sources,
                "file_size_mb": round(self._db_path.stat().st_size / 1024 / 1024, 2) if self._db_path.exists() else 0}


def _row_to_slim_dict(row: tuple) -> Dict[str, Any]:
    return {"chunk_id": row[0], "relative_path": row[1], "title": row[2],
            "section_path": _parse_json_array(row[3]), "content_preview": row[4],
            "chunk_type": row[5], "structural_tags": _parse_json_array(row[6]), "token_count": row[7]}


def _row_to_full_dict(row: tuple) -> Dict[str, Any]:
    cols = ["chunk_id", "source_name", "relative_path", "document_hash", "title", "section_path",
            "level", "content", "content_preview", "line_start", "line_end", "chunk_type",
            "segment_index", "segment_count", "structural_tags", "extracted_fields",
            "token_count", "token_freq", "updated_at"]
    data = dict(zip(cols, row))
    data["section_path"] = _parse_json_array(data.get("section_path", "[]"))
    data["structural_tags"] = _parse_json_array(data.get("structural_tags", "[]"))
    data["extracted_fields"] = _parse_json_dict(data.get("extracted_fields", "{}"))
    data["token_freq"] = _parse_json_dict(data.get("token_freq", "{}"))
    return data


def _parse_json_array(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
