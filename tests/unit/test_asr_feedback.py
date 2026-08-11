"""ASR 反馈数据模型 — 单元测试。"""

import os
import tempfile

from iris.wiki.asr._types import AsrCorrection, AsrTerm
from iris.wiki.asr.feedback import (
    save_correction,
    load_corrections,
    extract_mappings_from_corrections,
    compute_hit_frequency,
    apply_feedback_to_dict,
    find_zombie_rules,
    build_feedback_recommendations,
    apply_feedback_optimizations,
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

    def test_manual_prefix_stripped(self):
        """v3.24: [手动] 前缀条目（asr-report 写入）与 [LLM] 一致剥离，
        不再把整段原始文本当误识别词入库。"""
        corrections = [
            AsrCorrection(
                corrections_applied=["[手动] 检测板→剪切板"],
            ),
        ]
        mappings = extract_mappings_from_corrections(corrections)
        assert "剪切板" in mappings
        assert mappings["剪切板"] == ["检测板"]  # 前缀已剥离，误识别词不包含 "[手动]"

    def test_llm_prefix_stripped(self):
        corrections = [
            AsrCorrection(
                corrections_applied=["[LLM] 检测板→剪切板"],
            ),
        ]
        mappings = extract_mappings_from_corrections(corrections)
        assert mappings["剪切板"] == ["检测板"]


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


class TestFindZombieRules:
    def test_finds_untriggered_rules(self):
        """从未出现的规则应被标记为僵尸（须在 history_rules 时间窗内）。"""
        terms = [
            AsrTerm(term="剪切板", category="concept", context="",
                    mis_asr=["检测板", "剪切版"]),
        ]
        # 只触发了"检测板→剪切板"，"剪切版→剪切板"从未触发
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"])
            for _ in range(60)
        ]
        zombies = find_zombie_rules(
            corrections, terms, min_samples=50,
            history_rules={"检测板→剪切板", "剪切版→剪切板"},
        )
        assert len(zombies) == 1
        assert zombies[0][0] == "剪切版"
        assert zombies[0][1] == "剪切板"

    def test_all_triggered_no_zombies(self):
        """全部规则都被触发过，应该没有僵尸。"""
        terms = [
            AsrTerm(term="剪切板", category="concept", context="",
                    mis_asr=["检测板"]),
        ]
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"])
            for _ in range(60)
        ]
        zombies = find_zombie_rules(
            corrections, terms, min_samples=50,
            history_rules={"检测板→剪切板"},
        )
        assert len(zombies) == 0

    def test_no_history_rules_returns_empty(self):
        """v3.24: 无 history_rules（上次词典缺失/首次构建）→ 不淘汰任何规则，
        防止本次新生成规则被误判为僵尸（生成→淘汰→再生成振荡）。"""
        terms = [
            AsrTerm(term="剪切板", category="concept", context="",
                    mis_asr=["检测板", "剪切版"]),
        ]
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"])
            for _ in range(60)
        ]
        assert find_zombie_rules(corrections, terms, min_samples=50) == []
        assert find_zombie_rules(corrections, terms, min_samples=50, history_rules=set()) == []

    def test_only_history_rules_participate(self):
        """仅 history 中的规则参与判定：本次新生成的规则即使未命中也不淘汰。"""
        terms = [
            AsrTerm(term="剪切板", category="concept", context="",
                    mis_asr=["检测板", "剪切版", "新生成规则"]),
        ]
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"])
            for _ in range(60)
        ]
        zombies = find_zombie_rules(
            corrections, terms, min_samples=50,
            history_rules={"剪切版→剪切板"},  # 只有"剪切版"在历史词典中
        )
        assert len(zombies) == 1
        assert zombies[0][0] == "剪切版"
        assert "新生成规则" not in [z[0] for z in zombies]

    def test_insufficient_samples_returns_empty(self):
        """样本量不足时跳过分析。"""
        terms = [
            AsrTerm(term="剪切板", category="concept", context="",
                    mis_asr=["检测板"]),
        ]
        corrections = [
            AsrCorrection(corrections_applied=["检测板→剪切板"])
            for _ in range(10)
        ]
        zombies = find_zombie_rules(corrections, terms, min_samples=50)
        assert zombies == []

    def test_ignores_llm_discoveries(self):
        """LLM 发现的修正不计入词典命中统计。"""
        terms = [
            AsrTerm(term="数据湖", category="project", context="",
                    mis_asr=["数据湖工程"]),
        ]
        # 只有 [LLM] 标记的修正，没有词典命中
        corrections = [
            AsrCorrection(corrections_applied=["[LLM] 数据湖工程→数据湖"])
            for _ in range(60)
        ]
        zombies = find_zombie_rules(
            corrections, terms, min_samples=50,
            history_rules={"数据湖工程→数据湖"},
        )
        # "数据湖工程→数据湖" 前面带 [LLM] 前缀，不算词典命中，应标记为僵尸
        assert len(zombies) == 1
        assert zombies[0][0] == "数据湖工程"


