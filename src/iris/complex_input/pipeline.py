"""双阶段处理流水线：adv_model 理解非文本 → base_model 生成最终输出。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from iris.complex_input.detector import ComplexityResult, EncodedImage, InputDetector
from iris.config.loader import ConfigBundle
from iris.llm import EnvironmentConfiguredLLMProvider, LLMProviderError, LLMRequest


STAGE1_PROMPT = """你是一个分析助手。请仔细查看提供的图片，用中文输出一份结构化的内容描述。

要求：
1. 如果图片包含文字，逐字提取所有文字内容
2. 如果图片包含流程图/架构图/表格，描述其结构和关键信息
3. 如果图片是截图/界面，描述界面元素和核心信息
4. 如果图片包含数据，整理出关键数据点
5. 输出格式为 Markdown，使用标题、列表等使结构清晰
6. 不要添加主观评论，只描述客观内容"""


@dataclass(frozen=True)
class PipelineResult:
    query: str
    is_complex: bool
    stage1_output: Optional[str]
    stage2_output: str
    stage1_model: Optional[str]
    stage2_model: str
    image_paths: List[str]
    detection_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ComplexInputPipeline:
    def __init__(self, config: ConfigBundle):
        self._config = config
        self._provider = EnvironmentConfiguredLLMProvider(config)
        self._detector = InputDetector()

    def process(self, query: str, *, image_paths: Optional[List[str]] = None,
                output_path: Optional[str] = None) -> PipelineResult:
        detection = self._detector.detect(query, image_paths=image_paths)

        if not detection.is_complex:
            stage2_text = self._text_only_answer(query)
            result = PipelineResult(query=query, is_complex=False, stage1_output=None, stage2_output=stage2_text,
                                    stage1_model=None, stage2_model=self._provider.get_active_model_config("base_model")["model"],
                                    image_paths=detection.image_paths, detection_reason=detection.reason)
        else:
            stage1_text = self._stage1_multimodal(query, detection.encoded_images)
            stage2_text = self._stage2_text(query, stage1_text, detection.encoded_images)
            result = PipelineResult(query=query, is_complex=True, stage1_output=stage1_text, stage2_output=stage2_text,
                                    stage1_model=self._provider.get_active_model_config("adv_model")["model"],
                                    stage2_model=self._provider.get_active_model_config("base_model")["model"],
                                    image_paths=detection.image_paths, detection_reason=detection.reason)

        if output_path:
            from pathlib import Path
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(result.stage2_output, encoding="utf-8")

        return result

    def _stage1_multimodal(self, query: str, images: List[EncodedImage]) -> str:
        image_parts = [{"type": "image_url", "image_url": {"url": img.data_url}} for img in images]
        content_parts = [{"type": "text", "text": f"用户指令：{query}\n\n{STAGE1_PROMPT}"}, *image_parts]
        try:
            text = self._provider.generate_multimodal(
                content_parts,
                route_context={"input_type": "multimodal", "task_type": "image_understanding",
                               "complexity": "complex", "use_case": "image_understanding"},
            )
            return text.strip()
        except LLMProviderError as exc:
            return f"[Stage1 失败] adv_model 调用出错: {exc}"

    def _stage2_text(self, query: str, stage1_output: str, images: List[EncodedImage]) -> str:
        image_note = f"（共 {len(images)} 张图片）" if images else ""
        prompt = f"""用户原始指令{image_note}：{query}

以下是已提取的图片内容描述，请基于此内容回答用户问题：

---
{stage1_output}
---

请用中文给出直接、有用的回答。如涉及数据或流程，优先用结构化方式呈现。"""
        try:
            response = self._provider.generate(
                LLMRequest(prompt=prompt, route_context={"input_type": "text", "task_type": "qa",
                                                          "complexity": "standard", "use_case": "qa"})
            )
            return response.text.strip()
        except LLMProviderError as exc:
            return f"[Stage2 失败] base_model 调用出错: {exc}\n\n以下是 Stage1 提取的中间内容：\n\n{stage1_output}"

    def _text_only_answer(self, query: str) -> str:
        try:
            response = self._provider.generate(
                LLMRequest(prompt=query, route_context={"input_type": "text", "task_type": "qa",
                                                         "complexity": "standard", "use_case": "qa"})
            )
            return response.text.strip()
        except LLMProviderError as exc:
            return f"base_model 调用出错: {exc}"
