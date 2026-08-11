"""LLMPromptOptimizer 单元测试 — _render_v2 & _pick_inference_examples 边界情况。"""

from __future__ import annotations


from iris.wiki.asr._types import AsrTerm
from iris.wiki.asr.prompt_optimizer import LLMPromptOptimizer


def _make_term(term: str, category: str, mis_asr=None) -> AsrTerm:
    return AsrTerm(term=term, category=category, context="", mis_asr=mis_asr or [])


# ── _pick_inference_examples ─────────────────────────────────────

class TestPickInferenceExamples:
    def test_uses_mis_asr_from_persons(self):
        persons = [_make_term("张三", "person", ["章三", "张珊"])]
        result = LLMPromptOptimizer._pick_inference_examples(persons, [])
        assert "章三" in result
        assert "张三" in result

    def test_uses_mis_asr_from_projects(self):
        projects = [_make_term("质检平台", "project", ["图眼平台"])]
        result = LLMPromptOptimizer._pick_inference_examples([], projects)
        assert "图眼平台" in result
        assert "质检平台" in result

    def test_falls_back_when_no_mis_asr(self):
        persons = [_make_term("李四", "person")]  # 无 mis_asr
        result = LLMPromptOptimizer._pick_inference_examples(persons, [])
        assert "李四" in result

    def test_falls_back_to_hardcoded_when_empty(self):
        result = LLMPromptOptimizer._pick_inference_examples([], [])
        assert "大模型" in result or "矫正" in result

    def test_at_most_two_person_examples(self):
        persons = [
            _make_term("人甲", "person", ["人假"]),
            _make_term("人乙", "person", ["人义"]),
            _make_term("人丙", "person", ["人病"]),  # 第三个不应出现
        ]
        result = LLMPromptOptimizer._pick_inference_examples(persons, [])
        assert result.count("人甲") + result.count("人乙") == 2
        assert "人丙" not in result

    def test_returns_string_ending_with_newline(self):
        result = LLMPromptOptimizer._pick_inference_examples([], [])
        assert result.endswith("\n")


# ── _render_v2 ───────────────────────────────────────────────────

class TestRenderV2:
    def test_returns_non_empty_string(self):
        result = LLMPromptOptimizer._render_v2([], [])
        assert isinstance(result, str)
        assert len(result) > 100

    def test_contains_core_sections(self):
        result = LLMPromptOptimizer._render_v2([], [])
        assert "核心规则" in result
        assert "校正" in result
        assert "润色" in result
        assert "格式" in result
        assert "领域保护名单" in result

    def test_empty_terms_shows_no_mappings(self):
        result = LLMPromptOptimizer._render_v2([], [])
        assert "（暂无）" in result

    def test_terms_with_mis_asr_injected(self):
        terms = [_make_term("检测平台", "project", ["检测盼他", "检测判他"])]
        result = LLMPromptOptimizer._render_v2([], terms)
        assert "检测平台" in result
        assert "检测盼他" in result or "检测判他" in result

    def test_protected_terms_include_projects_and_concepts(self):
        terms = [
            _make_term("质检3.0", "project"),
            _make_term("BM25", "concept"),
        ]
        result = LLMPromptOptimizer._render_v2([], terms)
        assert "质检3.0" in result or "BM25" in result

    def test_top_persons_appear_in_result(self):
        terms = [_make_term("张三", "person"), _make_term("李雷", "person")]
        result = LLMPromptOptimizer._render_v2([], terms)
        assert "张三" in result
        assert "李雷" in result

    def test_domain_context_injected(self):
        result = LLMPromptOptimizer._render_v2([], [], domain_context="电商质检场景")
        assert "电商质检场景" in result

    def test_default_domain_bg_when_empty(self):
        result = LLMPromptOptimizer._render_v2([], [], domain_context="")
        assert "专业团队" in result

    def test_hotwords_section_appears(self):
        """v3.24: hotwords 参数真正使用——渲染「高频词（优先识别）」段。"""
        result = LLMPromptOptimizer._render_v2(["图验技术", "质检平台", "AI巡检"], [])
        assert "高频词" in result
        assert "图验技术" in result
        assert "质检平台" in result
        assert "AI巡检" in result

    def test_hotwords_empty_placeholder(self):
        result = LLMPromptOptimizer._render_v2([], [])
        assert "高频词" in result
        assert "（暂无）" in result

    def test_hotwords_capped_at_40(self):
        hotwords = [f"热词{i}" for i in range(60)]
        result = LLMPromptOptimizer._render_v2(hotwords, [])
        # 只嵌入前 40 个
        assert "热词0" in result
        assert "热词39" in result
        assert "热词40" not in result

    def test_top_n_mappings_limit(self):
        terms = [
            _make_term(f"词{i}", "project", [f"词{i}误"])
            for i in range(50)
        ]
        result = LLMPromptOptimizer._render_v2([], terms, top_n_mappings=5)
        # 提取「映射」节（首次检查以下映射…到空行）中的「→」数量
        # 仅验证映射节的数量，不依赖全文中 → 的总计数（格式/推断示例也含 →）
        mapping_section_start = result.find("首先检查以下映射")
        mapping_section_end = result.find("\n\n", mapping_section_start)
        mapping_section = result[mapping_section_start:mapping_section_end]
        assert mapping_section.count("→") <= 5

    def test_no_duplicate_method_definition(self):
        """确认 optimize() 和 _render_v2 正常工作，无重复定义导致的 NotImplementedError。"""
        result = LLMPromptOptimizer.optimize([], [])
        assert isinstance(result, str)

    def test_dynamic_inference_example_appears(self):
        """有实际 mis_asr 时，动态示例应替代硬编码出现在 prompt 中。"""
        persons = [_make_term("王五", "person", ["汪舞"])]
        result = LLMPromptOptimizer._render_v2([], persons)
        assert "汪舞" in result
        # 无真实术语的硬编码示例在有数据时不应作为人名示例出现
        # （但如果既有人名示例又有项目兜底示例，大模型/矫正仍可能出现）


# ── optimize() 接口 ───────────────────────────────────────────────

class TestOptimize:
    def test_returns_string(self):
        result = LLMPromptOptimizer.optimize([], [])
        assert isinstance(result, str)

    def test_same_output_as_render_v2(self):
        terms = [_make_term("测试词", "concept", ["测试词误"])]
        via_optimize = LLMPromptOptimizer.optimize([], terms)
        via_render = LLMPromptOptimizer._render_v2([], terms)
        assert via_optimize == via_render

    def test_hotwords_flow_through_optimize(self):
        """v3.24: hotwords 经 optimize 流入渲染（不再有死参数）。"""
        result = LLMPromptOptimizer.optimize(["团队热词X"], [])
        assert "团队热词X" in result
