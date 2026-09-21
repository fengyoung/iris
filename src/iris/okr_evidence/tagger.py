"""全文指纹增量打标，更新替换旧证据，失败不推进游标。"""
import hashlib
import json
from iris.core.exceptions import IrisValueError
from iris.decisions.schema import now
from iris.intelligence.context import documents, read_document
from iris.taskpanel.budget import TaskBudget
from .kr_vector_index import cosine


class OKREvidenceTagger:
    def __init__(self, store, vectors, embedder, *, threshold=0.65, max_documents=100):
        if not 0 <= threshold <= 1:
            raise IrisValueError('证据阈值必须在 0–1 之间')
        self.store, self.vectors, self.embedder = store, vectors, embedder
        self.threshold, self.max_documents = threshold, max_documents

    def run_incremental(self, root, cycle, krs):
        budget = TaskBudget(max_calls=self.max_documents + 1, max_seconds=600)
        budget.check()
        vectors = self.vectors.build(cycle, krs, self.embedder)
        budget.record()
        signature = hashlib.sha256(json.dumps([krs, self.embedder.model, self.threshold], sort_keys=True).encode()).hexdigest()
        snapshot = self.store.read()
        prior = snapshot.get('documents', {}).get(cycle, {})
        same = snapshot.get('signatures', {}).get(cycle) == signature
        docs = documents(root)
        current = {d.path: d for d in docs}
        changed = [d for d in docs if not same or prior.get(d.path) != d.fingerprint]
        changed = sorted(changed, key=lambda d: (d.date, d.path), reverse=True)[:self.max_documents]
        updates = {}
        for doc in changed:
            budget.check()
            snippets = [doc.text[n:n+1800] for n in range(0, len(doc.text), 1800)]
            if len(snippets) > 100:
                continue  # 大文档不截断记成功，下次仍保留为待处理。
            embeddings = self.embedder.embed(snippets)
            budget.record()
            if len(embeddings) != len(snippets):
                raise IrisValueError('文档向量数量不完整')
            matches = {}
            for kr_id, vector in vectors.items():
                scored = [(cosine(vector, emb), snippet) for emb, snippet in zip(embeddings, snippets)]
                best = max(scored, default=(0.0, ''))
                if best[0] > self.threshold:
                    matches[kr_id] = {'cycle_id': cycle, 'kr_id': kr_id, 'kr_text': krs[kr_id],
                        'doc_path': doc.path, 'doc_date': doc.date.isoformat(), 'document_hash': doc.fingerprint,
                        'relevance_score': best[0], 'evidence_snippet': best[1], 'tagged_at': now()}
            updates[doc.path] = (doc, matches)
        def mutate(data):
            # 禁止旧批次覆盖另一个批次已经发布的证据。
            if data.get('documents', {}).get(cycle, {}) != prior or data.get('signatures', {}).get(cycle) != snapshot.get('signatures', {}).get(cycle):
                raise IrisValueError('证据快照已变化，请重试增量打标')
            valid = {}
            for path, (doc, matches) in updates.items():
                source = root / path
                latest = read_document(root, source) if source.exists() else None
                if latest and latest.fingerprint == doc.fingerprint:
                    valid[path] = (doc, matches)
            for key in list(data['records']):
                if key.startswith(cycle + ':'):
                    if key[len(cycle)+1:] not in krs or not same:
                        del data['records'][key]
                        continue
                    data['records'][key]['evidence'] = [r for r in data['records'][key]['evidence']
                        if r['doc_path'] in current and r['doc_path'] not in valid
                        and r.get('document_hash') == current[r['doc_path']].fingerprint]
            tracked = {p: h for p, h in prior.items() if same and p in current and current[p].fingerprint == h}
            for path, (doc, matches) in valid.items():
                tracked[path] = doc.fingerprint
                for kr_id, record in matches.items():
                    row = data['records'].setdefault(cycle + ':' + kr_id, {'evidence': []})
                    row['evidence'].append(record)
            data.setdefault('documents', {})[cycle] = tracked
            data.setdefault('signatures', {})[cycle] = signature
            data['last_updated'] = now()
            return {'processed': len(valid), 'remaining': len(docs) - len(tracked), 'cycle': cycle,
                    'budget': budget.snapshot()}
        return self.store.change(mutate)
