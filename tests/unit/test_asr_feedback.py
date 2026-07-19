"""ASR 反馈数据模型 — 单元测试。"""

import json
import os
import tempfile
from pathlib import Path

import pytest
from iris.wiki.asr._types import AsrCorrection, AsrTerm
from iris.wiki.asr.feedback import (
    save_correction,
    load_corrections,
    extract_mappings_from_corrections,
    compute_hit_frequency,
    apply_feedback_to_dict,
)


class TestSaveAndLoad:
    def test_save_single_record(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name

        try:
            record = AsrCorrection(
                timestamp="2026-07-18T17:30:00",
                raw_text="我写到检测板里头",
                fast_corrected="我写到剪切板里头",
                full_corrected="我写到剪切板里头",
                mode="full",
                corrections_applied=["检测板→剪切板"],
            )
            save_correction(record, path)

            loaded = load_corrections(path)
            assert len(loaded) == 1
            assert loaded[0].raw_text == "我写到检测板里头"
            assert loaded[0].corrections_applied == ["检测板→剪切板"]
        finally:
            os.unlink(path)

    def test_save_multiple_records(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name

        try:
            for i in range(3):
                record = AsrCorrection(
                    timestamp=f"2026-07-18T17:30:{i:02d}",
                    raw_text=f"文本{i}",
                    fast_corrected=f"校正{i}",
                    full_corrected=f"校正{i}",
                    mode="full",
                    corrections_applied=[],
                )
                save_correction(record, path)

            loaded = load_corrections(path)
            assert len(loaded) == 3
        finally:
            os.unlink(path)

    def test_load_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            path = f.name

        try:
            loaded = load_corrections(path)
            assert loaded == []
        finally:
            os.unlink(path)

    def test_load_nonexistent_file(self):
        loaded = load_corrections("/tmp/nonexistent_feedback_12345.jsonl")
        assert loaded == []

    def test_load_corrupt_lines_skipped(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"timestamp":"...","raw_text":"ok","fast_corrected":"ok","full_corrected":"ok","mode":"fast","corrections_applied":[]}\n')
            f.write('this is not valid json\n')
            f.write('{"timestamp":"...","raw_text":"ok2","fast_corrected":"ok2","full_corrected":"ok2","mode":"fast","corrections_applied":[]}\n')
            path = f.name

        try:
            loaded = load_corrections(path)
            assert len(loaded) == 2
        finally:
            os.unlink(path)


class TestExtractMappings:
    def test_extract_from_applied(self):
        corrections = [
            AsrCorrection(
                timestamp="...",
                raw_text="我写到检测板里头",
                fast_corrected="我写到剪切板里头",
                full_corrected="我写到剪切板里头",
                mode="full",
                corrections_applied=["检测板→剪切板"],
            ),
        ]
        mappings = extract_mappings_from_corrections(corrections)
        assert "剪切板" in mappings
        assert "检测板" in mappings["剪切板"]

    def test_multiple_same_mapping(self):
        corrections = [
            AsrCorrection(
                corrections_applied=["检测板→剪切板"],
            ),
            AsrCorrection(
                corrections_applied=["检测板→剪切板"],
            ),
        ]
        mappings = extract_mappings_from_corrections(corrections)
        # 应去重
        assert len(mappings["剪切板"]) == 1


class TestHitFrequency:
    def test_frequency_count(self):
        corrections = [
            AsrCorrection(corrections_applied=["A→B"]),
            AsrCorrection(corrections_applied=["A→B"]),
            AsrCorrection(corrections_applied=["C→D"]),
        ]
        freq = compute_hit_frequency(corrections)
        assert freq["A→B"] == 2
        assert freq["C→D"] == 1


class TestApplyFeedback:
    def test_add_new_mapping(self):
        existing = [
            AsrTerm(term="剪切板", category="domain_term", context="",
                    mis_asr=["剪切版"]),
        ]
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"]),
        ]
        updated = apply_feedback_to_dict(corrections, existing)
        # 应新增 "检测板" 误识别
        found = [t for t in updated if t.term == "剪切板"]
        assert len(found) == 1
        assert "检测板" in found[0].mis_asr

    def test_skip_duplicate_mapping(self):
        existing = [
            AsrTerm(term="剪切板", category="domain_term", context="",
                    mis_asr=["检测板"]),
        ]
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"]),
        ]
        updated = apply_feedback_to_dict(corrections, existing)
        found = [t for t in updated if t.term == "剪切板"]
        # 不应重复添加
        assert found[0].mis_asr.count("检测板") == 1

    def test_add_new_term(self):
        existing: list = []
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"]),
        ]
        updated = apply_feedback_to_dict(corrections, existing)
        assert len(updated) > 0
        assert any(t.term == "剪切板" for t in updated)
