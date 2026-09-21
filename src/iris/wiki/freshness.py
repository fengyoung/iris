"""文档流鲜度：仅惩罚活跃且尚未吸收的新证据。"""
from datetime import date
from pathlib import Path
from iris.intelligence.context import local_today, read_document
from iris.wiki.searcher import parse_frontmatter


def compute_freshness_score(updated_at, source_docs, *, today=None):
    today = today or local_today()
    active = [d for d in source_docs if 0 <= (today - d.date).days < 30]
    if not active:
        return 1.0
    latest = max(d.date for d in active)
    if updated_at is None:
        return 0.0
    lag = max(0, (latest - updated_at).days)
    # 活跃度越高，更新滞后影响越大；无活跃文档不会被误判。
    return max(0.0, 1.0 - min(1.0, lag / 30) * min(1.0, len(active) / 3))


class WikiFreshnessChecker:
    def __init__(self, bundle, retriever=None):
        from iris.retrieval import EnhancedRetriever
        self.bundle = bundle
        self.retriever = retriever or EnhancedRetriever(bundle)

    def run(self, docs, *, today=None):
        root = Path(self.bundle.wiki['wiki_root'])
        by_path = {d.path: d for d in docs}
        rows = []
        for path in sorted(root.rglob('*.md')):
            page = read_document(root, path)
            if not page or path.name in {'index.md', 'changelog.md'}:
                continue
            hits = self.retriever.search(page.title, top_k=10).hits
            related = [by_path[h.relative_path] for h in hits if h.relative_path in by_path]
            related = list({d.path: d for d in related}.values())
            fields, _ = parse_frontmatter(path.read_text(encoding='utf-8'))
            raw = fields.get('updated_at', fields.get('updated', ''))
            try:
                updated = date.fromisoformat(raw[:10]) if raw else None
            except ValueError:
                updated = None
            score = compute_freshness_score(updated, related, today=today)
            if score < 0.5:
                rows.append({'page': page.title, 'path': page.path, 'score': score,
                             'sources': [d.path for d in related]})
        return sorted(rows, key=lambda r: (r['score'], r['path']))[:5]
