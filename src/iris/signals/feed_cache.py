"""将 feed 检测结果保存为可供每日信号读取的最小缓存。"""
from iris.intelligence.context import local_today
from iris.intelligence.storage import SnapshotStore
from iris.wiki._sensitive import is_sensitive_title


def cache_topics(root, topics):
    store = SnapshotStore(root / 'feed' / 'topics')
    def mutate(data):
        for topic in topics:
            if is_sensitive_title(topic.title):
                continue
            data['records'][topic.topic_id] = {'title': topic.title, 'date': local_today().isoformat(),
                                               'chats': [chat.name for chat in topic.source_chats]}
    store.change(mutate)


def recent_topics(root, today):
    from datetime import date
    rows = SnapshotStore(root / 'feed' / 'topics').read()['records']
    result: dict[str, list[str]] = {}
    for row in rows.values():
        if 0 <= (today - date.fromisoformat(row['date'])).days <= 7 and not is_sensitive_title(row['title']):
            result.setdefault(row['title'], []).extend(row['chats'])
    return result
