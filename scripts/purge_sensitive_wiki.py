#!/usr/bin/env python3
"""清理 Wiki 中已渗入的敏感内容 — 调薪/人员盘点/绩效评价类信息。

背景：敏感文档（调薪方案、人员盘点、绩效评估）只允许保留 SOURCE 原件，
但历史检索会把它们作为证据带进 Wiki 人物页正文、source_fingerprint 与
index.md 聚合摘要。2026-09-20 实测：76 个人物页含敏感标记，其中 13 页含
真实薪资数字。

本脚本做**外科式清理**，不重建页面：
  - 删除含敏感标记的列表项行与段落句子
  - 删除指向敏感文档的 source_fingerprint 条目
  - 删除引用敏感文档的参考行与悬空 [[wikilink]]

不重建的原因：人物页重建会因检索不到证据生成空模板覆盖原内容（历史坑）。

用法：
    python3 scripts/purge_sensitive_wiki.py              # 预览（默认）
    python3 scripts/purge_sensitive_wiki.py --apply      # 实际写入
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from iris.wiki._sensitive import matched_sensitive_term  # noqa: E402

# 清理标记：按用户确认的敏感边界（薪资 + 绩效 + 930名单 + 盘点评估）。
# 注意不含裸「晋升」——职级晋升属正常组织信息，经确认不在敏感范围内。
CLEANUP_MARKERS = (
    # 主题词
    "调薪", "薪资", "薪酬", "绩效",
    "人员盘点", "人才盘点", "leader盘点",
    "盘点落位", "盘点评估", "评估过程记录",
    "930名单", "职级定级", "晋升确认", "晋升评审", "述职",
    # 数值表达：不含主题词但本身就是薪酬信息（如「上调 3,600 元」「涨幅 20%」）
    "元/月", "元至", "涨幅", "月薪", "年薪",
)

# 句子切分：中文句末标点后切
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；])")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


def _has_marker(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in CLEANUP_MARKERS)


def _is_fingerprint_line(line: str) -> bool:
    return line.strip().startswith("- \"") and "@" in line


def _is_reference_line(line: str) -> bool:
    """参考来源行：- [路径:行号] 描述"""
    return bool(re.match(r"^\s*-\s*\[[^\]]+:\d+\]", line))


def _clean_wikilinks(line: str) -> tuple[str, int]:
    """删除指向敏感页面的 [[wikilink]]；返回 (新行, 删除数)。"""
    removed = 0

    def _sub(m: re.Match) -> str:
        nonlocal removed
        target = m.group(1)
        display = target.split("|", 1)[1] if "|" in target else target
        if matched_sensitive_term(target) or matched_sensitive_term(display):
            removed += 1
            return ""
        return m.group(0)

    return _WIKILINK_RE.sub(_sub, line), removed


def _clean_sentences(line: str) -> tuple[str, int]:
    """从段落行中删除含敏感标记的整句。

    **刻意不做分句级剥离**（只删句中含敏感词的那半句）。实测该做法会泄漏残片：
    敏感分句内部本身含逗号（「月度薪资上调 5,000 元至 47,000 元/月（涨幅
    11.90%），2026 年 7 月生效」），按逗号切开后碎片不再含「薪资」标记，
    会被误判为安全而保留，产出「000 元至 47,000 元/月（涨幅 11.90%）」这种
    残缺但仍有泄漏价值的文本。安全过滤必须 fail-closed：整句命中即整句删除。

    代价是同一句里的正常信息会被连带删除（如「2026 年 7 月晋升至 5 级」），
    这是可接受的——信息损失远小于泄漏残片。
    """
    if not _has_marker(line):
        return line, 0
    sentences = _SENTENCE_SPLIT_RE.split(line)
    kept = [s for s in sentences if s and not _has_marker(s)]
    removed = len([s for s in sentences if s and _has_marker(s)])
    if not removed:
        return line, 0
    # 保留原有缩进
    indent = line[: len(line) - len(line.lstrip())]
    return indent + "".join(kept).strip(), removed


# 残留检测：清洗后仍像薪酬/绩效数据的模式。
# 只报告不自动删——用于验证 fail-closed 是否奏效（若出现残留说明有规则漏洞）。
_RESIDUAL_PATTERNS = [
    re.compile(r"\d[\d,]*\s*元"),
    re.compile(r"绩效\s*[ABCD][+级]?"),
    re.compile(r"930\s*名单"),
    re.compile(r"涨幅"),
]


def find_residuals(text: str) -> list[str]:
    """返回清洗后文本中的疑似残留片段。"""
    found: list[str] = []
    for pattern in _RESIDUAL_PATTERNS:
        for m in pattern.finditer(text):
            start = max(0, m.start() - 25)
            found.append(text[start:m.end() + 25].replace("\n", " "))
    return found


def clean_page(text: str) -> tuple[str, dict]:
    """返回 (清理后文本, 统计)。"""
    out_lines: list[str] = []
    stats = {"lines": 0, "sentences": 0, "fingerprints": 0, "refs": 0, "links": 0}

    for line in text.split("\n"):
        # 1. 指向敏感文档的指纹条目
        if _is_fingerprint_line(line) and matched_sensitive_term(line):
            stats["fingerprints"] += 1
            continue
        # 2. 引用敏感文档的参考行
        if _is_reference_line(line) and matched_sensitive_term(line):
            stats["refs"] += 1
            continue
        # 3. 悬空 wikilink（指向敏感主题）
        new_line, n_links = _clean_wikilinks(line)
        if n_links:
            stats["links"] += n_links
            line = new_line
            if not line.strip():
                continue
        # 4. 按句清洗（对列表项同样适用，保住同行的正常信息）
        line, n_sent = _clean_sentences(line)
        stats["sentences"] += n_sent
        # 5. 兜底：清洗后仍含标记（如整行无句末标点、或分句间无标点）
        #    → 整行删除。fail-closed，宁可多删不可漏。
        if line.strip() and _has_marker(line):
            stats["lines"] += 1
            continue
        out_lines.append(line)

    # 收敛连续空行
    result: list[str] = []
    for line in out_lines:
        if line.strip() == "" and result and result[-1].strip() == "":
            continue
        result.append(line)
    return "\n".join(result), stats


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 Wiki 中的敏感内容")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认仅预览）")
    parser.add_argument("--wiki-root", default="", help="覆盖 Wiki 根目录")
    parser.add_argument("--include-bak", action="store_true",
                        help="一并处理 .bak.*.md 备份文件")
    args = parser.parse_args()

    wiki_root = args.wiki_root or os.environ.get("IRIS_WIKI_ROOT", "")
    if not wiki_root:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("IRIS_WIKI_ROOT"):
                    wiki_root = os.path.expandvars(line.split("=", 1)[1].strip().strip('"'))
    root = Path(wiki_root)
    if not root.is_dir():
        print(f"❌ Wiki 根目录不存在: {root}", file=sys.stderr)
        return 1

    if args.apply:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = root.parent / f"{root.name}-backup-{ts}"
        shutil.copytree(root, backup)
        print(f"📦 已备份 → {backup}\n")

    targets: list[Path] = []
    for p in root.rglob("*.md"):
        if not args.include_bak and ".bak." in p.name:
            continue
        targets.append(p)

    total = {"files": 0, "lines": 0, "sentences": 0,
             "fingerprints": 0, "refs": 0, "links": 0}
    residual_files: list[tuple[str, list[str]]] = []
    for path in sorted(targets):
        original = path.read_text(encoding="utf-8")
        cleaned, stats = clean_page(original)
        if sum(stats.values()) == 0:
            continue
        total["files"] += 1
        for k in stats:
            total[k] += stats[k]
        rel = path.relative_to(root)
        print(f"{'✅' if args.apply else '·'} {rel}")
        print(f"    指纹 {stats['fingerprints']} | 参考行 {stats['refs']} | "
              f"列表行 {stats['lines']} | 句子 {stats['sentences']} | 链接 {stats['links']}")
        residuals = find_residuals(cleaned)
        if residuals:
            residual_files.append((str(rel), residuals))
        if args.apply:
            path.write_text(cleaned, encoding="utf-8")

    if residual_files:
        print(f"\n⚠️  {len(residual_files)} 个文件清洗后仍有疑似残留（需人工复核）：")
        for rel, items in residual_files[:15]:
            print(f"  {rel}")
            for item in items[:3]:
                print(f"      …{item}…")
    else:
        print("\n✅ 残留检测通过：清洗后无薪酬/绩效数据残留")

    print(f"\n{'已清理' if args.apply else '将清理'} {total['files']} 个文件")
    print(f"  指纹 {total['fingerprints']} | 参考行 {total['refs']} | "
          f"列表行 {total['lines']} | 句子 {total['sentences']} | 链接 {total['links']}")
    if not args.apply:
        print("\n（预览模式，未写入。加 --apply 执行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
