"""UsageTracker 单元测试。"""

from __future__ import annotations

import pytest
from pathlib import Path
from iris.llm.usage_tracker import UsageTracker


@pytest.fixture()
def tracker(tmp_path: Path) -> UsageTracker:
    return UsageTracker(tmp_path)


# ── 初始化 ──────────────────────────────────────────────────

class TestInit:
    def test_db_file_created(self, tmp_path):
        UsageTracker(tmp_path)
        assert (tmp_path / "llm_usage.db").exists()

    def test_available(self, tracker):
        assert tracker._available is True

    def test_empty_stats_returns_empty_list(self, tracker):
        assert tracker.stats() == []

    def test_total_records_zero(self, tracker):
        assert tracker.total_records() == 0


# ── record ───────────────────────────────────────────────────

class TestRecord:
    def test_record_increments_count(self, tracker):
        tracker.record(model="deepseek-v4-flash", provider="deepseek",
                       prompt_tokens=100, completion_tokens=50)
        assert tracker.total_records() == 1

    def test_record_multiple(self, tracker):
        for _ in range(3):
            tracker.record(model="qwen", provider="bailian",
                           prompt_tokens=200, completion_tokens=80)
        assert tracker.total_records() == 3

    def test_record_multimodal_flag(self, tracker):
        tracker.record(model="qwen", provider="bailian",
                       prompt_tokens=500, completion_tokens=100,
                       is_multimodal=True)
        rows = tracker._query(
            "SELECT is_multimodal FROM api_calls WHERE model = 'qwen'", []
        )
        assert rows[0]["is_multimodal"] == 1

    def test_record_defaults_zeros(self, tracker):
        tracker.record(model="m", provider="p")
        rows = tracker._query("SELECT prompt_tokens, completion_tokens FROM api_calls", [])
        assert rows[0]["prompt_tokens"] == 0
        assert rows[0]["completion_tokens"] == 0


# ── stats ────────────────────────────────────────────────────

class TestStats:
    def _seed(self, tracker, date: str, model: str, pt: int, ct: int):
        """直接插入测试数据（绕过时间戳自动生成）。"""
        with tracker._connect() as conn:
            conn.execute(
                "INSERT INTO api_calls (ts, date, model, provider, route_role, matched_rule, "
                "prompt_tokens, completion_tokens, is_multimodal) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"{date}T00:00:00", date, model, "test_provider", "base_model", "test_rule",
                 pt, ct, 0),
            )

    def test_stats_by_day(self, tracker):
        self._seed(tracker, "2026-07-01", "model-a", 100, 50)
        self._seed(tracker, "2026-07-01", "model-a", 200, 80)
        self._seed(tracker, "2026-07-02", "model-a", 150, 60)

        rows = tracker.stats(by="day")
        assert len(rows) == 2
        day1 = next(r for r in rows if r["period"] == "2026-07-01")
        assert day1["calls"] == 2
        assert day1["prompt_tokens"] == 300
        assert day1["completion_tokens"] == 130
        assert day1["total_tokens"] == 430

    def test_stats_by_month(self, tracker):
        self._seed(tracker, "2026-06-15", "model-a", 100, 40)
        self._seed(tracker, "2026-07-01", "model-a", 200, 80)
        self._seed(tracker, "2026-07-20", "model-a", 300, 120)

        rows = tracker.stats(by="month")
        assert len(rows) == 2
        periods = [r["period"] for r in rows]
        assert "2026-06" in periods
        assert "2026-07" in periods

        jul = next(r for r in rows if r["period"] == "2026-07")
        assert jul["calls"] == 2
        assert jul["total_tokens"] == 700

    def test_stats_by_year(self, tracker):
        self._seed(tracker, "2025-12-31", "m", 100, 50)
        self._seed(tracker, "2026-01-01", "m", 200, 60)

        rows = tracker.stats(by="year")
        assert len(rows) == 2
        years = [r["period"] for r in rows]
        assert "2025" in years
        assert "2026" in years

    def test_stats_filter_model(self, tracker):
        self._seed(tracker, "2026-07-01", "model-a", 100, 50)
        self._seed(tracker, "2026-07-01", "model-b", 200, 80)

        rows = tracker.stats(by="day", model="model-a")
        assert len(rows) == 1
        assert rows[0]["prompt_tokens"] == 100

    def test_stats_filter_since(self, tracker):
        self._seed(tracker, "2026-06-01", "m", 100, 50)
        self._seed(tracker, "2026-07-01", "m", 200, 80)

        rows = tracker.stats(by="month", since="2026-07-01")
        assert len(rows) == 1
        assert rows[0]["period"] == "2026-07"

    def test_stats_invalid_by_raises(self, tracker):
        with pytest.raises(ValueError, match="by 参数无效"):
            tracker.stats(by="quarter")


# ── stats_by_model ───────────────────────────────────────────

class TestStatsByModel:
    def _seed(self, tracker, date, model, provider, pt, ct):
        with tracker._connect() as conn:
            conn.execute(
                "INSERT INTO api_calls (ts, date, model, provider, route_role, matched_rule, "
                "prompt_tokens, completion_tokens, is_multimodal) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"{date}T00:00:00", date, model, provider, "", "", pt, ct, 0),
            )

    def test_by_model_groups_correctly(self, tracker):
        self._seed(tracker, "2026-07-01", "model-a", "prov-a", 100, 40)
        self._seed(tracker, "2026-07-15", "model-a", "prov-a", 200, 60)
        self._seed(tracker, "2026-07-10", "model-b", "prov-b", 300, 120)

        rows = tracker.stats_by_model("2026-07", by="month")
        assert len(rows) == 2
        # model-a 有 2 次调用，排序在前
        assert rows[0]["model"] == "model-a"
        assert rows[0]["calls"] == 2
        assert rows[0]["total_tokens"] == 400

    def test_by_model_empty_period(self, tracker):
        self._seed(tracker, "2026-06-01", "m", "p", 100, 50)
        rows = tracker.stats_by_model("2026-07", by="month")
        assert rows == []
