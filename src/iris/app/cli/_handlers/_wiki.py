"""Wiki + ASR + 深度评估 命令处理器。"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

from iris.llm import LLMService
from iris.app.cli.helpers import _emit_output
from iris.core.exceptions import IrisValueError
from iris.utils.paths import get_project_root, resolve_data_path
from iris.wiki.asr.artifacts import (
    HOTWORD_LIMIT,
    VOCOTYPE_BACKUP_DIRNAME,
    asr_bak_dir,
    prune_bak,
)
from iris.utils.shared import atomic_write_json, atomic_write_text


# ── Wiki 命令 ────────────────────────────────────────────


def handle_discover_wiki(args, bundle, logger) -> int:
    from iris.wiki import CandidateDiscovery

    discovery = CandidateDiscovery(bundle)
    candidates = discovery.discover(limit=args.limit, incremental=args.incremental)
    payload: Dict[str, Any] = {"items": [{"title": item.title, "page_type": item.page_type, "query": item.query,
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
        payload: Dict[str, Any] = {"page_type": draft.page_type, "title": draft.title, "slug": draft.slug,
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

    --mode 支持四种模式（产物统一落在 output/asr-corrector-bak/ 存档目录）：
      all          全流程（热词 + 替换词典 + 校正提示词）
      hotwords     仅热词提取 → asr-hotwords-{date}-{time}.txt
      replace-dict 仅替换词典 → asr-replace-dict-{date}-{time}.json
      prompt       仅校正提示词 → asr-prompt-v{ver}-{date}-{time}.md

    存档只保留最近若干份，更早的移入同目录 legacy/（见 asr.artifacts）。

    实际编排在 ``run_build_asr_prompt``（供 daily-start 复用），本函数只负责
    参数解析与输出。
    """
    try:
        payload = run_build_asr_prompt(args, bundle, logger)
    except IrisValueError as exc:
        _emit_output(args.command, {"error": str(exc)}, pretty=args.pretty)
        return 1
    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


