"""complex_input/pipeline.py 三阶段流水线专项测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris.complex_input.detector import ComplexityResult, EncodedImage
from iris.complex_input.pipeline import ComplexInputPipeline, PipelineResult, _safe_format


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


# ── PDF Stage 2 路由 ──────────────────────────────────────────────


def _make_pipeline_with_mocks(config_bundle):
    """构建 pipeline 并返回 pipeline + mock_llm_service。"""
    with patch("iris.complex_input.pipeline.LLMService") as MockLLM:
        mock_svc = MagicMock()
        MockLLM.return_value = mock_svc
        pipeline = ComplexInputPipeline(config_bundle)
        pipeline._llm = mock_svc
    return pipeline, mock_svc


def test_stage2_routes_to_pdf_when_file_type_is_pdf(config_bundle):
    """file_type=pdf 且无 encoded_images 时，路由到 _stage2_pdf。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    detection = ComplexityResult(
        is_complex=True,
        file_type="pdf",
        file_paths=["/tmp/test.pdf"],
        reason="PDF 输入",
        encoded_images=[],
    )

    with patch.object(pipeline, "_stage2_pdf", return_value=("PDF 分析结果", "qwen")) as mock_pdf:
        result = pipeline._stage2_multimodal("分析 PDF", detection)
        mock_pdf.assert_called_once()
        assert result == ("PDF 分析结果", "qwen")


def test_stage2_routes_to_images_when_encoded_images_present(config_bundle):
    """有 encoded_images 时优先走图片路径（即使 file_type=pdf，mixed 场景）。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    detection = ComplexityResult(
        is_complex=True,
        file_type="mixed",
        file_paths=["/tmp/test.pdf", "/tmp/test.png"],
        reason="混合输入",
        encoded_images=[EncodedImage(path="/tmp/test.png", mime_type="image/png",
                                      data_url="data:image/png;base64,xxx")],
    )

    with patch.object(pipeline, "_stage2_images", return_value=("图片结果", "qwen")) as mock_img, \
         patch.object(pipeline, "_stage2_pdf", return_value=("PDF结果", "qwen")) as mock_pdf:
        result = pipeline._stage2_multimodal("分析文件", detection)
        mock_img.assert_called_once()
        mock_pdf.assert_not_called()
        assert result == ("图片结果", "qwen")


def test_stage2_routes_to_video_when_file_type_is_video(config_bundle):
    """file_type=video 且无 encoded_images 时，路由到 _stage2_video。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    detection = ComplexityResult(
        is_complex=True,
        file_type="video",
        file_paths=["/tmp/clip.mp4"],
        reason="视频输入",
        encoded_images=[],
    )

    with patch.object(pipeline, "_stage2_video", return_value=("视频分析结果", "qwen-vl")) as mock_video:
        result = pipeline._stage2_multimodal("分析视频", detection)
        mock_video.assert_called_once()
        assert result == ("视频分析结果", "qwen-vl")


