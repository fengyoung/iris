"""Trello 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TrelloLabel:
    id: str
    name: str
    color: Optional[str] = None


@dataclass
class TrelloList:
    id: str
    name: str
    id_board: str


@dataclass
class TrelloBoard:
    id: str
    name: str
    url: str
    lists: List[TrelloList] = field(default_factory=list)
    labels: List[TrelloLabel] = field(default_factory=list)


@dataclass
class TrelloCard:
    id: str
    name: str
    desc: str = ""
    due: Optional[str] = None
    due_complete: bool = False
    closed: bool = False
    id_list: str = ""
    id_board: str = ""
    list_name: str = ""
    labels: List[TrelloLabel] = field(default_factory=list)
    url: str = ""

    @property
    def is_completed(self) -> bool:
        return self.closed or self.due_complete

    @property
    def category(self) -> Optional[str]:
        for label in self.labels:
            if label.color == "blue" or "工作" in (label.name or ""):
                return "work"
            if label.color == "green" or "生活" in (label.name or ""):
                return "life"
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "desc": self.desc,
            "due": self.due,
            "due_complete": self.due_complete,
            "closed": self.closed,
            "list_name": self.list_name,
            "labels": [{"name": lb.name, "color": lb.color} for lb in self.labels],
            "category": self.category,
            "url": self.url,
        }


@dataclass
class TrelloOverview:
    board_name: str
    total_incomplete: int
    by_list: Dict[str, int] = field(default_factory=dict)
    by_category: Dict[str, int] = field(default_factory=dict)
    today: List[TrelloCard] = field(default_factory=list)
    this_week: List[TrelloCard] = field(default_factory=list)
    overdue: List[TrelloCard] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "board_name": self.board_name,
            "total_incomplete": self.total_incomplete,
            "by_list": self.by_list,
            "by_category": self.by_category,
            "today_count": len(self.today),
            "this_week_count": len(self.this_week),
            "overdue_count": len(self.overdue),
            "today": [c.to_dict() for c in self.today],
            "this_week": [c.to_dict() for c in self.this_week],
            "overdue": [c.to_dict() for c in self.overdue],
        }