def run_build_asr_prompt(args, bundle, logger) -> Dict[str, Any]:
    """构建 ASR 三件套并（可选）部署。返回输出 payload，不做任何打印。

    前置校验失败抛 ``IrisValueError``——调用方（CLI handler / daily-start）
    各自决定如何呈现，避免这里直接往 stdout 写 JSON 污染上层输出。

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
        raise IrisValueError("Wiki 配置缺失")
    wiki_root = Path(bundle.wiki["wiki_root"]).resolve()
    if not wiki_root.exists():
        raise IrisValueError("Wiki 根目录不存在")

    loader = WikiContextLoader(wiki_root)
    sort_order = ["person", "concept", "project", "domain"]
    pages = loader.load_pages(sort_order=sort_order)

    if not pages:
        raise IrisValueError("Wiki 目录为空，无页面可提取")

    _t0 = time.monotonic()  # 全流程计时起点
    mode = getattr(args, "asr_mode", "all") or "all"
    today = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # 整条 ASR 链路（产物 / 存档 / 版本状态 / data 副本）统一以 bundle.root 为基准：
    # resolve_data_path 走的是 get_project_root()，读不到 --project-root，两者
    # 混用会在显式指定 --project-root 时把产物与生效点裂到两个根下。
    data_dir = Path(bundle.root) / "data"
    bak_dir = asr_bak_dir(bundle.root)
    bak_dir.mkdir(parents=True, exist_ok=True)
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
        # 敏感热词守卫：下游 hotwords_to_terms / 写盘 / --deploy 均消费此列表
        hotwords = _drop_sensitive_hotwords(hotwords, "Phase 1")
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

            _apply_asr_feedback(terms, hotwords, data_dir)
            # 反馈反向优化会就地向 hotwords 追加热词，完全绕过 Phase 1 守卫，
            # 且 --deploy 读的是内存列表而非落盘文件——此处必须再滤一次。
            hotwords = _drop_sensitive_hotwords(hotwords, "反馈补充")

            max_mappings = _resolve_max_mappings(args, logger)
            max_chars = getattr(args, "max_chars", 20) or 20
            replace_path = f"asr-replace-dict-{today}.json"
            if args.output_file and mode == "replace-dict":
                replace_path = args.output_file
            replace_dict_file = format_replace_dict(
                terms, str(bak_dir / replace_path),
                max_mappings=max_mappings, max_chars=max_chars,
            )

    # 热词文件写盘：在 feedback 补充热词之后统一写盘
    # （all 模式此时 hotwords 已含反馈补充；hotwords 模式无 Phase 2 直接写盘）
    if mode in ("all", "hotwords"):
        hotwords_path = f"asr-hotwords-{today}.txt"
        if args.output_file and mode != "all":
            hotwords_path = args.output_file
        hotwords_file = format_hotwords_file(
            hotwords, str(bak_dir / hotwords_path)
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
        output_path = _write_asr_prompt(prompt, args.output_file, new_version.version,
                                        bak_dir / f"asr-prompt-v{new_version.version}-{today}.md")

        # 持久化版本
        new_version.prompt_text = prompt
        save_version(data_dir, new_version)

        print(f"[asr]   ... Phase 3 完成 ({time.monotonic() - _t3:.1f}s): "
              f"{len(prompt)} 字符", file=sys.stderr)

    # ── 部署到 vocotype（--deploy） ───────────────────────
    deployed: List[str] = []
    if getattr(args, "deploy", False):
        deployed = _deploy_asr_to_vocotype(
            hotwords, hotwords_file, replace_dict_file, prompt, output_path, logger,
            bak_dir=bak_dir, data_dir=data_dir)

    # ── 存档裁剪（保留最近 N 份，更早移入 legacy/） ──────
    pruned = prune_bak(bak_dir)
    if pruned:
        print(f"[asr] 存档裁剪：{len(pruned)} 份旧产物移入 {bak_dir.name}/legacy/",
              file=sys.stderr)

    # ── 总耗时汇总 ─────────────────────────────────────
    _summary_parts = [f"总耗时 {time.monotonic() - _t0:.1f}s"]
    if hotwords:
        _summary_parts.append(f"热词 {len(hotwords)}")
    if terms:
        _summary_parts.append(f"术语 {len(terms)}")
    print(f"[asr] {' | '.join(_summary_parts)}", file=sys.stderr)
    print(file=sys.stderr)

    # ── 输出报告 ─────────────────────────────────────────
    return _asr_prompt_payload(
        hotwords, hotwords_file, terms, replace_dict_file,
        prompt, output_path, new_version if prompt else None, deployed)


# ── daily-start 用的 ASR 资源刷新 ───────────────────────


def refresh_asr_resources(bundle, logger) -> dict:
    """Wiki 有变化时重建 ASR 三件套 → 部署 → 重启 VocoType。

    供 daily-start 自动调用。三件事按序发生，任一步失败都显式记账、不影响
    daily-start 其余阶段：

      1. **指纹增量**：Wiki 页面指纹与上次生成的版本一致则整体跳过。否则每天
         白跑 14 次 LLM 调用，且替换词典会被逐日重写、校正行为持续漂移。
      2. **生成 + 部署**（``--mode all --deploy``）。
      3. **重启 VocoType**：热词表只在 App 启动时加载，不重启等于没部署。

    重启失败**不回滚**：配置本身是好的，回滚反而丢掉刚生成的新产物；报告里
    给出备份路径供人工处置。
    """
    from iris.wiki.asr._vocotype import restart as restart_vocotype
    from iris.wiki.asr.version import compute_input_fingerprint, load_version

    wiki_root_str = (bundle.wiki or {}).get("wiki_root", "") if bundle.wiki else ""
    if not wiki_root_str:
        return {"status": "skipped", "reason": "Wiki 未配置"}
    wiki_root = Path(wiki_root_str)
    if not wiki_root.is_dir():
        return {"status": "skipped", "reason": "Wiki 目录不存在"}

    from iris.wiki.context_loader import WikiContextLoader

    data_dir = Path(bundle.root) / "data"
    pages = WikiContextLoader(wiki_root).load_pages(
        sort_order=["person", "concept", "project", "domain"])
    if not pages:
        return {"status": "skipped", "reason": "Wiki 目录为空，无页面可提取"}

    # 含手动热词：改 data/asr_manual_hotwords.txt 也应触发重建（与 determine_new_version 同源）
    fingerprint = compute_input_fingerprint(pages, data_dir)
    stored = load_version(data_dir)
    if stored is not None and stored.fingerprint == fingerprint:
        return {"status": "skipped",
                "reason": f"Wiki 未变化（指纹 {fingerprint}，{len(pages)} 页），跳过重建",
                "fingerprint": fingerprint, "pages": len(pages)}

    args = SimpleNamespace(command="build-asr-prompt", asr_mode="all", deploy=True,
                           output_file="", bump="auto", max_hotwords=490,
                           max_mappings=None, max_chars=20, pretty=False)
    started = time.monotonic()
    result = run_build_asr_prompt(args, bundle, logger)
    elapsed = time.monotonic() - started

    restart_result = restart_vocotype()
    payload: Dict[str, Any] = {
        "status": "ok" if restart_result["status"] in ("restarted", "not_running")
                  else "deploy_only",
        "fingerprint": fingerprint,
        "pages": len(pages),
        "previous_fingerprint": stored.fingerprint if stored else "",
        "version": result.get("version", ""),
        "hotword_count": result.get("hotword_count", 0),
        "replace_mapping_count": result.get("replace_mapping_count", 0),
        "deployed": result.get("deployed", []),
        "vocotype": restart_result,
        "elapsed_seconds": round(elapsed, 1),
    }
    if restart_result["status"] in ("quit_failed", "start_failed"):
        # 显式告警：配置已换但 App 还开着旧热词表，用户必须知道。
        # not_running 不告警——App 没开，下次启动自会读到新配置。
        payload["warning"] = (f"VocoType 未重启（{restart_result['detail']}），"
                              f"新热词表尚未生效")
    return payload


# ── build-asr-prompt 子步骤 ─────────────────────────────


def _write_asr_prompt(prompt: str, output_file: str, version: str, auto_path: Path) -> str:
    """写校正提示词：--output-file 时在 stem 后附 _v{ver}；否则写 auto_path。返回实际路径。"""
    if output_file:
        out = Path(output_file)
        out = out.with_name(f"{_strip_version_suffix(out.stem)}_v{version}{out.suffix}")
    else:
        out = auto_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt, encoding="utf-8")
    return str(out)


def _asr_prompt_payload(hotwords: List[str], hotwords_file: str, terms: List,
                        replace_dict_file: str, prompt: str, output_path: str,
                        new_version, deployed: List[str]) -> Dict[str, Any]:
    """build-asr-prompt 输出报告。"""
    payload: Dict[str, Any] = {}
    if hotwords_file:
        payload["hotwords_file"] = hotwords_file
        payload["hotword_count"] = len(hotwords)
    if replace_dict_file:
        payload["replace_dict_file"] = replace_dict_file
        if terms:
            payload["replace_mapping_count"] = sum(len(t.mis_asr) for t in terms)
    if prompt and new_version is not None:
        payload["version"] = new_version.version
        payload["output_file"] = output_path
        payload["fingerprint"] = new_version.fingerprint[:8]
        payload["prompt_chars"] = len(prompt)
    if deployed:
        payload["deployed"] = deployed
    return payload


def _drop_sensitive_hotwords(hotwords: List[str], stage: str) -> List[str]:
    """剔除敏感热词并回报。

    热词链路有两处来源（Phase 1 LLM 提取、_apply_asr_feedback 反馈追加），
    且 ``--deploy`` 直接消费内存列表而非落盘文件，故两处都需过滤。

    只打印命中的敏感词类别、不打印完整词面——避免把新泄漏词写进日志。
    """
    from iris.wiki._sensitive import filter_sensitive_terms

    def _report(term: str, matched: str) -> None:
        print(f"[asr] ⛔ 剔除敏感热词（命中「{matched}」）", file=sys.stderr)

    kept = filter_sensitive_terms(hotwords, on_drop=_report)
    if len(kept) != len(hotwords):
        print(f"[asr]   {stage}: 剔除 {len(hotwords) - len(kept)} 个敏感热词",
              file=sys.stderr)
    return kept


def _apply_asr_feedback(terms: List, hotwords: List[str], data_dir: Path) -> None:
    """反馈反向优化：在 LLM 误识别生成之后应用（就地修改 terms / hotwords）。

    时序要求：提升映射 append 进 mis_asr 后不再被 generate_misreadings
    整体覆盖；僵尸淘汰面对的是已填充的规则；补充热词随后统一写盘。
    """
    from iris.wiki.asr.feedback import (
        load_corrections, build_feedback_recommendations,
        apply_feedback_optimizations,
    )
    feedback_path = data_dir / "asr_feedback.jsonl"
    if not feedback_path.exists():
        return
    try:
        corrections = load_corrections(str(feedback_path))
        if len(corrections) < 50:
            print(f"[asr] ⏭ 反馈数据不足（{len(corrections)}<50 条），跳过反向优化", file=sys.stderr)
            return
        # 僵尸判定时间窗：仅上次部署词典中已有的规则参与淘汰，
        # 防止本次新生成规则被误判为僵尸（生成→淘汰→再生成振荡）
        history_rules = _load_history_replace_rules(data_dir)
        recs = build_feedback_recommendations(
            corrections, terms, hotwords,
            min_samples=50, promote_threshold=3,
            history_rules=history_rules,
        )
        removed, promoted, added_hw = apply_feedback_optimizations(terms, hotwords, recs)
        parts = []
        if removed:
            parts.append(f"淘汰 {removed} 条僵尸规则")
        if promoted:
            parts.append(f"提升 {promoted} 条 LLM 发现")
        if added_hw:
            parts.append(f"补充 {added_hw} 个热词")
        if parts:
            print(f"[asr] 📊 反馈反向优化（{len(corrections)} 条记录）: " + ", ".join(parts),
                  file=sys.stderr)
        else:
            print(f"[asr] 📊 反馈分析完成（{len(corrections)} 条记录），无需优化", file=sys.stderr)
    except Exception as exc:
        print(f"[asr] ⚠ 反馈分析失败（不影响主流程）: {exc}", file=sys.stderr)


def _resolve_max_mappings(args, logger) -> int:
    """替换词典条数上限：CLI 参数 > asr_profiles.json 对应 profile > 默认 2000。"""
    cli_value = getattr(args, "max_mappings", None)
    if cli_value is not None:
        return cli_value or 2000
    profile_path = resolve_data_path("config/asr_profiles.json")
    if profile_path.exists():
        try:
            with open(profile_path) as pf:
                profiles = json.load(pf)
            profile_name = getattr(args, "profile", "default") or "default"
            profile_cfg = profiles.get(profile_name, profiles.get("default", {}))
            profile_max = profile_cfg.get("max_mappings")
            if profile_max is not None:
                return int(profile_max)
        except Exception as e:
            logger.debug("加载 asr_profiles.json 中 max_mappings 失败，使用默认值: %s", e)
    return 2000


def _merge_manual_hotwords(hotwords: List[str], deployed: List[str], logger,
                           *, data_dir: Path) -> List[str]:
    """合并手动热词（去重，保留手动词）。

    读法与指纹计算共用 ``version.load_manual_hotwords``——两处各读一套的话，
    「指纹认为变了」和「实际合并进来的词」可能对不上。

    手动词同样是「热词来源」，必须过敏感词过滤：`_drop_sensitive_hotwords`
    只作用于 Phase 1 与 feedback 追加两处，而本函数在 deploy 内部、在它们之后
    执行，且 deploy 直接写盘、绕过了 `format_hotwords_file` 的兜底过滤——
    不在这里滤一次，手动词就是唯一能不经判定进入 VocoType 的通道。
    """
    from iris.wiki.asr.version import load_manual_hotwords

    merged = list(hotwords)
    manual_words = _drop_sensitive_hotwords(
        load_manual_hotwords(data_dir), "手动热词")
    existing = set(merged)
    added = 0
    for w in manual_words:
        if w not in existing:
            merged.append(w)
            existing.add(w)
            added += 1
    if added:
        deployed.append(f"手动热词 +{added}")
    return merged


def _patch_json_file(path: Path, mutate, label: str, deployed: List[str], logger) -> None:
    """读 JSON → mutate(dict) → 原子写回；失败只记 warning 不中断部署。"""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        atomic_write_json(path, data)
        deployed.append(label)
    except Exception as e:
        logger.warning("写入 %s 失败: %s", path.name, e)


def _deploy_asr_to_vocotype(hotwords: List[str], hotwords_file: str, replace_dict_file: str,
                            prompt: str, output_path: str, logger, *,
                            bak_dir: Path, data_dir: Path) -> List[str]:
    """--deploy：备份 vocotype 配置 → 写热词 → 关闭 vocotype 自带 LLM/替换 → 同步到 Iris data/。

    ``bak_dir`` / ``data_dir`` 由调用方按 ``bundle.root`` 算出后传入，本函数不再
    自行解析路径——否则两处基准不同会在 --project-root 下分家。
    """
    import shutil
    from datetime import datetime as _dt

    voco_dir = os.environ.get(
        "IRIS_VOCOTYPE_DIR", os.path.expanduser("~/Library/Application Support/VocoType"))
    voco_path = Path(voco_dir)
    deployed: List[str] = []
    if not voco_path.exists():
        return [f"⚠ vocotype 目录不存在: {voco_dir}"]

    # 备份（与产物同处一个存档目录，见 asr.artifacts）
    backup_dir = bak_dir / VOCOTYPE_BACKUP_DIRNAME / _dt.now().strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("hotwords.txt", "postprocess.json", "ai_settings.json"):
        src = voco_path / fname
        if src.exists():
            shutil.copy2(str(src), str(backup_dir / fname))
    deployed.append(f"备份: {backup_dir}")

    # 部署热词（合并手动热词）
    if hotwords_file and hotwords:
        merged = _merge_manual_hotwords(hotwords, deployed, logger, data_dir=data_dir)
        # VocoType 对热词表有硬上限，超出会被其拒绝——此处显式拦截，
        # 不静默截断：静默截断会落一张「看起来正常」的表，与 v3.41.5 的
        # 差旅报销截断同源（调用不抛异常，产出却少了内容）。
        if len(merged) > HOTWORD_LIMIT:
            raise IrisValueError(
                f"热词 {len(merged)} 条超过 VocoType 上限 {HOTWORD_LIMIT} 条："
                f"请调低 --max-hotwords，或精简 data/asr_manual_hotwords.txt")
        # 原子写：VocoType 常驻运行，直接 write_text 可能让它读到半截文件。
        atomic_write_text(voco_path / "hotwords.txt", "\n".join(merged) + "\n")
        deployed.append(f"hotwords.txt ({len(merged)} 词)")

    # 写入 vocotype ai_settings.json：关闭 LLM 优化；postprocess.json：清空 replace_map
    def _disable_llm(d: dict) -> None:
        if "global" in d:
            d["global"]["enabled"] = False
    _patch_json_file(voco_path / "ai_settings.json", _disable_llm,
                     "ai_settings.json (LLM 优化已关闭)", deployed, logger)
    _patch_json_file(voco_path / "postprocess.json", lambda d: d.__setitem__("replace_map", {}),
                     "postprocess.json (替换词典已清空)", deployed, logger)

    # 部署到 Iris data/
    data_dir.mkdir(parents=True, exist_ok=True)
    if replace_dict_file:
        # 原子写：asr-corrector 常驻运行且每 5 秒轮询这两个文件，copy2 期间
        # 它可能读到半截 JSON/Prompt——与上面 hotwords.txt 是同一类风险。
        atomic_write_text(data_dir / "asr_replace_dict.json",
                          Path(replace_dict_file).read_text(encoding="utf-8"))
        deployed.append("data/asr_replace_dict.json")
    if prompt and output_path:
        atomic_write_text(data_dir / "asr_prompt.md",
                          Path(output_path).read_text(encoding="utf-8"))
        deployed.append("data/asr_prompt.md")
    return deployed


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
    """运行 ASR 覆盖分析和替换词典质量检查。

    实现收敛在 ``iris.wiki.asr.audit``：daily-start 第 7 阶段调用的是同一函数，
    历史上两份实现各读各的目录、各出一套字段，导致修一处漏一处。
    """
    from iris.wiki.asr.audit import run_asr_audit

    result = run_asr_audit(bundle)
    if result.payload.get("status") == "skipped":
        _emit_output("asr-audit", {"error": result.payload.get("reason", "无产物")},
                     pretty=args.pretty)
        return 1

    if args.pretty and result.text:
        print(result.text, file=sys.stderr)
    _emit_output("asr-audit", result.payload, pretty=args.pretty)
    return 0


# ── asr-corrector ─────────────────────────────────────────

def _load_asr_profile(profile_name: str, logger) -> dict:
    """加载 ASR profile 配置。"""
    import json as _json

    profile_config: dict = {}
    profile_path = resolve_data_path("config/asr_profiles.json")
    if profile_path.exists():
        try:
            with open(profile_path) as f:
                profiles = _json.load(f)
            profile_config = profiles.get(profile_name, profiles.get("default", {}))
        except Exception as e:
            logger.warning("加载 asr_profiles.json 失败，使用空 profile: %s", e)
    return profile_config


def resolve_profile_path(path_str: str) -> Path:
    """把 asr_profiles.json 里的路径归一为绝对路径（基准：项目根）。

    profile 里写的是 ``data/asr_replace_dict.json`` 这类**相对路径**，直接
    ``Path(p).exists()`` 会以 CWD 为基准——从非项目根启动 corrector 时词典会
    「不存在」而退出，而 ``llm_prompt`` 只判 exists、不存在就静默置空，于是
    LLM 校正悄悄降级成「仅词典校正」，没有失败信号。绝对路径原样返回。

    裸相对路径（不带 ``data/`` 等前缀）也按项目根解析，而不是交给
    ``resolve_data_path`` 抛 ValueError——那是配置写法问题，不该以异常形式
    冒到 CLI 顶层。
    """
    path = Path(path_str).expanduser()
    if path.is_absolute():
        return path
    if path_str.startswith(("data/", "config/", "output/")):
        return resolve_data_path(path_str)
    return get_project_root() / path


def _load_asr_replace_dict(dict_path: str, args) -> tuple[dict, str | None]:
    """加载 ASR 替换词典。

    Returns:
        (replace_dict, error_message) - 成功时 error_message 为 None
    """
    import json as _json

    resolved = resolve_profile_path(dict_path)
    if not resolved.exists():
        return {}, f"替换词典不存在: {resolved}，请先运行 build-asr-prompt --deploy"

    try:
        with open(resolved) as f:
            data = _json.load(f)
        return data.get("replace_map", {}), None
    except Exception:
        return {}, f"替换词典加载失败: {resolved}"


def _init_asr_llm_provider(mode: str, llm_prompt: str, bundle, args):
    """初始化 LLM Provider（仅 full 模式）。

    Returns:
        (provider, service, effective_mode) - 失败时降级为 fast 模式
    """
    from iris.llm.service import LLMService

    if mode != "full" or not llm_prompt:
        return None, None, mode

    try:
        service = LLMService(bundle)
        provider = service.get_provider()
        return provider, service, mode
    except Exception as e:
        print(f"[warn] LLM Provider 初始化失败: {e}", file=sys.stderr)
        print("[warn] 将降级为 fast 模式（仅替换词典）", file=sys.stderr)
        return None, None, "fast"


def _configure_asr_corrector(corrector, provider, service,
                             prompt_path: "str | Path | None", dict_path: str):
    """配置 ASR 校正器（注入 provider、熔断器、路径）。"""
    from pathlib import Path as _Path

    if provider:
        corrector.set_provider(provider)
        # 注入 ASR 独立熔断器：更低阈值（2 次失败即熔断，30s 后半开）
        try:
            from iris.llm.provider import _CircuitBreaker as _CB
            _asr_breaker = _CB(threshold=2, reset_after=30.0)
            provider.set_circuit_breaker(_asr_breaker)
            print("[Iris] ASR 熔断器: threshold=2 reset=30s (独立实例)", file=sys.stderr)
        except Exception:
            pass  # 降级：使用 provider 默认的全局熔断器

        # 同时注入 LLMService
        try:
            corrector.set_llm_service(service)
        except Exception:
            pass  # LLMService 不可用时降级为直接 provider 调用

    if prompt_path:
        corrector.set_prompt_path(str(prompt_path))
    if dict_path:
        corrector.set_dict_path(str(_Path(dict_path)))


def handle_asr_corrector(args, bundle, logger) -> int:
    """启动 ASR 实时校正守护进程。"""
    from iris.wiki.asr.corrector import AsrCorrector

    mode = getattr(args, "correct_mode", "full") or "full"
    profile_name = getattr(args, "profile", "default") or "default"

    # 阶段 1：加载配置
    profile_config = _load_asr_profile(profile_name, logger)

    # 阶段 2：加载替换词典
    dict_path = profile_config.get("replace_dict", "data/asr_replace_dict.json")
    replace_dict, error = _load_asr_replace_dict(dict_path, args)
    if error:
        _emit_output("asr-corrector", {"error": error}, pretty=args.pretty)
        return 1

    # 阶段 3：加载 LLM Prompt
    # profile 里 llm_prompt 显式为 null 表示「该 profile 不用提示词」（如 quick），
    # 与「配了路径但文件缺失」是两回事，不能都渲染成同一个警告。
    prompt_cfg = profile_config.get("llm_prompt", "data/asr_prompt.md")
    prompt_path = resolve_profile_path(prompt_cfg) if prompt_cfg else None
    llm_prompt = ""
    if prompt_path is None:
        pass
    elif prompt_path.exists():
        llm_prompt = prompt_path.read_text(encoding="utf-8")
    else:
        # 静默置空会让 LLM 校正悄悄降级为「仅词典校正」，必须显式告警。
        print(f"[warn] 校正提示词不存在: {prompt_path}，LLM 校正将降级为仅词典校正",
              file=sys.stderr)

    # 阶段 4：初始化 LLM Provider
    provider, service, mode = _init_asr_llm_provider(mode, llm_prompt, bundle, args)

    # 阶段 5：提取运行参数
    feedback_path = str(resolve_data_path("data/asr_feedback.jsonl"))
    context_window_size = profile_config.get("context_window_size", 5)
    context_expire_minutes = profile_config.get("context_expire_minutes", 10)
    context_ab = getattr(args, "context_ab", False)

    _llm_profile = profile_config.get("llm", {}) if isinstance(profile_config, dict) else {}
    llm_timeout_ms = _llm_profile.get("timeout_ms", 8000) if isinstance(_llm_profile, dict) else 8000

    max_asr_length = getattr(args, "max_asr_length", None)
    if max_asr_length is None:
        max_asr_length = profile_config.get("max_asr_length", 500)
    max_asr_length = int(max_asr_length)

    # 阶段 6：创建并配置校正引擎
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

    _configure_asr_corrector(corrector, provider, service, prompt_path, dict_path)

    # 阶段 7：启动
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
