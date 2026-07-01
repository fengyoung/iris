"""三阶段处理流水线：base 生成指令 → adv 多模态理解 → base 整合润色。

设计动机：
  - Stage 1: base_model 根据 query + file_type 动态生成 adv_model 的分析指令
  - Stage 2: adv_model 按指令分析非文本内容（图片/PDF/视频等）
  - Stage 3: base_model 整合分析结果并润色输出
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.complex_input.detector import (
    ComplexityResult,
    EncodedImage,
    InputDetector,
)
from iris.config.loader import ConfigBundle
from iris.llm import LLMProviderError
from iris.llm.service import LLMService

logger = logging.getLogger(__name__)


def _safe_format(template: str, **kwargs: str) -> str:
    """安全的模板替换 — 用逐字段 replace 代替 str.format()，

    使用 {{key}} 语法，与 prompting.py 和 wiki 模板保持一致。
    避免用户输入或 LLM 输出中的花括号触发 KeyError。
    """
    result = template
    for key, value in kwargs.items():
        result = result.replace("{{" + key + "}}", value)
    return result


@dataclass(frozen=True)
class PipelineResult:
    """三阶段 pipeline 输出结果。"""

    query: str
    is_complex: bool
    file_type: str
    stage1_prompt: Optional[str]
    stage1_model: Optional[str]
    stage2_output: Optional[str]
    stage2_model: Optional[str]
    stage3_output: str
    stage3_model: Optional[str]
    file_paths: List[str]
    detection_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Stage 1 prompt 模板 ────────────────────────────────────────────

_STAGE1_TEMPLATE = """用户问题：{{query}}

输入文件类型：{{file_type}}

你的任务是生成一段给多模态模型的分析指令，指导它如何处理用户上传的附件。

要求：
1. 指令应聚焦于"用户需要从附件中获取什么信息"
2. 如果用户问题中包含具体指令（如"识别文字"、"描述图片"），请在指令中保留
3. 指令应简洁、可执行，100-300 字
4. 只输出指令内容，不要额外说明"""

# ── Stage 3 prompt 模板 ────────────────────────────────────────────

_STAGE3_TEMPLATE = """用户问题：{{query}}

输入文件类型：{{file_type}}

以下是对附件的分析结果：
---
{{stage2_output}}
---

