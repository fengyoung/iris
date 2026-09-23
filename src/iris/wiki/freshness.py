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
    # 公式：score = 1 - lag因子 * 活跃因子
    # lag因子 = min(1, lag/30)：滞后 30 天时达到最大惩罚
    # 活跃因子 = min(1, active文档数/3)：3篇以上活跃文档时惩罚达到满额
    # 两因子相乘后从 1 中减去：活跃度低或滞后少时得分高
    # 与设计文档的差异：设计文档采用乘法组合(1-lag/60)*activity，本实现
    # 采用减法组合以便独立控制两个维度的敏感度（lag 30天、activity 3篇）。
    return max(0.0, 1.0 - min(1.0, lag / 30) * min(1.0, len(active) / 3))


class WikiFreshnessChecker:
    def __init__(self, bundle, retriever=None):
        from iris.retrieval import EnhancedRetriever
        self.bundle = bundle
        self.retriever = retriever or EnhancedRetriever(bundle)

    def run(self, docs, *, today=None):
        today = today or local_today()
        root = Path(self.bundle.wiki['wiki_root'])
        by_path = {d.path: d for d in docs}
        rows = []
        for path in sorted(root.rglob('*.md')):
            page = read_document(root, path)
            if not page or path.name in {'index.md', 'changelog.md'}:
                continue
            # 预过滤：只对 30 天以上未更新的页面做检索，避免对最近更新的页面
            # 发起不必要的检索调用（225 页面全量检索会显著拖慢 daily-start）。
            fields, _ = parse_frontmatter(path.read_text(encoding='utf-8'))
            raw = fields.get('updated_at', fields.get('updated', ''))
            try:
                updated = date.fromisoformat(raw[:10]) if raw else None
            except ValueError:
                updated = None
            if updated is not None and (today - updated).days < 30:
                continue
            hits = self.retriever.search(page.title, top_k=10).hits
            related = [by_path[h.relative_path] for h in hits if h.relative_path in by_path]
            related = list({d.path: d for d in related}.values())
            score = compute_freshness_score(updated, related, today=today)
            if score < 0.5:
                rows.append({'page': page.title, 'path': page.path, 'score': score,
                             'sources': [d.path for d in related]})
        return sorted(rows, key=lambda r: (r['score'], r['path']))[:5]
