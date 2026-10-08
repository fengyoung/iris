"""scripts/extract_travel_invoice.py — 单元测试（Stage 2 分段归并）。

脚本非包，按 tests/unit/test_sync_memory.py 的做法以文件路径加载。

本文件只测 Stage 2 的**纯逻辑与编排**，不触网、不读 PDF：
分段（按年份 / 按日期边界对半拆）、表格解析、截断守卫、金额容错、
分段合并与合计重算，以及 stage2_consolidate 在假 LLM 下的端到端行为。

分段机制的存在理由是**静默截断**：思考型模型一次喂入数百条时，思考会烧光
max_tokens 使正文表格被截尾，而调用本身不抛异常。因此这里重点验证
「截断必须显式失败」而不是被当成一张正常的短表。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "extract_travel_invoice.py"
_spec = importlib.util.spec_from_file_location("extract_travel_invoice", _SCRIPT)
ti = importlib.util.module_from_spec(_spec)
# 必须先注册再 exec：模块内 @dataclass + `from __future__ import annotations`
# 在 3.13 下会回查 sys.modules[cls.__module__]，未注册则报 NoneType.__dict__。
sys.modules[_spec.name] = ti
_spec.loader.exec_module(ti)

TripEntry = ti.TripEntry


# ── 测试素材 ────────────────────────────────────────────────

HEADER = "| 行程# | 起始日期 | 完成日期 | 天数 | 目的城市 | 往返机场/车站费用 | 目的城市交通费用 | 单次差旅费用 |"
SEP = "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"


def _table(*rows: str) -> str:
    """拼一张带表头/分隔行的 Markdown 表格，rows 为数据行内容（不含首尾竖线）。"""
    body = "\n".join(f"| {r} |" for r in rows)
    return f"{HEADER}\n{SEP}\n{body}"


def _entry(date: str, amount: float = 10.0, airport: bool = False, time: str = "09:00") -> TripEntry:
    return TripEntry(
        date=date, time=time, origin="A", destination="B",
        amount=amount, is_airport_or_station=airport, source_file="f.pdf",
    )


class _FakeLLM:
    """返回预置文本的假 LLM；记录每次收到的 prompt 以便断言分段内容。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str, **kwargs):
        self.prompts.append(prompt)
        return SimpleNamespace(text=self._responses[len(self.prompts) - 1])


# ── _format_entries ─────────────────────────────────────────


class TestFormatEntries:
    def test_numbers_from_one_and_marks_hub(self):
        text = ti._format_entries([
            _entry("2026-01-01", 12.5, airport=True),
            _entry("2026-01-02", 8.0, airport=False),
        ])
        lines = text.splitlines()
        assert lines[0].startswith("1. [2026-01-01 09:00]")
        assert lines[1].startswith("2. [2026-01-02 09:00]")
        assert "【机场/车站】" in lines[0]
        assert "【市内】" in lines[1]
        assert "¥12.50" in lines[0]

    def test_empty_entries_yields_empty_string(self):
        assert ti._format_entries([]) == ""


# ── _parse_table_rows ───────────────────────────────────────


class TestParseTableRows:
    def test_drops_header_separator_and_total(self):
        rows = ti._parse_table_rows(_table(
            "1 | 2026-01-01 | 2026-01-03 | 3 | 北京 | 100.00 | 50.00 | 150.00",
            "合计 | | | | | 100.00 | 50.00 | 150.00",
        ))
        assert len(rows) == 1
        assert rows[0][0] == "1"
        assert rows[0][4] == "北京"

    def test_ignores_non_table_lines(self):
        text = "说明文字\n" + _table("1 | 2026-01-01 | 2026-01-03 | 3 | 北京 | 1.00 | 2.00 | 3.00")
        assert len(ti._parse_table_rows(text)) == 1

    def test_drops_rows_with_wrong_column_count(self):
        """模型少写一列时该行被丢弃——宁可少一行，也不要把错位的列当成金额。"""
        rows = ti._parse_table_rows(_table(
            "1 | 2026-01-01 | 2026-01-03 | 3 | 北京 | 100.00 | 150.00",
            "2 | 2026-02-01 | 2026-02-03 | 3 | 上海 | 200.00 | 30.00 | 230.00",
        ))
        assert [r[0] for r in rows] == ["2"]

    def test_drops_separator_variants(self):
        """分隔行不止 :---: 一种写法，凡首格只由 : 和 - 组成都应丢弃。"""
        for sep_first in (":---", "---:", "---", ":-:"):
            sep = "|" + "|".join([sep_first] * 8) + "|"
            text = f"{HEADER}\n{sep}\n| 1 | 2026-01-01 | 2026-01-03 | 3 | 北京 | 1.00 | 2.00 | 3.00 |"
            assert len(ti._parse_table_rows(text)) == 1


