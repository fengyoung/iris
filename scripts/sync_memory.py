#!/usr/bin/env python3
"""sync-memory — 将 Claude Code 系统记忆同步到 Iris 长期记忆。

读取 .claude/projects/<slug>/memory/ 下的 Markdown 文件，
根据元数据标记或兜底规则分类，转换为 Iris 的 profile.json 和
corrections.json 格式，增量写入。

用法：
    python scripts/run_cli.py sync-memory --pretty
    python scripts/run_cli.py sync-memory --pretty --dry-run
    python scripts/sync_memory.py --project-root . --pretty --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ── 常量 ────────────────────────────────────────────────────
_METADATA_BLOCK_RE = re.compile(r"^metadata:\s*$", re.MULTILINE)
_DESCRIPTION_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)
_TYPE_RE = re.compile(r"^type:\s*(\w+)$", re.MULTILINE)
_NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_SYNC_TO_IRIS_RE = re.compile(r"^sync_to_iris:\s*(true|false)$", re.MULTILINE)
_IRIS_TARGET_RE = re.compile(r"^iris_target:\s*(\S+)$", re.MULTILINE)
_LIKE_RE = re.compile(r"(?:我喜欢|我偏好|我喜欢|我偏好)\s*(.+?)(?:[。；;]|$)")
_DISLIKE_RE = re.compile(r"(?:我不喜欢|我不希望|我不要)\s*(.+?)(?:[。；;]|$)")
_CORRECTION_RE = re.compile(
    r"(?:纠正|(?P<concept>\S{1,40})\s*(?:不是|应该|应|指))\S*\s*(?P<value>.+?)(?:[。\n]|$)"
)


# ── 系统记忆路径 ──────────────────────────────────────────

def _system_memory_dir(project_root: Path) -> Path:
    """根据项目根目录推导 Claude Code 系统记忆路径。"""
    slug = "-" + str(project_root.resolve()).lstrip("/").replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


# ── Frontmatter 解析 ─────────────────────────────────────

def _parse_frontmatter(text: str) -> Dict[str, Any]:
    """解析 Markdown frontmatter，兼容嵌套 metadata 写法。"""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fm_text = parts[1]

    result: Dict[str, Any] = {}

    # 顶层字段
    result["name"] = _extract_yaml_str(fm_text, "name")
    result["description"] = _extract_yaml_str(fm_text, "description")
    result["type"] = _extract_yaml_str(fm_text, "type")
    result["sync_to_iris"] = _extract_yaml_bool(fm_text, "sync_to_iris")
    result["iris_target"] = _extract_yaml_str(fm_text, "iris_target")

    # 处理嵌套 metadata: 块（缩进子字段）
    meta_match = re.search(r"^metadata:\s*$(.+?)(?:^\S|\Z)", fm_text, re.MULTILINE | re.DOTALL)
    if meta_match:
        meta_text = meta_match.group(1)
        if not result["type"]:
            result["type"] = _extract_yaml_str(meta_text, "type")
        if not result["sync_to_iris"]:
            result["sync_to_iris"] = _extract_yaml_bool(meta_text, "sync_to_iris")
        if not result["iris_target"]:
            result["iris_target"] = _extract_yaml_str(meta_text, "iris_target")

    return result


def _extract_yaml_str(text: str, key: str) -> str:
    m = re.search(rf"^\s*{key}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip().strip("\"'") if m else ""


def _extract_yaml_bool(text: str, key: str) -> Optional[bool]:
    raw = _extract_yaml_str(text, key).lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    return None


def _extract_body(text: str) -> str:
    """去除 frontmatter 后的正文。"""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2] if len(parts) >= 3 else text


def _extract_why_and_how(body: str) -> str:
    """从正文中提取 Why + How to apply 区段，用于纠正规则的值。"""
    result_parts = []
    for section in ("**Why:**", "**How to apply:**"):
        idx = body.find(section)
        if idx >= 0:
            segment = body[idx + len(section):]
            # 截断到下一个 ** 标题或文末
            next_section = re.search(r"\*\*[A-Z]", segment)
            if next_section:
                segment = segment[:next_section.start()]
            result_parts.append(segment.strip())
    if result_parts:
        return "；".join(result_parts)[:500]
    # 回退：取第一段实质内容
    lines = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]
    return lines[0][:500] if lines else body.strip()[:500]


# ── 分类规则 ──────────────────────────────────────────────

def _classify(fm: Dict[str, Any], body: str) -> Optional[str]:
    """返回 Iris 目标类型，或 None 表示不同步。"""
    # 1. 显式标记优先级最高
    sync_flag = fm.get("sync_to_iris")
    if sync_flag is False:
        return None
    if sync_flag is True:
        target = fm.get("iris_target", "")
        if target:
            return target
        # 有标记但无 target，回退到规则

    # 2. 兜底规则
    mem_type = fm.get("type", "")
    if mem_type == "project":
        return None
    if mem_type == "user":
        return "profile"
    if mem_type == "reference":
        return "profile_notes"
    if mem_type == "feedback":
        # 判断是否为纠正类
        if re.search(r"纠正|不是.*而是|应该是|应为|指的是|纠正规则", body):
            return "corrections"
        if _LIKE_RE.search(body):
            return "profile_likes"
        if _DISLIKE_RE.search(body):
            return "profile_dislikes"
        return "profile_notes"
    return None


# ── 内容提取 ──────────────────────────────────────────────

def _extract_correction_entry(fm: Dict[str, Any], body: str) -> Tuple[str, Dict[str, Any]]:
    """从反馈记忆提取纠正条目 -> (concept, entry)。"""
    concept = fm.get("name", fm.get("description", ""))
    # 美化 concept 名
    concept = concept.replace("-", " ").replace("_", " ").strip()
    if len(concept) > 40:
        concept = fm.get("description", concept[:40])

    preferred = _extract_why_and_how(body)
    # 如果 description 更精确就用它作为 preferred 的前缀
    desc = fm.get("description", "")
    if desc and desc not in preferred:
        preferred = desc + "：" + preferred

    return concept, {
        "preferred": preferred[:800],
        "update_count": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "last_source": fm.get("name", ""),
    }


def _extract_likes(fm: Dict[str, Any], body: str) -> List[str]:
    """提取用户偏好（喜欢）。"""
    items: List[str] = []
    for m in _LIKE_RE.finditer(body):
        val = m.group(1).strip(" ，,、")
        if val and len(val) < 40:
            items.append(val)
    # 也检查 description
    desc = fm.get("description", "")
    if "爱好" in desc or "嗜好" in desc:
        for part in desc.split("、"):
            part = part.strip()
            if part and len(part) < 20:
                items.append(part)
    return list(dict.fromkeys(items))  # 去重保序


def _concept_overlap(desc: str, concept: str) -> bool:
    """判断 description 和 concept 是否为同一概念的表述（中英文/简繁变体）。

    用于去重：如 desc="飞书操作的两条核心规则" ↔ concept="飞书操作规则"。
    采用双向前缀匹配 + 核心字符重叠率检测。
    """
    if not desc or not concept:
        return False
    # 提取中文字符
    desc_cn = "".join(ch for ch in desc if "一" <= ch <= "鿿")
    concept_cn = "".join(ch for ch in concept if "一" <= ch <= "鿿")
    if len(desc_cn) < 2 or len(concept_cn) < 2:
        return False
    # 双向包含
    if concept_cn in desc_cn or desc_cn in concept_cn:
        return True
    # 字符重叠率 ≥ 60%
    common = set(desc_cn) & set(concept_cn)
    overlap = len(common) / min(len(set(desc_cn)), len(set(concept_cn)))
    return overlap >= 0.6


def _extract_dislikes(fm: Dict[str, Any], body: str) -> List[str]:
    """提取用户偏好（不喜欢）。"""
    items: List[str] = []
    for m in _DISLIKE_RE.finditer(body):
        val = m.group(1).strip(" ，,、")
        if val and len(val) < 40:
            items.append(val)
    return list(dict.fromkeys(items))


def _extract_note(fm: Dict[str, Any], body: str) -> str:
    """提取备注文本。"""
    # 优先取 Why 段落
    why_idx = body.find("**Why:**")
    if why_idx >= 0:
        note = body[why_idx + 7:].strip()
        # 截取第一句
        end = note.find("\n\n")
        if end > 0:
            note = note[:end]
        return note.strip()[:500]

    # reference 类：取 description + 第一段
    desc = fm.get("description", "")
    lines = [line.strip() for line in body.splitlines() if line.strip() and not line.strip().startswith("#")]
    first_line = lines[0] if lines else ""
    return f"{desc}：{first_line}"[:500] if desc else first_line[:500]


def _extract_persona(fm: Dict[str, Any], body: str) -> str:
    """提取 Iris 人设描述。"""
    desc = fm.get("description", "")
    # 从正文取关键要点
    lines = [line.strip() for line in body.splitlines() if line.strip().startswith("- **")]
    if lines:
        return lines[0].lstrip("- ").replace("**", "")[:300]
    return desc[:300]


# ── 核心同步逻辑 ──────────────────────────────────────────

def run_sync(
    system_memory_dir: Path,
    iris_memory_dir: Path,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """执行一次同步，返回变更摘要。"""
    if not system_memory_dir.exists():
        return {"error": f"系统记忆目录不存在: {system_memory_dir}"}

    # 加载 Iris 当前记忆
    profile_path = iris_memory_dir / "long_term" / "profile.json"
    corrections_path = iris_memory_dir / "long_term" / "corrections.json"

    iris_profile = _load_json(profile_path, default={
        "iris_persona": {},
        "user_preferences": {"likes": [], "dislikes": [], "style_preferences": [], "notes": []},
        "updated_at": None,
    })
    iris_corrections = _load_json(corrections_path, default={
        "items": {},
        "updated_at": None,
    })

    # 扫描系统记忆文件
    system_files = sorted(system_memory_dir.glob("*.md"))
    if not system_files:
        return {"synced": False, "reason": "系统记忆目录为空"}

    stats: Dict[str, Any] = {
        "scanned": len(system_files),
        "profile_likes_added": 0,
        "profile_dislikes_added": 0,
        "profile_notes_added": 0,
        "corrections_added": 0,
        "corrections_updated": 0,
        "skipped": 0,
        "details": [],
    }

    likes_set = set(iris_profile.setdefault("user_preferences", {}).setdefault("likes", []))
    dislikes_set = set(iris_profile.setdefault("user_preferences", {}).setdefault("dislikes", []))
    notes_list: List[str] = iris_profile.setdefault("user_preferences", {}).setdefault("notes", [])
    corrections_items: Dict[str, Any] = iris_corrections.setdefault("items", {})

    persona_updated = False

    for sf in system_files:
        text = sf.read_text(encoding="utf-8", errors="ignore")
        fm = _parse_frontmatter(text)
        body = _extract_body(text)
        target = _classify(fm, body)

        if target is None:
            stats["skipped"] += 1
            continue

        filename = sf.name

        if target == "corrections":
            concept, entry = _extract_correction_entry(fm, body)
            existing = corrections_items.get(concept, {})
            dedup_matched = False  # 是否通过去重匹配到已有条目

            # 去重：检查是否有其他条目已引用同一 CC 文件（防止英文 slug
            # 与中文概念名重复，如 "deep discussion rule" vs "深入讨论流程"）。
            # Iris 自身创建的条目 last_source 可能是 "合并自: ..., file_slug, ..."，
            # 因此用子串匹配而非精确匹配。
            if not existing:
                file_slug = fm.get("name", "")
                if file_slug:
                    for exist_key, exist_val in corrections_items.items():
                        exist_source = exist_val.get("last_source", "")
                        # 同时匹配 kebab-case（deep-discussion-rule）和空格形式（deep discussion rule）
                        if file_slug in exist_source or file_slug.replace("-", " ") in exist_source:
                            # 已有条目覆盖同一 CC 文件，更新该条目而非新建
                            existing = exist_val
                            concept = exist_key
                            dedup_matched = True
                            break

            # 内容去重：如果新条目 concept 是纯英文，用 CC 文件的 description
            # 与已有中文概念名做模糊匹配，防止同一条规则中英文各存一份。
            # 如 "document signature rule" → "文档签名规则"
            if not existing:
                file_desc = fm.get("description", "")
                if file_desc:
                    for exist_key, exist_val in corrections_items.items():
                        # description 的核心词包含在已有概念名中，或反之
                        if _concept_overlap(file_desc, exist_key):
                            existing = exist_val
                            concept = exist_key
                            dedup_matched = True
                            break

            if existing.get("preferred") == entry["preferred"]:
                continue  # 未变化

            # 去重匹配到的中文条目通常更完整（经多次合并），新的英文版
            # preferred 较短时跳过，避免用不完整内容覆盖
            if dedup_matched and len(existing.get("preferred", "")) > len(entry.get("preferred", "")):
                continue
            entry["update_count"] = existing.get("update_count", 0) + 1
            corrections_items[concept] = entry
            if existing:
                stats["corrections_updated"] += 1
                stats["details"].append(f"更新纠正: {concept}")
            else:
                stats["corrections_added"] += 1
                stats["details"].append(f"新增纠正: {concept}")

        elif target == "profile_likes":
            for item in _extract_likes(fm, body):
                if item not in likes_set:
                    likes_set.add(item)
                    stats["profile_likes_added"] += 1
                    stats["details"].append(f"新增偏好(喜欢): {item}")

        elif target == "profile_dislikes":
            for item in _extract_dislikes(fm, body):
                if item not in dislikes_set:
                    dislikes_set.add(item)
                    stats["profile_dislikes_added"] += 1
                    stats["details"].append(f"新增偏好(避免): {item}")

        elif target == "profile_notes":
            note = _extract_note(fm, body)
            if note and note not in notes_list:
                notes_list.append(note)
                stats["profile_notes_added"] += 1
                stats["details"].append(f"新增备注: {note[:80]}")

        elif target == "profile":
            # 处理用户人设
            if "iris" in filename.lower() or "身份" in filename:
                persona = _extract_persona(fm, body)
                if persona and iris_profile.get("iris_persona", {}).get("description") != persona:
                    iris_profile.setdefault("iris_persona", {})["description"] = persona
                    persona_updated = True
                    stats["details"].append("更新 Iris 人设")
            # 同时提取 likes/dislikes/notes
            for item in _extract_likes(fm, body):
                if item not in likes_set:
                    likes_set.add(item)
                    stats["profile_likes_added"] += 1
            for item in _extract_dislikes(fm, body):
                if item not in dislikes_set:
                    dislikes_set.add(item)
                    stats["profile_dislikes_added"] += 1

    # 回写
    has_changes = bool(
        stats["profile_likes_added"]
        or stats["profile_dislikes_added"]
        or stats["profile_notes_added"]
        or stats["corrections_added"]
        or stats["corrections_updated"]
        or persona_updated
    )

    if has_changes:
        iris_profile["user_preferences"]["likes"] = sorted(likes_set)
        iris_profile["user_preferences"]["dislikes"] = sorted(dislikes_set)
        iris_profile["user_preferences"]["notes"] = notes_list
        iris_profile["updated_at"] = datetime.now().isoformat(timespec="seconds")
        iris_corrections["updated_at"] = datetime.now().isoformat(timespec="seconds")

        if not dry_run:
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text(
                json.dumps(iris_profile, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            corrections_path.parent.mkdir(parents=True, exist_ok=True)
            corrections_path.write_text(
                json.dumps(iris_corrections, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    stats["synced"] = has_changes
    stats["dry_run"] = dry_run
    return stats


def _load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return default


# ── CLI 入口 ──────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="同步 Claude Code 系统记忆到 Iris 长期记忆")
    p.add_argument("--project-root", default=".", help="Iris 项目根目录")
    p.add_argument("--pretty", action="store_true", help="人类可读输出")
    p.add_argument("--dry-run", action="store_true", help="仅预览，不写入")
    return p


def _format_pretty(stats: Dict[str, Any]) -> str:
    lines = ["## 记忆同步"]
    if stats.get("error"):
        lines.append(f"  错误：{stats['error']}")
        return "\n".join(lines)
    if not stats.get("synced"):
        lines.append(f"  扫描 {stats.get('scanned', 0)} 个文件，无变更，跳过 {stats.get('skipped', 0)} 个")
        if stats.get("dry_run"):
            lines.append("  (dry-run 模式)")
        return "\n".join(lines)

    lines.append(f"  扫描：{stats.get('scanned', 0)} 个系统记忆文件")
    lines.append(f"  跳过：{stats.get('skipped', 0)} 个（project 或标记 false）")
    if stats.get("profile_likes_added"):
        lines.append(f"  偏好(喜欢)：+{stats['profile_likes_added']} 条")
    if stats.get("profile_dislikes_added"):
        lines.append(f"  偏好(避免)：+{stats['profile_dislikes_added']} 条")
    if stats.get("profile_notes_added"):
        lines.append(f"  备注：+{stats['profile_notes_added']} 条")
    if stats.get("corrections_added"):
        lines.append(f"  纠正规则：+{stats['corrections_added']} 条")
    if stats.get("corrections_updated"):
        lines.append(f"  纠正规则：Δ{stats['corrections_updated']} 条")
    if stats.get("dry_run"):
        lines.append("  (dry-run 模式，未实际写入)")
    if stats.get("details"):
        lines.append("")
        lines.append("  详情：")
        for d in stats["details"]:
            lines.append(f"    · {d}")
    return "\n".join(lines)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    sys_mem_dir = _system_memory_dir(project_root)

    # Iris 记忆目录：memory/long_term/
    iris_mem_dir = project_root / "memory"

    result = run_sync(sys_mem_dir, iris_mem_dir, dry_run=args.dry_run)

    if args.pretty:
        print(_format_pretty(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if not result.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
