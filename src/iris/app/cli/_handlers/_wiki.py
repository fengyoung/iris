"""Wiki + ASR + 深度评估 命令处理器。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

from iris.llm import LLMService
from iris.app.cli.helpers import _emit_output


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


# ── 通用辅助函数 ─────────────────────────────────────────

import re as _re

_VERSION_SUFFIX_PATTERN = _re.compile(r"_v\d+\.\d+\.\d+$")


def _strip_version_suffix(stem: str) -> str:
    """移除文件名中的版本号后缀，避免叠加。

    如 "asr_prompt_v1.0.0" → "asr_prompt"
    """
    return _VERSION_SUFFIX_PATTERN.sub("", stem)


# ── 命令映射 ─────────────────────────────────────────────

WIKI_HANDLERS = {
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
}