# ── _assert_chunk_complete ──────────────────────────────────


class TestAssertChunkComplete:
    def test_passes_when_last_line_is_total(self):
        ti._assert_chunk_complete(_table(
            "1 | 2026-01-01 | 2026-01-03 | 3 | 北京 | 1.00 | 2.00 | 3.00",
            "合计 | | | | | 1.00 | 2.00 | 3.00",
        ), "2026", 1)

    def test_raises_when_truncated_mid_table(self):
        """被 max_tokens 截断的表缺「合计」收尾，必须显式失败而非当成短表。"""
        truncated = _table("1 | 2026-01-01 | 2026-01-03 | 3 | 北京 | 1.00 | 2.00 | 3.00")
        with pytest.raises(RuntimeError, match="疑似被截断"):
            ti._assert_chunk_complete(truncated, "2026", 1)

    def test_raises_on_empty_output(self):
        with pytest.raises(RuntimeError, match="疑似被截断"):
            ti._assert_chunk_complete("   \n\n", "2026", 0)

    def test_error_message_carries_label_and_row_count(self):
        with pytest.raises(RuntimeError) as exc:
            ti._assert_chunk_complete("| 1 | x |", "2026a", 7)
        assert "2026a" in str(exc.value)
        assert "7" in str(exc.value)


# ── _to_amount ──────────────────────────────────────────────


class TestToAmount:
    @pytest.mark.parametrize("raw,expected", [
        ("100.00", 100.0),
        ("¥100.00", 100.0),
        ("1,234.50", 1234.5),
        ("¥1,234.50", 1234.5),
        (" 88 ", 88.0),
        ("", 0.0),
        ("-", 0.0),
        ("不适用", 0.0),
    ])
    def test_tolerant_parsing(self, raw, expected):
        assert ti._to_amount(raw) == expected


# ── _merge_tables ───────────────────────────────────────────


class TestMergeTables:
    def test_sorts_globally_by_start_date_and_renumbers(self):
        out = ti._merge_tables([
            _table("1 | 2026-03-01 | 2026-03-02 | 2 | 上海 | 200.00 | 30.00 | 230.00"),
            _table("1 | 2025-12-20 | 2025-12-22 | 3 | 北京 | 100.00 | 50.00 | 150.00"),
        ])
        rows = ti._parse_table_rows(out)
        assert [r[1] for r in rows] == ["2025-12-20", "2026-03-01"]
        assert [r[0] for r in rows] == ["1", "2"]

    def test_recomputes_total_from_rows_ignoring_model_arithmetic(self):
        """合计由 Python 重算——模型写的 999.99 不应出现在结果里。"""
        out = ti._merge_tables([
            _table("1 | 2025-12-20 | 2025-12-22 | 3 | 北京 | 100.00 | 50.00 | 999.99"),
        ])
        total = out.splitlines()[-1]
        assert "150.00" in total
        assert "999.99" not in out

    def test_total_row_sums_all_chunks(self):
        out = ti._merge_tables([
            _table("1 | 2025-12-20 | 2025-12-22 | 3 | 北京 | 100.00 | 50.00 | 150.00"),
            _table("1 | 2026-01-05 | 2026-01-07 | 3 | 上海 | 200.00 | 30.00 | 230.00"),
        ])
        # 合计行：往返 300.00 / 市内 80.00 / 总计 380.00
        assert "| 合计 | | | | | 300.00 | 80.00 | 380.00 |" in out

    def test_single_trip_amount_is_airport_plus_city(self):
        out = ti._merge_tables([
            _table("1 | 2026-01-05 | 2026-01-07 | 3 | 上海 | 200.00 | 30.00 | 0.00"),
        ])
        assert "| 1 | 2026-01-05 | 2026-01-07 | 3 | 上海 | 200.00 | 30.00 | 230.00 |" in out

    def test_empty_partials_yields_header_and_zero_total(self):
        out = ti._merge_tables([""])
        rows = ti._parse_table_rows(out)
        assert rows == []
        assert "| 合计 | | | | | 0.00 | 0.00 | 0.00 |" in out


# ── _split_oversized / _chunk_entries ───────────────────────


