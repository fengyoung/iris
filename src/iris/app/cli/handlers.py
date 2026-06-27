"""CLI 命令处理器 — Phase 2.1 版本。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from iris.complex_input import ComplexInputPipeline
from iris.config import load_config_bundle
from iris.ingest import MarkdownChunker, MarkdownScanner
from iris.llm import EnvironmentConfiguredLLMProvider
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
    if image_paths:
        from iris.complex_input import ComplexInputPipeline
        pipeline = ComplexInputPipeline(bundle)
        result = pipeline.process(args.query, image_paths=image_paths)
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
        from pathlib import Path
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


def handle_build_biweekly_report(args, bundle, logger) -> int:
    from iris.analysis import AnalysisReportService
    from datetime import datetime

    service = AnalysisReportService(bundle)
    query = getattr(args, "query", "") or ""
    result = service.build_biweekly_report(query=query, mode=getattr(args, "mode", "llm"))
    payload = result.to_dict()

    # 确定输出路径
    output = args.output_file
    if not output and getattr(args, "to_source", False):
        # 自动生成文件名：双周报-w{week}-{name}-{date}.md
        cfg = bundle.app.get("biweekly_report", {})
        author = cfg.get("author_name", "")
        today = datetime.now()
        _, week, _ = today.isocalendar()
        date_str = today.strftime("%Y%m%d")
        filename = f"双周报-w{week:02d}-{author}-{date_str}.md" if author else f"双周报-w{week:02d}-{date_str}.md"

        # 输出到 SOURCE/06-我的周报/
        from iris.app.transcribe_meeting.pipeline import TranscribeMeetingPipeline
        pipeline = TranscribeMeetingPipeline(bundle)
        source_dir = pipeline._resolve_source_dir().parent / "06-我的周报"
        output = str(source_dir / filename)

    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.markdown, encoding="utf-8")
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
    from pathlib import Path

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
            import json as _json
            print(_json.dumps(fix_result, ensure_ascii=False, indent=2))
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


def handle_build_asr_prompt(args, bundle, logger) -> int:
    """从 Wiki 知识库构建 ASR 校正系统提示词（供 vocotype 等工具的 LLM 校正环节使用）。

    流程：
    1. 加载 Wiki 页面 → 规则提取术语（纯本地）
    2. 调用 base_model 批量生成 ASR 误识别映射（一次 LLM 调用）
    3. 渲染为紧凑的系统提示词
    4. 版本管理（三段式版本号，基于内容指纹自动检测变化）
    """
    from pathlib import Path as _Pt
    from iris.wiki.context_loader import WikiContextLoader
    from iris.wiki.term_extractor import (
        TermExtractor, render_asr_prompt, determine_new_version,
        load_version, save_version,
    )
    from iris.llm import EnvironmentConfiguredLLMProvider

    # 1. 校验 Wiki 配置
    if not bundle.wiki or not bundle.wiki.get("wiki_root"):
        _emit_output(args.command, {"error": "Wiki 配置缺失"}, pretty=args.pretty)
        return 1
    wiki_root = _Pt(bundle.wiki["wiki_root"]).resolve()
    if not wiki_root.exists():
        _emit_output(args.command, {"error": "Wiki 根目录不存在"}, pretty=args.pretty)
        return 1

    # 2. 加载所有 Wiki 页面（人名和术语优先，去重时保留）
    loader = WikiContextLoader(wiki_root)
    sort_order = ["person", "concept", "project", "domain"]
    pages = loader.load_pages(sort_order=sort_order)

    if not pages:
        _emit_output(args.command, {"error": "Wiki 目录为空，无页面可提取"}, pretty=args.pretty)
        return 1

    # 3. 阶段 1：规则提取术语
    extractor = TermExtractor(pages)
    terms = extractor.extract_terms()

    # 4. 版本判定
    data_dir = bundle.root / "data"
    bump = getattr(args, "bump", "auto") or "auto"
    new_version = determine_new_version(pages, data_dir, bump=bump)

    # auto 模式下指纹无变化则跳过 LLM 调用
    if bump == "auto":
        old = load_version(data_dir)
        if old and old.fingerprint == new_version.fingerprint:
            _emit_output(args.command, {
                "version": old.version,
                "message": "Wiki 内容无变化，prompt 无需更新",
                "wiki_page_count": len(pages),
                "term_count": len(terms),
            }, pretty=args.pretty)
            return 0

    # 填充版本中的术语计数
    new_version.term_count = len(terms)
    new_version.wiki_page_count = len(pages)

    # 5. 阶段 2：LLM 批量生成误识别映射
    provider = EnvironmentConfiguredLLMProvider(bundle)
    terms = extractor.generate_misreadings(terms, provider)

    # 6. 渲染 prompt
    output_format = getattr(args, "output_format", "standard") or "standard"
    # "md" / "docx" 是 --output-format 的默认值，对 asr-prompt 映射到 standard
    if output_format in ("md", "docx"):
        output_format = "standard"
    prompt = render_asr_prompt(terms, new_version, output_format=output_format)

    # 7. 输出到文件
    if args.output_file:
        out = _Pt(args.output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(prompt, encoding="utf-8")

    # 8. 持久化版本
    save_version(data_dir, new_version)

    _emit_output(args.command, {
        "version": new_version.version,
        "fingerprint": new_version.fingerprint[:8],
        "wiki_page_count": new_version.wiki_page_count,
        "term_count": new_version.term_count,
        "prompt_chars": len(prompt),
        "terms_by_category": {
            cat: len([t for t in terms if t.category == cat])
            for cat in ["person", "concept", "project", "domain_term"]
        },
    }, pretty=args.pretty)
    if args.pretty:
        print(f"\n{prompt[:2000]}")
    return 0


# ── 辅助函数（wiki 相关） ─────────────────────────────


def _load_batch_items(path: Path):
    import json
    import sys
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
        if "query" not in payload:
            print(f"[警告] 第 {idx} 行缺少必填字段 query，已跳过", file=sys.stderr)
            continue
        from iris.wiki import BatchWikiItem
        items.append(BatchWikiItem(query=payload["query"], title=payload.get("title", payload["query"]),
                                    page_type=payload.get("page_type", "domain")))
    return items


def _load_review_items(path: Path):
    import json
    import sys
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
        if not payload.get("selected", False):
            continue
        if "query" not in payload:
            print(f"[警告] 第 {idx} 行缺少必填字段 query，已跳过", file=sys.stderr)
            continue
        from iris.wiki import BatchWikiItem
        items.append(BatchWikiItem(query=payload["query"], title=payload.get("title", payload["query"]),
                                    page_type=payload.get("page_type", "domain")))
    return items


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

    metadata_root = bundle.root / "data" / "metadata"
    sources = bundle.data_source.get("sources", {})
    target_sources = {args.source: sources[args.source]} if args.source and args.source in sources else sources
    results = []
    for source_name in target_sources:
        summary_path = metadata_root / f"{source_name}_chunk_summary.json"
        if not summary_path.exists():
            results.append({"source": source_name, "status": "skipped", "reason": "chunk_summary 不存在"})
            continue
        import json as _json
        payload = _json.loads(summary_path.read_text(encoding="utf-8"))
        from iris.ingest.chunker import ChunkRecord
        chunks = [ChunkRecord(**item) for item in payload["chunks"]]
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
        print("未匹配到任何文件", file=__import__('sys').stderr)
        return 1
    pipeline = TranscribeMeetingPipeline(bundle)
    result = pipeline.run_batch(file_paths, output_dir=args.output_dir or None, whisper_model=args.whisper_model, force_retranscribe=args.force)
    _emit_output(args.command, result, pretty=args.pretty)
    return 0 if result["failed"] == 0 else 1


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
        print("需要 --url <文档URL> 或 --from-config", file=__import__('sys').stderr)
        return 1

    _emit_output(args.command, results, pretty=args.pretty)
    # 汇总统计
    success = sum(1 for r in results if r.get("status") == "success")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    errors = sum(1 for r in results if r.get("status") == "error")
    if success:
        print(f"✅ {success} 成功, {skipped} 跳过, {errors} 失败", file=__import__('sys').stderr)
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
            print("未找到可用的群聊", file=__import__('sys').stderr)
            return 1
        print("📋 可提取的聊天目标：", file=__import__('sys').stderr)
        for i, g in enumerate(groups, 1):
            print(f"  {i}. {g['name']}（{g.get('member_count', 0)} 人）", file=__import__('sys').stderr)
        print("请输入序号（逗号分隔多选，留空全部）：", end=" ", file=__import__('sys').stderr)
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
        print(f"✅ {success}/{len(results)} 成功", file=__import__('sys').stderr)
        return 0

    if from_config:
        results = digester.digest_from_config(force=force, dry_run=dry_run)
        _emit_output(args.command, results, pretty=args.pretty)
        return 0

    if not group and not user:
        print("需要 --group <群聊名> 或 --user <用户名> 或 --interactive 或 --from-config",
              file=__import__('sys').stderr)
        return 1

    result = digester.digest(group=group or None, user=user or None,
                              time_range=time_range, force=force, dry_run=dry_run)
    _emit_output(args.command, [result], pretty=args.pretty)
    if result.get("status") == "success":
        print(f"✅ {result.get('message_count', 0)} 条消息 → {result.get('route', '')}",
              file=__import__('sys').stderr)
        return 0
    elif result.get("status") == "skipped":
        print(f"⏭️ {result.get('reason', '')}", file=__import__('sys').stderr)
        return 0
    else:
        print(f"❌ {result.get('error', '')}", file=__import__('sys').stderr)
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
    from iris.ingest import MarkdownScanner, MarkdownChunker
    from iris.wiki import CandidateDiscovery, WikiNavigationBuilder, append_changelog

    # 1. 记忆同步
    from iris.app.cli.helpers import _run_sync_memory
    sync_result = _run_sync_memory(bundle)
    if sync_result.get("synced"):
        logger.log("sync_memory", {"corrections_added": sync_result.get("corrections_added", 0)})

    # 2. 记忆自治维护
    lifecycle = MemoryLifecycle(bundle)
    maintenance_report = lifecycle.maintenance()

    # 3. 扫描 + 切块
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
        scan_info.append({"source_name": scan_summary.source_name, "document_count": scan_summary.document_count})

    # 3.5 向量索引增量更新（若 embedding 已启用）
    try:
        from iris.retrieval.embedder import EmbedderError, build_embedder_from_config
        emb_cfg = bundle.llm.get("embedding", {})
        if emb_cfg.get("enabled", False):
            from iris.retrieval.vector_index import VectorIndex, build_vector_index
            embedder = build_embedder_from_config(bundle.llm)
            if embedder:
                import json as _vi_json
                ds_name = (bundle.data_source or {}).get("default_source", "work_docs_main")
                summary_path = bundle.root / "data" / "metadata" / f"{ds_name}_chunk_summary.json"
                if summary_path.exists():
                    vi_payload = _vi_json.loads(summary_path.read_text(encoding="utf-8"))
                    from iris.ingest.chunker import ChunkRecord as _ChunkRecord
                    vi_chunks = [_ChunkRecord(**item) for item in vi_payload["chunks"]]
                    index_path = bundle.root / "data" / "metadata" / f"{ds_name}_vector_index"
                    existing = VectorIndex(index_path)
                    existing.load()
                    idx = build_vector_index(ds_name, vi_chunks, embedder, index_path, existing_index=existing)
                    vector_index_result = {"status": "ok", "indexed": idx.size()}
                else:
                    vector_index_result = {"status": "skipped", "reason": "no_chunk_summary"}
            else:
                vector_index_result = {"status": "skipped", "reason": "embedder_not_configured"}
        else:
            vector_index_result = {"status": "skipped", "reason": "embedding_disabled"}
    except Exception as _vi_exc:
        vector_index_result = {"status": "error", "reason": str(_vi_exc)}

    # 4. Wiki 自动发现 + 索引维护 + 增量更新
    total_rebuilt = sum(cs.build_stats.get("rebuilt_documents", 0) for cs in chunk_summaries)
    from iris.app.cli.helpers import _auto_discover_wiki
    wiki_discover_result = _auto_discover_wiki(bundle, changed_count=total_rebuilt)
    if bundle.wiki:
        from iris.wiki.generator import WikiGenerator
        wiki_update_result = WikiGenerator(bundle).update_all_pages(top_k=4)
        builder = WikiNavigationBuilder(bundle)
        builder.build(write=True)
        append_changelog(Path(bundle.wiki["wiki_root"]), "daily-start 自动维护")

    payload = {"memory_sync": {"scanned": sync_result.get("scanned", 0), "skipped": sync_result.get("skipped", 0),
                                "corrections_added": sync_result.get("corrections_added", 0)},
               "memory_maintenance": maintenance_report, "scan": scan_info,
               "chunks": [{"source_name": cs.source_name, "chunk_count": cs.chunk_count,
                            "reused_documents": cs.build_stats.get("reused_documents", 0),
                            "rebuilt_documents": cs.build_stats.get("rebuilt_documents", 0)} for cs in chunk_summaries],
               "vector_index": vector_index_result,
               "wiki_discover": wiki_discover_result,
               "wiki_update": wiki_update_result}
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


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
    result = pipeline.process(args.query, image_paths=image_paths or None, output_path=args.output_file or None)
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
    "build-asr-prompt": handle_build_asr_prompt,
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
}
