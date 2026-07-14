"""LLM API 用量统计 — 本地 SQLite 持久化。

写入 data/llm_usage.db，支持按时间粒度（日/周/月/年）和模型聚合查询。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT    NOT NULL,
    date              TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    provider          TEXT    NOT NULL,
    route_role        TEXT    NOT NULL DEFAULT '',
    matched_rule      TEXT    NOT NULL DEFAULT '',
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    is_multimodal     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_date  ON api_calls(date);
CREATE INDEX IF NOT EXISTS idx_model ON api_calls(model);
"""

_PERIOD_EXPR: Dict[str, str] = {
    "day":   "date",
    "week":  "strftime('%Y-W%W', date)",
    "month": "strftime('%Y-%m', date)",
    "year":  "strftime('%Y', date)",
}


class UsageTracker:
    """SQLite 用量追踪器，写入 <data_dir>/llm_usage.db。

    record() 和 stats() 均静默失败，不影响主流程。
    """

    def __init__(self, data_dir: Path):
        self._db_path = data_dir / "llm_usage.db"
        self._available = self._init_db()

    # ── 初始化 ──────────────────────────────────────────────

    def _init_db(self) -> bool:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
            return True
        except Exception as exc:
            logger.warning("UsageTracker 初始化失败（用量统计将不可用）: %s", exc)
            return False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 写入 ─────────────────────────────────────────────────

    def record(
        self,
        *,
        model: str,
        provider: str,
        route_role: str = "",
        matched_rule: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        is_multimodal: bool = False,
    ) -> None:
        """记录一次 API 调用（静默失败）。"""
        if not self._available:
            return
        now = datetime.now(tz=timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H:%M:%S")
        date = now.strftime("%Y-%m-%d")
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO api_calls
                       (ts, date, model, provider, route_role, matched_rule,
                        prompt_tokens, completion_tokens, is_multimodal)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ts, date, model, provider, route_role, matched_rule,
                     prompt_tokens, completion_tokens, 1 if is_multimodal else 0),
                )
        except Exception as exc:
            logger.debug("UsageTracker.record 失败（静默）: %s", exc)

    # ── 查询 ─────────────────────────────────────────────────

    def stats(
        self,
        by: str = "month",
        *,
        model: Optional[str] = None,
        since: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按时间粒度聚合统计。

        Args:
            by:    时间粒度 — day / week / month / year
            model: 过滤指定模型名称，None 表示全部
            since: 起始日期（含），格式 YYYY-MM-DD，None 表示全部历史

        Returns:
            [{"period", "calls", "prompt_tokens", "completion_tokens", "total_tokens"}, ...]
        """
        if by not in _PERIOD_EXPR:
            raise ValueError(f"by 参数无效: {by!r}，可选: {list(_PERIOD_EXPR)}")

        period_expr = _PERIOD_EXPR[by]
        conditions: List[str] = []
        params: List[Any] = []
        if model:
            conditions.append("model = ?")
            params.append(model)
        if since:
            conditions.append("date >= ?")
            params.append(since)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = f"""
            SELECT
                {period_expr}                          AS period,
                COUNT(*)                               AS calls,
                COALESCE(SUM(prompt_tokens), 0)        AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)    AS completion_tokens,
                COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens
            FROM api_calls
            {where}
            GROUP BY {period_expr}
            ORDER BY {period_expr}
        """
        return self._query(sql, params)

    def stats_by_model(
        self,
        period_value: str,
        *,
        by: str = "month",
    ) -> List[Dict[str, Any]]:
        """指定时间段内按模型分布。

        Args:
            period_value: 时间段标识（stats() 返回的 period 字段值）
            by:           与 stats() 保持一致的时间粒度
        """
        period_expr = _PERIOD_EXPR.get(by, "strftime('%Y-%m', date)")
        sql = f"""
            SELECT
                model,
                provider,
                COUNT(*)                               AS calls,
                COALESCE(SUM(prompt_tokens), 0)        AS prompt_tokens,
                COALESCE(SUM(completion_tokens), 0)    AS completion_tokens,
                COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens
            FROM api_calls
            WHERE {period_expr} = ?
            GROUP BY model, provider
            ORDER BY calls DESC
        """
        return self._query(sql, [period_value])

    def total_records(self) -> int:
        """返回数据库中的总记录数（用于 status 命令展示）。"""
        rows = self._query("SELECT COUNT(*) AS n FROM api_calls", [])
        return int(rows[0]["n"]) if rows else 0

    # ── 内部 ─────────────────────────────────────────────────

    def _query(self, sql: str, params: List[Any]) -> List[Dict[str, Any]]:
        if not self._available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
                return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("UsageTracker 查询失败: %s", exc)
            return []
