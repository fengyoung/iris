"""测试查询规划器：规则规划 + LLM 增强。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iris.retrieval.planner import (
    LLMQueryPlanner,
    QueryPlan,
    QueryPlanner,
    _extract_keywords,
    _infer_query_intent,
    _infer_question_type,
    _infer_time_scope,
)


# ── 规则意图识别 ──────────────────────────────────────────


@pytest.mark.parametrize("query,expected", [
    ("RRF 是什么", "definition"),
    ("微服务的含义是什么", "definition"),
    ("对比 BM25 和向量检索", "comparison"),
    ("项目 Alpha 和 Beta 的区别", "comparison"),
    ("最近进展如何", "timeline"),
    ("当前有哪些风险", "risk"),
    ("为什么搜索不生效", "reason"),
    ("随便看看", "general"),
])
def test_infer_query_intent(query, expected):
    assert _infer_query_intent(query) == expected


@pytest.mark.parametrize("query,expected", [
    ("项目 Alpha 的目标", "project"),
    ("当前进展和里程碑", "project"),
    ("RRF 是什么", "term"),
    ("微服务定义", "term"),
    ("随便聊聊", "topic"),
])
def test_infer_question_type(query, expected):
    assert _infer_question_type(query) == expected


@pytest.mark.parametrize("query,expected", [
    ("最近有哪些更新", "recent"),
    ("本周计划", "recent"),
    ("去年 Q3 的目标", "historical"),
    ("2026 年规划", "historical"),
    ("什么是什么", "unspecified"),
])
def test_infer_time_scope(query, expected):
    assert _infer_time_scope(query) == expected


def test_extract_keywords_removes_stopwords():
    result = _extract_keywords("帮我查一下最近搜索怎么样")
    assert "一下" not in result
    assert "帮我" not in result
    assert len(result) > 0


# ── QueryPlanner 整体 ─────────────────────────────────────


def test_query_planner_full_plan():
    planner = QueryPlanner()
    plan = planner.build("什么是 RRF？与 BM25 对比有何区别？")
    assert plan.query_intent in ("definition", "comparison")
    assert plan.explain is not None
    assert len(plan.explain) >= 2


def test_query_planner_generic_query():
    planner = QueryPlanner()
    plan = planner.build("hello")
    assert plan.query_intent == "general"
    assert plan.question_type == "topic"


# ── LLMQueryPlanner._should_enhance ────────────────────────


def make_plan(query="test", intent="general", qtype="topic", entities=None):
    return QueryPlan(
        original_query=query,
        normalized_query=query,
        query_intent=intent,
        question_type=qtype,
        time_scope="unspecified",
        entities=entities or [],
        keywords=[],
    )


def test_should_enhance_generic_intent():
    """generic intent 应触发 LLM 增强。"""
    llm = LLMQueryPlanner(None, None)
    plan = make_plan(intent="general")
    assert llm._should_enhance(plan) is True


def test_should_enhance_topic_intent():
    """topic intent 也应触发（低置信度）。"""
    llm = LLMQueryPlanner(None, None)
    plan = make_plan(intent="general", qtype="topic")
    assert llm._should_enhance(plan) is True


def test_should_enhance_definition_with_entities():
    """definition + 足够实体 → 跳过增强。"""
    llm = LLMQueryPlanner(None, None)
    plan = make_plan(intent="definition", entities=["RRF", "BM25"])
    assert llm._should_enhance(plan) is False


def test_should_enhance_comparison_no_entities():
    """comparison 但无实体 → 仍需增强。"""
    llm = LLMQueryPlanner(None, None)
    plan = make_plan(intent="comparison")
    assert llm._should_enhance(plan) is True


# ── LLMQueryPlanner.enhance ────────────────────────────────


def test_enhance_skips_when_not_needed():
    """条件不满足时直接返回原 plan。"""
    mock_llm = MagicMock()
    llm = LLMQueryPlanner(mock_llm, None)
    plan = make_plan(intent="definition", entities=["RRF", "BM25", "搜索"])
    result = llm.enhance(plan)
    assert result is plan  # 同一对象
    mock_llm.generate.assert_not_called()


def test_enhance_calls_llm_when_needed():
    """generic intent 触发 LLM 调用。"""
    mock_resp = MagicMock()
    mock_resp.text = json.dumps({
        "intent": "definition",
        "question_type": "term",
        "time_scope": "recent",
        "entities": ["RRF"],
        "keywords": ["检索", "融合"],
    })
    mock_llm = MagicMock()
    mock_llm.generate.return_value = mock_resp

    llm = LLMQueryPlanner(mock_llm, None)
    plan = make_plan(intent="general", entities=[])
    result = llm.enhance(plan)

    assert result.query_intent == "definition"
    assert result.question_type == "term"
    assert "RRF" in result.entities
    assert result.llm_enhanced is True
    mock_llm.generate.assert_called_once()


def test_enhance_fallback_on_llm_error():
    """LLM 失败 → 返回原 plan（优雅降级）。"""
    mock_llm = MagicMock()
    mock_llm.generate.side_effect = RuntimeError("API 挂了")

    llm = LLMQueryPlanner(mock_llm, None)
    plan = make_plan(intent="general")
    result = llm.enhance(plan)

    assert result is plan  # 降级返回原始 plan
    assert result.llm_enhanced is False


def test_enhance_fallback_on_bad_json():
    """LLM 返回非 JSON → 降级。"""
    mock_resp = MagicMock()
    mock_resp.text = "这不是 JSON，抱歉。"
    mock_llm = MagicMock()
    mock_llm.generate.return_value = mock_resp

    llm = LLMQueryPlanner(mock_llm, None)
    plan = make_plan(intent="general")
    result = llm.enhance(plan)

    assert result is plan
    assert result.llm_enhanced is False


def test_enhance_fallback_on_empty_response():
    """LLM 返回空文本 → 降级。"""
    mock_resp = MagicMock()
    mock_resp.text = ""
    mock_llm = MagicMock()
    mock_llm.generate.return_value = mock_resp

    llm = LLMQueryPlanner(mock_llm, None)
    plan = make_plan(intent="general")
    result = llm.enhance(plan)

    assert result is plan
