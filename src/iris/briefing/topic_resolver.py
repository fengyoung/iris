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
        """复用已有 Wiki 检索器的语义检索结果，避免对所有 Wiki 页面重新 embed。
        原实现直接调用 embedder.embed 对所有页面全量编码，绕过了检索缓存层且
        每次调用成本高（225 页 × 每次 briefing）；改为复用 WikiSearcher 的
        现有检索路径，保持语义搜索能力同时消除重复的 embedding 开销。
        """
        try:
            from iris.wiki.searcher import WikiSearcher
            hits = WikiSearcher(self.bundle).search(topic, top_k=10)
            rows = []
            for hit in hits:
                path = root / hit.relative_path
                if path.is_file():
                    doc = read_document(root, path)
                    if doc:
                        rows.append(doc.evidence(1200))
            return rows
        except Exception as exc:
            logger.warning('Wiki 语义解析失败，使用词法检索：%s', exc)
            return []
