"""稳定排序与去重。"""
def rank(signals, limit=5):
    unique = {(s.kind, s.title): s for s in signals}
    return sorted(unique.values(), key=lambda s: (-s.urgency, -s.impact, s.kind, s.title))[:limit]
