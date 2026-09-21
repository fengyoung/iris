"""带版本、备份和原子发布的多文件快照。"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from uuid import uuid4

from iris.core.exceptions import IrisValueError
from iris.core.locks import FileLock
from iris.utils.shared import atomic_write_json, atomic_write_text


class SnapshotStore:
    """所有写入在稳定锁内发布；读者只会看到完整的一代。"""

    def __init__(self, root: Path):
        self.root = Path(root)

    def read(self) -> dict:
        pointer = self.root / 'CURRENT'
        if not pointer.exists():
            legacy = self.root / 'index.json'
            if legacy.exists():
                return self._validate(json.loads(legacy.read_text(encoding='utf-8')))
            return {'schema_version': 1, 'records': {}, 'candidates': {}, 'counters': {}}
        generation = pointer.read_text(encoding='utf-8').strip()
        if not re.fullmatch(r'[a-f0-9]{32}', generation):
            raise IrisValueError('无效的情报快照指针')
        path = self.root / 'generations' / generation / 'index.json'
        return self._validate(json.loads(path.read_text(encoding='utf-8')))

    @staticmethod
    def _validate(data: dict) -> dict:
        if not isinstance(data, dict) or data.get('schema_version', 0) not in (0, 1):
            raise IrisValueError('不支持的情报数据版本，拒绝读写')
        return data

    def change(self, mutate):
        with FileLock(self.root / 'state'):
            old = self.read()
            data = copy.deepcopy(old)
            if data.get('schema_version', 0) == 0:
                atomic_write_json(self.root / 'backups' / f'{uuid4().hex}.json', old)
                data = self.migrate_v0(data)
            result = mutate(data)
            self._publish(data)
            return result

    @staticmethod
    def migrate_v0(data: dict) -> dict:
        """旧列表/单来源格式升级；未知结构拒绝，防止静默丢失。"""
        records = data.get('records', data.get('decisions', {}))
        if isinstance(records, list):
            records = {d['decision_id']: d for d in records}
        if not isinstance(records, dict):
            raise IrisValueError('旧版 records 结构无效')
        for record in records.values():
            if 'source_doc' in record:
                record.setdefault('sources', []).append({'path': record.pop('source_doc'), 'title': '', 'quote': ''})
        data.update(schema_version=1, records=records)
        data.setdefault('candidates', {})
        data.setdefault('counters', {})
        return data

    def migrate(self) -> dict:
        self.change(lambda data: None)
        return self.read()

    def _publish(self, data: dict) -> None:
        generation = uuid4().hex
        target = self.root / 'generations' / generation
        atomic_write_json(target / 'index.json', data)
        for key, record in data.get('records', {}).items():
            # 外部标识绝不能成为路径。
            import hashlib
            filename = key if re.fullmatch(r'DEC-\d{8}-\d{4,}', key) else hashlib.sha256(key.encode()).hexdigest()
            atomic_write_json(target / f'{filename}.json', record)
        self._artifacts(target, data)
        atomic_write_text(self.root / 'CURRENT', generation)

    def _artifacts(self, target: Path, data: dict) -> None:
        """子类在发布指针前生成关联制品。"""
