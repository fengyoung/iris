"""问答数据结构。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Citation:
    relative_path: str
    section_path: List[str]
    line_start: int
    line_end: int


@dataclass(frozen=True)
class AnswerBlock:
    title: str
    summary: str
    citation: Citation
    score: float
    evidence_type: str = "general"
    tags: List[str] = field(default_factory=list)
    extracted_fields: Dict[str, List[str]] = field(default_factory=dict)
    explanation: str = ""


@dataclass(frozen=True)
class QAResponse:
    question: str
    answer: str
    retrieval_total_hits: int
    mode: str
    blocks: List[AnswerBlock]
    structured: Optional[Dict[str, Any]] = None
    llm: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = {"question": self.question, "answer": self.answer,
                   "retrieval_total_hits": self.retrieval_total_hits, "mode": self.mode,
                   "blocks": [{**asdict(block), "citation": asdict(block.citation)} for block in self.blocks]}
        if self.structured is not None:
            payload["structured"] = self.structured
        if self.llm is not None:
            payload["llm"] = self.llm
        return payload
