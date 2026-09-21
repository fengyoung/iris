"""正式决策的图谱投影，不经 LLM 重新生成事实。"""
from iris.wiki._graph_engine import GraphNode, GraphEdge


def add_decisions(graph, records):
    # 清掉上一轮投影，避免撤回记录继续存在。
    removed = {key for key, node in graph._nodes.items() if node.page_type == 'decision'}
    graph._nodes = {k: n for k, n in graph._nodes.items() if k not in removed}
    graph._edges = [e for e in graph._edges if e.source_type != 'decision' and e.source not in removed and e.target not in removed]
    for record in records:
        key = record['decision_id']
        graph._nodes[key] = GraphNode(id=key, title=record['title'], page_type='decision',
                                      tags=record['tags'], summary=record['outcome'])
    for record in records:
        key = record['decision_id']
        for owner in record['owners']:
            target = next((k for k, n in graph._nodes.items() if n.page_type == 'person' and n.title.removeprefix('人物-') == owner.removeprefix('人物-')), None)
            if target:
                graph._edges.append(GraphEdge(source=target, target=key, relation='owns_decision', source_type='decision'))
        for target, node in graph._nodes.items():
            if node.page_type in {'project', 'concept', 'domain'} and node.title in record['tags']:
                graph._edges.append(GraphEdge(source=key, target=target, relation='relates_to', source_type='decision'))
        for target in record['related_decisions']:
            if target in graph._nodes:
                graph._edges.append(GraphEdge(source=key, target=target, relation='relates_to', source_type='decision'))
    graph._rebuild_adjacency()
    return len(records)
