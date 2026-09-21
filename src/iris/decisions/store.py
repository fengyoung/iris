"""正式决策与候选隔离存储，锁内编号和多来源去重。"""
from __future__ import annotations
import re
import builtins
from datetime import datetime
from uuid import uuid4
from iris.core.exceptions import IrisValueError
from iris.intelligence.storage import SnapshotStore
from .schema import Decision, now


def identity(record: dict) -> str:
    return re.sub(r'\s+', '', record['outcome']).casefold() + '|' + record['decided_at']


class DecisionStore(SnapshotStore):
    def list(self, *, status='', owner='', kr='', since='', query='') -> list[dict]:
        rows = []
        for row in self.read().get('records', {}).values():
            try:
                Decision(**row).validate()
            except (IrisValueError, TypeError):
                continue  # 词表更新后的敏感记录不进入任何下游。
            if row.get('review_status') not in {'approved', 'auto_approved'}:
                continue
            if status and row['status'] != status:
                continue
            if owner and owner not in row['owners']:
                continue
            if kr and not any(k == kr or k.startswith(kr + '.') for k in row['related_krs']):
                continue
            if since and row['decided_at'] < since:
                continue
            if query and query.casefold() not in ' '.join([row['title'], row['outcome'], row['context'], *row['tags']]).casefold():
                continue
            rows.append(row)
        return sorted(rows, key=lambda r: (r['decided_at'], r['decision_id']), reverse=True)

    def get(self, decision_id: str) -> dict:
        for row in self.list():
            if row['decision_id'] == decision_id:
                return row
        raise IrisValueError('决策不存在或不可见')

    def save(self, decision: Decision) -> dict:
        record = decision.to_dict()
        if decision.review_status not in {'approved', 'auto_approved'}:
            raise IrisValueError('未通过审核不能写入正式库')
        return self.change(lambda data: self._save(data, record))

    def _save(self, data: dict, record: dict) -> dict:
        for existing in data['records'].values():
            if identity(existing) == identity(record):
                for source in record['sources']:
                    if source not in existing['sources']:
                        existing['sources'].append(source)
                existing['updated_at'] = now()
                existing['change_history'].append({'at': now(), 'action': 'merge_sources'})
                return existing
        day = datetime.now().astimezone().strftime('%Y%m%d')
        prefix = f'DEC-{day}-'
        highest = max((int(k[len(prefix):]) for k in data['records'] if k.startswith(prefix)), default=0)
        seq = max(data['counters'].get(day, 0), highest) + 1
        data['counters'][day] = seq
        record['decision_id'] = f'{prefix}{seq:04d}'
        data['records'][record['decision_id']] = record
        return record

    def stage(self, decision: Decision, reason: str) -> dict:
        record = decision.to_dict()
        record['review_status'] = 'pending'
        record['review_details']['reason'] = reason
        def mutate(data):
            for candidate in data['candidates'].values():
                if identity(candidate['decision']) == identity(record) and candidate['status'] == 'pending':
                    for source in record['sources']:
                        if source not in candidate['decision']['sources']:
                            candidate['decision']['sources'].append(source)
                    return candidate
            cid = 'CAN-' + uuid4().hex
            candidate = {'candidate_id': cid, 'status': 'pending', 'decision': record}
            data['candidates'][cid] = candidate
            return candidate
        return self.change(mutate)

    def pending(self) -> builtins.list[dict]:
        rows: builtins.list[dict] = []
        for row in self.read().get('candidates', {}).values():
            try:
                Decision(**row['decision']).validate()
            except (IrisValueError, TypeError):
                continue
            if row['status'] == 'pending':
                rows.append(row)
        return rows

    def review(self, candidate_id: str, *, approve: bool, reason='', patch=None) -> dict:
        def mutate(data):
            candidate = data['candidates'].get(candidate_id)
            if not candidate or candidate['status'] != 'pending':
                raise IrisValueError('候选不存在或已审核')
            record = candidate['decision']
            if patch:
                allowed = {'title', 'outcome', 'decided_at', 'owners', 'review_at', 'related_krs', 'context', 'rationale'}
                if set(patch) - allowed:
                    raise IrisValueError('包含不可修改的候选字段')
                record.update(patch)
            Decision(**record).validate()
            candidate['status'] = record['review_status'] = 'approved' if approve else 'rejected'
            record['review_details'].update(human_reason=reason, reviewed_at=now())
            if approve:
                result = self._save(data, record)
                candidate['decision_id'] = result['decision_id']
                return result
            return candidate
        return self.change(mutate)

    def update(self, decision_id: str, *, status: str) -> dict:
        self.get(decision_id)
        def mutate(data):
            record = data['records'][decision_id]
            old = record['status']
            record['status'] = status
            Decision(**record).validate()
            record['updated_at'] = now()
            record['change_history'].append({'at': now(), 'from': old, 'to': status})
            return record
        return self.change(mutate)
