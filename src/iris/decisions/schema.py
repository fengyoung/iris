"""决策实体与来源证据。"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import PurePosixPath
from iris.core.exceptions import IrisValueError
from iris.wiki._sensitive import is_sensitive_path, is_sensitive_title


def now() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass
class DecisionSource:
    path: str
    title: str = ''
    quote: str = ''
    line_start: int = 0
    field_evidence: dict = field(default_factory=dict)


@dataclass
class Decision:
    title: str
    outcome: str
    decided_at: str
    sources: list[dict] = field(default_factory=list)
    decision_id: str = ''
    summary: str = ''
    context: str = ''
    rationale: str = ''
    owners: list[str] = field(default_factory=list)
    stakeholders: list[str] = field(default_factory=list)
    review_at: str | None = None
    status: str = 'open'
    related_krs: list[str] = field(default_factory=list)
    related_decisions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    review_status: str = 'pending'
    review_details: dict = field(default_factory=dict)
    change_history: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def validate(self) -> None:
        if not isinstance(self.title, str) or not isinstance(self.outcome, str) or not self.title.strip() or not self.outcome.strip():
            raise IrisValueError('决策标题和结论不能为空')
        for key in ('summary', 'context', 'rationale'):
            if not isinstance(getattr(self, key), str):
                raise IrisValueError(f'{key} 必须是字符串')
        for key in ('owners', 'stakeholders', 'related_krs', 'related_decisions', 'tags'):
            values = getattr(self, key)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise IrisValueError(f'{key} 必须是字符串列表')
        if not isinstance(self.sources, list) or any(not isinstance(v, dict) for v in self.sources):
            raise IrisValueError('sources 必须是来源列表')
        if self.status not in {'open', 'implemented', 'revised', 'cancelled'}:
            raise IrisValueError('无效执行状态')
        if self.review_status not in {'pending', 'auto_approved', 'approved', 'rejected'}:
            raise IrisValueError('无效审核状态')
        try:
            date.fromisoformat(self.decided_at)
            if self.review_at:
                date.fromisoformat(self.review_at)
        except (ValueError, TypeError) as exc:
            raise IrisValueError('日期必须为 YYYY-MM-DD') from exc
        if is_sensitive_title(self.title):
            raise IrisValueError('拒绝敏感决策')
        for source in self.sources:
            path = source.get('path', '')
            if not isinstance(path, str) or '\\' in path:
                raise IrisValueError('来源路径必须使用正斜杠')
            if not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
                raise IrisValueError('来源必须为 SOURCE 相对路径')
            if is_sensitive_path(path) or is_sensitive_title(source.get('title', '')):
                raise IrisValueError('拒绝敏感来源')

    def to_dict(self) -> dict:
        self.validate()
        return asdict(self)