class TestBuildFeedbackRecommendations:
    def _make_terms(self):
        return [
            AsrTerm(term="数据湖", category="project", context="",
                    mis_asr=["数据糊", "数据湖工程"]),
            AsrTerm(term="剪切板", category="concept", context="",
                    mis_asr=["检测板"]),
        ]

    def _make_corrections(self, n=100):
        """生成混合修正记录：词典命中 + LLM 发现。"""
        corrections = []
        for i in range(n):
            applied = []
            # 每条记录都命中"数据糊→数据湖"（词典）
            applied.append("数据糊→数据湖")
            # 部分记录有 LLM 发现
            if i % 10 == 0:
                applied.append("[LLM] 检策板→剪切板")
            # 部分记录有另一个 LLM 发现
            if i % 5 == 0:
                applied.append("[LLM] 数据胡→数据湖")
            corrections.append(AsrCorrection(
                corrections_applied=applied,
                fast_corrected="fast",
                full_corrected="full" if i % 10 == 0 else "fast",
            ))
        return corrections

    def test_full_recommendations(self):
        terms = self._make_terms()
        corrections = self._make_corrections(100)
        recs = build_feedback_recommendations(
            corrections, terms, [],
            min_samples=50, promote_threshold=3,
            history_rules={"数据糊→数据湖", "数据湖工程→数据湖", "检测板→剪切板"},
        )

        assert recs["total_corrections"] == 100
        assert recs["dict_hit_count"] == 100  # 每条记录都有"数据糊→数据湖"
        assert recs["total_rules"] == 3

        # 僵尸规则："数据湖工程→数据湖"和"检测板→剪切板"从未被触发（且在历史窗内）
        assert recs["zombie_count"] == 2

    def test_full_recommendations_no_history_skips_zombies(self):
        """v3.24: 无历史词典 → 僵尸维度降级为不淘汰（其余维度不受影响）。"""
        terms = self._make_terms()
        corrections = self._make_corrections(100)
        recs = build_feedback_recommendations(
            corrections, terms, [],
            min_samples=50, promote_threshold=3,
        )
        assert recs["zombie_count"] == 0
        assert recs["promote_count"] > 0  # 提升维度正常

        # LLM 发现 "数据胡→数据湖" 出现 20 次 (i%5==0, n=100)
        # "检策板→剪切板" 出现 10 次 (i%10==0)
        # 都 ≥ promote_threshold=3，应被提升
        promote = recs["promote_to_dict"]
        assert "数据湖" in promote
        assert "数据胡" in promote["数据湖"]
        assert "剪切板" in promote
        assert "检策板" in promote["剪切板"]

    def test_insufficient_samples(self):
        """样本不足时返回空结果。"""
        terms = self._make_terms()
        corrections = self._make_corrections(10)
        recs = build_feedback_recommendations(
            corrections, terms, [],
            min_samples=50,
        )
        assert recs["zombie_count"] == 0
        assert recs["promote_count"] == 0

    def test_new_hotwords_from_feedback(self):
        """LLM 纠正的高频专有名词应建议为热词。"""
        terms: list = []
        hotwords: list = []
        corrections = []
        for i in range(60):
            corrections.append(AsrCorrection(
                corrections_applied=["[LLM] 数据虎→数据湖"],
                fast_corrected="数据虎",
                full_corrected="数据湖",  # ≠ fast → LLM 做了修正
            ))
        recs = build_feedback_recommendations(
            corrections, terms, hotwords,
            min_samples=50, promote_threshold=3,
        )
        # "数据湖"在 LLM 中被纠正了 60 次(>2)，且不在现有热词/term 中 → 应建议补充
        assert len(recs["new_hotwords"]) >= 1
        assert any("数据湖" in hw for hw in recs["new_hotwords"])


