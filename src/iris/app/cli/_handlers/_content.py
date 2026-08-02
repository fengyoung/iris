"""分析报告 + 会议转录 + 飞书导入 + 图文处理 命令处理器。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

from iris.complex_input import ComplexInputPipeline
from iris.app.cli.helpers import (
    _parse_image_list, _emit_output,
)
from iris.utils.paths import resolve_source_root as _resolve_data_source_root


# ── 双周报辅助 ────────────────────────────────────────────


def _build_biweekly_filename(bundle, today: datetime) -> str:
    """生成双周报文件名：{YYYYMMDD}-双周报-w{week}-{author}.md。

    日期前缀供 resolve_source_archive_path 识别归档子目录（06-我的周报 按年归档）。
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
    return f"{date_str}-双周报-w{week:02d}-{author}.md" if author else f"{date_str}-双周报-w{week:02d}.md"


# ── 分析报告 ────────────────────────────────────────────


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
            from iris.utils.paths import resolve_source_archive_path
            output = str(resolve_source_archive_path(
                source_root, "06-我的周报", auto_filename))

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
        # ── 注入 frontmatter ──────────────────────────────
        try:
            from iris.core.frontmatter import inject_frontmatter
            period = (result.llm or {}).get("period", "")
            _fm_fields = {
                "title": f"双周报 - {report_author}" if report_author else "双周报",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "type": "我的周报",
                "period": period,
                "author": report_author,
            }
            markdown = inject_frontmatter(markdown, _fm_fields)
        except Exception:
            pass  # frontmatter 注入失败不阻塞双周报生成
        path.write_text(markdown, encoding="utf-8")
        payload["output_file"] = str(path)

    _emit_output(args.command, payload, pretty=args.pretty)
    return 0


# ── 会议转录 ────────────────────────────────────────────


def handle_transcribe_meeting(args, bundle, logger) -> int:
    from iris.app.transcribe_meeting import TranscribeMeetingPipeline
    if not args.audio_file and not args.transcript_file:
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


# ── 飞书导入 ──────────────────────────────────────────────


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


# ── 图文处理 ───────────────────────────────────────────────


def handle_process(args, bundle, logger) -> int:
    if not args.query:
        raise ValueError("process 需要 --query")
    image_paths = _parse_image_list(args.image)
    pipeline = ComplexInputPipeline(bundle)
    result = pipeline.process(args.query, file_paths=image_paths or None, output_path=args.output_file or None)
    _emit_output(args.command, result.to_dict(), pretty=args.pretty)
    return 0


# ── 辅助函数 ─────────────────────────────────────────────


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
            print(f"[警告] 文件不存在，已跳过: {p}", file=sys.stderr)
            continue
        seen.add(abs_p)
        result.append(p)
    return result


# ── 命令映射 ─────────────────────────────────────────────

CONTENT_HANDLERS = {
    "build-report": handle_build_report,
    "build-mindmap": handle_build_mindmap,
    "build-biweekly-report": handle_build_biweekly_report,
    "transcribe-meeting": handle_transcribe_meeting,
    "batch-transcribe": handle_batch_transcribe,
    "feishu-doc-convert": handle_feishu_doc_convert,
    "chat-digest": handle_chat_digest,
    "process": handle_process,
}