class TestChunking:
    def test_splits_at_date_boundary_never_mid_day(self):
        """切点必须落在日期边界上，同一天的条目不能被分到两段。"""
        entries = [_entry(f"2026-01-{d:02d}") for d in (1, 2, 3, 4, 5) for _ in range(2)]
        chunks = ti._split_oversized("2026", entries, max_size=4)
        for _, items in chunks:
            dates = [e.date for e in items]
            # 每个日期在段内出现次数非 0 即 2（同一天不被切开）
            for d in set(dates):
                assert dates.count(d) == 2
        assert sum(len(items) for _, items in chunks) == len(entries)

    def test_under_limit_not_split(self):
        entries = [_entry("2026-01-01")] * 3
        assert ti._split_oversized("2026", entries, max_size=10) == [("2026", entries)]

    def test_single_day_over_limit_returned_unsplit(self):
        """一天之内条目就超上限时无法再拆——原样返回，交给模型，不无限递归。"""
        entries = [_entry("2026-01-01")] * 5
        chunks = ti._split_oversized("2026", entries, max_size=2)
        assert len(chunks) == 1
        assert len(chunks[0][1]) == 5

    def test_recursion_terminates_and_preserves_all_entries(self):
        entries = [_entry(f"2026-{m:02d}-01") for m in range(1, 13)]
        chunks = ti._split_oversized("2026", entries, max_size=2)
        assert sum(len(items) for _, items in chunks) == 12
        assert all(len(items) <= 2 for _, items in chunks)

    def test_groups_by_year_ascending(self):
        entries = [_entry("2026-05-01"), _entry("2025-05-01"), _entry("2026-06-01")]
        chunks = ti._chunk_entries(entries)
        assert [label for label, _ in chunks] == ["2025", "2026"]
        assert len(chunks[1][1]) == 2

    def test_missing_date_falls_into_unknown_bucket(self):
        chunks = ti._chunk_entries([_entry("")])
        assert [label for label, _ in chunks] == ["未知"]


# ── stage2_consolidate（端到端，假 LLM）─────────────────────


class TestStage2Consolidate:
    def test_multi_year_entries_use_one_call_per_year_and_merge(self):
        llm = _FakeLLM([
            _table("1 | 2025-12-20 | 2025-12-22 | 3 | 北京 | 100.00 | 50.00 | 150.00",
                   "合计 | | | | | 100.00 | 50.00 | 150.00"),
            _table("1 | 2026-01-05 | 2026-01-07 | 3 | 上海 | 200.00 | 30.00 | 230.00",
                   "合计 | | | | | 200.00 | 30.00 | 230.00"),
        ])
        out = ti.stage2_consolidate(llm, [_entry("2025-12-20"), _entry("2026-01-05")])

        assert len(llm.prompts) == 2
        assert "2025-12-20" in llm.prompts[0]
        assert "2026-01-05" in llm.prompts[1]
        rows = ti._parse_table_rows(out)
        assert [r[1] for r in rows] == ["2025-12-20", "2026-01-05"]
        assert "| 合计 | | | | | 300.00 | 80.00 | 380.00 |" in out

    def test_each_chunk_prompt_contains_only_its_own_entries(self):
        """分段的意义在于单次输入变短——别的年份的条目不能混进来。"""
        llm = _FakeLLM([
            _table("1 | 2025-01-01 | 2025-01-02 | 2 | 北京 | 1.00 | 1.00 | 2.00",
                   "合计 | | | | | 1.00 | 1.00 | 2.00"),
            _table("1 | 2026-01-01 | 2026-01-02 | 2 | 上海 | 1.00 | 1.00 | 2.00",
                   "合计 | | | | | 1.00 | 1.00 | 2.00"),
        ])
        ti.stage2_consolidate(llm, [_entry("2025-01-01"), _entry("2026-01-01")])
        assert "2026-01-01" not in llm.prompts[0]
        assert "2025-01-01" not in llm.prompts[1]

    def test_truncated_chunk_raises_instead_of_silently_dropping_rows(self):
        """核心防回归：截断的分段必须失败，不能悄悄少算一笔差旅。"""
        llm = _FakeLLM([
            _table("1 | 2025-12-20 | 2025-12-22 | 3 | 北京 | 100.00 | 50.00 | 150.00"),  # 无合计行
            _table("1 | 2026-01-05 | 2026-01-07 | 3 | 上海 | 200.00 | 30.00 | 230.00",
                   "合计 | | | | | 200.00 | 30.00 | 230.00"),
        ])
        with pytest.raises(RuntimeError, match="疑似被截断"):
            ti.stage2_consolidate(llm, [_entry("2025-12-20"), _entry("2026-01-05")])

    def test_single_year_makes_exactly_one_call(self):
        llm = _FakeLLM([
            _table("1 | 2026-01-01 | 2026-01-02 | 2 | 北京 | 1.00 | 1.00 | 2.00",
                   "合计 | | | | | 1.00 | 1.00 | 2.00"),
        ])
        ti.stage2_consolidate(llm, [_entry("2026-01-01")])
        assert len(llm.prompts) == 1
