"""complex_input/pipeline.py 三阶段流水线专项测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris.complex_input.pipeline import ComplexInputPipeline, PipelineResult, _safe_format
from iris.complex_input.detector import ComplexityResult


# ── _safe_format ──────────────────────────────────────────────────────


def test_safe_format_basic():
    tmpl = "用户问题：{{query}}，类型：{{file_type}}"
    result = _safe_format(tmpl, query="测试查询", file_type="image")
    assert result == "用户问题：测试查询，类型：image"


def test_safe_format_ignores_braces_in_value():
    """用户输入含花括号时不崩溃。"""
    tmpl = "问题：{{query}}"
    result = _safe_format(tmpl, query="what is {json} format?")
    assert "what is {json} format?" in result


def test_safe_format_missing_key_leaves_placeholder():
    """未提供的 key 保留原始占位符。"""
    tmpl = "{{query}} and {{other}}"
    result = _safe_format(tmpl, query="hello")
    assert "hello" in result
    assert "{{other}}" in result


# ── pipeline.process 正常路径 ─────────────────────────────────────────


def _make_pipeline(config_bundle):
    """构建带 FakeLLMProvider 的 ComplexInputPipeline。"""
    from iris.core.fake_provider import FakeLLMProvider
    with patch("iris.complex_input.pipeline.LLMService") as MockLLM:
        svc = MagicMock()
        svc.get_provider.return_value = FakeLLMProvider()
        MockLLM.return_value = svc
        pipeline = ComplexInputPipeline(config_bundle)
        pipeline._llm = svc
    return pipeline


def test_process_non_complex_input(config_bundle):
    """纯文本输入直接返回 is_complex=False，不调用 LLM。"""
    pipeline = _make_pipeline(config_bundle)

    fake_detection = ComplexityResult(
        is_complex=False,
        file_type="text",
        file_paths=[],
        reason="纯文本",
        encoded_images=[],
    )
    with patch.object(pipeline._detector, "detect", return_value=fake_detection):
        result = pipeline.process("简单问题")

    assert isinstance(result, PipelineResult)
    assert result.is_complex is False
    assert result.stage1_prompt is None
    assert result.stage2_output is None
    assert result.stage3_output == ""


def test_process_stage1_failure_returns_early(config_bundle):
    """Stage 1 失败时立即返回，stage2/stage3 为 None / 空串。"""
    pipeline = _make_pipeline(config_bundle)

    fake_detection = ComplexityResult(
        is_complex=True,
        file_type="image",
        file_paths=["/tmp/test.png"],
        reason="图片输入",
        encoded_images=[],
    )
    with patch.object(pipeline._detector, "detect", return_value=fake_detection), \
         patch.object(pipeline, "_stage1_prompt_gen", return_value=("[Stage1 失败] 错误", None)):
        result = pipeline.process("分析图片", file_paths=["/tmp/test.png"])

    assert result.is_complex is True
    assert result.stage1_prompt is not None and "Stage1 失败" in result.stage1_prompt
    assert result.stage2_output is None
    # Stage1 失败时 stage3_output 包含错误信息（不为空，但 stage2 为 None）
    assert "失败" in result.stage3_output or result.stage3_output == ""


def test_process_stage2_failure_falls_back_to_text(config_bundle):
    """Stage 2（多模态）失败时，Stage 3 用 stage2_fallback 文本回退。"""
    pipeline = _make_pipeline(config_bundle)

    fake_detection = ComplexityResult(
        is_complex=True,
        file_type="image",
        file_paths=["/tmp/test.png"],
        reason="图片输入",
        encoded_images=[],
    )
    with patch.object(pipeline._detector, "detect", return_value=fake_detection), \
         patch.object(pipeline, "_stage1_prompt_gen", return_value=("分析这张图", "base")), \
         patch.object(pipeline, "_stage2_multimodal", return_value=("[Stage2 失败] 无法调用多模态", "adv")), \
         patch.object(pipeline, "_stage3_integrate", return_value=("最终回答", "base")):
        result = pipeline.process("分析图片", file_paths=["/tmp/test.png"])

    # stage3 被调用（即使 stage2 失败也要尝试）
    assert result.stage3_output == "最终回答"


def test_process_full_success_returns_stage3_output(config_bundle):
    """全流程成功时 stage3_output 非空。"""
    pipeline = _make_pipeline(config_bundle)

    fake_detection = ComplexityResult(
        is_complex=True,
        file_type="image",
        file_paths=["/tmp/test.png"],
        reason="图片输入",
        encoded_images=[],
    )
    with patch.object(pipeline._detector, "detect", return_value=fake_detection), \
         patch.object(pipeline, "_stage1_prompt_gen", return_value=("分析这张图", "base")), \
         patch.object(pipeline, "_stage2_multimodal", return_value=("图片内容：一只猫", "adv")), \
         patch.object(pipeline, "_stage3_integrate", return_value=("这是一只猫的图片", "base")):
        result = pipeline.process("图中是什么", file_paths=["/tmp/test.png"])

    assert result.is_complex is True
    assert result.stage2_output == "图片内容：一只猫"
    assert result.stage3_output == "这是一只猫的图片"
    assert result.file_paths == ["/tmp/test.png"]


# ── PipelineResult.to_dict ────────────────────────────────────────────


def test_pipeline_result_to_dict():
    r = PipelineResult(
        query="q", is_complex=True, file_type="image",
        stage1_prompt="p1", stage1_model="m1",
        stage2_output="p2", stage2_model="m2",
        stage3_output="p3", stage3_model="m3",
        file_paths=["/f.png"], detection_reason="reason",
    )
    d = r.to_dict()
    assert d["query"] == "q"
    assert d["is_complex"] is True
    assert d["stage3_output"] == "p3"
    assert d["file_paths"] == ["/f.png"]
