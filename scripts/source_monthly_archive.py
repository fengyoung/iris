#!/usr/bin/env python3
"""SOURCE 目录归档迁移脚本 — 将平铺文件按配置搬入年/月度子目录。

用法:
    python3 scripts/source_monthly_archive.py          # 正式执行（dry-run=False）
    python3 scripts/source_monthly_archive.py --dry-run  # 预览模式
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load_archive_config(project_root: Path) -> dict:
    path = project_root / "config" / "source_archive.json"
    if not path.exists():
        print(f"[ERROR] 配置文件不存在: {path}")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def resolve_source_root(project_root: Path) -> Path | None:
    """从 data_source.json 或默认路径推导 SOURCE 根目录。"""
    # 优先尝试从 data_source.json 读取
    ds_path = project_root / "config" / "data_source.json"
    if ds_path.exists():
        with open(ds_path) as f:
            ds = json.load(f)
        for cfg in ds.get("sources", {}).values():
            raw = cfg.get("path", "")
            if raw:
                # 解析 ${IRIS_WORK_DOCS_DIR} 等环境变量
                import os
                resolved = os.path.expandvars(raw)
                p = Path(resolved).resolve()
                if p.exists():
                    return p

    # fallback: 尝试 Obsidian 默认路径
    candidate = Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/WORK_ZZ/IRIS-3/SOURCE"
    if candidate.exists():
        return candidate
    return None


def get_archive_mode(categories: dict, dirname: str) -> str:
    info = categories.get(dirname, {})
    return info.get("mode", "flat")


def _extract_date(text: str) -> tuple[str, str] | None:
    """从文件名中提取 YYYY 和 YYYYMM。

    支持格式（按优先级）：
      - YYYYMMDD-xxx           (标准前缀)
      - xxx-YYYYMMDD           (后缀，如 双周报-w01-冯扬-20260104)
      - YYYYMMDD_xxx           (下划线分隔)
      - 文件名中任意位置 8 位数字
    """
    # 标准前缀
    m = re.match(r"(\d{4})(\d{2})\d{2}-", text)
    if m:
        return m.group(1), m.group(1) + m.group(2)
    # 后缀
    m = re.search(r"-(\d{4})(\d{2})\d{2}(?:\.[a-z]+)?$", text)
    if m:
        return m.group(1), m.group(1) + m.group(2)
    # 下划线分隔
    m = re.match(r"(\d{4})(\d{2})\d{2}_", text)
    if m:
        return m.group(1), m.group(1) + m.group(2)
    # 文件名中任意 8 位数字
    m = re.search(r"(\d{4})(\d{2})\d{2}", text)
    if m:
        return m.group(1), m.group(1) + m.group(2)
    return None


def archive_file(filepath: Path, mode: str, dry_run: bool) -> Path | None:
    """计算归档目标路径。返回 None 表示不需要搬迁。"""
    date = _extract_date(filepath.name)
    if not date or mode == "flat":
        return None

    if mode == "yearly":
        sub = date[0]  # YYYY
    elif mode == "monthly":
        sub = date[1]  # YYYYMM
    else:
        return None

    target = filepath.parent / sub / filepath.name
    if target == filepath:
        return None  # 已经在子目录中
    return target


def main():
    parser = argparse.ArgumentParser(description="SOURCE 目录归档迁移")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际移动文件")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    config = load_archive_config(project_root)
    categories = config.get("categories", {})
    source_root = resolve_source_root(project_root)

    if not source_root:
        print("[ERROR] 无法定位 SOURCE 根目录")
        sys.exit(1)

    print(f"SOURCE 根目录: {source_root}")
    print(f"归档配置: {len(categories)} 个目录")
    print(f"模式: {'预览 (dry-run)' if args.dry_run else '执行'}")
    print()

    total_moved = 0
    total_skipped = 0

    # 遍历每个一级子目录
    for child in sorted(source_root.iterdir()):
        if not child.is_dir():
            continue
        dirname = child.name
        if dirname.startswith("."):
            continue

        mode = get_archive_mode(categories, dirname)
        if mode == "flat":
            continue

        # 仅扫描当前层级（不递归子目录）的文件
        moved = 0
        skipped = 0
        for f in sorted(child.glob("*.md")):
            target = archive_file(f, mode, args.dry_run)
            if target is None:
                skipped += 1
                continue

            if args.dry_run:
                print(f"  [{mode}] {f.relative_to(source_root)}")
                print(f"         → {target.relative_to(source_root)}")
                moved += 1
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                f.rename(target)
                moved += 1

        if moved or skipped:
            print(f"  {dirname} ({mode}): {moved} 搬迁, {skipped} 跳过")
        total_moved += moved
        total_skipped += skipped

    print(f"\n总计: {total_moved} 搬迁, {total_skipped} 跳过")

    if not args.dry_run and total_moved:
        print("\n✅ 搬迁完成")
    elif args.dry_run and total_moved:
        print("\n💡 以上为预览结果，去掉 --dry-run 执行实际搬迁")
    else:
        print("\n无需搬迁")


if __name__ == "__main__":
    main()
