#!/usr/bin/env python3
"""从回收目录还原 Wiki 备份文件（*.bak.N.md）。

2026-09-30 清理时，123 个「与 live 页配对」的备份被移出 Wiki 根目录，
集中存放到 data/backups/wiki-bak-<日期>/，并生成 MANIFEST.json 记录
原始路径、字节数与 sha256 前缀。本脚本按清单还原。

用法：
    python3 scripts/restore_wiki_bak.py --list              # 只列清单
    python3 scripts/restore_wiki_bak.py --dry-run           # 预演
    python3 scripts/restore_wiki_bak.py                     # 执行还原
    python3 scripts/restore_wiki_bak.py --verify            # 仅校验完整性

注意：Wiki 根目录不是 git 仓库，还原是唯一回滚手段，故脚本默认
不覆盖已存在的文件（--force 才覆盖）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

DEFAULT_TRASH = Path(__file__).resolve().parent.parent / "data/backups/wiki-bak-20260930"


def load_manifest(trash: Path) -> dict:
    mf = trash / "MANIFEST.json"
    if not mf.exists():
        sys.exit(f"找不到清单文件：{mf}")
    return json.loads(mf.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="从回收目录还原 Wiki 备份文件")
    ap.add_argument("--trash", type=Path, default=DEFAULT_TRASH,
                    help=f"回收目录（默认 {DEFAULT_TRASH}）")
    ap.add_argument("--list", action="store_true", help="只列出清单内容")
    ap.add_argument("--dry-run", action="store_true", help="预演，不实际写入")
    ap.add_argument("--verify", action="store_true", help="仅校验文件完整性")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    args = ap.parse_args()

    data = load_manifest(args.trash)
    files = data["files"]
    print(f"清单：{args.trash / 'MANIFEST.json'}")
    print(f"创建于 {data['created']}，共 {data['count']} 个文件")
    print(f"Wiki 根：{data['wiki_root']}\n")

    if args.list:
        for f in files:
            print(f"  {f['rel']}  ({f['size']}B)")
        return 0

    restored = skipped = missing = corrupt = 0
    for f in files:
        src = Path(f["trashed"])
        dst = Path(f["original"])

        if not src.exists():
            print(f"  ✗ 回收文件缺失：{f['rel']}")
            missing += 1
            continue

        actual = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
        if actual != f["sha256_16"]:
            print(f"  ✗ 校验失败（内容已被改动）：{f['rel']}")
            corrupt += 1
            if not args.force:
                continue

        if args.verify:
            restored += 1
            continue

        if dst.exists() and not args.force:
            print(f"  ⏭ 已存在，跳过：{f['rel']}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"  → 将还原：{f['rel']}")
            restored += 1
            continue

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        restored += 1

    verb = "校验通过" if args.verify else ("预演完成" if args.dry_run else "还原完成")
    print(f"\n{verb}：{restored} 个 | 跳过 {skipped} | 缺失 {missing} | 校验失败 {corrupt}")
    if corrupt and not args.force:
        print("提示：校验失败的文件未还原；确认无误后可加 --force 强制还原。")
    return 1 if (missing or corrupt) else 0


if __name__ == "__main__":
    raise SystemExit(main())