class TestApplyFeedbackOptimizations:
    def test_zombie_removal(self):
        """僵尸规则应从 terms 的 mis_asr 中移除。"""
        terms = [
            AsrTerm(term="数据湖", category="project", context="",
                    mis_asr=["数据糊", "数据湖工程", "数据胡"]),
        ]
        hotwords: list = []
        recs = {
            "zombie_rules": [
                ("数据湖工程", "数据湖", "project"),
                ("数据胡", "数据湖", "project"),
            ],
            "promote_to_dict": {},
            "new_hotwords": [],
        }

        removed, promoted, added = apply_feedback_optimizations(
            terms, hotwords, recs,
        )
        assert removed == 2
        assert promoted == 0
        assert added == 0
        # "数据糊" 保留，"数据湖工程"和"数据胡"被移除
        assert terms[0].mis_asr == ["数据糊"]

    def test_promote_llm_discoveries(self):
        """LLM 发现应提升为词典规则。"""
        terms = [
            AsrTerm(term="数据湖", category="project", context="",
                    mis_asr=["数据糊"]),
        ]
        hotwords: list = []
        recs = {
            "zombie_rules": [],
            "promote_to_dict": {
                "数据湖": ["数据胡", "数据虎"],
            },
            "new_hotwords": [],
        }

        removed, promoted, added = apply_feedback_optimizations(
            terms, hotwords, recs,
        )
        assert removed == 0
        assert promoted == 2
        assert "数据胡" in terms[0].mis_asr
        assert "数据虎" in terms[0].mis_asr

    def test_promote_new_term(self):
        """不存在的 term 应自动创建。"""
        terms: list = []
        hotwords: list = []
        recs = {
            "zombie_rules": [],
            "promote_to_dict": {
                "新术语": ["新数语"],
            },
            "new_hotwords": [],
        }

        removed, promoted, added = apply_feedback_optimizations(
            terms, hotwords, recs,
        )
        assert promoted == 1
        assert len(terms) == 1
        assert terms[0].term == "新术语"
        assert "新数语" in terms[0].mis_asr

    def test_add_hotwords(self):
        """新热词应追加到 hotwords 列表。"""
        terms: list = []
        hotwords = ["已有词"]
        recs = {
            "zombie_rules": [],
            "promote_to_dict": {},
            "new_hotwords": ["数据湖", "剪切板"],
        }

        removed, promoted, added = apply_feedback_optimizations(
            terms, hotwords, recs,
        )
        assert added == 2
        assert "数据湖" in hotwords
        assert "剪切板" in hotwords

    def test_hotwords_dedup_with_terms(self):
        """已在 terms 中的词不重复加入热词。"""
        terms = [
            AsrTerm(term="数据湖", category="project", context="", mis_asr=[]),
        ]
        hotwords: list = []
        recs = {
            "zombie_rules": [],
            "promote_to_dict": {},
            "new_hotwords": ["数据湖"],
        }

        removed, promoted, added = apply_feedback_optimizations(
            terms, hotwords, recs,
        )
        assert added == 0  # 已在 terms 中，不重复

    def test_combined_optimization(self):
        """综合场景：淘汰 + 提升 + 补热词。"""
        terms = [
            AsrTerm(term="数据湖", category="project", context="",
                    mis_asr=["数据糊", "数据湖工程"]),
        ]
        hotwords = ["已有词"]
        recs = {
            "zombie_rules": [("数据湖工程", "数据湖", "project")],
            "promote_to_dict": {"数据湖": ["数据胡"]},
            "new_hotwords": ["新热词"],
        }

        removed, promoted, added = apply_feedback_optimizations(
            terms, hotwords, recs,
        )
        assert removed == 1
        assert promoted == 1
        assert added == 1
        assert terms[0].mis_asr == ["数据糊", "数据胡"]  # 移除数据湖工程，加入数据胡
        assert "新热词" in hotwords