def test_stage2_skips_unsupported_types(config_bundle):
    """未知类型（非图片/PDF/DOCX/视频）返回跳过提示。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    detection = ComplexityResult(
        is_complex=True,
        file_type="unknown",
        file_paths=["/tmp/data.bin"],
        reason="未知输入",
        encoded_images=[],
    )

    result_text, result_model = pipeline._stage2_multimodal("分析文件", detection)
    assert "跳过" in result_text
    assert result_model is None


# ── _stage2_pdf 集成测试 ─────────────────────────────────────────


def test_stage2_pdf_success(config_bundle):
    """_stage2_pdf 成功调用多模态模型。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    # Mock PdfAdapter.process 返回值
    fake_pdf_content = MagicMock()
    fake_pdf_content.total_pages = 3
    fake_pdf_content.rendered_pages = 3
    fake_pdf_content.text = "PDF text content"
    fake_pdf_content.page_images = [
        EncodedImage(path="/tmp/test.pdf#page=1", mime_type="image/png",
                     data_url="data:image/png;base64,page1"),
        EncodedImage(path="/tmp/test.pdf#page=2", mime_type="image/png",
                     data_url="data:image/png;base64,page2"),
        EncodedImage(path="/tmp/test.pdf#page=3", mime_type="image/png",
                     data_url="data:image/png;base64,page3"),
    ]
    fake_pdf_content.error = None

    # Mock LLM 多模态返回
    mock_svc.generate_multimodal.return_value = "这是 PDF 的分析结果。"
    mock_svc.get_provider.return_value.get_active_model_config.return_value = {
        "model": "qwen3.7-plus"
    }

    detection = ComplexityResult(
        is_complex=True,
        file_type="pdf",
        file_paths=["/tmp/test.pdf"],
        reason="PDF 输入",
        encoded_images=[],
    )

    with patch("iris.complex_input.pdf_adapter.PdfAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        mock_adapter.process.return_value = fake_pdf_content
        MockAdapter.return_value = mock_adapter

        text, model = pipeline._stage2_pdf("请分析这份 PDF", detection)

    assert "PDF 的分析结果" in text
    assert model == "qwen3.7-plus"
    mock_svc.generate_multimodal.assert_called_once()


def test_stage2_pdf_adapter_error_graceful(config_bundle):
    """PdfAdapter 抛出 PdfAdapterError 时优雅降级。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    detection = ComplexityResult(
        is_complex=True,
        file_type="pdf",
        file_paths=["/tmp/broken.pdf"],
        reason="PDF 输入",
        encoded_images=[],
    )

    with patch("iris.complex_input.pdf_adapter.PdfAdapter") as MockAdapter:
        from iris.complex_input.pdf_adapter import PdfAdapterError
        mock_adapter = MagicMock()
        mock_adapter.process.side_effect = PdfAdapterError("PyMuPDF (fitz) 未安装")
        MockAdapter.return_value = mock_adapter

        text, model = pipeline._stage2_pdf("分析 PDF", detection)

    # 全部 PDF 失败时返回跳过
    assert "跳过" in text or "失败" in text
    assert model is None


def test_stage2_pdf_llm_error_fallback(config_bundle):
    """LLM 多模态调用失败时返回错误文本。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    fake_pdf_content = MagicMock()
    fake_pdf_content.total_pages = 1
    fake_pdf_content.rendered_pages = 1
    fake_pdf_content.text = "Content"
    fake_pdf_content.page_images = [
        EncodedImage(path="/tmp/test.pdf#page=1", mime_type="image/png",
                     data_url="data:image/png;base64,test")
    ]
    fake_pdf_content.error = None

    from iris.llm import LLMProviderError
    mock_svc.generate_multimodal.side_effect = LLMProviderError("API 不可用")

    detection = ComplexityResult(
        is_complex=True,
        file_type="pdf",
        file_paths=["/tmp/test.pdf"],
        reason="PDF 输入",
        encoded_images=[],
    )

    with patch("iris.complex_input.pdf_adapter.PdfAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        mock_adapter.process.return_value = fake_pdf_content
        MockAdapter.return_value = mock_adapter

        text, model = pipeline._stage2_pdf("分析 PDF", detection)

    assert "Stage2 失败" in text
    assert model is None


def test_stage2_pdf_multiple_files(config_bundle):
    """多个 PDF 文件时全部处理。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    fake_content_1 = MagicMock()
    fake_content_1.total_pages = 2
    fake_content_1.rendered_pages = 2
    fake_content_1.text = "PDF 1 text"
    fake_content_1.page_images = [
        EncodedImage(path="/tmp/a.pdf#page=1", mime_type="image/png",
                     data_url="data:image/png;base64,a1"),
    ]
    fake_content_1.error = None

    fake_content_2 = MagicMock()
    fake_content_2.total_pages = 1
    fake_content_2.rendered_pages = 1
    fake_content_2.text = "PDF 2 text"
    fake_content_2.page_images = [
        EncodedImage(path="/tmp/b.pdf#page=1", mime_type="image/png",
                     data_url="data:image/png;base64,b1"),
    ]
    fake_content_2.error = None

    mock_svc.generate_multimodal.return_value = "多文件分析结果"
    mock_svc.get_provider.return_value.get_active_model_config.return_value = {
        "model": "qwen3.7-plus"
    }

    detection = ComplexityResult(
        is_complex=True,
        file_type="pdf",
        file_paths=["/tmp/a.pdf", "/tmp/b.pdf"],
        reason="多 PDF 输入",
        encoded_images=[],
    )

    with patch("iris.complex_input.pdf_adapter.PdfAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        mock_adapter.process.side_effect = [fake_content_1, fake_content_2]
        MockAdapter.return_value = mock_adapter

        text, model = pipeline._stage2_pdf("分析 PDF", detection)

    assert "多文件分析结果" in text
    assert model == "qwen3.7-plus"
    # 验证 process 被调用了两次（每个文件一次）
    assert mock_adapter.process.call_count == 2


def test_stage2_pdf_partial_failure_continues(config_bundle):
    """多个 PDF 中某个失败时，其他继续处理。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    fake_content = MagicMock()
    fake_content.total_pages = 1
    fake_content.rendered_pages = 1
    fake_content.text = "OK PDF text"
    fake_content.page_images = [
        EncodedImage(path="/tmp/ok.pdf#page=1", mime_type="image/png",
                     data_url="data:image/png;base64,ok"),
    ]
    fake_content.error = None

    mock_svc.generate_multimodal.return_value = "部分成功分析"
    mock_svc.get_provider.return_value.get_active_model_config.return_value = {
        "model": "qwen3.7-plus"
    }

    detection = ComplexityResult(
        is_complex=True,
        file_type="pdf",
        file_paths=["/tmp/broken.pdf", "/tmp/ok.pdf"],
        reason="多 PDF 输入",
        encoded_images=[],
    )

    with patch("iris.complex_input.pdf_adapter.PdfAdapter") as MockAdapter:
        from iris.complex_input.pdf_adapter import PdfAdapterError
        mock_adapter = MagicMock()
        mock_adapter.process.side_effect = [
            PdfAdapterError("无法打开文件"),
            fake_content,
        ]
        MockAdapter.return_value = mock_adapter

        text, model = pipeline._stage2_pdf("分析 PDF", detection)

    # 至少成功的文件被处理了
    assert "部分成功分析" in text
    assert mock_adapter.process.call_count == 2


# ── _stage2_images 重构后仍正常工作 ──────────────────────────────


def test_stage2_images_still_works(config_bundle):
    """重构后 _stage2_images 逻辑不变（从原 _stage2_multimodal 提取）。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    mock_svc.generate_multimodal.return_value = "图片分析结果"
    mock_svc.get_provider.return_value.get_active_model_config.return_value = {
        "model": "qwen3.7-plus"
    }

    detection = ComplexityResult(
        is_complex=True,
        file_type="image",
        file_paths=["/tmp/test.png"],
        reason="图片输入",
        encoded_images=[
            EncodedImage(path="/tmp/test.png", mime_type="image/png",
                         data_url="data:image/png;base64,imgdata"),
        ],
    )

    text, model = pipeline._stage2_images("分析图片", detection)

    assert "图片分析结果" in text
    assert model == "qwen3.7-plus"
    mock_svc.generate_multimodal.assert_called_once()

    # 验证 content_parts 包含图片 data_url
    call_args = mock_svc.generate_multimodal.call_args[0][0]
    assert any(p.get("type") == "image_url" for p in call_args)


def test_stage2_images_llm_error(config_bundle):
    """图片路径 LLM 失败时的错误处理不变。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    from iris.llm import LLMProviderError
    mock_svc.generate_multimodal.side_effect = LLMProviderError("API 错误")

    detection = ComplexityResult(
        is_complex=True,
        file_type="image",
        file_paths=["/tmp/test.png"],
        reason="图片输入",
        encoded_images=[
            EncodedImage(path="/tmp/test.png", mime_type="image/png",
                         data_url="data:image/png;base64,imgdata"),
        ],
    )

    text, model = pipeline._stage2_images("分析图片", detection)
    assert "Stage2 失败" in text
    assert model is None


# ── _stage2_docx 集成测试 ─────────────────────────────────────


def test_stage2_routes_to_docx_when_file_type_is_document(config_bundle):
    """file_type=document 时路由到 _stage2_docx。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    detection = ComplexityResult(
        is_complex=True,
        file_type="document",
        file_paths=["/tmp/test.docx"],
        reason="文档输入",
        encoded_images=[],
    )

    with patch.object(pipeline, "_stage2_docx", return_value=("DOCX文字内容", "qwen")) as mock_docx:
        result = pipeline._stage2_multimodal("分析文档", detection)
        mock_docx.assert_called_once()
        assert result == ("DOCX文字内容", "qwen")


def test_stage2_docx_success(config_bundle):
    """_stage2_docx 成功提取 DOCX 文字。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    fake_docx_content = MagicMock()
    fake_docx_content.text = "Document content here."
    fake_docx_content.paragraph_count = 5
    fake_docx_content.table_count = 0
    fake_docx_content.has_images = False
    fake_docx_content.error = None

    mock_svc.get_provider.return_value.get_active_model_config.return_value = {
        "model": "qwen3.7-plus"
    }

    detection = ComplexityResult(
        is_complex=True,
        file_type="document",
        file_paths=["/tmp/test.docx"],
        reason="文档输入",
        encoded_images=[],
    )

    with patch("iris.complex_input.docx_adapter.DocxAdapter") as MockAdapter:
        mock_adapter = MagicMock()
        mock_adapter.process.return_value = fake_docx_content
        MockAdapter.return_value = mock_adapter

        text, model = pipeline._stage2_docx("分析文档", detection)

    assert "Document content here" in text
    assert model == "docx_text_extraction"


def test_stage2_docx_adapter_error(config_bundle):
    """DocxAdapter 抛出错误时优雅返回跳过。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)

    detection = ComplexityResult(
        is_complex=True,
        file_type="document",
        file_paths=["/tmp/broken.docx"],
        reason="文档输入",
        encoded_images=[],
    )

    with patch("iris.complex_input.docx_adapter.DocxAdapter") as MockAdapter:
        from iris.complex_input.docx_adapter import DocxAdapterError
        mock_adapter = MagicMock()
        mock_adapter.process.side_effect = DocxAdapterError("无法打开文件")
        MockAdapter.return_value = mock_adapter

        text, model = pipeline._stage2_docx("分析文档", detection)

    # 全部 DOCX 失败时返回跳过或错误信息
    assert "跳过" in text or "Stage2" in text
    assert model is None


# ── _stage2_video 集成测试 ────────────────────────────────────────

from iris.complex_input.video_adapter import VideoContent, VideoAdapterError  # noqa: E402


def _video_detection(paths=None):
    return ComplexityResult(
        is_complex=True,
        file_type="video",
        file_paths=paths or ["/tmp/clip.mp4"],
        reason="视频输入",
        encoded_images=[],
    )


def test_stage2_video_success(config_bundle):
    """_stage2_video 成功：帧 + 转写送入多模态模型。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)
    mock_svc.generate_multimodal.return_value = "视频内容分析结果。"
    mock_svc.get_provider.return_value.get_active_model_config.return_value = {"model": "qwen-vl-max"}

    fake_content = VideoContent(
        path="/tmp/clip.mp4",
        transcript="这是转写文本",
        frames=[EncodedImage(path="/tmp/f0.jpg", mime_type="image/jpeg", data_url="data:image/jpeg;base64,xxx")],
        duration_sec=12.0,
        frame_count=1,
        has_audio=True,
    )
    with patch("iris.complex_input.video_adapter.VideoAdapter") as MockAdapter:
        instance = MockAdapter.return_value
        instance.process.return_value = fake_content
        text, model = pipeline._stage2_video("分析视频", _video_detection())

    assert text == "视频内容分析结果。"
    assert model == "qwen-vl-max"
    mock_svc.generate_multimodal.assert_called_once()
    # content_parts 应含转写文字与帧图片
    call_args = mock_svc.generate_multimodal.call_args[0][0]
    assert any(p.get("type") == "image_url" for p in call_args)
    assert any("转写" in p.get("text", "") for p in call_args if p.get("type") == "text")


def test_stage2_video_ffmpeg_missing_graceful(config_bundle):
    """ffmpeg 缺失（VideoAdapter 构造抛错）时优雅降级为跳过提示。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)
    with patch("iris.complex_input.video_adapter.VideoAdapter", side_effect=VideoAdapterError("ffmpeg 未安装")):
        text, model = pipeline._stage2_video("分析视频", _video_detection())
    assert "跳过" in text
    assert "ffmpeg" in text
    assert model is None
    mock_svc.generate_multimodal.assert_not_called()


def test_stage2_video_no_frames_no_transcript_skips(config_bundle):
    """既无帧也无转写时返回跳过提示，不调用 LLM。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)
    empty = VideoContent(path="/tmp/clip.mp4", transcript="", frames=[], has_audio=False,
                         error="抽帧失败")
    with patch("iris.complex_input.video_adapter.VideoAdapter") as MockAdapter:
        MockAdapter.return_value.process.return_value = empty
        text, model = pipeline._stage2_video("分析视频", _video_detection())
    assert "跳过" in text
    assert model is None
    mock_svc.generate_multimodal.assert_not_called()


