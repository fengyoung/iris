"""问答 prompt 的上下文压缩。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from iris.config.loader import ConfigBundle
from iris.utils.tokenization import estimate_tokens

from .models import AnswerBlock


@dataclass(frozen=True)
class PackedPromptContext:
    wiki_hits: List[Dict[str, str]]
    blocks: List[AnswerBlock]
    metadata: Dict[str, int | bool]


class PromptContextPacker:
    def __init__(self, config: ConfigBundle):
        qa_config = config.app["qa"]
        self._max_prompt_context_chars = qa_config["max_prompt_context_chars"]
        self._max_evidence_blocks = qa_config["max_evidence_blocks"]
        self._max_wiki_hits = qa_config["max_wiki_hits"]
        self._max_block_summary_chars = qa_config["max_block_summary_chars"]
        self._max_wiki_summary_chars = qa_config["max_wiki_summary_chars"]

    def pack(self, blocks: List[AnswerBlock], wiki_hits: List[Dict[str, str]]) -> PackedPromptContext:
        remaining = self._max_prompt_context_chars
        selected_wiki = []
        selected_blocks = []
        wiki_truncated = False
        block_truncated = False

        for hit in wiki_hits[:self._max_wiki_hits]:
            packed_hit = {**hit, "summary": _compress_text(str(hit.get("summary", "")), self._max_wiki_summary_chars)}
            cost = estimate_tokens(packed_hit.get("title", "") + packed_hit.get("relative_path", "") + packed_hit["summary"]) + 24
            if selected_wiki and cost > remaining:
                wiki_truncated = True
                break
            if not selected_wiki and cost > remaining:
                packed_hit["summary"] = _compress_text(packed_hit["summary"], max(80, remaining // 2))
                cost = estimate_tokens(packed_hit.get("title", "") + packed_hit.get("relative_path", "") + packed_hit["summary"]) + 24
            if cost > remaining:
                wiki_truncated = True
                continue
            selected_wiki.append(packed_hit)
            remaining -= cost

        for block in blocks[:self._max_evidence_blocks]:
            packed_block = AnswerBlock(title=block.title, summary=_compress_text(block.summary, self._max_block_summary_chars),
                                       citation=block.citation, score=block.score)
            section = " > ".join(packed_block.citation.section_path) if packed_block.citation.section_path else packed_block.title
            cost = estimate_tokens(packed_block.title + section + packed_block.summary + packed_block.citation.relative_path) + 32
            if selected_blocks and cost > remaining:
                block_truncated = True
                break
            if not selected_blocks and cost > remaining:
                packed_block = AnswerBlock(title=packed_block.title, summary=_compress_text(packed_block.summary, max(120, remaining // 2)),
                                           citation=packed_block.citation, score=packed_block.score)
                cost = estimate_tokens(packed_block.title + section + packed_block.summary + packed_block.citation.relative_path) + 32
            if cost > remaining:
                block_truncated = True
                continue
            selected_blocks.append(packed_block)
            remaining -= cost

        return PackedPromptContext(wiki_hits=selected_wiki, blocks=selected_blocks,
                                   metadata={"budget_chars": self._max_prompt_context_chars,
                                             "used_chars": self._max_prompt_context_chars - remaining,
                                             "original_wiki_hits": len(wiki_hits),
                                             "selected_wiki_hits": len(selected_wiki),
                                             "original_blocks": len(blocks),
                                             "selected_blocks": len(selected_blocks),
                                             "wiki_truncated": wiki_truncated or len(wiki_hits) > len(selected_wiki),
                                             "block_truncated": block_truncated or len(blocks) > len(selected_blocks)})


def _compress_text(text: str, max_chars: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return normalized
    if max_chars <= 1:
        return normalized[:max_chars]
    return normalized[:max_chars - 1].rstrip() + "…"
