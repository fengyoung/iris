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
    "build-asr-prompt", "enrich-persons", "deep-eval",
    "memory-status", "memory-list", "memory-delete", "memory-maintenance",
    "memory-export", "memory-import", "working-set", "working-show",
    "working-clear", "process", "transcribe-meeting", "batch-transcribe", "daily-start",
    "secrets-set", "secrets-list", "secrets-delete",
    "build-graph",
    # ── 委托命令 ──
    "trello", "extract-weekly-reports", "extract-didi-travel",
    "sync-memory", "feishu-doc-convert", "chat-digest",
]

_DELEGATED_SCRIPTS = {
    "trello": "trello.py",
    "extract-weekly-reports": "extract_weekly_reports.py",
    "extract-didi-travel": "extract_didi_travel.py",
    "sync-memory": "sync_memory.py",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Iris 命令行入口")
    parser.add_argument("command", choices=COMMANDS, help="执行的命令")
    parser.add_argument("--project-root", default=".", help="Iris 项目根目录")
    parser.add_argument("--context", default="{}", help="route-model 使用的 JSON 上下文")
    parser.add_argument("--pretty", action="store_true", help="人类可读输出")
    # 数据源层
    parser.add_argument("--source", default="", help="scan-source / build-chunks 指定数据源名称")
    parser.add_argument("--summary-only", action="store_true", help="仅输出摘要")
    parser.add_argument("--write-summary", action="store_true", help="写入摘要到 data/metadata")
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
    parser.add_argument("--output", default="", help="transcribe-meeting 输出路径")
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
    parser.add_argument("--incremental", action="store_true", help="discover-wiki 增量模式")
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
    # 飞书文档转换
    parser.add_argument("--url", default="", help="feishu-doc-convert 飞书文档 URL（逗号分隔多文档）")
    parser.add_argument("--from-config", action="store_true", help="feishu-doc-convert / chat-digest 从配置文件读取目标列表")
    parser.add_argument("--dry-run", action="store_true", help="预览模式不写入文件（feishu-doc-convert/chat-digest/build-biweekly-report）")
    # 知识图谱
    parser.add_argument("--full", action="store_true", help="build-graph 全量重建 LLM 关系")
    parser.add_argument("--page", default="", help="build-graph 单页重建")
    # 聊天提炼
    parser.add_argument("--group", default="", help="chat-digest 群聊名称")
    parser.add_argument("--user", default="", help="chat-digest 用户名称（单聊）")
    parser.add_argument("--range", default="", help="chat-digest 时间范围（天数或 ISO 开始~结束）")
    parser.add_argument("--interactive", action="store_true", help="chat-digest 交互选择模式")
    return parser


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in _DELEGATED_SCRIPTS:
        project_root = Path(__file__).resolve().parent.parent.parent.parent
        return run_delegated_script(_DELEGATED_SCRIPTS[sys.argv[1]], project_root)

    parser = build_parser()
    args = parser.parse_args()
    bundle = load_config_bundle(Path(args.project_root))
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


if __name__ == "__main__":
    raise SystemExit(main())