def test_stage2_video_frames_only_no_audio(config_bundle):
    """无音轨但有帧时仍走多模态分析。"""
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)
    mock_svc.generate_multimodal.return_value = "仅凭画面的分析"
    mock_svc.get_provider.return_value.get_active_model_config.return_value = {"model": "qwen-vl-max"}
    content = VideoContent(
        path="/tmp/clip.mp4", transcript="", has_audio=False, frame_count=2,
        frames=[
            EncodedImage(path="/tmp/f0.jpg", mime_type="image/jpeg", data_url="data:image/jpeg;base64,a"),
            EncodedImage(path="/tmp/f1.jpg", mime_type="image/jpeg", data_url="data:image/jpeg;base64,b"),
        ],
    )
    with patch("iris.complex_input.video_adapter.VideoAdapter") as MockAdapter:
        MockAdapter.return_value.process.return_value = content
        text, model = pipeline._stage2_video("分析视频", _video_detection())
    assert text == "仅凭画面的分析"
    mock_svc.generate_multimodal.assert_called_once()


def test_stage2_video_llm_error_fallback(config_bundle):
    """多模态模型调用失败时返回 [Stage2 失败]。"""
    from iris.llm import LLMProviderError
    pipeline, mock_svc = _make_pipeline_with_mocks(config_bundle)
    mock_svc.generate_multimodal.side_effect = LLMProviderError("API 不可用")
    content = VideoContent(
        path="/tmp/clip.mp4", transcript="有转写", has_audio=True, frame_count=1,
        frames=[EncodedImage(path="/tmp/f0.jpg", mime_type="image/jpeg", data_url="data:image/jpeg;base64,a")],
    )
    with patch("iris.complex_input.video_adapter.VideoAdapter") as MockAdapter:
        MockAdapter.return_value.process.return_value = content
        text, model = pipeline._stage2_video("分析视频", _video_detection())
    assert "[Stage2 失败]" in text
    assert model is None
