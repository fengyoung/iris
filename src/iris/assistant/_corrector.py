"""校正引擎：Aho-Corasick 词典快速校正 + LLM 深度校正。

完全独立于 iris.wiki.asr（零 import 依赖），自行实现 AC 自动机替换逻辑。
与 asr-corrector 校正逻辑一致但代码隔离——两套独立实例、独立配置、独立数据。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Dict, Optional

_logger = logging.getLogger(__name__)


class CorrectorAdapter:
    """ASR 校正适配器（完全独立于 iris.wiki.asr）。

    - fast(text)：Aho-Corasick 词典替换（毫秒级），结果立即入上下文窗口
    - deep(text)：LLM 深度校正（带内部 deadline 降级，失败返回原文）
    - push_context(text)：手动滚动近期上下文窗口
    """

    # 上下文窗口配置
    _CONTEXT_SIZE = 5          # 最多保留最近 N 句
    _CONTEXT_EXPIRE_SEC = 600  # 上下文过期时间（10 分钟）

    def __init__(
        self,
        replace_dict: Dict[str, str],
        *,
        llm_prompt: str = "",
        llm_timeout_ms: int = 8000,
    ):
        self._replace_dict = replace_dict
        self._llm_prompt = llm_prompt
        self._llm_timeout_ms = llm_timeout_ms
        self._llm: Optional[object] = None
        # 近期上下文窗口：（句子, 时间戳）——全局兜底
        self._recent: deque = deque(maxlen=self._CONTEXT_SIZE)
        # v3.25.5 per-speaker 上下文：{speaker_id: deque(maxlen=3)}
        # 生效路径：prefetch 阶段 speaker 尚未被 LLM 后验填充（为空 → 全局上下文）；
        # 分析后 speaker 回填，后续同 speaker 段的校正才走隔离上下文。
        # 是渐进增强而非全量隔离——首轮校正始终用全局上下文兜底。
        self._speaker_ctx: Dict[str, deque] = {}
        self._build_ac()

    # ── 公开接口 ──────────────────────────────────────────

    def set_llm_service(self, llm_service: object) -> None:
        """注入 LLM 服务（延迟绑定，支持 None 降级为仅词典模式）。"""
        self._llm = llm_service

    def fast(self, text: str) -> str:
        """词典快速校正（Aho-Corasick，毫秒级）。"""
        if not self._replace_dict or not text:
            return text
        return self._ac_replace(text)

    def deep(self, text: str, speaker_id: str = "") -> str:
        """LLM 深度校正；无 LLM 或无 Prompt 时降级返回 fast 原文。

        v3.25.5: speaker_id 用于隔离上下文——同说话人上下文优先。
        """
        if not self._llm or not self._llm_prompt:
            return text
        context = self._build_context(speaker_id=speaker_id)
        # 简单模板替换（兼容 asr-corrector 的 prompt 格式）
        prompt = (self._llm_prompt
                  .replace("{{context}}", context)
                  .replace("{{text}}", text))
        try:
            result = self._llm.generate(
                prompt,
                route_context={
                    "task_type": "asr_correction",
                    "input_type": "text",
                },
                temperature=0.1,
                max_tokens=2048,
                max_retries=0,
                extra_body={"thinking": {"type": "disabled"}},
                _deadline=time.monotonic() + self._llm_timeout_ms / 1000,
            )
            corrected = (result.text or "").strip()
            return corrected if corrected and self._is_similar(text, corrected) else text
        except Exception as e:
            _logger.warning("LLM 深度校正异常，保留原文: %s", e)
            return text

    def push_context(self, text: str, speaker_id: str = "") -> None:
        """将校正后文本推入近期上下文窗口。

        v3.25.5: 若提供 speaker_id，同时写入 per-speaker 上下文（maxlen=3），
        后续同说话人校正时优先使用隔离上下文，避免混入他人文本。
        """
        self._recent.append((text, time.monotonic()))
        if speaker_id:
            ctx = self._speaker_ctx.setdefault(speaker_id,
                                               deque(maxlen=self._CONTEXT_SIZE))
            ctx.append((text, time.monotonic()))

    # ── Aho-Corasick ──────────────────────────────────────

    def _build_ac(self) -> None:
        """从替换词典构建 Aho-Corasick 自动机。

        使用 pyahocorasick（已存在于 Iris 依赖中）。
        """
        import ahocorasick
        self._automaton = ahocorasick.Automaton()
        for key, value in self._replace_dict.items():
            if key and value and key != value:  # 跳过无效和恒等映射
                self._automaton.add_word(key, (key, value))
        self._automaton.make_automaton()

    def _ac_replace(self, text: str) -> str:
        """Aho-Corasick 替换：按匹配位置从后往前替换，避免偏移问题。"""
        try:
            matches = list(self._automaton.iter(text))
        except Exception:
            return text  # 自动机损坏时降级
        if not matches:
            return text
        # 按结束位置降序排列 → 从后往前替换
        matches.sort(key=lambda m: m[0], reverse=True)
        chars = list(text)
        for end_idx, (key, value) in matches:
            start = end_idx - len(key) + 1
            if start >= 0:
                chars[start:end_idx + 1] = list(value)
        return "".join(chars)

    # ── 上下文 ────────────────────────────────────────────

    def _build_context(self, speaker_id: str = "") -> str:
        """构建注入 Prompt 的近期上下文文本块。

        双重过滤：deque maxlen（数量）+ 时间过期（防止长时间暂停后旧语境残留）。
        v3.25.5: 优先同说话人上下文（隔离噪音），无 speaker_id 时回退全局。
        """
        now = time.monotonic()
        source = self._speaker_ctx.get(speaker_id) if speaker_id else None
        ctx_deque = source if source else self._recent
        valid = [
            text for text, ts in tuple(ctx_deque)
            if now - ts <= self._CONTEXT_EXPIRE_SEC
        ]
        if not valid:
            return ""
        return "\n".join(f"- {s}" for s in valid)

    # ── 辅助 ──────────────────────────────────────────────

    @staticmethod
    def _is_similar(a: str, b: str, threshold: Optional[float] = None) -> bool:
        """检查两个字符串是否相似（ratio ≥ threshold）。防止 LLM 幻觉完全改写。

        threshold=None 时自适应选择：短文本（< 20 字符）0.5，长文本 0.35。
        长文本降低阈值是因为 LLM 修正时可能调整语序/增加连接词，
        SequenceMatcher 对长文本的编辑距离比短文本敏感。
        """
        from difflib import SequenceMatcher
        if threshold is None:
            threshold = 0.5 if len(a) < 20 else 0.35
        return SequenceMatcher(None, a, b).ratio() >= threshold
