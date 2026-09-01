"""Wiki + ASR + 深度评估 命令处理器。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List

from iris.llm import LLMService
from iris.app.cli.helpers import _emit_output
from iris.utils.paths import resolve_data_path


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
    from iris.app.cli.helpers import _auto_discover_wiki

    result = _auto_discover_wiki(bundle, changed_count=999)
    _emit_output(args.command, result, pretty=args.pretty)
    return 0


def handle_build_wiki(args, bundle, logger) -> int:
    from iris.wiki import WikiGenerator
    from iris.taskpanel.reporter import TaskReporter

    generator = WikiGenerator(bundle)

    def _on_page(done: int, total: int) -> None:
        _tr.report_phase("build", f"生成 Wiki 页面 {done}/{total}",
                         progress=done / max(total, 1))

    with TaskReporter("build-wiki", command="build-wiki") as _tr:
        if args.review_file:
            items = _load_review_items(Path(args.review_file))
            result = generator.build_pages(items, write=args.write, overwrite=args.overwrite,
                                           backup=args.backup, progress_callback=_on_page)
            _emit_output(args.command, {"items": result.items}, pretty=args.pretty)
            return 0
        if args.batch_file:
            items = _load_batch_items(Path(args.batch_file))
            result = generator.build_pages(items, write=args.write, overwrite=args.overwrite,
                                           backup=args.backup, progress_callback=_on_page)
            _emit_output(args.command, {"items": result.items}, pretty=args.pretty)
            return 0

        title = args.title or args.query
        _tr.report_phase("build_page", f"生成单页：{args.page_type} {title}")
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
    from iris.wiki.asr import (
        TermExtractor, render_asr_prompt, determine_new_version,
        save_version, format_hotwords_file,
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

    _t0 = time.monotonic()  # 全流程计时起点
    mode = getattr(args, "asr_mode", "all") or "all"
    today = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    data_dir = bundle.root / "data"
    llm_service = LLMService(bundle)

    # 从配置构建领域背景描述（无配置时使用通用占位）
    from iris.wiki._constants import build_domain_context
    domain_context = build_domain_context(bundle.app)

    # ── Phase 1：LLM 热词提取 ────────────────────────────
    # prompt 模式也提取热词——供 Phase 3 优化器语境样例使用（但不落盘热词文件）
    hotwords: List[str] = []
    hotwords_file = ""
    if mode in ("all", "hotwords", "prompt"):
        print("[asr] Phase 1/3: LLM 热词提取...", file=sys.stderr)
        _t1 = time.monotonic()
        hotword_extractor = LLMHotwordExtractor(pages)
        max_hotwords = getattr(args, "max_hotwords", 490) or 490
        hotwords = hotword_extractor.extract(llm_service, max_hotwords=max_hotwords,
                                             domain_context=domain_context)
        print(f"[asr]   ... Phase 1 完成 ({time.monotonic() - _t1:.1f}s): "
              f"{len(hotwords)} 热词", file=sys.stderr)

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
            print(f"[asr] Phase 2/3: LLM 误识别生成（{len(terms)} 术语）...",
                  file=sys.stderr)
            _t2 = time.monotonic()
            terms = extractor.generate_misreadings(terms, llm_service,
                                                   domain_context=domain_context)
            total_mappings = sum(len(t.mis_asr) for t in terms)
            print(f"[asr]   ... Phase 2 完成 ({time.monotonic() - _t2:.1f}s): "
                  f"{total_mappings} 映射", file=sys.stderr)

            # ── 反馈反向优化：在 LLM 误识别生成之后应用 ──
            # 时序要求：提升映射 append 进 mis_asr 后不再被 generate_misreadings
            # 整体覆盖；僵尸淘汰面对的是已填充的规则；补充热词随后统一写盘
            _fb_removed, _fb_promoted, _fb_hotwords = 0, 0, 0
            from iris.wiki.asr.feedback import (
                load_corrections, build_feedback_recommendations,
                apply_feedback_optimizations,
            )
            feedback_path = data_dir / "asr_feedback.jsonl"
            if feedback_path.exists():
                try:
                    corrections = load_corrections(str(feedback_path))
                    if len(corrections) >= 50:
                        # 僵尸判定时间窗：仅上次部署词典中已有的规则参与淘汰，
                        # 防止本次新生成规则被误判为僵尸（生成→淘汰→再生成振荡）
                        history_rules = _load_history_replace_rules(data_dir)
                        recs = build_feedback_recommendations(
                            corrections, terms, hotwords,
                            min_samples=50, promote_threshold=3,
                            history_rules=history_rules,
                        )
                        # 应用优化
                        _fb_removed, _fb_promoted, _fb_hotwords = \
                            apply_feedback_optimizations(terms, hotwords, recs)
                        # 输出摘要
                        _parts = []
                        if _fb_removed:
                            _parts.append(f"淘汰 {_fb_removed} 条僵尸规则")
                        if _fb_promoted:
                            _parts.append(f"提升 {_fb_promoted} 条 LLM 发现")
                        if _fb_hotwords:
                            _parts.append(f"补充 {_fb_hotwords} 个热词")
                        if _parts:
                            print(
                                f"[asr] 📊 反馈反向优化（{len(corrections)} 条记录）: "
                                + ", ".join(_parts),
                                file=sys.stderr,
                            )
                        else:
                            print(
                                f"[asr] 📊 反馈分析完成（{len(corrections)} 条记录），"
                                f"无需优化",
                                file=sys.stderr,
                            )
                    else:
                        print(
                            f"[asr] ⏭ 反馈数据不足（{len(corrections)}<50 条），"
                            f"跳过反向优化",
                            file=sys.stderr,
                        )
                except Exception as _fb_exc:
                    print(
                        f"[asr] ⚠ 反馈分析失败（不影响主流程）: {_fb_exc}",
                        file=sys.stderr,
                    )

            max_mappings = getattr(args, "max_mappings", 2000) or 2000
            max_chars = getattr(args, "max_chars", 20) or 20
            # 从 profile 配置读取 max_mappings 覆盖（优先级：CLI 参数 > profile > 默认值）
            import json as _profile_json
            profile_path = resolve_data_path("config/asr_profiles.json")
            if profile_path.exists() and getattr(args, "max_mappings", None) is None:
                try:
                    with open(profile_path) as _pf:
                        _profiles = _profile_json.load(_pf)
                    _profile_name = getattr(args, "profile", "default") or "default"
                    _profile_cfg = _profiles.get(_profile_name, _profiles.get("default", {}))
                    _profile_max = _profile_cfg.get("max_mappings")
                    if _profile_max is not None:
                        max_mappings = int(_profile_max)
                except Exception as e:
                    logger.debug("加载 asr_profiles.json 中 max_mappings 失败，使用默认值: %s", e)
            replace_path = f"asr-replace-dict-{today}.json"
            if args.output_file and mode == "replace-dict":
                replace_path = args.output_file
            replace_dict_file = format_replace_dict(
                terms, bundle.root / "output" / replace_path,
                max_mappings=max_mappings, max_chars=max_chars,
            )

    # 热词文件写盘：在 feedback 补充热词之后统一写盘
    # （all 模式此时 hotwords 已含反馈补充；hotwords 模式无 Phase 2 直接写盘）
    if mode in ("all", "hotwords"):
        hotwords_path = f"asr-hotwords-{today}.txt"
        if args.output_file and mode != "all":
            hotwords_path = args.output_file
        hotwords_file = format_hotwords_file(
            hotwords, bundle.root / "output" / hotwords_path
        )

    # ── Phase 3：LLM Prompt 优化 ────────────────────────
    prompt = ""
    output_path = ""
    if mode in ("all", "prompt"):
        # terms 与 new_version 已在 Phase 2 填充（"all"/"prompt" 均经过 Phase 2）
        print("[asr] Phase 3/3: 校正提示词渲染...", file=sys.stderr)
        _t3 = time.monotonic()
        new_version.term_count = len(terms)
        new_version.wiki_page_count = len(pages)

        # 优化器渲染规则式 prompt（纯模板渲染，零 LLM 调用）
        optimizer = LLMPromptOptimizer()
        prompt = optimizer.optimize(hotwords, terms, domain_context=domain_context)
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

        print(f"[asr]   ... Phase 3 完成 ({time.monotonic() - _t3:.1f}s): "
              f"{len(prompt)} 字符", file=sys.stderr)

    # ── 部署到 vocotype（--deploy） ───────────────────────
    deployed: List[str] = []
    if getattr(args, "deploy", False):
        import shutil
        from datetime import datetime as _dt

        VOCO_DIR = os.environ.get(
            "IRIS_VOCOTYPE_DIR",
            os.path.expanduser("~/Library/Application Support/VocoType"),
        )
        voco_path = Path(VOCO_DIR)

        if voco_path.exists():
            # 备份
            backup_dir = resolve_data_path("output/") / "vocotype-backup" / _dt.now().strftime("%Y%m%d-%H%M%S")
            backup_dir.mkdir(parents=True, exist_ok=True)
            for fname in ("hotwords.txt", "postprocess.json", "ai_settings.json"):
                src = voco_path / fname
                if src.exists():
                    shutil.copy2(str(src), str(backup_dir / fname))
            deployed.append(f"备份: {backup_dir}")

            # 部署热词（合并手动热词）
            if hotwords_file and hotwords:
                merged = list(hotwords)
                manual_path = resolve_data_path("data/asr_manual_hotwords.txt")
                if manual_path.exists():
                    try:
                        manual_words = [
                            line.strip() for line in
                            manual_path.read_text(encoding="utf-8").splitlines()
                            if line.strip() and not line.strip().startswith("#")
                        ]
                        # 去重：保留手动词（可能不在 LLM 生成的列表中）
                        existing = set(merged)
                        added = 0
                        for w in manual_words:
                            if w not in existing:
                                merged.append(w)
                                existing.add(w)
                                added += 1
                        if added:
                            deployed.append(f"手动热词 +{added}")
                    except Exception as e:
                        logger.warning("合并手动热词失败 (%s): %s", manual_path, e)
                (voco_path / "hotwords.txt").write_text(
                    "\n".join(merged) + "\n", encoding="utf-8"
                )
                deployed.append(f"hotwords.txt ({len(merged)} 词)")

            # 写入 vocotype ai_settings.json：关闭 LLM 优化 + 清空替换词典
            ai_settings_path = voco_path / "ai_settings.json"
            if ai_settings_path.exists():
                try:
                    ai_settings = json.loads(ai_settings_path.read_text(encoding="utf-8"))
                    if "global" in ai_settings:
                        ai_settings["global"]["enabled"] = False
                    ai_settings_path.write_text(
                        json.dumps(ai_settings, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    deployed.append("ai_settings.json (LLM 优化已关闭)")
                except Exception as e:
                    logger.warning("写入 ai_settings.json 失败: %s", e)

            # 写入 postprocess.json：清空 replace_map
            pp_path = voco_path / "postprocess.json"
            if pp_path.exists():
                try:
                    pp = json.loads(pp_path.read_text(encoding="utf-8"))
                    pp["replace_map"] = {}
                    pp_path.write_text(
                        json.dumps(pp, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    deployed.append("postprocess.json (替换词典已清空)")
                except Exception as e:
                    logger.warning("写入 postprocess.json 失败: %s", e)

            # 部署到 Iris data/
            data_dir = resolve_data_path("data/")
            data_dir.mkdir(parents=True, exist_ok=True)
            if replace_dict_file:
                shutil.copy2(replace_dict_file, str(data_dir / "asr_replace_dict.json"))
                deployed.append("data/asr_replace_dict.json")
            if prompt and output_path:
                shutil.copy2(output_path, str(data_dir / "asr_prompt.md"))
                deployed.append("data/asr_prompt.md")
        else:
            deployed.append(f"⚠ vocotype 目录不存在: {VOCO_DIR}")

    # ── 总耗时汇总 ─────────────────────────────────────
    _elapsed = time.monotonic() - _t0
    _summary_parts = [f"总耗时 {_elapsed:.1f}s"]
    if hotwords:
        _summary_parts.append(f"热词 {len(hotwords)}")
    if terms:
        _summary_parts.append(f"术语 {len(terms)}")
    print(f"[asr] {' | '.join(_summary_parts)}", file=sys.stderr)
    print(file=sys.stderr)

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
    if deployed:
        payload["deployed"] = deployed

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


def _load_history_replace_rules(data_dir: Path) -> set:
    """加载上次部署的替换词典规则键集合（"误→正"），供僵尸规则时间窗判定。

    缺失/损坏 → 空集合（僵尸淘汰自动降级为不淘汰，安全默认）。
    """
    path = Path(data_dir) / "asr_replace_dict.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            f"{mis}→{correct}"
            for mis, correct in data.get("replace_map", {}).items()
        }
    except Exception:
        return set()


# ── asr-audit ──────────────────────────────────────────────

def handle_asr_audit(args, bundle, logger) -> int:
    """运行 ASR 覆盖分析和替换词典质量检查。"""
    from iris.wiki.context_loader import WikiContextLoader
    from iris.wiki.asr.coverage import (
        analyze_coverage, analyze_dict_quality,
        render_coverage_text, render_dict_quality_text,
    )

    wiki_root = bundle.wiki.get("wiki_root", "")
    if not wiki_root or not os.path.isdir(wiki_root):
        _emit_output("asr-audit", {"error": "Wiki 根目录未配置或不存在"}, pretty=args.pretty)
        return 1

    loader = WikiContextLoader(wiki_root)
    pages = loader.load_pages(sort_order=["person", "concept", "project", "domain"])

    # 加载最新热词文件
    hotwords: List[str] = []
    output_dir = Path(bundle.app.get("data_dir", os.getcwd())) / "output" / "asr-modify"
    hotword_files = sorted(output_dir.glob("asr-hotwords-*.txt"), reverse=True)
    if hotword_files:
        hotwords = [line.strip() for line in hotword_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]

    # 加载最新替换词典
    terms: List = []
    dict_files = sorted(output_dir.glob("asr-replace-dict-*.json"), reverse=True)
    if dict_files:
        with open(dict_files[0]) as f:
            data = json.load(f)
        replace_map = data.get("replace_map", {})
        from iris.wiki.asr._types import AsrTerm
        for wrong, right in replace_map.items():
            terms.append(AsrTerm(term=right, category="domain_term", context="", mis_asr=[wrong]))

    # 覆盖分析
    cov_report = analyze_coverage(hotwords, pages)
    cov_text = render_coverage_text(cov_report)

    # 词典质量
    dict_report = analyze_dict_quality(terms)
    dict_text = render_dict_quality_text(dict_report)

    result = {
        "coverage": {
            "hotword_count": cov_report.hotword_count,
            "persons": f"{cov_report.persons_covered}/{cov_report.persons_total}",
            "projects": f"{cov_report.projects_covered}/{cov_report.projects_total}",
            "concepts": f"{cov_report.concepts_covered}/{cov_report.concepts_total}",
            "noise_count": len(cov_report.noise_words),
            "slot_efficiency": f"{cov_report.slot_efficiency:.1%}",
        },
        "dict_quality": {
            "total_rules": dict_report.total_rules,
            "format_errors": len(dict_report.format_errors),
            "conflicts": len(dict_report.conflicting_pairs),
        },
    }

    if args.pretty:
        print(cov_text, file=sys.stderr)
        print("", file=sys.stderr)
        print(dict_text, file=sys.stderr)
    _emit_output("asr-audit", result, pretty=args.pretty)

    return 0


# ── asr-corrector ─────────────────────────────────────────

def handle_asr_corrector(args, bundle, logger) -> int:
    """启动 ASR 实时校正守护进程。"""
    import json as _json
    from pathlib import Path as _Path

    from iris.wiki.asr.corrector import AsrCorrector
    from iris.llm.service import LLMService

    mode = getattr(args, "correct_mode", "full") or "full"
    profile_name = getattr(args, "profile", "default") or "default"

    # 加载 profile 配置
    profile_config: dict = {}
    profile_path = resolve_data_path("config/asr_profiles.json")
    if profile_path.exists():
        try:
            with open(profile_path) as f:
                profiles = _json.load(f)
            profile_config = profiles.get(profile_name, profiles.get("default", {}))
        except Exception as e:
            logger.warning("加载 asr_profiles.json 失败，使用空 profile: %s", e)

    # 加载替换词典
    dict_path = profile_config.get(
        "replace_dict",
        str(resolve_data_path("data/asr_replace_dict.json")),
    )
    replace_dict: dict = {}
    if _Path(dict_path).exists():
        try:
            with open(dict_path) as f:
                data = _json.load(f)
            replace_dict = data.get("replace_map", {})
        except Exception:
            _emit_output("asr-corrector", {"error": f"替换词典加载失败: {dict_path}"}, pretty=args.pretty)
            return 1
    else:
        _emit_output("asr-corrector", {"error": f"替换词典不存在: {dict_path}，请先运行 build-asr-prompt --deploy"}, pretty=args.pretty)
        return 1

    # 加载 LLM Prompt
    prompt_path = profile_config.get(
        "llm_prompt",
        str(resolve_data_path("data/asr_prompt.md")),
    )
    llm_prompt = ""
    if _Path(prompt_path).exists():
        llm_prompt = _Path(prompt_path).read_text(encoding="utf-8")

    # LLM Provider（仅 full 模式）
    provider = None
    if mode == "full" and llm_prompt:
        try:
            service = LLMService(bundle)
            provider = service.get_provider()
        except Exception as e:
            print(f"[warn] LLM Provider 初始化失败: {e}", file=sys.stderr)
            print("[warn] 将降级为 fast 模式（仅替换词典）", file=sys.stderr)
            mode = "fast"

    # 反馈路径
    feedback_path = str(resolve_data_path("data/asr_feedback.jsonl"))

    # 近期上下文配置
    context_window_size = profile_config.get("context_window_size", 5)
    context_expire_minutes = profile_config.get("context_expire_minutes", 10)
    context_ab = getattr(args, "context_ab", False)

    # LLM 降级链总超时（从 profile 读取，默认 8000ms）
    _llm_profile = profile_config.get("llm", {}) if isinstance(profile_config, dict) else {}
    llm_timeout_ms = _llm_profile.get("timeout_ms", 8000) if isinstance(_llm_profile, dict) else 8000

    # ASR 文本长度上限（优先级：CLI 参数 > profile > 默认 500），
    # 覆盖长语音场景（corrector 与 meeting-live-assistant 对齐可放宽）
    max_asr_length = getattr(args, "max_asr_length", None)
    if max_asr_length is None:
        max_asr_length = profile_config.get("max_asr_length", 500)
    max_asr_length = int(max_asr_length)

    # 启动校正引擎
    corrector = AsrCorrector(
        replace_dict=replace_dict,
        llm_prompt=llm_prompt,
        mode=mode,
        feedback_path=feedback_path,
        context_window_size=context_window_size,
        context_expire_minutes=context_expire_minutes,
        context_ab=context_ab,
        llm_timeout_ms=llm_timeout_ms,
        max_asr_length=max_asr_length,
    )

    if provider:
        corrector.set_provider(provider)
        # 注入 ASR 独立熔断器：更低阈值（2 次失败即熔断，30s 后半开）
        # 实时场景不能容忍像批量任务那样连试 5 次才熔断
        try:
            from iris.llm.provider import _CircuitBreaker as _CB
            _asr_breaker = _CB(threshold=2, reset_after=30.0)
            provider.set_circuit_breaker(_asr_breaker)
            print("[Iris] ASR 熔断器: threshold=2 reset=30s (独立实例)",
                  file=sys.stderr)
        except Exception:
            pass  # 降级：使用 provider 默认的全局熔断器
        # 同时注入 LLMService（推荐路径：享受缓存、熔断器）
        try:
            corrector.set_llm_service(service)
        except Exception:
            pass  # LLMService 不可用时降级为直接 provider 调用

    if prompt_path:
        corrector.set_prompt_path(str(_Path(prompt_path)))
    if dict_path:
        corrector.set_dict_path(str(_Path(dict_path)))

    corrector.run_forever()
    return 0


# ── asr-report ───────────────────────────────────────────

def handle_asr_report(args, bundle, logger) -> int:
    """手动纠错：从剪贴板读取 ASR 原文，用户提供正确文本，写入 feedback。"""
    import subprocess as _sp
    from iris.wiki.asr.feedback import save_correction
    from iris.wiki.asr._types import AsrCorrection

    # 从位置参数获取正确文本
    correct_text = getattr(args, "notes", "") or " ".join(getattr(args, "extra", []) or [])
    if not correct_text:
        _emit_output("asr-report", {"error": "用法: iris3 asr-report --notes '正确的文本'"}, pretty=False)
        return 1

    # 读取剪贴板中的 ASR 原文
    try:
        raw_text = _sp.check_output(["pbpaste"], text=True).strip()
    except Exception:
        _emit_output("asr-report", {"error": "无法读取剪贴板"}, pretty=False)
        return 1

    if not raw_text:
        _emit_output("asr-report", {"error": "剪贴板为空"}, pretty=False)
        return 1

    # 写入 feedback
    record = AsrCorrection(
        timestamp=datetime.now(timezone.utc).isoformat(),
        raw_text=raw_text,
        fast_corrected=correct_text,
        full_corrected=correct_text,
        mode="manual",
        corrections_applied=[f"[手动] {raw_text}→{correct_text}"],
    )
    save_correction(record, str(resolve_data_path("data/asr_feedback.jsonl")))

    result = {"ok": True, "raw": raw_text, "corrected": correct_text}
    _emit_output("asr-report", result, pretty=args.pretty)
    return 0


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
    "asr-corrector": handle_asr_corrector,
    "asr-audit": handle_asr_audit,
    "asr-report": handle_asr_report,
    "deep-eval": handle_deep_eval,
}
