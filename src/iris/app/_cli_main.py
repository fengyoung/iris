"""Iris CLI 入口 — Phase 2.1 版本，含数据源层命令。"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from iris.config import load_config_bundle
from iris.utils.logging import IrisLogger
from iris.core.script_loader import run_delegated_script

from iris.app.cli.helpers import _show_banner, _emit_output
from iris.app.cli.handlers import COMMAND_HANDLERS


COMMANDS = [
    "check-config", "route-model", "diagnose", "status", "agent-spec",
    "scan-source", "build-chunks", "build-vector-index",
    "search", "ask", "build-report", "build-mindmap", "build-biweekly-report",
    "discover-wiki", "discover-wiki-auto", "build-wiki",
    "build-wiki-nav", "wiki-pipeline", "wiki-lint", "wiki-update",
    "build-asr-prompt", "asr-corrector", "asr-audit", "asr-report",
    "meeting-live-assistant",
    "enrich-persons", "deep-eval",
    "memory-status", "memory-list", "memory-delete", "memory-maintenance",
    "memory-export", "memory-import", "working-set", "working-show",
    "working-clear", "process", "transcribe-meeting", "batch-transcribe", "daily-start",
    "secrets-set", "secrets-list", "secrets-delete",
    "build-graph",
    "graph-query",
    "usage-stats",
    "metrics-export",
    "reminders",
    "watch",
    # ── 委托命令 ──
    "trello", "extract-weekly-reports", "extract-travel-invoice",
    "sync-memory", "feishu-doc-convert", "chat-digest",
    # ── 信息汇聚 ──
    "feed-setup", "feed-list", "feed-add", "feed-remove",
    "feed-config", "feed-collect", "feed-pending",
    "feed-confirm", "feed-ignore",
    "frontmatter-batch",
]

_DELEGATED_SCRIPTS = {
    "trello": "trello.py",
    "extract-weekly-reports": "extract_weekly_reports.py",
    "extract-travel-invoice": "extract_travel_invoice.py",
    "sync-memory": "sync_memory.py",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Iris 命令行入口")
    parser.add_argument("command", choices=COMMANDS, help="执行的命令")
    parser.add_argument("--project-root", default=".", help="Iris 项目根目录")
    parser.add_argument("--workspace", default="", help="工作空间名称（覆盖 config/workspaces.json 中的路径配置）")
    parser.add_argument("--context", default="{}", help="route-model 使用的 JSON 上下文")
    parser.add_argument("--pretty", action="store_true", help="人类可读输出")
    # 数据源层
    parser.add_argument("--source", default="", help="scan-source / build-chunks 指定数据源名称")
    parser.add_argument("--call-source", default="cli", choices=["cli", "skill"],
                        help="LLM 调用来源标记（cli=命令行直接调用, skill=Claude Skill 触发）")
    parser.add_argument("--summary-only", action="store_true", help="仅输出摘要")
    parser.add_argument("--write-summary", action="store_true", help="写入摘要到 data/metadata")
    parser.add_argument("--incremental", action="store_true", help="增量扫描/构建（仅处理变更文件）")
    parser.add_argument("--force-rebuild", action="store_true",
                        help="build-vector-index 全量重建向量索引（丢弃旧向量重新嵌入，embedding 模型变更后必须执行）")
    # 记忆系统
    parser.add_argument("--memory-type", default="all", choices=["all", "profile", "corrections"], help="memory-list 类型")
    parser.add_argument("--concept", default="", help="memory-delete 概念名")
    parser.add_argument("--replace", action="store_true", help="memory-import 覆盖模式")
    parser.add_argument("--age-days", type=int, default=90, help="老化阈值天数")
    parser.add_argument("--auto-age", action="store_true", help="自动老化归档")
    # 工作上下文
    parser.add_argument("--task", default="", help="working-set 当前任务")
    parser.add_argument("--pending", default="", help="working-set 待办事项，| 分隔")
    parser.add_argument("--add-pending", default="", help="working-set 追加待办")
    parser.add_argument("--change", default="", help="working-set 最近变更，| 分隔")
    parser.add_argument("--add-change", default="", help="working-set 追加变更")
    parser.add_argument("--notes", default="", help="working-set 备注")
    # 输出
    parser.add_argument("--output-file", default="", help="输出文件路径")
    parser.add_argument("--input-file", default="", help="输入文件路径")
    # 图文处理
    parser.add_argument("--query", default="", help="process 查询文本")
    parser.add_argument("--image", default="", help="图片路径（逗号分隔）")
    # 搜索问答
    parser.add_argument("--top-k", type=int, default=5, help="search/ask/report/mindmap 返回条数")
    parser.add_argument("--mode", default="local", choices=["local", "llm"], help="search/ask/report/mindmap 模式")
    parser.add_argument("--format", default="mermaid", choices=["mermaid", "xmind", "both"], help="build-mindmap 格式")
    parser.add_argument("--two-stage", action="store_true", help="build-report 两阶段审查")
    parser.add_argument("--output-format", default="md", choices=["md", "docx", "standard", "compact"],
                        help="build-report 输出格式 / build-asr-prompt prompt 格式")
    # 会议转录
    parser.add_argument("--audio-file", default="", help="transcribe-meeting 录音文件路径")
    parser.add_argument("--transcript-file", default="", help="transcribe-meeting 已有转写文本路径")
    parser.add_argument("--output", default="", help="transcribe-meeting / meeting-live-assistant 输出路径")
    parser.add_argument("--asr", default="", help="meeting-live-assistant ASR 模式（local|remote，默认从 app.json 读取）")
    parser.add_argument("--to-source", action="store_true", help="输出归档到 SOURCE（会议纪要→05/，双周报→06/）")
    parser.add_argument("--style-from", default="", help="build-biweekly-report 风格参考文件路径")
    parser.add_argument("--whisper-model", default="base", help="Whisper 模型规格")
    parser.add_argument("--force", action="store_true", help="强制重新转写")
    parser.add_argument("--files", default="", help="batch-transcribe 文件列表（逗号分隔）")
    parser.add_argument("--dir", default="", help="batch-transcribe 批量处理目录下所有 .txt")
    parser.add_argument("--output-dir", default="", help="batch-transcribe 统一输出目录")
    # 密钥链
    parser.add_argument("--key", default="", help="密钥名称")
    parser.add_argument("--value", default="", help="密钥值（留空则交互式输入）")
    # Wiki
    parser.add_argument("--page-type", default="domain", choices=["domain", "concept", "project", "person"],
                        help="build-wiki 页面类型")
    parser.add_argument("--title", default="", help="build-wiki 页面标题")
    parser.add_argument("--write", action="store_true", help="build-wiki 时写入 Wiki 目录")
    parser.add_argument("--overwrite", action="store_true", help="build-wiki 允许覆盖已有页")
    parser.add_argument("--backup", action="store_true", help="build-wiki 覆盖前保留备份")
    parser.add_argument("--batch-file", default="", help="build-wiki 批量模式输入 JSONL")
    parser.add_argument("--review-file", default="", help="build-wiki 审核模式输入 JSONL")
    parser.add_argument("--limit", type=int, default=20, help="discover-wiki 候选上限")
    parser.add_argument("--export-jsonl", default="", help="discover-wiki 导出候选 JSONL")
    parser.add_argument("--export-review", default="", help="discover-wiki 导出审核 JSONL")
    parser.add_argument("--export-review-md", default="", help="discover-wiki 导出审核 Markdown")
    parser.add_argument("--fix", action="store_true", help="wiki-lint 自动修复模式")
    parser.add_argument("--bump", default="auto", choices=["auto", "major", "minor", "patch"],
                        help="build-asr-prompt 版本号递增方式")
    parser.add_argument("--asr-mode", default="all", choices=["all", "hotwords", "replace-dict", "prompt"],
                        help="build-asr-prompt 输出模式")
    parser.add_argument("--max-hotwords", type=int, default=490,
                        help="build-asr-prompt 热词最大数量")
    parser.add_argument("--max-mappings", type=int, default=990,
                        help="build-asr-prompt 替换映射最大数量")
    parser.add_argument("--max-chars", type=int, default=20,
                        help="build-asr-prompt 热词/映射最大字符数")
    parser.add_argument("--deploy", action="store_true",
                        help="build-asr-prompt 生成后直接部署到 vocotype 配置目录")
    parser.add_argument("--profile", default="default",
                        help="asr-corrector 校正策略配置名")
    parser.add_argument("--correct-mode", default="full", choices=["fast", "full"],
                        help="asr-corrector 校正模式")
    parser.add_argument("--context-ab", action="store_true",
                        help="asr-corrector 开启上下文 A/B 对比（每句 LLM 跑两次，对比有无上下文的效果）")
    parser.add_argument("--max-asr-length", type=int, default=None,
                        help="asr-corrector 单段转写长度上限（默认 500，长语音场景可放宽，如 2000）")
    # 飞书文档转换
    parser.add_argument("--url", default="", help="feishu-doc-convert 飞书文档 URL（逗号分隔多文档）")
    parser.add_argument("--from-config", action="store_true", help="feishu-doc-convert / chat-digest 从配置文件读取目标列表")
    parser.add_argument("--dry-run", action="store_true", help="预览模式不写入文件（feishu-doc-convert/chat-digest/build-biweekly-report）")
    # 知识图谱
    parser.add_argument("--full", action="store_true", help="build-graph 全量重建 LLM 关系")
    parser.add_argument("--page", default="", help="build-graph 单页重建")
    # 图谱查询
    parser.add_argument("--op", default="", choices=["", "neighbors", "related", "path", "orphans", "bridges", "density"],
                        help="graph-query 操作类型")
    parser.add_argument("--node", default="", help="graph-query 目标节点标题（neighbors/related/path）")
    parser.add_argument("--to", default="", help="graph-query path 的终点节点标题")
    parser.add_argument("--hops", type=int, default=1, help="graph-query neighbors 跳数（默认 1）")
    parser.add_argument("--min-degree", type=int, default=3, dest="min_degree", help="graph-query bridges 最小度阈值（默认 3）")
    # 深度评估
    parser.add_argument("--page-filter", default="", help="deep-eval 只评估标题包含该子串的页面")
    parser.add_argument("--sample-rate", type=float, default=None, help="deep-eval 抽样比例 0.0~1.0（默认全量评估）")
    # 用量统计
    parser.add_argument("--by", default="month", choices=["day", "week", "month", "year"],
                        help="usage-stats 时间粒度（默认 month）")
    parser.add_argument("--model", default="", help="usage-stats 过滤模型名称")
    parser.add_argument("--since", default="", help="usage-stats 起始日期 YYYY-MM-DD")
    parser.add_argument("--cost", action="store_true", help="usage-stats 按价格表估算费用（需 config/llm_pricing.json）")
    # 指标导出
    parser.add_argument("--trend", action="store_true", help="metrics-export 输出最近 N 周趋势（配合 --weeks）")
    parser.add_argument("--weeks", type=int, default=4, help="metrics-export 趋势周数（默认 4）")
    # 文件监听
    parser.add_argument("--poll-interval", type=int, default=30, help="watch 轮询间隔秒数（默认 30）")
    parser.add_argument("--run-once", action="store_true", help="watch 单次检测后退出")
    # 聊天提炼
    parser.add_argument("--group", default="", help="chat-digest 群聊名称")
    parser.add_argument("--user", default="", help="chat-digest 用户名称（单聊）")
    parser.add_argument("--range", default="", help="chat-digest 时间范围（天数或 ISO 开始~结束）")
    parser.add_argument("--interactive", action="store_true", help="chat-digest / feed-setup 交互选择模式")
    # 信息汇聚 feed
    parser.add_argument("--chat", default="", help="feed-add/remove/config/collect 群聊名或 ID")
    parser.add_argument("--chat-type", default="group", choices=["group", "single"],
                        help="feed-add 会话类型（group/single）")
    parser.add_argument("--tags", default="", help="feed-add/config OKR 标签（逗号分隔）")
    parser.add_argument("--import-mode", default="auto_import", choices=["auto_import", "confirm", "all"],
                        help="feed-add/config/collect 导入模式")
    parser.add_argument("--topic-id", default="", help="feed-confirm/ignore 话题 ID")
    parser.add_argument("--all", action="store_true", dest="all_", help="feed-confirm 批量确认全部")
    parser.add_argument("--show", action="store_true", help="feed-config 显示完整配置")
    parser.add_argument("--no-extract-docs", action="store_true",
                        help="feed-collect 跳过飞书文档提取")
    # frontmatter-batch
    parser.add_argument("--source-dir", action="append", dest="source_dirs", default=[],
                        help="frontmatter-batch 目标 SOURCE 子目录（可多次指定）")
    parser.add_argument("--no-llm", action="store_true",
                        help="frontmatter-batch 跳过 LLM 提取（仅正则）")
    parser.add_argument("--no-wikilink", action="store_true",
                        help="frontmatter-batch 跳过 wikilink 注入")
    parser.add_argument("--no-backup", action="store_true",
                        help="frontmatter-batch 跳过备份")
    parser.add_argument("--list-backups", action="store_true",
                        help="frontmatter-batch 列出所有备份")
    parser.add_argument("--restore", default="",
                        help="frontmatter-batch 从指定时间戳备份恢复")
    return parser


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in _DELEGATED_SCRIPTS:
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        return run_delegated_script(_DELEGATED_SCRIPTS[sys.argv[1]], project_root)

    parser = build_parser()
    args = parser.parse_args()

    # 注入调用来源标记，供 LLMService 读取并写入用量统计
    import os as _os
    _os.environ["IRIS_CALL_SOURCE"] = getattr(args, "call_source", "cli")

    # workspace 命令：不需要加载完整配置
    if args.command == "workspace":
        return _handle_workspace_cmd(args)

    project_root = Path(args.project_root)
    bundle = load_config_bundle(project_root)

    # 应用工作空间配置
    if getattr(args, "workspace", ""):
        from iris.config.workspace import WorkspaceManager
        mgr = WorkspaceManager(project_root)
        bundle = mgr.apply(bundle, workspace_name=args.workspace)

    logger = IrisLogger(bundle)

    _show_banner(args.command)

    handler = COMMAND_HANDLERS.get(args.command)
    if handler is None:
        parser.error("未知命令")
        return 2

    try:
        return handler(args, bundle, logger)
    except Exception as exc:
        _emit_output(args.command, {
            "error": str(exc),
            "type": type(exc).__name__,
        }, pretty=args.pretty)
        if args.pretty:
            traceback.print_exc(file=sys.stderr)
        return 1


def _handle_workspace_cmd(args) -> int:
    """处理 workspace 命令（list / current）。"""
    from iris.config.workspace import WorkspaceManager
    mgr = WorkspaceManager(Path(args.project_root))
    ws_name = getattr(args, "workspace", "") or mgr.config.default_workspace

    if getattr(args, "list_workspaces", False):
        workspaces = mgr.list_workspaces()
        for ws in workspaces:
            print(f"  {ws.name:20s}  source={ws.source_root or '(默认)'}  wiki={ws.wiki_root or '(默认)'}")
        return 0

    # current
    ws = mgr.resolve(ws_name)
    print(f"当前工作空间: {ws.name}")
    print(f"  数据源路径:   {ws.source_root or '(使用 data_source.json 默认值)'}")
    print(f"  Wiki 根目录:  {ws.wiki_root or '(使用 wiki.json 默认值)'}")
    print(f"  数据目录:     {ws.data_dir or '(默认)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
