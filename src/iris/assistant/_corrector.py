"""校正适配：包装 AsrCorrector 双通道（词典快速 + LLM 深度），供会议助理一次性调用。"""

from __future__ import annotations

from typing import Dict

from iris.wiki.asr import AsrCorrector


class CorrectorAdapter:
    """包装 AsrCorrector：fast 同步毫秒级；deep 带内部 deadline 自动降级。

    - fast(text)：替换词典校正（Aho-Corasick，毫秒级），结果立即入上下文窗口
    - deep(fast_text)：LLM 深度校正（correct_full，内部 8s deadline，失败降级返回原文）
    - push_context(text)：手动滚动本场会议语境（correct_full 一次性调用不更新上下文）
    """

    def __init__(
        self,
        replace_dict: Dict[str, str],
        llm_prompt: str = "",
        *,
        llm_timeout_ms: int = 8000,
    ):
        self._corrector = AsrCorrector(
            replace_dict=replace_dict,
            llm_prompt=llm_prompt,
            mode="full",
            llm_timeout_ms=llm_timeout_ms,
        )

    def set_llm_service(self, llm_service: object) -> None:
        self._corrector.set_llm_service(llm_service)

    def fast(self, text: str) -> str:
        """词典快速校正，返回校正后文本。"""
        return self._corrector.correct_fast(text)[0]

    def deep(self, fast_text: str) -> str:
        """LLM 深度校正；内部 deadline 降级链保证失败返回 fast 原文。"""
        return self._corrector.correct_full(fast_text)[0]

    def push_context(self, text: str) -> None:
        """将校正后文本推入近期上下文滚动窗口（本场会议语境）。"""
        self._corrector.push_context(text)
