"""正式决策检索投影：保留 SOURCE 引用，不暴露候选。"""
from iris.intelligence.context import data_root, source_root, read_document
from iris.retrieval.searcher import RetrievalHit
from iris.utils.tokenization import tokenize
from .store import DecisionStore


def decision_hits(bundle, query, limit=5):
    terms = set(tokenize(query))
    rows = DecisionStore(data_root(bundle) / 'decisions').list()
    if not rows:
        return []
    root = source_root(bundle)
    hits = []
    for row in rows:
        text = ' '.join([row['title'], row['outcome'], row['context'], *row['tags']])
        matched = terms.intersection(tokenize(text))
        if not matched or not row['sources']:
            continue
        source = row['sources'][0]
        quote = source.get('quote', '')
        path = root / source['path']
        doc = read_document(root, path) if path.is_file() else None
        if not quote or doc is None or quote not in doc.text:
            continue
        line = source.get('line_start', 1)
        hits.append(RetrievalHit(chunk_id=row['decision_id'], score=float(len(matched)), title=row['title'],
            relative_path=source['path'], section_path=['决策', row['decision_id']],
            content_preview=f"决策 {row['decision_id']}（{row['status']}）：{row['outcome']}\n原文：{quote}",
            line_start=line, line_end=line + quote.count('\n'), structural_tags=['decision'],
            explanation='正式审核决策库', matched_terms=sorted(matched)))
    return sorted(hits, key=lambda h: (-h.score, h.chunk_id))[:limit]
