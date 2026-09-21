"""Wiki 主题与两跳图谱上下文。"""
from pathlib import Path
import logging
from iris.core.exceptions import IrisError
from iris.intelligence.context import data_root

logger = logging.getLogger(__name__)
from iris.intelligence.context import read_document


class TopicResolver:
    def __init__(self, bundle):
        self.bundle = bundle

    def resolve(self, topic):
        from iris.wiki.searcher import WikiSearcher
        from iris.wiki.graph import WikiGraph
        hits = WikiSearcher(self.bundle).search(topic, top_k=5)
        rows = []
        root = Path(self.bundle.wiki['wiki_root'])
        for hit in hits:
            path = root / hit.relative_path
            if path.is_file():
                doc = read_document(root, path)
                if doc:
                    rows.append(doc.evidence(1200))
        semantic = self._semantic_pages(root, topic)
        rows = list({r['path']: r for r in semantic + rows}.values())[:5]
        graph = WikiGraph(self.bundle)
        related: list[str] = []
        if graph.load():
            for row in rows:
                related.extend(n.title for n in graph.neighbors(row['title'], hops=2))
        return {'pages': rows, 'related_entities': sorted(set(related))[:30]}

    def _semantic_pages(self, root, topic):
        """复用现有 Embedding 缓存；不可用时显式降级为 Wiki 词法检索。"""
        from iris.retrieval import build_embedder_from_config
        from iris.intelligence.context import documents
        from iris.okr_evidence.kr_vector_index import cosine
        embedder = build_embedder_from_config(self.bundle.llm, data_dir=data_root(self.bundle))
        if embedder is None:
            return []
        pages = documents(root)
        if not pages:
            return []
        try:
            vectors = embedder.embed([topic] + [p.title + '\n' + p.text[:1000] for p in pages])
            if len(vectors) != len(pages) + 1:
                raise IrisError('Wiki 向量响应不完整')
            scored = [(cosine(vectors[0], v), p) for p, v in zip(pages, vectors[1:])]
            return [p.evidence(1200) for score, p in sorted(scored, key=lambda pair: -pair[0])[:5] if score > 0.3]
        except (IrisError, OSError, ValueError) as exc:
            logger.warning('Wiki 语义解析失败，使用词法检索：%s', exc)
            return []
