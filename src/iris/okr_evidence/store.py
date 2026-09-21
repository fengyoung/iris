"""证据快照与 JSONL 投影，索引和日志一代发布。"""
import hashlib
import json
from iris.intelligence.storage import SnapshotStore
from iris.utils.shared import atomic_write_text


class OKREvidenceStore(SnapshotStore):
    def get(self, cycle, kr_id, *, since=''):
        row = self.read().get('records', {}).get(cycle + ':' + kr_id, {})
        return [r for r in row.get('evidence', []) if not since or r['doc_date'] >= since]

    def _artifacts(self, target, data):
        for key, row in data.get('records', {}).items():
            filename = hashlib.sha256(key.encode()).hexdigest() + '.jsonl'
            atomic_write_text(target / filename, ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in row['evidence']))
