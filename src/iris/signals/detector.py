"""信号检测：每条信号带来源，证据缺失不冒充实际停滞。"""
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from enum import Enum
import re
from iris.intelligence.context import local_today
from iris.wiki._sensitive import is_sensitive_title


class SignalType(str, Enum):
    UNDOCUMENTED_TOPIC = 'UNDOCUMENTED_TOPIC'
    STALE_WIKI_ACTIVE = 'STALE_WIKI_ACTIVE'
    KR_EVIDENCE_GAP = 'KR_EVIDENCE_GAP'
    KR_AT_RISK = 'KR_AT_RISK'
    DECISION_REVIEW_DUE = 'DECISION_REVIEW_DUE'
    OPEN_DECISION_STALE = 'OPEN_DECISION_STALE'
    NEW_PERSON_NO_PAGE = 'NEW_PERSON_NO_PAGE'
    ORPHAN_CONCEPT = 'ORPHAN_CONCEPT'


@dataclass
class Signal:
    kind: str
    title: str
    evidence: list[str]
    action: str
    urgency: int = 1
    impact: int = 1

    def to_dict(self):
        return asdict(self)


class SignalDetector:
    def __init__(self, *, today=None):
        self.today = today or local_today()

    def decisions(self, records, docs):
        signals = []
        for row in records:
            if row['status'] != 'open':
                continue
            if row.get('review_at'):
                days = (date.fromisoformat(row['review_at']) - self.today).days
                if 0 <= days <= 7:
                    signals.append(Signal('DECISION_REVIEW_DUE', f"{row['title']}：{days} 天后复盘",
                        [row['decision_id'], row['review_at']], '安排决策复盘', 10 - days, 3))
            latest = max([date.fromisoformat(row['decided_at'])] +
                         [d.date for d in docs if row['title'] in d.text or row['decision_id'] in d.text])
            if (self.today - latest).days > 30:
                signals.append(Signal('OPEN_DECISION_STALE', row['title'] + ' 超过30天无后续证据',
                                      [row['decision_id'], latest.isoformat()], '核对执行状态', 4, 3))
        return signals

    def kr_signals(self, krs, evidence):
        signals = []
        cutoff = (self.today - timedelta(days=14)).isoformat()
        for key, text in krs.items():
            rows = evidence.get(key, [])
            recent = [r for r in rows if cutoff <= r['doc_date'] <= self.today.isoformat()]
            if not recent:
                signals.append(Signal('KR_EVIDENCE_GAP', f'{key}：14天内无新增支撑证据',
                                      [r['doc_path'] for r in rows[-1:]], '核对进展与文档覆盖；不代表实际停滞', 5, 3))
            risky = [r for r in recent if re.search('风险|阻塞|延期|受阻', r['evidence_snippet'])]
            if risky:
                signals.append(Signal('KR_AT_RISK', f'{key}：相关记录提及风险',
                                      [r['doc_path'] for r in risky[:3]], '核实风险是否仍然存在', 7, 4))
        return signals

    def undocumented(self, topics, docs):
        signals = []
        for topic, chats in topics.items():
            if len(set(chats)) < 2 or is_sensitive_title(topic):
                continue
            if not any(topic in d.title for d in docs):
                signals.append(Signal('UNDOCUMENTED_TOPIC', topic + '：多群讨论但标题中未找到文档',
                                      sorted(set(chats)), '核验是否需要补录讨论文档', 3, len(set(chats))))
        return signals

    def missing_entities(self, docs, page_titles):
        references: dict[str, set[str]] = {}
        for doc in docs:
            if not 0 <= (self.today - doc.date).days <= 14:
                continue
            for name in re.findall(r'\[\[((?:人物|概念)-[^\]|]+)(?:\|[^\]]+)?\]\]', doc.text):
                if name not in page_titles and not is_sensitive_title(name):
                    references.setdefault(name, set()).add(doc.path)
        signals = []
        for name, paths in references.items():
            if name.startswith('概念-') and len(paths) < 2:
                continue
            kind = 'NEW_PERSON_NO_PAGE' if name.startswith('人物-') else 'ORPHAN_CONCEPT'
            signals.append(Signal(kind, name + ' 尚无页面', sorted(paths), '核验实体并补建页面', 2, len(paths)))
        return signals
