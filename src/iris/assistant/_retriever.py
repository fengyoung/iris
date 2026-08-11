"""知识库检索适配：包装 EnhancedRetriever，失败优雅降级（无上下文继续会议）。"""

from __future__ import annotations

import logging
import time
from typing import List

from iris.retrieval import EnhancedRetriever, RetrievalHit

_logger = logging.getLogger(__name__)

# 检索总时间预算：超过即降级为空上下文（与 deep 校正 8s deadline 对齐，
# 保证线程池槽位有界返回，防止 provider 挂起占死池）
_RETRIEVER_DEADLINE_SEC = 8.0


class RetrieverAdapter:
    """每段校正文本 → 知识库检索（Wiki/文档/记忆），返回检索命中列表。

    降级策略：构造失败置 None（无知识库上下文继续）；查询失败返回 []；
    LLM 查询增强带 _deadline（全链路唯一无默认 deadline 的 LLM 调用）。
    """

    def __init__(self, bundle):
        try:
            self._retriever = EnhancedRetriever(bundle)
        except Exception as e:
            self._retriever = None
            _logger.warning("检索初始化失败，本场会议无知识库上下文: %s", e)

    def search(self, text: str, *, top_k: int = 5) -> List[RetrievalHit]:
        if self._retriever is None:
            return []
        try:
            return self._retriever.search(
                text,
                top_k=top_k,
                mode="local",
                _deadline=time.monotonic() + _RETRIEVER_DEADLINE_SEC,
            ).hits
        except Exception:
            return []

    @staticmethod
    def format_context(hits: List[RetrievalHit], max_chars: int = 1500) -> str:
        """命中列表 → 分析 Prompt 上下文块；截断到 max_chars。"""
        if not hits:
            return ""
        lines = []
        for hit in hits:
            source = " > ".join(
                [p for p in [hit.title] + list(hit.section_path or []) if p]
            ) or hit.relative_path
            preview = (hit.content_preview or "").strip().replace("\n", " ")
            lines.append(f"- [{source}] {preview}")
        block = "\n".join(lines)
        if len(block) > max_chars:
            block = block[:max_chars] + "…"
        return block
