"""Frontmatter 批量补全命令处理器。"""

from __future__ import annotations

import sys
from pathlib import Path

from iris.core.frontmatter_batch import (
    BatchConfig,
    FrontmatterBatchProcessor,
)
from iris.app.cli.helpers import _emit_output


def handle_frontmatter_batch(args, bundle, logger) -> int:
    """frontmatter-batch: 批量补全 SOURCE 文档的 YAML frontmatter 元数据。

    流水线: 正则提取 → LLM 提取 → frontmatter 注入 → wikilink 注入
    """
    from iris.llm import LLMService
    from iris.utils.paths import resolve_source_root

    # ── 解析参数 ────────────────────────────────────────────
    dry_run = getattr(args, "dry_run", False)
    list_backups = getattr(args, "list_backups", False)
    restore_ts = getattr(args, "restore", "")

    # 确定 SOURCE 根目录
    source_root = resolve_source_root(bundle)
    if not source_root:
        print("错误: 无法确定 SOURCE 数据源路径", file=sys.stderr)
        return 1

    # ── 列出备份 ────────────────────────────────────────────
    if list_backups:
        backups = FrontmatterBatchProcessor.list_backups(source_root)
        if not backups:
            print("无备份记录")
        else:
            for b in backups:
                print(f"  {b.name}")
        return 0

    # ── 恢复备份 ────────────────────────────────────────────
    if restore_ts:
        try:
            count = FrontmatterBatchProcessor.restore_directory(source_root, restore_ts)
            print(f"已从 {restore_ts} 恢复 {count} 个文件")
            return 0
        except FileNotFoundError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 1

    # ── 解析目标目录 ────────────────────────────────────────
    dirs_raw = getattr(args, "source_dirs", []) or []
    if not dirs_raw:
        # 默认：所有 9 个类别目录
        target_dirs = sorted(
            d for d in source_root.iterdir()
            if d.is_dir() and not d.name.startswith("_") and not d.name.startswith(".")
        )
    else:
        target_dirs = []
        for d in dirs_raw:
            p = Path(d)
            if not p.is_absolute():
                p = source_root / p
            if not p.exists():
                print(f"警告: 目录不存在 {p}，跳过", file=sys.stderr)
                continue
            target_dirs.append(p)

    if not target_dirs:
        print("无目标目录", file=sys.stderr)
        return 1

    # ── 构建配置 ────────────────────────────────────────────
    config = BatchConfig(
        use_llm=not getattr(args, "no_llm", False),
        use_wikilink=not getattr(args, "no_wikilink", False),
        force_overwrite=getattr(args, "force", False),
        no_backup=getattr(args, "no_backup", False),
    )

    # ── 初始化处理器 ────────────────────────────────────────
    wiki_root = (bundle.wiki or {}).get("wiki_root", "")
    llm = LLMService(bundle) if config.use_llm else None
    processor = FrontmatterBatchProcessor(llm, wiki_root, config)

    # ── 逐目录处理 ──────────────────────────────────────────
    all_results = []
    for d in target_dirs:
        print(f"\n处理: {d.name} ({d})")
        result = processor.process_directory(d, dry_run=dry_run)
        all_results.append((d.name, result))

    # ── 汇总输出 ────────────────────────────────────────────
    total_all = sum(r.total for _, r in all_results)
    success_all = sum(r.success for _, r in all_results)
    skipped_all = sum(r.skipped for _, r in all_results)
    failed_all = sum(r.failed for _, r in all_results)

    summary = {
        "mode": "dry_run" if dry_run else "executed",
        "directories": len(all_results),
        "total": total_all,
        "success": success_all,
        "skipped": skipped_all,
        "failed": failed_all,
        "per_directory": [
            {
                "dir": name,
                "total": r.total,
                "success": r.success,
                "skipped": r.skipped,
                "failed": r.failed,
                "backup": r.backup_path,
            }
            for name, r in all_results
        ],
    }

    _emit_output("frontmatter-batch", summary, pretty=getattr(args, "pretty", False))

    if failed_all > 0:
        print(f"\n⚠ {failed_all} 个文件处理失败，详情见上", file=sys.stderr)

    return 0 if failed_all == 0 else 1


# ── Handler 注册 ────────────────────────────────────────────

FRONTMATTER_HANDLERS = {
    "frontmatter-batch": handle_frontmatter_batch,
}
