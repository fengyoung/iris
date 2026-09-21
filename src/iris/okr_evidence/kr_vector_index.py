"""KR 向量缓存，以周期、全文及模型身份判定失效。"""
import hashlib
import io
import json
import math
from iris.core.exceptions import IrisValueError
from iris.intelligence.storage import SnapshotStore
from iris.utils.shared import atomic_write_bytes


def cosine(left, right):
    if not left or len(left) != len(right) or any(not math.isfinite(x) for x in left + right):
        raise IrisValueError('向量维度或数值不合法')
    a = math.sqrt(sum(x*x for x in left))
    b = math.sqrt(sum(x*x for x in right))
    return sum(x*y for x, y in zip(left, right)) / (a*b) if a and b else 0.0


class KRVectorIndex(SnapshotStore):
    def build(self, cycle, krs, embedder):
        key = hashlib.sha256(json.dumps([cycle, krs, embedder.model], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cached = self.read().get('vectors', {})
        if cached.get('key') == key:
            return cached['values']
        ids = sorted(krs)
        vectors = embedder.embed([krs[k] for k in ids])
        if len(vectors) != len(ids):
            raise IrisValueError('KR 向量数量不完整')
        if vectors:
            for vector in vectors:
                cosine(vectors[0], vector)
        values = dict(zip(ids, vectors))
        self.change(lambda data: data.update(vectors={'key': key, 'values': values, 'model': embedder.model}))
        return values

    def _artifacts(self, target, data):
        import numpy as np
        values = data.get('vectors', {}).get('values', {})
        buffer = io.BytesIO()
        np.savez_compressed(buffer, ids=np.array(list(values)), vectors=np.array(list(values.values())))
        atomic_write_bytes(target / 'kr_vectors.npz', buffer.getvalue())
