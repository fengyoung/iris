"""与会人页面及近期证据；主题命中不能冒充人员命中。"""
from pathlib import Path
from iris.intelligence.context import read_document
from .doc_retriever import RecentDocsRetriever


class PersonRetriever:
    def __init__(self, bundle, retriever=None):
        self.bundle = bundle
        self.docs = RecentDocsRetriever(bundle, retriever)

    def retrieve(self, participants, topic, today=None):
        root = Path(self.bundle.wiki['wiki_root']) if self.bundle.wiki else None
        result = {}
        for name in participants:
            name = name.strip()
            if not name:
                continue
            pages = []
            if root:
                for path in (root / '04-人物').rglob('*.md'):
                    if path.stem != '人物-' + name:
                        continue
                    doc = read_document(root, path)
                    if doc:
                        pages.append(doc.evidence(1200))
            recent = self.docs.retrieve(f'{name} {topic}', days=14, today=today, limit=15)
            recent = [r for r in recent if name in r['title'] or name in r['snippet']][:5]
            result[name] = {'person_pages': pages, 'recent_documents': recent,
                            'ambiguous': len(pages) > 1}
        return result
