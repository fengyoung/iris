"""数据管线 + 搜索问答 + 知识图谱 命令处理器。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

from iris.llm.router import ModelRouter
from iris.app.cli.helpers import (
    _parse_context, _parse_image_list,
    _scan_payload, _chunk_payload,
    _emit_output, _print_config_summary,
)


# ── 命令处理器 ──────────────────────────────────────────────


def handle_check_config(args, bundle, logger) -> int:
    _print_config_summary(bundle)
    return 0


def handle_route_model(args, bundle, logger) -> int:
    context = _parse_context(args.context)
    router = ModelRouter(bundle)
    decision = router.route(context)
    payload = {"selected_role": decision.selected_role,
               "fallback_role": decision.fallback_role,
               "matched_rule": decision.matched_rule}
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


# ── 数据源层 ──────────────────────────────────────────────


def handle_scan_source(args, bundle, logger) -> int:
    from iris.ingest import MarkdownScanner
    scanner = MarkdownScanner(bundle)
    summaries = [scanner.scan_source_by_name(args.source)] if args.source else scanner.scan_all_enabled_sources()
    payloads = []
    for summary in summaries:
        p = _scan_payload(summary, summary_only=args.summary_only)
        if args.write_summary:
            p["summary_path"] = str(scanner.write_summary(summary))
        payloads.append(p)
        logger.log("scan_source", {"source_name": summary.source_name,
                                    "document_count": summary.document_count,
                                    "scanned_at": summary.scanned_at})
    _emit_output(args.command, {"sources": payloads}, pretty=args.pretty)
    return 0


def handle_build_chunks(args, bundle, logger) -> int:
    from iris.ingest import MarkdownChunker
    incremental = getattr(args, "incremental", False)
    chunker = MarkdownChunker(bundle)
    if args.source:
        summaries = [chunker.build_source_chunks(args.source, incremental=incremental)]
    else:
        summaries = chunker.build_all_enabled_sources_chunks(incremental=incremental)
    payloads = []
    for summary in summaries:
        p = _chunk_payload(summary, summary_only=args.summary_only)
        if args.write_summary:
            p["summary_path"] = str(chunker.write_summary(summary))
        payloads.append(p)
        stats = summary.build_stats
        logger.log("build_chunks", {"source_name": summary.source_name,
                                     "chunk_count": summary.chunk_count,
                                     "build_stats": {"reused_documents": stats.get("reused_documents", 0),
                                                     "rebuilt_documents": stats.get("rebuilt_documents", 0),
                                                     "cleaned_documents": stats.get("cleaned_documents", 0)}})
    _emit_output(args.command, {"sources": payloads}, pretty=args.pretty)
    return 0


# ── 搜索问答 ────────────────────────────────────────────


def handle_search(args, bundle, logger) -> int:
    from iris.retrieval import EnhancedRetriever
    retriever = EnhancedRetriever(bundle)
    result = retriever.search(args.query, top_k=args.top_k, mode=args.mode)
    _emit_output(args.command, result.to_dict(), pretty=args.pretty)
    return 0


def handle_ask(args, bundle, logger) -> int:
    from iris.complex_input import ComplexInputPipeline
    image_paths = _parse_image_list(args.image)
    if not image_paths and args.query:
        # 尝试从 query 文本中自动提取文件路径
        from iris.complex_input.detector import extract_file_paths_from_text
        image_paths = extract_file_paths_from_text(args.query)
    if image_paths:
        pipeline = ComplexInputPipeline(bundle)
        result = pipeline.process(args.query, file_paths=image_paths)
        _emit_output(args.command, result.to_dict(), pretty=args.pretty)
        return 0
    from iris.qa import QAService
    service = QAService(bundle)
    response = service.ask(args.query, top_k=args.top_k, mode=args.mode)
    _emit_output(args.command, response.to_dict(), pretty=args.pretty)
    return 0


# ── 向量索引 ────────────────────────────────────────────


def handle_build_vector_index(args, bundle, logger) -> int:
    from iris.retrieval.embedder import EmbedderError, build_embedder_from_config
    from iris.retrieval.vector_index import VectorIndex, build_vector_index

    emb_cfg = bundle.llm.get("embedding", {})
    if not emb_cfg.get("enabled", False):
        _emit_output(args.command, {"error": "向量检索未启用，请在 llm.json 的 embedding 段设置 enabled=true"}, pretty=args.pretty)
        return 1
    embedder = build_embedder_from_config(bundle.llm)
    if embedder is None:
        _emit_output(args.command, {"error": "embedding 配置不完整"}, pretty=args.pretty)
        return 1

    from iris.ingest import iter_chunk_items
    from iris.ingest.chunker import ChunkRecord
    metadata_root = bundle.root / "data" / "metadata"
    sources = bundle.data_source.get("sources", {})
    target_sources = {args.source: sources[args.source]} if args.source and args.source in sources else sources
    results = []
    for source_name in target_sources:
        summary_path = metadata_root / f"{source_name}_chunk_summary.json"
        if not summary_path.exists():
            results.append({"source": source_name, "status": "skipped", "reason": "chunk_summary 不存在"})
            continue
        chunks = [ChunkRecord(**item) for item in iter_chunk_items(metadata_root, {source_name: sources.get(source_name, {})})]
        index_path = metadata_root / f"{source_name}_vector_index"
        existing = VectorIndex(index_path)
        existing.load()
        try:
            idx = build_vector_index(source_name, chunks, embedder, index_path, existing_index=existing)
            results.append({"source": source_name, "status": "ok", "indexed": idx.size()})
        except EmbedderError as exc:
            results.append({"source": source_name, "status": "error", "reason": str(exc)})
    _emit_output(args.command, {"results": results}, pretty=args.pretty)
    return 0


# ── 知识图谱 ────────────────────────────────────────────


def handle_build_graph(args, bundle, logger) -> int:
    """构建/更新知识图谱。"""
    from iris.wiki import WikiGraph

    if not bundle.wiki or not bundle.wiki.get("wiki_root"):
        _emit_output("build-graph", {"error": "Wiki 配置缺失"}, pretty=args.pretty)
        return 1

    full_llm = getattr(args, "full", False)
    page_title = getattr(args, "page", "") or None

    graph = WikiGraph(bundle)

    # 尝试加载已有图谱
    graph.load()

    report = graph.refresh(full_llm=full_llm, page_title=page_title)

    if args.pretty:
        density = report.get("density", {})
        print(f"## 知识图谱{'（全量重建）' if full_llm else ''}")
        print(f"  节点: {report.get('nodes', 0)}")
        print(f"  wikilink 边: {report.get('wikilink_edges', 0)}")
        print(f"  LLM 关系边: {report.get('llm_edges', 0)}")
        if report.get("llm_error"):
            print(f"  LLM 错误: {report['llm_error']}")
        print(f"  孤立节点: {report.get('orphan_count', 0)}")
        if density:
            print(f"  图密度: {density.get('density', 0)}")
            print(f"  桥接节点: {density.get('bridges', 0)}")

    _emit_output("build-graph", report, pretty=args.pretty)
    return 0 if not report.get("llm_error") else 1


def handle_graph_query(args, bundle, logger) -> int:
    """查询已构建的知识图谱（neighbors/related/path/orphans/bridges/density）。"""
    from iris.wiki import WikiGraph

    if not bundle.wiki or not bundle.wiki.get("wiki_root"):
        _emit_output("graph-query", {"error": "Wiki 配置缺失"}, pretty=args.pretty)
        return 1

    op = getattr(args, "op", "") or ""
    node = getattr(args, "node", "") or ""
    to = getattr(args, "to", "") or ""
    hops = int(getattr(args, "hops", 1) or 1)
    min_degree = int(getattr(args, "min_degree", 3) or 3)

    graph = WikiGraph(bundle)
    if not graph.load():
        _emit_output("graph-query", {"error": "图谱尚未构建，请先运行 iris build-graph"}, pretty=args.pretty)
        return 1

    id_to_title = {nid: n.title for nid, n in graph._nodes.items()}

    def _node_dict(n) -> dict:
        return {"id": n.id, "title": n.title, "page_type": n.page_type}

    payload: Dict[str, Any] = {"op": op}

    if op == "neighbors":
        if not node:
            _emit_output("graph-query", {"error": "neighbors 需要 --node"}, pretty=args.pretty)
            return 1
        nodes = graph.neighbors(node, hops=hops)
        payload.update({"node": node, "hops": hops, "count": len(nodes),
                        "neighbors": [_node_dict(n) for n in nodes]})
    elif op == "related":
        if not node:
            _emit_output("graph-query", {"error": "related 需要 --node"}, pretty=args.pretty)
            return 1
        payload.update({"node": node, "related": graph.related_entities(node)})
    elif op == "path":
        if not node or not to:
            _emit_output("graph-query", {"error": "path 需要 --node 与 --to"}, pretty=args.pretty)
            return 1
        edges = graph.find_path(node, to)
        if edges is None:
            payload.update({"from": node, "to": to, "found": False, "edges": []})
        else:
            payload.update({"from": node, "to": to, "found": True,
                            "hops": len(edges),
                            "edges": [{"source": e.source, "target": e.target,
                                       "relation": e.relation, "source_type": e.source_type}
                                      for e in edges]})
    elif op == "orphans":
        orphans = graph.find_orphans()
        payload.update({"count": len(orphans), "orphans": orphans})
    elif op == "bridges":
        payload.update({"min_degree": min_degree, "bridges": graph.find_bridges(min_degree=min_degree)})
    elif op == "density":
        payload.update({"density": graph.density_report()})
    else:
        _emit_output("graph-query", {"error": f"未知 op: {op!r}，可选: neighbors/related/path/orphans/bridges/density"},
                     pretty=args.pretty)
        return 1

    if args.pretty:
        _print_graph_query_pretty(op, payload, id_to_title)
        return 0

    _emit_output("graph-query", payload, pretty=False)
    return 0


def _print_graph_query_pretty(op: str, payload: Dict[str, Any], id_to_title: Dict[str, str]) -> None:
    """graph-query --pretty 的可读输出。"""
    if op == "neighbors":
        print(f"## {payload['node']} 的邻居（{payload['hops']} 跳，共 {payload['count']} 个）")
        by_type: Dict[str, list] = {}
        for n in payload["neighbors"]:
            by_type.setdefault(n["page_type"], []).append(n["title"])
        for ptype, titles in sorted(by_type.items()):
            print(f"  [{ptype}] {', '.join(sorted(titles))}")
    elif op == "related":
        related = payload["related"]
        if not related:
            print(f"{payload['node']} 无相关实体（或节点不存在）。")
            return
        print(f"## 与 {payload['node']} 相关的实体")
        for ptype, items in sorted(related.items()):
            entries = [f"{it['title']}（{it['relation']}）" for it in items]
            print(f"  [{ptype}] {', '.join(entries)}")
    elif op == "path":
        if not payload["found"]:
            print(f"未找到 {payload['from']} → {payload['to']} 的路径。")
            return
        frm = payload["from"]
        ends = [payload["edges"][0]["source"], payload["edges"][0]["target"]]
        cur = next((c for c in ends if c == frm or id_to_title.get(c) == frm), ends[0])
        parts = [id_to_title.get(cur, cur)]
        for e in payload["edges"]:
            nxt = e["target"] if e["source"] == cur else e["source"]
            parts.append(f"→[{e['relation']}]→ {id_to_title.get(nxt, nxt)}")
            cur = nxt
        print(f"## 路径（{payload['hops']} 跳）")
        print("  " + " ".join(parts))
    elif op == "orphans":
        print(f"## 孤立节点（零入链，共 {payload['count']} 个）")
        for oid in payload["orphans"]:
            print(f"  - {id_to_title.get(oid, oid)}")
    elif op == "bridges":
        bridges = payload["bridges"]
        print(f"## 桥接节点（度 ≥ {payload['min_degree']}，共 {len(bridges)} 个）")
        for b in bridges:
            print(f"  - {b['title']} [{b['page_type']}] 度={b['degree']} 跨类型={'/'.join(b['connected_types'])}")
    elif op == "density":
        d = payload["density"]
        print("## 知识图谱密度报告")
        print(f"  节点数: {d['nodes']}    边数: {d['edges']}（wikilink {d['edges_wikilink']} / LLM {d['edges_llm']}）")
        print(f"  图密度: {d['density']}    平均度: {d['avg_degree']}    最大度: {d['max_degree']}（{d['max_degree_node']}）")
        print(f"  孤立节点: {d['orphans']}    桥接节点: {d['bridges']}")
        if d.get("by_type"):
            print("  类型分布: " + "  ".join(f"{k}={v}" for k, v in sorted(d["by_type"].items())))


# ── 文件监听 ─────────────────────────────────────────


def handle_watch(args, bundle, logger) -> int:
    from iris.ingest.watcher import SourceWatcher, build_incremental_on_change
    watcher = SourceWatcher(bundle)
    poll_interval = getattr(args, "poll_interval", 30)
    run_once = getattr(args, "run_once", False)

    if run_once:
        events = watcher.poll()
        print(f"检测到 {len(events)} 个文件变更")
        for evt in events:
            print(f"  [{evt.event_type}] {evt.relative_path}")
        if events:
            build_incremental_on_change(bundle)(events)
        return 0

    # 进程注册：防止重复启动
    from iris.core.locks import ProcessRegistry
    registry = ProcessRegistry("iris-watch", bundle.root / "data")
    if not registry.register():
        print("[Iris] ⚠ iris-watch 已有实例在运行，退出", file=__import__("sys").stderr)
        return 1

    on_change = build_incremental_on_change(bundle)
    try:
        watcher.start(on_change, poll_interval=poll_interval, run_once=run_once)
    finally:
        registry.unregister()
    return 0


# ── 命令映射 ─────────────────────────────────────────────

DATA_HANDLERS = {
    "check-config": handle_check_config,
    "route-model": handle_route_model,
    "scan-source": handle_scan_source,
    "build-chunks": handle_build_chunks,
    "build-vector-index": handle_build_vector_index,
    "search": handle_search,
    "ask": handle_ask,
    "build-graph": handle_build_graph,
    "graph-query": handle_graph_query,
    "watch": handle_watch,
}
