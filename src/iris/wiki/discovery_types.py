"""Wiki 候选发现共享类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


# 4 种页面类型
PAGE_TYPES = ("domain", "concept", "project", "person")

# 页面类型→中文名映射
PAGE_TYPE_NAMES = {
    "domain": "领域",
    "concept": "概念",
    "project": "项目",
    "person": "人物",
}


@dataclass(frozen=True)
class CandidateItem:
    title: str
    page_type: str
    query: str
    score: int
    evidence_count: int
    sample_paths: List[str]
    rationale: str = ""
    has_wiki: bool = False
    wiki_stale: bool = False
    wiki_path: str = ""

    def to_batch_json(self) -> Dict[str, str]:
        return {"query": self.query, "title": self.title, "page_type": self.page_type}

    def to_review_record(self) -> Dict[str, object]:
        return {"selected": True, "query": self.query, "title": self.title,
                "page_type": self.page_type, "score": self.score,
                "evidence_count": self.evidence_count, "sample_paths": self.sample_paths,
                "rationale": self.rationale, "has_wiki": self.has_wiki,
                "wiki_stale": self.wiki_stale, "notes": ""}
