"""CLI 命令处理器 — Phase 2.1 版本。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from iris.complex_input import ComplexInputPipeline
from iris.config import load_config_bundle
from iris.ingest import MarkdownChunker, MarkdownScanner
from iris.llm import LLMService
from iris.llm.router import ModelRouter
from iris.memory import (
    CorrectionMemoryStore,
    LongTermMemoryManager,
    MemoryLifecycle,
    UserProfileMemoryStore,
    WorkingContextStore,
)
from iris.utils.logging import IrisLogger

from iris.app.cli.helpers import (
    _parse_context, _parse_image_list,
    _build_diagnose_payload, _build_status_payload,
    _build_agent_spec_payload,
    _scan_payload, _chunk_payload,
    _emit_output, _print_config_summary,
)
from iris.utils.paths import resolve_source_root as _resolve_data_source_root


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
    chunker = MarkdownChunker(bundle)
    summaries = [chunker.build_source_chunks(args.source)] if args.source else chunker.build_all_enabled_sources_chunks()
    payloads = []
    for summary in summaries:
        p = _chunk_payload(summary, summary_only=args.summary_only)
        if args.write_summary:
            p["summary_path"] = str(chunker.write_summary(summary))
        payloads.append(p)
        logger.log("build_chunks", {"source_name": summary.source_name,
                                     "chunk_count": summary.chunk_count,
                                     "build_stats": {"reused_documents": summary.build_stats.get("reused_documents", 0),
                                                     "rebuilt_documents": summary.build_stats.get("rebuilt_documents", 0)}})
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


def handle_build_report(args, bundle, logger) -> int:
    from iris.analysis import AnalysisReportService
    service = AnalysisReportService(bundle)
    result = service.build_report(args.query, top_k=max(args.top_k, 4), mode=args.mode, two_stage=getattr(args, "two_stage", False))
    payload = result.to_dict()
    if args.output_file:
        output_path = Path(args.output_file)
        report_format = getattr(args, "output_format", "md") or "md"
        from iris.output.converters import convert_report
        try:
            written = convert_report(result.markdown, output_path, format=report_format, title=args.query)
            payload["output_file"] = str(written)
            payload["format"] = report_format
        except (ValueError, RuntimeError) as exc:
            output_path = output_path.with_suffix(".md")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(result.markdown, encoding="utf-8")
            payload["output_file"] = str(output_path)
            payload["format"] = "md"
            payload["format_error"] = str(exc)
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def handle_build_mindmap(args, bundle, logger) -> int:
    from iris.analysis import MindmapService
    from iris.analysis.mindmap import _build_xmind_bytes
    service = MindmapService(bundle)
    result = service.build_mindmap(args.query, top_k=max(args.top_k, 4), mode=args.mode, format=args.format)
    payload = result.to_dict()
    if args.format == "mermaid" and args.output_file:
        from iris.app.cli.helpers import _write_text_file
        payload["output_file"] = str(_write_text_file(args.output_file, result.markdown))
    elif args.format in ("xmind", "both") and result.tree:
        xmind_bytes = _build_xmind_bytes(result.tree)
        if xmind_bytes:
            from iris.app.cli.helpers import _resolve_output_path, _write_bytes_file
            xmind_path = _resolve_output_path(args.output_file, args.query, ".xmind")
            _write_bytes_file(str(xmind_path), xmind_bytes)
            payload["xmind_file"] = str(xmind_path)
            if args.format == "both" and args.output_file:
                from iris.app.cli.helpers import _write_text_file
                payload["output_file"] = str(_write_text_file(args.output_file, result.markdown))
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


# ── 双周报 ────────────────────────────────────────────


def _build_biweekly_filename(bundle, today: datetime) -> str:
    """生成双周报文件名：双周报-w{week}-{author}-{date}.md。

    周一生成时，周数归属上周（如 W27 而非 W28）。
    """
    cfg = bundle.app.get("biweekly_report", {})
    author = cfg.get("author_name", "")
    if today.weekday() == 0:  # 周一
        report_week_date = today - timedelta(days=1)
    else:
        report_week_date = today
    _, week, _ = report_week_date.isocalendar()
    date_str = today.strftime("%Y%m%d")
    return f"双周报-w{week:02d}-{author}-{date_str}.md" if author else f"双周报-w{week:02d}-{date_str}.md"


def handle_build_biweekly_report(args, bundle, logger) -> int:
    from iris.analysis import AnalysisReportService
    service = AnalysisReportService(bundle)
    query = getattr(args, "query", "") or ""
    style_from = getattr(args, "style_from", "") or None
    dry_run = getattr(args, "dry_run", False)
    # 默认走 llm 模式，LLM 不可用时 service 内部自动降级为 local
    result = service.build_biweekly_report(query=query, mode="llm",
                                           style_from=style_from, dry_run=dry_run)
    payload = result.to_dict()

    # dry-run 模式不写文件，直接输出预览
    if dry_run:
        _emit_output(args.command, payload, pretty=args.pretty)
        return 0

    # 确定输出路径
    output = args.output_file
    # 提取自动生成的文件名（to_source 或 output 指向目录时使用）
    auto_filename = _build_biweekly_filename(bundle, datetime.now())
    if output:
        out_path = Path(output)
        # 用户指定了目录路径 → 自动拼接文件名
        if output.endswith("/") or output.endswith(os.sep) or (out_path.exists() and out_path.is_dir()):
            output = str(out_path / auto_filename)
    elif getattr(args, "to_source", False):
        source_root = _resolve_data_source_root(bundle)
        if source_root:
            output = str(source_root / "06-我的周报" / auto_filename)

    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        markdown = result.markdown.strip()
        # 追加尾注（report_author 为空时不追加）
        report_author = (bundle.app.get("biweekly_report", {}).get("report_author") or "").strip()
        if report_author:
            footer = f"\n\n---\n> This report was written by Iris and revised by {report_author}."
            if not markdown.endswith(footer.strip()):
                markdown += footer
        path.write_text(markdown, encoding="utf-8")
        payload["output_file"] = str(path)

    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


# ── Wiki 命令 ────────────────────────────────────────────


def handle_discover_wiki(args, bundle, logger) -> int:
    from iris.wiki import CandidateDiscovery

    discovery = CandidateDiscovery(bundle)
    candidates = discovery.discover(limit=args.limit, incremental=args.incremental)
    payload = {"items": [{"title": item.title, "page_type": item.page_type, "query": item.query,
                          "score": item.score, "evidence_count": item.evidence_count,
                          "sample_paths": item.sample_paths, "rationale": item.rationale,
                          "has_wiki": item.has_wiki, "wiki_stale": item.wiki_stale,
                          "wiki_path": item.wiki_path} for item in candidates]}
    if args.export_jsonl:
        payload["export_jsonl"] = str(discovery.export_jsonl(candidates, Path(args.export_jsonl)))
    if args.export_review:
        payload["export_review"] = str(discovery.export_review_jsonl(candidates, Path(args.export_review)))
    if args.export_review_md:
        payload["export_review_md"] = str(discovery.export_review_markdown(candidates, Path(args.export_review_md)))
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def handle_discover_wiki_auto(args, bundle, logger) -> int:
    from iris.wiki import CandidateDiscovery
    from iris.app.cli.helpers import _auto_discover_wiki

    result = _auto_discover_wiki(bundle, changed_count=999)
    _emit_output(args.command, result, pretty=args.pretty)
    return 0


def handle_build_wiki(args, bundle, logger) -> int:
    from iris.wiki import BatchWikiItem, WikiGenerator

    generator = WikiGenerator(bundle)
    if args.review_file:
        items = _load_review_items(Path(args.review_file))
        result = generator.build_pages(items, write=args.write, overwrite=args.overwrite, backup=args.backup)
        _emit_output(args.command, {"items": result.items}, pretty=args.pretty)
        return 0
    if args.batch_file:
        items = _load_batch_items(Path(args.batch_file))
        result = generator.build_pages(items, write=args.write, overwrite=args.overwrite, backup=args.backup)
        _emit_output(args.command, {"items": result.items}, pretty=args.pretty)
        return 0

    title = args.title or args.query
    draft = generator.build_page(query=args.query, page_type=args.page_type, title=title)
    payload = {"page_type": draft.page_type, "title": draft.title, "slug": draft.slug,
               "output_path": draft.output_path, "markdown": draft.markdown}
    if args.write:
        write_result = generator.write_page(draft, overwrite=args.overwrite, backup=args.backup)
        payload["write_result"] = {"path": write_result.path, "action": write_result.action,
                                   "backup_path": write_result.backup_path}
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def handle_build_wiki_nav(args, bundle, logger) -> int:
    from iris.wiki import WikiNavigationBuilder

    builder = WikiNavigationBuilder(bundle)
    result = builder.build(write=True)
    _emit_output(args.command, {"nav_path": result.nav_path, "pages_written": result.pages_written,
                                 "errors": result.errors}, pretty=args.pretty)
    return 0


def handle_wiki_pipeline(args, bundle, logger) -> int:
    from iris.wiki import CandidateDiscovery

    discovery = CandidateDiscovery(bundle)
    candidates = discovery.discover(limit=args.limit, incremental=args.incremental)
    temp_root = bundle.root / "temp" / "wiki_pipeline"
    export_review = Path(args.export_review) if args.export_review else temp_root / "review.jsonl"
    export_review_md = Path(args.export_review_md) if args.export_review_md else temp_root / "review.md"
    export_jsonl = Path(args.export_jsonl) if args.export_jsonl else temp_root / "candidates.jsonl"
    payload = {"candidate_count": len(candidates),
               "export_jsonl": str(discovery.export_jsonl(candidates, export_jsonl)),
               "export_review": str(discovery.export_review_jsonl(candidates, export_review)),
               "export_review_md": str(discovery.export_review_markdown(candidates, export_review_md)),
               "next_step": "请人工编辑 review.jsonl 的 selected 字段，再执行 build-wiki --review-file <path> --write。"}
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def handle_wiki_lint(args, bundle, logger) -> int:
    from iris.wiki import lint_wiki, fix_wiki

    wiki_root = Path(bundle.wiki["wiki_root"]).resolve() if bundle.wiki else Path()
    data_root = bundle.root / "data"

    if getattr(args, "fix", False):
        fix_result = fix_wiki(wiki_root)
        if args.pretty:
            total = fix_result.get("actions_taken", 0)
            details = fix_result.get("details", {})
            print(f"## 自动修复完成（{total} 处）")
            for key, items in details.items():
                if items:
                    print(f"  {key}: {len(items)} 处 ({', '.join(items[:5])})")
        else:
            print(json.dumps(fix_result, ensure_ascii=False, indent=2))
        return 0

    result = lint_wiki(wiki_root, data_root=data_root)
    _emit_output(args.command, result, pretty=args.pretty)
    return 0


def handle_wiki_update(args, bundle, logger) -> int:
    """增量更新 Wiki 页面。"""
    from iris.wiki.generator import WikiGenerator

    generator = WikiGenerator(bundle)
    if args.title:
        result = generator.update_page(title=args.title, page_type=args.page_type)
    else:
        result = generator.update_all_pages()
    _emit_output(args.command, result, pretty=args.pretty)
    return 0


def handle_enrich_persons(args, bundle, logger) -> int:
    """从飞书通讯录补充人物 Wiki 页面的部门和邮箱信息。"""
    from iris.wiki.person_enricher import PersonEnricher

    enricher = PersonEnricher(bundle)
    if args.dry_run:
        logger.log("enrich_persons", {"status": "dry_run"})
    result = enricher.enrich(dry_run=args.dry_run)
    payload = {
        "total": result.total,
        "updated": result.updated,
        "not_found": result.not_found,
        "ambiguous": result.ambiguous,
        "no_change": result.no_change,
        "errors": result.errors,
        "details": [
            {"name": d.name, "status": d.status, "department": d.department,
             "email": d.email, "message": d.message}
            for d in result.details
        ],
    }
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def handle_build_asr_prompt(args, bundle, logger) -> int:
    """从 Wiki 知识库构建语音转写优化资源。

    --mode 支持四种模式：
      all          全流程（热词 + 替换词典 + 校正提示词）
      hotwords     仅热词提取 → asr-hotwords-{date}-{time}.txt
      replace-dict 仅替换词典 → asr-replace-dict-{date}-{time}.json
      prompt       仅校正提示词 → asr-prompt-v{ver}-{date}-{time}.md

    Phase 1: LLM 热词提取（分 5 批 LLM 调用）
    Phase 2: LLM 误识别映射生成（分 8 批 LLM 调用）
    Phase 3: LLM Prompt 优化压缩（1 次 LLM 调用）
    """
    from iris.wiki.context_loader import WikiContextLoader
    from iris.wiki.term_extractor import (
        TermExtractor, render_asr_prompt, determine_new_version,
        load_version, save_version, format_hotwords_file,
        format_replace_dict, LLMHotwordExtractor, LLMPromptOptimizer,
        hotwords_to_terms,
    )

    # ── 0. 校验 Wiki ────────────────────────────────────
    if not bundle.wiki or not bundle.wiki.get("wiki_root"):
        _emit_output(args.command, {"error": "Wiki 配置缺失"}, pretty=args.pretty)
        return 1
    wiki_root = Path(bundle.wiki["wiki_root"]).resolve()
    if not wiki_root.exists():
        _emit_output(args.command, {"error": "Wiki 根目录不存在"}, pretty=args.pretty)
        return 1

    loader = WikiContextLoader(wiki_root)
    sort_order = ["person", "concept", "project", "domain"]
    pages = loader.load_pages(sort_order=sort_order)

    if not pages:
        _emit_output(args.command, {"error": "Wiki 目录为空，无页面可提取"}, pretty=args.pretty)
        return 1

    mode = getattr(args, "asr_mode", "all") or "all"
    today = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    data_dir = bundle.root / "data"
    llm_service = LLMService(bundle)
    provider = llm_service.get_provider()

    # 从配置构建领域背景描述（无配置时使用通用占位）
    from iris.wiki._constants import build_domain_context
    domain_context = build_domain_context(bundle.app)

    # ── Phase 1：LLM 热词提取 ────────────────────────────
    # prompt 模式也提取热词——供 Phase 3 优化器语境样例使用（但不落盘热词文件）
    hotwords: List[str] = []
    hotwords_file = ""
    if mode in ("all", "hotwords", "prompt"):
        print("[asr] Phase 1: LLM 热词提取...", file=sys.stderr)
        hotword_extractor = LLMHotwordExtractor(pages)
        max_hotwords = getattr(args, "max_hotwords", 490) or 490
        hotwords = hotword_extractor.extract(provider, max_hotwords=max_hotwords,
                                             domain_context=domain_context)

        # 仅在需要热词文件的模式写盘；prompt 模式只将热词喂给优化器
        if mode in ("all", "hotwords"):
            hotwords_path = f"asr-hotwords-{today}.txt"
            if args.output_file and mode != "all":
                hotwords_path = args.output_file
            hotwords_file = format_hotwords_file(
                hotwords, bundle.root / "output" / hotwords_path
            )

    # ── Phase 2：术语提取 + 替换词典 ─────────────────────
    terms: List = []
    replace_dict_file = ""
    if mode in ("all", "replace-dict", "prompt"):
        # 规则提取术语
        extractor = TermExtractor(pages)
        terms = extractor.extract_terms()

        # Phase 1 热词补充：将 LLM 提取的热词也纳入误识别生成
        if hotwords and mode == "all":
            terms = hotwords_to_terms(hotwords, terms)

        # 版本判定
        bump = getattr(args, "bump", "auto") or "auto"
        new_version = determine_new_version(pages, data_dir, bump=bump)

        if mode in ("all", "replace-dict"):
            # LLM 生成误识别映射
            print(f"[asr] Phase 2: 术语 {len(terms)} 个 → LLM 误识别生成...",
                  file=sys.stderr)
            terms = extractor.generate_misreadings(terms, provider,
                                                   domain_context=domain_context)

            max_mappings = getattr(args, "max_mappings", 990) or 990
            max_chars = getattr(args, "max_chars", 20) or 20
            replace_path = f"asr-replace-dict-{today}.json"
            if args.output_file and mode == "replace-dict":
                replace_path = args.output_file
            replace_dict_file = format_replace_dict(
                terms, bundle.root / "output" / replace_path,
                max_mappings=max_mappings, max_chars=max_chars,
            )

    # ── Phase 3：LLM Prompt 优化 ────────────────────────
    prompt = ""
    output_path = ""
    if mode in ("all", "prompt"):
        # terms 与 new_version 已在 Phase 2 填充（"all"/"prompt" 均经过 Phase 2）
        print("[asr] Phase 3: LLM Prompt 优化压缩...", file=sys.stderr)
        new_version.term_count = len(terms)
        new_version.wiki_page_count = len(pages)

        # LLM 优化器生成紧凑 prompt
        optimizer = LLMPromptOptimizer()
        prompt = optimizer.optimize(hotwords, terms, provider, domain_context=domain_context)
        if not prompt:
            prompt = render_asr_prompt(
                terms, new_version, output_format="standard"
            )

        # 写入文件
        if args.output_file:
            out = Path(args.output_file)
            clean_stem = _strip_version_suffix(out.stem)
            version_tag = f"v{new_version.version}"
            out = out.with_name(f"{clean_stem}_{version_tag}{out.suffix}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(prompt, encoding="utf-8")
            output_path = str(out)
        else:
            auto_path = bundle.root / "output" / f"asr-prompt-v{new_version.version}-{today}.md"
            auto_path.parent.mkdir(parents=True, exist_ok=True)
            auto_path.write_text(prompt, encoding="utf-8")
            output_path = str(auto_path)

        # 持久化版本
        new_version.prompt_text = prompt
        save_version(data_dir, new_version)

    # ── 输出报告 ─────────────────────────────────────────
    payload: Dict[str, Any] = {}
    if hotwords_file:
        payload["hotwords_file"] = hotwords_file
        payload["hotword_count"] = len(hotwords)
    if replace_dict_file:
        payload["replace_dict_file"] = replace_dict_file
        if terms:
            payload["replace_mapping_count"] = sum(
                len(t.mis_asr) for t in terms
            )
    if prompt:
        payload["version"] = new_version.version
        payload["output_file"] = output_path
        payload["fingerprint"] = new_version.fingerprint[:8]
        payload["prompt_chars"] = len(prompt)

    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


# ── 辅助函数（wiki 相关） ─────────────────────────────


def _load_wiki_items_from_jsonl(path: Path, *, only_selected: bool = False):
    """从 JSONL 文件加载 BatchWikiItem 列表。only_selected=True 时只加载 selected=True 的行。"""
    from iris.wiki import BatchWikiItem
    items = []
    for idx, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[警告] 第 {idx} 行 JSON 解析失败: {exc}", file=sys.stderr)
            continue
        if only_selected and not payload.get("selected", False):
            continue
        if "query" not in payload:
            print(f"[警告] 第 {idx} 行缺少必填字段 query，已跳过", file=sys.stderr)
            continue
        items.append(BatchWikiItem(
            query=payload["query"],
            title=payload.get("title", payload["query"]),
            page_type=payload.get("page_type", "domain"),
        ))
    return items


def _load_batch_items(path: Path):
    return _load_wiki_items_from_jsonl(path)


def _load_review_items(path: Path):
    return _load_wiki_items_from_jsonl(path, only_selected=True)


def handle_deep_eval(args, bundle, logger) -> int:
    """深度评估：校验 Wiki 页面内容准确性 + 全面性。"""
    from iris.evaluation import DeepEvaluator, deep_eval_result_to_json, print_deep_eval_pretty

    if not bundle.wiki or not bundle.wiki.get("wiki_root"):
        print("Wiki 配置缺失", file=sys.stderr)
        return 1

    page_filter = getattr(args, "page_filter", None) or None
    sample_rate = getattr(args, "sample_rate", None) or None

    evaluator = DeepEvaluator(bundle)
    print("开始深度评估...", file=sys.stderr)
    result = evaluator.evaluate(page_filter=page_filter, sample_rate=sample_rate)

    if getattr(args, "pretty", False):
        print_deep_eval_pretty(result)
    else:
        print(json.dumps(deep_eval_result_to_json(result), ensure_ascii=False, indent=2))

    # 摘要
    if result.overall_accuracy_rate is not None:
        print(f"\n准确率: {result.overall_accuracy_rate:.1%}", file=sys.stderr)
    print(f"引用: {result.total_references} 条 | 一致 {result.consistent_count} / "
          f"不一致 {result.inconsistent_count} / 无法验证 {result.unverifiable_count} / "
          f"源缺失 {result.source_missing_count}", file=sys.stderr)
    print(f"全面性: {result.total_gaps} 处可能遗漏（{result.pages_with_gaps} 页）",
          file=sys.stderr)
    return 0


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


# ── 会议转录 ────────────────────────────────────────────


def handle_transcribe_meeting(args, bundle, logger) -> int:
    from iris.app.transcribe_meeting import TranscribeMeetingPipeline
    if not args.audio_file and not args.transcript_file:
        import sys
        print("transcribe-meeting 需要 --audio-file 或 --transcript-file", file=sys.stderr)
        return 1
    pipeline = TranscribeMeetingPipeline(bundle)

    # --to-source 模式：LLM 动态路由到 SOURCE 对应子目录
    to_source = getattr(args, "to_source", False)

    # --output 优先级高于 --to-source
    output = args.output if args.output else None

    result = pipeline.run(args.audio_file, transcript_path=args.transcript_file or None,
                          output_path=output, whisper_model=args.whisper_model,
                          force_retranscribe=args.force, to_source=to_source)
    _emit_output(args.command, result, pretty=args.pretty)
    return 0


def handle_batch_transcribe(args, bundle, logger) -> int:
    from iris.app.transcribe_meeting import TranscribeMeetingPipeline
    # --dir 支持：自动扫描目录下所有 .txt 文件
    if not args.files and getattr(args, "dir", ""):
        import glob as _g
        dir_path = Path(getattr(args, "dir", ""))
        if dir_path.exists() and dir_path.is_dir():
            args.files = ",".join(str(p) for p in sorted(dir_path.glob("*.txt")))
    if not args.files:
        raise ValueError("batch-transcribe 需要 --files 或 --dir")
    file_paths = _expand_file_list(args.files)
    if not file_paths:
        print("未匹配到任何文件", file=sys.stderr)
        return 1
    pipeline = TranscribeMeetingPipeline(bundle)
    result = pipeline.run_batch(file_paths, output_dir=args.output_dir or None, whisper_model=args.whisper_model, force_retranscribe=args.force)
    _emit_output(args.command, result, pretty=args.pretty)
    return 0 if result["failed"] == 0 else 1


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


def handle_feishu_doc_convert(args, bundle, logger) -> int:
    """飞书文档转本地 Markdown 并归档到 SOURCE。"""
    from iris.feishu.doc_convert import FeishuDocConverter

    converter = FeishuDocConverter(bundle)
    urls_str = getattr(args, "url", "")
    from_config = getattr(args, "from_config", False)
    force = getattr(args, "force", False)
    dry_run = getattr(args, "dry_run", False)

    if from_config:
        results = converter.convert_from_config(force=force, dry_run=dry_run)
    elif urls_str:
        urls = [u.strip() for u in urls_str.split(",") if u.strip()]
        results = converter.convert_batch(urls, force=force, dry_run=dry_run)
    else:
        print("需要 --url <文档URL> 或 --from-config", file=sys.stderr)
        return 1

    _emit_output(args.command, results, pretty=args.pretty)
    # 汇总统计
    success = sum(1 for r in results if r.get("status") == "success")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    errors = sum(1 for r in results if r.get("status") == "error")
    if success:
        print(f"✅ {success} 成功, {skipped} 跳过, {errors} 失败", file=sys.stderr)
    return 0 if errors == 0 else 1


def handle_chat_digest(args, bundle, logger) -> int:
    """聊天记录提炼。"""
    from iris.feishu.chat_digest import ChatDigester

    digester = ChatDigester(bundle)
    group = getattr(args, "group", "")
    user = getattr(args, "user", "")
    time_range = getattr(args, "range", "")
    from_config = getattr(args, "from_config", False)
    interactive = getattr(args, "interactive", False)
    force = getattr(args, "force", False)
    dry_run = getattr(args, "dry_run", False)

    if interactive:
        groups = digester.list_available_groups()
        if not groups:
            print("未找到可用的群聊", file=sys.stderr)
            return 1
        print("📋 可提取的聊天目标：", file=sys.stderr)
        for i, g in enumerate(groups, 1):
            print(f"  {i}. {g['name']}（{g.get('member_count', 0)} 人）", file=sys.stderr)
        print("请输入序号（逗号分隔多选，留空全部）：", end=" ", file=sys.stderr)
        try:
            choice = input().strip()
        except (EOFError, KeyboardInterrupt):
            return 1
        if choice:
            indices = [int(i.strip()) for i in choice.split(",") if i.strip().isdigit()]
            selected = [groups[i-1] for i in indices if 1 <= i <= len(groups)]
        else:
            selected = groups
        results = []
        for g in selected:
            r = digester.digest(group=g["name"], time_range=time_range, force=force, dry_run=dry_run)
            results.append(r)
        _emit_output(args.command, results, pretty=args.pretty)
        success = sum(1 for r in results if r.get("status") == "success")
        print(f"✅ {success}/{len(results)} 成功", file=sys.stderr)
        return 0

    if from_config:
        results = digester.digest_from_config(force=force, dry_run=dry_run)
        _emit_output(args.command, results, pretty=args.pretty)
        return 0

    if not group and not user:
        print("需要 --group <群聊名> 或 --user <用户名> 或 --interactive 或 --from-config",
              file=sys.stderr)
        return 1

    result = digester.digest(group=group or None, user=user or None,
                              time_range=time_range, force=force, dry_run=dry_run)
    _emit_output(args.command, [result], pretty=args.pretty)
    if result.get("status") == "success":
        print(f"✅ {result.get('message_count', 0)} 条消息 → {result.get('route', '')}",
              file=sys.stderr)
        return 0
    elif result.get("status") == "skipped":
        print(f"⏭️ {result.get('reason', '')}", file=sys.stderr)
        return 0
    else:
        print(f"❌ {result.get('error', '')}", file=sys.stderr)
        return 1


def _expand_file_list(files_expr: str):
    import glob
    paths = []
    for item in files_expr.split(","):
        item = item.strip()
        if not item:
            continue
        if any(c in item for c in "*?["):
            paths.extend(glob.glob(item, recursive=True))
        else:
            paths.append(item)
    seen = set()
    result = []
    for p in sorted(paths):
        abs_p = str(Path(p).resolve())
        if abs_p in seen:
            continue
        pp = Path(p)
        if pp.is_dir():
            continue
        if not pp.exists():
            import sys
            print(f"[警告] 文件不存在，已跳过: {p}", file=sys.stderr)
            continue
        seen.add(abs_p)
        result.append(p)
    return result


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

    payload = {"memory_sync": {"scanned": sync_result.get("scanned", 0), "skipped": sync_result.get("skipped", 0),
                                "corrections_added": sync_result.get("corrections_added", 0)},
               "memory_maintenance": maintenance_report, "scan": scan_info,
               "chunks": [{"source_name": cs.source_name, "chunk_count": cs.chunk_count,
                            "reused_documents": cs.build_stats.get("reused_documents", 0),
                            "rebuilt_documents": cs.build_stats.get("rebuilt_documents", 0)} for cs in chunk_summaries],
               "vector_index": vector_index_result,
               "wiki_discover": _auto_discover_wiki_for_daily(bundle, chunk_summaries),
               "wiki_update": wiki_update_result,
               "person_enrich": person_enrich_result}
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


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
    # Path 已在模块顶部导入

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
    from iris.app.cli.helpers import _build_agent_spec_payload
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


# ── 图文处理 ───────────────────────────────────────────────


def handle_process(args, bundle, logger) -> int:
    if not args.query:
        raise ValueError("process 需要 --query")
    image_paths = _parse_image_list(args.image)
    pipeline = ComplexInputPipeline(bundle)
    result = pipeline.process(args.query, file_paths=image_paths or None, output_path=args.output_file or None)
    _emit_output(args.command, result.to_dict(), pretty=args.pretty)
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


# ── 辅助函数 ─────────────────────────────────────────────

import re as _re

_VERSION_SUFFIX_PATTERN = _re.compile(r"_v\d+\.\d+\.\d+$")


def _strip_version_suffix(stem: str) -> str:
    """移除文件名中的版本号后缀，避免叠加。

    如 "asr_prompt_v1.0.0" → "asr_prompt"
    """
    return _VERSION_SUFFIX_PATTERN.sub("", stem)


def handle_usage_stats(args, bundle, logger) -> int:
    from iris.llm.usage_tracker import UsageTracker

    by = getattr(args, "by", "month") or "month"
    model_filter = getattr(args, "model", None) or None
    since = getattr(args, "since", None) or None

    tracker = UsageTracker(bundle.root / "data")
    rows = tracker.stats(by=by, model=model_filter, since=since)

    if args.pretty:
        if not rows:
            print("暂无用量数据（尚未发生任何 LLM 调用，或数据库路径有误）。")
            return 0

        period_label = {"day": "日期", "week": "周", "month": "月份", "year": "年份"}.get(by, by)
        header = f"{period_label:<14} {'调用次数':>8} {'输入Token':>11} {'输出Token':>11} {'合计Token':>11}"
        sep = "-" * len(header)
        print(f"\n{header}")
        print(sep)

        total_calls = total_pt = total_ct = 0
        for row in rows:
            calls = row["calls"]
            pt = row["prompt_tokens"]
            ct = row["completion_tokens"]
            tot = row["total_tokens"]
            print(f"{row['period']:<14} {calls:>8,} {pt:>11,} {ct:>11,} {tot:>11,}")
            total_calls += calls
            total_pt += pt
            total_ct += ct

        print(sep)
        print(f"{'合计':<14} {total_calls:>8,} {total_pt:>11,} {total_ct:>11,} {total_pt + total_ct:>11,}")

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
        "rows": rows,
    }, pretty=False)
    return 0


# ── 命令分发表 ─────────────────────────────────────────────
COMMAND_HANDLERS = {
    "check-config": handle_check_config,
    "route-model": handle_route_model,
    "scan-source": handle_scan_source,
    "build-chunks": handle_build_chunks,
    "build-vector-index": handle_build_vector_index,
    "search": handle_search,
    "ask": handle_ask,
    "build-report": handle_build_report,
    "build-mindmap": handle_build_mindmap,
    "build-biweekly-report": handle_build_biweekly_report,
    "discover-wiki": handle_discover_wiki,
    "discover-wiki-auto": handle_discover_wiki_auto,
    "build-wiki": handle_build_wiki,
    "build-wiki-nav": handle_build_wiki_nav,
    "wiki-pipeline": handle_wiki_pipeline,
    "wiki-lint": handle_wiki_lint,
    "wiki-update": handle_wiki_update,
    "enrich-persons": handle_enrich_persons,
    "build-asr-prompt": handle_build_asr_prompt,
    "deep-eval": handle_deep_eval,
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
    "process": handle_process,
    "transcribe-meeting": handle_transcribe_meeting,
    "batch-transcribe": handle_batch_transcribe,
    "daily-start": handle_daily_start,
    "secrets-set": handle_secrets_set,
    "secrets-list": handle_secrets_list,
    "secrets-delete": handle_secrets_delete,
    "feishu-doc-convert": handle_feishu_doc_convert,
    "chat-digest": handle_chat_digest,
    "build-graph": handle_build_graph,
    "usage-stats": handle_usage_stats,
}