请基于以上分析结果回答用户的问题。要求：
1. 直接回答用户的问题
2. 语言流畅、结构清晰
3. 适当引用分析结果中的关键信息
4. 如果分析结果不足以回答用户问题，如实说明"""


class ComplexInputPipeline:
    """复杂输入三阶段处理流水线。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._llm = LLMService(config)
        self._detector = InputDetector()

    def process(
        self,
        query: str,
        *,
        file_paths: Optional[List[str]] = None,
        output_path: Optional[str] = None,
    ) -> PipelineResult:
        """处理复杂输入。

        Args:
            query: 用户查询文本
            file_paths: 附件文件路径列表
            output_path: 可选输出文件路径

        Returns:
            PipelineResult
        """
        detection = self._detector.detect(query, file_paths=file_paths)

        if not detection.is_complex:
            # 纯文本不应走到此 pipeline（应由调用方自行判断）
            logger.warning(
                "纯文本输入进入 ComplexInputPipeline（应由调用方拦截）: %s",
                detection.reason,
            )
            return PipelineResult(
                query=query,
                is_complex=False,
                file_type=detection.file_type,
                stage1_prompt=None,
                stage1_model=None,
                stage2_output=None,
                stage2_model=None,
                stage3_output="",
                stage3_model=None,
                file_paths=detection.file_paths,
                detection_reason=detection.reason,
            )

        # Stage 1: base_model 生成 adv_model 指令
        stage1_text, stage1_model = self._stage1_prompt_gen(
            query, detection.file_type
        )
        if stage1_text.startswith("[Stage1 失败]"):
            result = PipelineResult(
                query=query,
                is_complex=True,
                file_type=detection.file_type,
                stage1_prompt=stage1_text,
                stage1_model=stage1_model,
                stage2_output=None,
                stage2_model=None,
                stage3_output=f"指令生成阶段不可用：{stage1_text}",
                stage3_model=stage1_model,
                file_paths=detection.file_paths,
                detection_reason=detection.reason,
            )
            self._maybe_write_output(result, output_path)
            return result

        # Stage 2: adv_model 多模态理解
        stage2_text, stage2_model = self._stage2_multimodal(
            stage1_text, detection
        )

        # Stage 3: base_model 整合润色
        stage3_text, stage3_model = self._stage3_integrate(
            query, stage2_text, detection.file_type
        )

        result = PipelineResult(
            query=query,
            is_complex=True,
            file_type=detection.file_type,
            stage1_prompt=stage1_text,
            stage1_model=stage1_model,
            stage2_output=stage2_text,
            stage2_model=stage2_model,
            stage3_output=stage3_text,
            stage3_model=stage3_model,
            file_paths=detection.file_paths,
            detection_reason=detection.reason,
        )

        self._maybe_write_output(result, output_path)
        return result

    # ── Stage 1: base_model 生成 adv_model 指令 ────────────────────

    def _stage1_prompt_gen(
        self, query: str, file_type: str
    ) -> Tuple[str, Optional[str]]:
        """调用 base_model 生成 adv_model 的分析指令。"""
        prompt = _safe_format(_STAGE1_TEMPLATE, query=query, file_type=file_type)
        try:
            result = self._llm.generate(
                prompt,
                route_context={
                    "input_type": "text",
                    "task_type": "prompt_gen",
                    "complexity": "standard",
                },
            )
            return result.text.strip(), result.model
        except LLMProviderError as exc:
            return f"[Stage1 失败] base_model 调用出错: {exc}", None

    # ── Stage 2: adv_model 多模态理解 ──────────────────────────────

    def _stage2_multimodal(
        self, adv_prompt: str, detection: ComplexityResult
    ) -> Tuple[str, Optional[str]]:
        """调用 adv_model 分析非文本内容。

        图片类附件以 image_url 形式传入。非图片类型（PDF/DOCX/VIDEO）
        暂不支持多模态直接读取，返回明确提示。
        """
        from iris.utils.constants import FILE_TYPE_IMAGE as _IMG

        # 无编码图片 → 全部为非图片类型，暂不支持
        if not detection.encoded_images:
            unsupported = [p.split("/")[-1] for p in detection.file_paths]
            return (
                f"[Stage2 跳过] 检测到非图片文件，暂不支持多模态直接分析：{', '.join(unsupported)}。"
                f"请先将文件转换为图片或文本格式。",
                None,
            )

        # 构建多模态内容
        content_parts: List[Dict[str, Any]] = []

        # 文件上下文说明（基于已编码图片）
        img_names = [img.path.split("/")[-1] for img in detection.encoded_images]
        file_context = (
            f"附件图片数：{len(detection.encoded_images)}\n"
            f"文件名：{', '.join(img_names)}"
        )

        content_parts.append({"type": "text", "text": f"{adv_prompt}\n\n{file_context}"})

        # 附件是图片类型时才嵌入 base64 编码
        for img in detection.encoded_images:
            content_parts.append(
                {"type": "image_url", "image_url": {"url": img.data_url}}
            )

        try:
            text = self._llm.generate_multimodal(
                content_parts,
                route_context={
                    "input_type": "multimodal",
                    "task_type": "image_understanding",
                    "complexity": "complex",
                },
            )
            model_info = self._llm.get_provider().get_active_model_config("adv_model")
            return text.strip(), model_info.get("model", "adv_model")
        except LLMProviderError as exc:
            return f"[Stage2 失败] adv_model 调用出错: {exc}", None

    # ── Stage 3: base_model 整合润色 ───────────────────────────────

    def _stage3_integrate(
        self, query: str, stage2_output: str, file_type: str
    ) -> Tuple[str, Optional[str]]:
        """调用 base_model 整合 Stage 2 输出并润色回答。

        Stage 2 失败时也会进入 Stage 3，将错误信息包装后返回。
        """
        if stage2_output.startswith("[Stage2 失败]"):
            prompt = (
                f"用户问题：{{query}}\n\n"
                f"输入类型：{{file_type}}\n\n"
                f"多模态分析不可用。请根据已有信息尽可能回答用户。"
            )
        else:
            prompt = _safe_format(_STAGE3_TEMPLATE,
                query=query, file_type=file_type, stage2_output=stage2_output
            )

        try:
            result = self._llm.generate(
                prompt,
                route_context={
                    "input_type": "text",
                    "task_type": "qa",
                    "complexity": "standard",
                },
            )
            return result.text.strip(), result.model
        except LLMProviderError as exc:
            fallback = (
                f"[Stage3 失败] base_model 调用出错: {exc}\n\n"
                f"以下是附件分析结果：\n\n{stage2_output}"
            )
            return fallback, None

    # ── 输出辅助 ────────────────────────────────────────────────────

    @staticmethod
    def _maybe_write_output(
        result: PipelineResult, output_path: Optional[str]
    ) -> None:
        if not output_path:
            return

        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(result.stage3_output, encoding="utf-8")
