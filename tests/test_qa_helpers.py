"""qa/helpers.py 纯函数单元测试。"""

from __future__ import annotations

from typing import Any


from iris.qa.helpers import (
    infer_evidence_type,
    intent_title,
    group_title,
    is_memory_only_instruction,
    infer_question_type,
    block_bonus,
)
from iris.qa.models import AnswerBlock, Citation


# ── 测试辅助 ──────────────────────────────────────────────


def _make_hit(**kwargs) -> Any:
    """构建模拟 RetrievalHit。"""
    from iris.retrieval import RetrievalHit

    defaults = {
        "chunk_id": "test-1",
        "score": 1.0,
        "title": "测试文档",
        "relative_path": "test/source.md",
        "section_path": ["章节1"],
        "content_preview": "测试内容预览",
        "line_start": 1,
        "line_end": 10,
        "chunk_type": "section",
        "structural_tags": [],
        "matched_terms": [],
        "explanation": "",
        "extracted_fields": {},
    }
    defaults.update(kwargs)
    return RetrievalHit(**defaults)


def _make_block(**kwargs) -> AnswerBlock:
    """构建模拟 AnswerBlock。"""
    defaults = {
        "title": "测试",
        "summary": "测试内容",
        "citation": Citation(
            relative_path="test/source.md",
            section_path=["章节1"],
            line_start=1,
            line_end=10,
        ),
        "score": 1.0,
        "evidence_type": "general",
        "tags": [],
    }
    defaults.update(kwargs)
    return AnswerBlock(**defaults)


# ── infer_evidence_type ────────────────────────────────────


def test_infer_evidence_type_from_extracted_fields():
    hit = _make_hit(extracted_fields={"decision": ["a"], "goal": ["b"]})
    assert infer_evidence_type(hit) == "decision"


def test_infer_evidence_type_from_structural_tags():
    hit = _make_hit(structural_tags=["progress", "weekly"])
    assert infer_evidence_type(hit) == "progress"


def test_infer_evidence_type_default():
    hit = _make_hit()
    assert infer_evidence_type(hit) == "general"


def test_infer_evidence_type_risk():
    hit = _make_hit(extracted_fields={"risk": ["阻塞"]})
    assert infer_evidence_type(hit) == "risk"


def test_infer_evidence_type_definition():
    hit = _make_hit(structural_tags=["definition"])
    assert infer_evidence_type(hit) == "definition"


def test_infer_evidence_type_timeline():
    hit = _make_hit(extracted_fields={"timeline": ["2025Q1"]})
    assert infer_evidence_type(hit) == "timeline"


# ── intent_title ───────────────────────────────────────────


def test_intent_title_definition():
    assert intent_title("definition") == "定义与解释"


def test_intent_title_comparison():
    assert intent_title("comparison") == "对比要点"


def test_intent_title_timeline():
    assert intent_title("timeline") == "时间线要点"


def test_intent_title_risk():
    assert intent_title("risk") == "风险与问题"


def test_intent_title_reason():
    assert intent_title("reason") == "背景与原因"


def test_intent_title_unknown():
    assert intent_title("unknown_intent") == "依据"


# ── group_title ────────────────────────────────────────────


def test_group_title_goal():
    assert group_title("goal") == "目标"


def test_group_title_progress():
    assert group_title("progress") == "进展"


def test_group_title_decision():
    assert group_title("decision") == "结论/决策"


def test_group_title_risk():
    assert group_title("risk") == "风险"


def test_group_title_definition():
    assert group_title("definition") == "定义"


def test_group_title_timeline():
    assert group_title("timeline") == "时间线"


def test_group_title_supporting():
    assert group_title("supporting") == "补充证据"


def test_group_title_unknown_returns_original():
    assert group_title("custom_group") == "custom_group"


# ── is_memory_only_instruction ─────────────────────────────


def test_memory_instruction_remember():
    assert is_memory_only_instruction("记住我的邮箱是 test@test.com")


def test_memory_instruction_correct():
    assert is_memory_only_instruction("纠正：我的职位是工程师")


def test_memory_instruction_preference():
    assert is_memory_only_instruction("我喜欢简洁的回答")


def test_memory_instruction_not_memory_too_long():
    long_text = "请分析一下" + "A" * 120
    assert not is_memory_only_instruction(long_text)


def test_memory_instruction_question_mark():
    assert not is_memory_only_instruction("记住我的偏好吗？")


def test_memory_instruction_contains_ask():
    assert not is_memory_only_instruction("请问进展如何")


def test_memory_instruction_contains_analysis():
    assert not is_memory_only_instruction("分析一下最近的进展")


# ── infer_question_type ────────────────────────────────────


def test_infer_project_from_wiki_hit():
    wiki_hits = [{"page_type": "project", "title": "测试项目"}]
    assert infer_question_type("随便问", wiki_hits) == "project"


def test_infer_topic_from_wiki_person():
    wiki_hits = [{"page_type": "person", "title": "张三"}]
    assert infer_question_type("张三的工作", wiki_hits) == "topic"


def test_infer_term_from_wiki_concept():
    wiki_hits = [{"page_type": "concept", "title": "BM25"}]
    assert infer_question_type("BM25是啥", wiki_hits) == "term"


def test_infer_project_from_keywords():
    assert infer_question_type("项目进展如何", []) == "project"


def test_infer_project_from_milestone():
    assert infer_question_type("里程碑进度", []) == "project"


def test_infer_term_from_definition():
    assert infer_question_type("AI是什么意思", []) == "term"


def test_infer_term_from_abbreviation():
    assert infer_question_type("BM25", []) == "term"


def test_infer_topic_from_mechanism():
    assert infer_question_type("推荐机制如何工作", []) == "topic"


def test_infer_topic_default():
    assert infer_question_type("随便看看", []) == "topic"


# ── block_bonus ────────────────────────────────────────────


def test_block_bonus_project_keywords():
    block = _make_block(title="项目目标与进展", summary="当前目标已完成80%")
    bonus = block_bonus(block, "project")
    assert bonus > 0


def test_block_bonus_term_keywords():
    block = _make_block(title="BM25术语定义", summary="BM25是一种排序算法")
    bonus = block_bonus(block, "term")
    assert bonus > 0


def test_block_bonus_noise_penalty():
    block = _make_block(title="会议信息汇总", summary="邮件信息模板")
    bonus = block_bonus(block, "topic")
    assert bonus < 0  # noise penalty


def test_block_bonus_decision_boost():
    block = _make_block(evidence_type="decision", summary="最终结论是...")
    bonus = block_bonus(block, "topic")
    assert bonus > 0


def test_block_bonus_topic_default():
    block = _make_block(summary="讨论了一些流程机制")
    bonus = block_bonus(block, "topic")
    assert bonus > 0
