"""复用混合检索，按文档日期排序并核验当前来源。"""
from datetime import timedelta
from iris.intelligence.context import local_today, read_document, source_root


class RecentDocsRetriever:
    def __init__(self, bundle, retriever=None):
        self.root = source_root(bundle)
        if retriever is None:
            from iris.retrieval import EnhancedRetriever
            retriever = EnhancedRetriever(bundle)
        self.retriever = retriever

    def retrieve(self, topic, *, days=45, today=None, limit=30):
        today = today or local_today()
        cutoff = today - timedelta(days=days)
        hits = self.retriever.search(topic, top_k=100).hits
        rows: dict[str, dict] = {}
        for hit in hits:
            path = self.root / hit.relative_path
            if not path.is_file():
                continue
            doc = read_document(self.root, path)
            if doc is None or not cutoff <= doc.date <= today:
                continue
            # 引用必须仍然存在于当前源，而非过期索引快照。
            snippet = hit.content_preview
            if not snippet or snippet not in doc.text:
                continue
            entry = doc.evidence()
            entry.update(snippet=snippet[:1800], score=hit.score)
            old = rows.get(doc.path)
            if old is None or hit.score > old['score']:
                rows[doc.path] = entry
        return sorted(rows.values(), key=lambda r: (r['date'], r['score']), reverse=True)[:limit]
