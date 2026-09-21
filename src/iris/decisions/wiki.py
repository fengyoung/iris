"""从正式决策直接导出 Wiki 页面，保留稳定编号。"""
import json
from iris.utils.shared import atomic_write_text
from .reporter import render_report


def export_pages(store, wiki_root):
    paths = []
    for row in store.list():
        path = wiki_root / '05-决策' / f"决策-{row['decision_id']}.md"
        frontmatter = '\n'.join(['---', 'type: decision',
            'title: ' + json.dumps(row['title'], ensure_ascii=False),
            'decision_id: ' + row['decision_id'], 'updated_at: ' + row['updated_at'],
            'status: ' + row['status'], '---', ''])
        atomic_write_text(path, frontmatter + render_report([row]))
        paths.append(str(path))
    return paths
