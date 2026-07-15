"""系统管理 + 记忆系统 + 工作上下文 + 密钥 + 用量统计 命令处理器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from iris.memory import (
    CorrectionMemoryStore,
    LongTermMemoryManager,
    MemoryLifecycle,
    UserProfileMemoryStore,
    WorkingContextStore,
)

from iris.app.cli.helpers import (
    _build_diagnose_payload, _build_status_payload,
    _build_agent_spec_payload,
    _emit_output,
)


# ── 日常启动 ────────────────────────────────────────────


def handle_daily_start(args, bundle, logger) -> int:
    from iris.memory import MemoryLifecycle
    from iris.app.cli.helpers import _run_sync_memory

    # 1. 记忆同步
    sync_result = _run_sync_memory(bundle)
    if sync_result.get("synced"):
        logger.log("sync_memory", {"corrections_added": sync_result.get("corrections_added", 0)})

    # 2. 记忆自治维护
    maintenance_report = MemoryLifecycle(bundle).maintenance()

    # 3. 扫描 + 切块 + 向量索引
    scan_info, chunk_summaries, vector_index_result = _daily_scan_and_chunk(bundle)

    # 4. Wiki 自动发现 + 索引维护 + 增量更新
    wiki_update_result, person_enrich_result = _daily_wiki_maintenance(
        bundle, chunk_summaries,
    )

    # 5. LLM 用量概要（今日/本周/本月 + 预算预警）
    usage_summary = _compute_daily_usage_summary(bundle)

    payload = {"memory_sync": {"scanned": sync_result.get("scanned", 0), "skipped": sync_result.get("skipped", 0),
                                "corrections_added": sync_result.get("corrections_added", 0)},
               "memory_maintenance": maintenance_report, "scan": scan_info,
               "chunks": [{"source_name": cs.source_name, "chunk_count": cs.chunk_count,
                            "reused_documents": cs.build_stats.get("reused_documents", 0),
                            "rebuilt_documents": cs.build_stats.get("rebuilt_documents", 0)} for cs in chunk_summaries],
               "vector_index": vector_index_result,
               "wiki_discover": _auto_discover_wiki_for_daily(bundle, chunk_summaries),
               "wiki_update": wiki_update_result,
               "person_enrich": person_enrich_result,
               "usage_summary": usage_summary}
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def _compute_daily_usage_summary(bundle) -> dict:
    """LLM 用量概要：今日/本周/本月调用与 token 汇总 + 预算预警（静默失败）。

    仅从本地 usage DB 读取，不产生任何 LLM 调用。DB 为空时返回 {"status": "empty"}。
    """
    try:
        from datetime import datetime, timezone
        from iris.llm.usage_tracker import UsageTracker, load_pricing

        tracker = UsageTracker(bundle.root / "data")
        if tracker.total_records() == 0:
            return {"status": "empty"}

        now = datetime.now(tz=timezone.utc)
        today_key = now.strftime("%Y-%m-%d")
        week_key = now.strftime("%Y-W%W")
        month_key = now.strftime("%Y-%m")

        def _pick(rows, key):
            for r in rows:
                if r["period"] == key:
                    return {"calls": r["calls"], "total_tokens": r["total_tokens"]}
            return {"calls": 0, "total_tokens": 0}

        today = _pick(tracker.stats(by="day"), today_key)
        this_week = _pick(tracker.stats(by="week"), week_key)
        this_month = _pick(tracker.stats(by="month"), month_key)

        summary = {"status": "ok", "today": today, "this_week": this_week, "this_month": this_month}

        # 预算预警：本月 token 超过 config/llm_pricing.json 中的 _budget.monthly_token_limit
        pricing = load_pricing(bundle.root / "config")
        limit = (pricing.get("_budget") or {}).get("monthly_token_limit")
        if isinstance(limit, (int, float)) and limit > 0 and this_month["total_tokens"] > limit:
            summary["budget_warning"] = (
                f"本月已用 {this_month['total_tokens']:,} token，超过预算上限 {int(limit):,}"
            )
        return summary
    except Exception as exc:  # noqa: BLE001 - 概要失败不应阻断 daily-start
        return {"status": "error", "reason": str(exc)}


def _daily_scan_and_chunk(bundle) -> tuple:
    """日常扫描、切块、向量索引更新。"""
    from iris.ingest import MarkdownScanner, MarkdownChunker
    scanner = MarkdownScanner(bundle)
    chunker = MarkdownChunker(bundle)
    scan_summaries = scanner.scan_all_enabled_sources()
    chunk_summaries = []
    scan_info = []
    for scan_summary in scan_summaries:
        scanner.write_summary(scan_summary)
        cs = chunker.build_source_chunks(scan_summary.source_name)
        chunker.write_summary(cs)
        chunk_summaries.append(cs)
        scan_info.append({"source_name": scan_summary.source_name,
                          "document_count": scan_summary.document_count})
    vector_index_result = _daily_vector_index(bundle)
    return scan_info, chunk_summaries, vector_index_result


def _daily_vector_index(bundle) -> dict:
    """向量索引增量更新（静默失败）。"""
    try:
        from iris.retrieval.embedder import build_embedder_from_config
        emb_cfg = bundle.llm.get("embedding", {})
        if not emb_cfg.get("enabled", False):
            return {"status": "skipped", "reason": "embedding_disabled"}
        embedder = build_embedder_from_config(bundle.llm)
        if not embedder:
            return {"status": "skipped", "reason": "embedder_not_configured"}
        from iris.retrieval.vector_index import VectorIndex, build_vector_index
        ds_name = (bundle.data_source or {}).get("default_source", "work_docs_main")
        summary_path = bundle.root / "data" / "metadata" / f"{ds_name}_chunk_summary.json"
        if not summary_path.exists():
            return {"status": "skipped", "reason": "no_chunk_summary"}
        vi_payload = json.loads(summary_path.read_text(encoding="utf-8"))
        from iris.ingest.chunker import ChunkRecord
        vi_chunks = [ChunkRecord(**item) for item in vi_payload["chunks"]]
        index_path = bundle.root / "data" / "metadata" / f"{ds_name}_vector_index"
        existing = VectorIndex(index_path)
        existing.load()
        idx = build_vector_index(ds_name, vi_chunks, embedder, index_path, existing_index=existing)
        return {"status": "ok", "indexed": idx.size()}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}


def _auto_discover_wiki_for_daily(bundle, chunk_summaries) -> dict:
    """Wiki 自动发现（封装 helpers 调用）。"""
    from iris.app.cli.helpers import _auto_discover_wiki
    total_rebuilt = sum(cs.build_stats.get("rebuilt_documents", 0) for cs in chunk_summaries)
    return _auto_discover_wiki(bundle, changed_count=total_rebuilt)


def _daily_wiki_maintenance(bundle, chunk_summaries) -> tuple:
    """Wiki 增量更新 + 人物信息丰富。"""
    from iris.wiki import WikiNavigationBuilder, append_changelog

    wiki_update_result = {"status": "skipped", "reason": "无 chunk 数据"}
    person_enrich_result = {"status": "skipped", "reason": "无 wiki_root 配置"}

    if not bundle.wiki:
        return wiki_update_result, person_enrich_result

    from iris.wiki.generator import WikiGenerator
    wiki_update_result = WikiGenerator(bundle).update_all_pages(top_k=4)

    # 飞书通讯录人物信息丰富（静默失败，不影响主流程）
    try:
        from iris.wiki.person_enricher import PersonEnricher
        enrich_result = PersonEnricher(bundle).enrich(dry_run=False)
        person_enrich_result = {
            "status": "ok",
            "updated": enrich_result.updated,
            "not_found": enrich_result.not_found,
            "ambiguous": enrich_result.ambiguous,
            "no_change": enrich_result.no_change,
        }
    except Exception as exc:
        person_enrich_result = {"status": "error", "reason": str(exc)}

    # 知识图谱增量刷新（静默失败，仅刷新节点和 wikilink 边，不调用 LLM）
    try:
        from iris.wiki.graph import WikiGraph
        from iris.wiki.context_loader import WikiContextLoader
        from iris.wiki.backlink import BacklinkBuilder
        graph = WikiGraph(bundle)
        graph.load()
        wiki_root_path = graph._wiki_root
        if wiki_root_path.exists():
            loader = WikiContextLoader(wiki_root_path)
            pages = loader.load_pages()
            graph.build_nodes(_pages=pages)
            backlink_builder = BacklinkBuilder(wiki_root_path)
            backlink_index = backlink_builder.build_from_wiki_pages(pages)
            graph.build_edges_from_backlinks(backlink_index)
        graph.save()
    except Exception:
        pass

    builder = WikiNavigationBuilder(bundle)
    builder.build(write=True)
    append_changelog(Path(bundle.wiki["wiki_root"]), "daily-start 自动维护")

    return wiki_update_result, person_enrich_result


# ── 系统 ──────────────────────────────────────────────────


def handle_diagnose(args, bundle, logger) -> int:
    _emit_output(args.command, _build_diagnose_payload(bundle, logger), pretty=args.pretty)
    return 0


def handle_status(args, bundle, logger) -> int:
    _emit_output(args.command, _build_status_payload(bundle, logger), pretty=args.pretty)
    return 0


def handle_agent_spec(args, bundle, logger) -> int:
    from iris.core.agent_adapter import IRIS_CAPABILITIES
    _emit_output(args.command, _build_agent_spec_payload(IRIS_CAPABILITIES), pretty=args.pretty)
    return 0


# ── 记忆系统 ────────────────────────────────────────────────


def handle_memory_status(args, bundle, logger) -> int:
    profile_store = UserProfileMemoryStore(bundle)
    correction_store = CorrectionMemoryStore(bundle)
    profile = profile_store.load()
    corrections = correction_store.load()
    payload = {"profile_updated_at": profile.get("updated_at"),
               "likes_count": len(profile.get("user_preferences", {}).get("likes", [])),
               "dislikes_count": len(profile.get("user_preferences", {}).get("dislikes", [])),
               "style_preferences_count": len(profile.get("user_preferences", {}).get("style_preferences", [])),
               "notes_count": len(profile.get("user_preferences", {}).get("notes", [])),
               "correction_count": len(corrections.get("items", {})),
               "correction_updated_at": corrections.get("updated_at"),
               "sample_corrections": [{"concept": c, "preferred": str(v.get("preferred", ""))}
                                       for c, v in list(corrections.get("items", {}).items())[:5]]}
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def handle_memory_list(args, bundle, logger) -> int:
    manager = LongTermMemoryManager(bundle)
    _emit_output(args.command, manager.list_memory(args.memory_type), pretty=args.pretty)
    return 0


def handle_memory_delete(args, bundle, logger) -> int:
    if not args.concept:
        raise ValueError("memory-delete 需要 --concept")
    manager = LongTermMemoryManager(bundle)
    _emit_output(args.command, manager.delete_correction(args.concept), pretty=args.pretty)
    return 0


def handle_memory_maintenance(args, bundle, logger) -> int:
    lifecycle = MemoryLifecycle(bundle)
    age_days = getattr(args, "age_days", 90)
    auto_age = getattr(args, "auto_age", False)
    report = lifecycle.maintenance(age_days=age_days)
    if auto_age:
        report["age_result"] = lifecycle.age(days=age_days)
    _emit_output("memory-maintenance", report, pretty=args.pretty)
    return 0


def handle_memory_export(args, bundle, logger) -> int:
    if not args.output_file:
        raise ValueError("memory-export 需要 --output-file")
    manager = LongTermMemoryManager(bundle)
    path = manager.export_to_file(Path(args.output_file))
    _emit_output(args.command, {"output_file": str(path)}, pretty=args.pretty)
    return 0


def handle_memory_import(args, bundle, logger) -> int:
    if not args.input_file:
        raise ValueError("memory-import 需要 --input-file")
    manager = LongTermMemoryManager(bundle)
    payload = manager.import_from_file(Path(args.input_file), replace=args.replace)
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


# ── 工作上下文 ─────────────────────────────────────────────


def handle_working_set(args, bundle, logger) -> int:
    store = WorkingContextStore(bundle)
    kwargs: Dict[str, Any] = {}
    if args.task:
        kwargs["current_task"] = args.task
    if args.pending:
        kwargs["pending_items"] = [item.strip() for item in args.pending.split("|") if item.strip()]
    if args.add_pending:
        kwargs["append_pending"] = [item.strip() for item in args.add_pending.split("|") if item.strip()]
    if args.change:
        kwargs["recent_changes"] = [item.strip() for item in args.change.split("|") if item.strip()]
    if args.add_change:
        kwargs["append_changes"] = [item.strip() for item in args.add_change.split("|") if item.strip()]
    if args.notes:
        kwargs["notes"] = args.notes
    _emit_output(args.command, store.update(**kwargs), pretty=args.pretty)
    return 0


def handle_working_show(args, bundle, logger) -> int:
    _emit_output(args.command, WorkingContextStore(bundle).load(), pretty=args.pretty)
    return 0


def handle_working_clear(args, bundle, logger) -> int:
    _emit_output(args.command, WorkingContextStore(bundle).clear(), pretty=args.pretty)
    return 0


# ── 密钥链管理 ─────────────────────────────────────────────


def handle_secrets_set(args, bundle, logger) -> int:
    key = args.key
    if not key:
        _emit_output("secrets-set", {"status": "失败", "error": "请通过 --key 指定密钥名称"}, pretty=args.pretty)
        return 1
    value = getattr(args, "value", None)
    if not value:
        try:
            import getpass
            value = getpass.getpass(f"请输入 {key} 的值: ")
        except (KeyboardInterrupt, EOFError):
            return 1
    if not value.strip():
        return 1
    from iris.config.secrets import set_secret, KeychainError
    try:
        set_secret(key, value.strip())
        _emit_output("secrets-set", {"key": key, "status": "已存储"}, pretty=args.pretty)
        return 0
    except KeychainError as exc:
        _emit_output("secrets-set", {"key": key, "status": "失败", "error": str(exc)}, pretty=args.pretty)
        return 1


def handle_secrets_list(args, bundle, logger) -> int:
    from iris.config.secrets import list_secrets
    names = list_secrets()
    _emit_output("secrets-list", {"keys": names, "count": len(names)}, pretty=args.pretty)
    return 0


def handle_secrets_delete(args, bundle, logger) -> int:
    key = args.key
    if not key:
        _emit_output("secrets-delete", {"status": "失败", "error": "请通过 --key 指定密钥名称"}, pretty=args.pretty)
        return 1
    from iris.config.secrets import delete_secret
    ok = delete_secret(key)
    _emit_output("secrets-delete", {"key": key, "deleted": ok}, pretty=args.pretty)
    return 0 if ok else 1


# ── 用量统计 ─────────────────────────────────────────────


def handle_usage_stats(args, bundle, logger) -> int:
    from iris.llm.usage_tracker import UsageTracker, load_pricing

    by = getattr(args, "by", "month") or "month"
    model_filter = getattr(args, "model", None) or None
    since = getattr(args, "since", None) or None
    show_cost = getattr(args, "cost", False)

    tracker = UsageTracker(bundle.root / "data")

    unpriced: list = []
    currency = "CNY"
    if show_cost:
        pricing = load_pricing(bundle.root / "config")
        cost_result = tracker.stats_with_cost(by=by, model=model_filter, since=since, pricing=pricing)
        rows = cost_result["rows"]
        unpriced = cost_result["unpriced_models"]
        currency = cost_result["currency"]
    else:
        rows = tracker.stats(by=by, model=model_filter, since=since)

    if args.pretty:
        if not rows:
            print("暂无用量数据（尚未发生任何 LLM 调用，或数据库路径有误）。")
            return 0

        period_label = {"day": "日期", "week": "周", "month": "月份", "year": "年份"}.get(by, by)
        cost_col = f"{'估算费用':>12}" if show_cost else ""
        header = f"{period_label:<14} {'调用次数':>8} {'输入Token':>11} {'输出Token':>11} {'合计Token':>11}{cost_col}"
        sep = "-" * len(header)
        print(f"\n{header}")
        print(sep)

        total_calls = total_pt = total_ct = 0
        total_cost = 0.0
        for row in rows:
            calls = row["calls"]
            pt = row["prompt_tokens"]
            ct = row["completion_tokens"]
            tot = row["total_tokens"]
            cost_str = ""
            if show_cost:
                cost = row.get("cost")
                cost_str = f"{cost:>12,.4f}" if cost is not None else f"{'—':>12}"
                total_cost += cost or 0.0
            print(f"{row['period']:<14} {calls:>8,} {pt:>11,} {ct:>11,} {tot:>11,}{cost_str}")
            total_calls += calls
            total_pt += pt
            total_ct += ct

        print(sep)
        total_cost_str = f"{total_cost:>12,.4f}" if show_cost else ""
        print(f"{'合计':<14} {total_calls:>8,} {total_pt:>11,} {total_ct:>11,} {total_pt + total_ct:>11,}{total_cost_str}")
        if show_cost:
            print(f"（货币：{currency}）")
            if unpriced:
                print(f"⚠ 未定价模型（未计入费用）：{', '.join(unpriced)}")

        # 最后一个时间段的按模型分布
        if rows:
            last_period = rows[-1]["period"]
            model_rows = tracker.stats_by_model(last_period, by=by)
            if model_rows:
                print(f"\n模型分布（{last_period}）：")
                for r in model_rows:
                    mname = r["model"]
                    print(f"  {mname:<32} {r['calls']:>4} 次  "
                          f"{r['prompt_tokens']:>8,} / {r['completion_tokens']:>8,} tokens")
        return 0

    _emit_output("usage-stats", {
        "by": by,
        "model_filter": model_filter,
        "since": since,
        "cost": show_cost,
        "currency": currency if show_cost else None,
        "unpriced_models": unpriced if show_cost else None,
        "rows": rows,
    }, pretty=False)
    return 0


# ── 指标导出 ─────────────────────────────────────────


def handle_metrics_export(args, bundle, logger) -> int:
    from iris.utils.metrics import MetricsExporter
    exporter = MetricsExporter(bundle)
    if getattr(args, "trend", False):
        result = exporter.trend(weeks=getattr(args, "weeks", 4))
        _emit_output("metrics-export", result, pretty=args.pretty)
        return 0
    snapshot = exporter.snapshot()
    output_path = exporter.export(snapshot)
    _emit_output("metrics-export", {"path": str(output_path), "snapshot": snapshot}, pretty=args.pretty)
    return 0


# ── 命令映射 ─────────────────────────────────────────────

SYSTEM_HANDLERS = {
    "daily-start": handle_daily_start,
    "diagnose": handle_diagnose,
    "status": handle_status,
    "agent-spec": handle_agent_spec,
    "memory-status": handle_memory_status,
    "memory-list": handle_memory_list,
    "memory-delete": handle_memory_delete,
    "memory-maintenance": handle_memory_maintenance,
    "memory-export": handle_memory_export,
    "memory-import": handle_memory_import,
    "working-set": handle_working_set,
    "working-show": handle_working_show,
    "working-clear": handle_working_clear,
    "secrets-set": handle_secrets_set,
    "secrets-list": handle_secrets_list,
    "secrets-delete": handle_secrets_delete,
    "usage-stats": handle_usage_stats,
    "metrics-export": handle_metrics_export,
}
