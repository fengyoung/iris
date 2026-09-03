"""Wiki 候选发现工具函数。"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ._constants import PAGE_TYPE_PRIORITY
from .discovery_rules import (
    HEADING_PREFIXES, TERM_PATTERNS, STOPWORDS,
    LOW_VALUE_TITLES, LOW_VALUE_PREFIXES, STRUCTURAL_TITLES,
    PATH_WEIGHTS, MIN_TERM_LENGTH, PROJECT_SUFFIX_PATTERNS,
    LEADING_ENUM_RE, ONLY_SECTION_RE,
    GENERIC_TERM_SUPPRESS, HIGH_VALUE_TOPIC_HINTS, LOW_VALUE_PATH_HINTS,
    LOW_VALUE_TERM_PATTERNS, PERSON_PATTERNS, CANDIDATE_EVIDENCE_THRESHOLDS,
    PERSON_EXCLUSIONS,
)
from .discovery_types import CandidateItem


def append_sample(paths: List[str], relative_path: str) -> None:
    if relative_path not in paths and len(paths) < 3:
        paths.append(relative_path)


def normalize_title(title: str) -> str:
    title = title.strip().strip("#*").strip()
    title = LEADING_ENUM_RE.sub("", title).strip()
    title = re.sub(r"\s+", " ", title)
    return title


def find_parent_title(titles: List[str]) -> Optional[str]:
    for raw in titles:
        title = normalize_title(raw)
        if not title:
            continue
        if any(keyword in title for keyword, pt in HEADING_PREFIXES if pt == "project"):
            return title
        if len(title) >= 6 and not ONLY_SECTION_RE.match(title):
            return title
    return None


def canonicalize_title(title: str, *, page_type: str, parent_title: Optional[str] = None) -> str:
    result = title
    if page_type == "project":
        for pattern in PROJECT_SUFFIX_PATTERNS:
            result = pattern.sub("", result).strip()
    if ONLY_SECTION_RE.match(result) or result in STRUCTURAL_TITLES:
        if parent_title:
            return canonicalize_title(parent_title, page_type=page_type)
        return ""
    return result.strip()


def infer_page_type(title: str) -> str:
    """从标题推断页面类型。"""
    for keyword, page_type in HEADING_PREFIXES:
        if keyword in title:
            return page_type
    return "domain"


def extract_terms(text: str) -> List[str]:
    found: List[str] = []
    for pattern in TERM_PATTERNS:
        found.extend(match.group(0) for match in pattern.finditer(text))
    return found


def extract_persons(text: str) -> List[str]:
    """从文本中提取可能的人名。"""
    import re as _re
    found: List[str] = []
    for pattern in PERSON_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            # 参会人列表：按 、，, 拆分
            parts = _re.split(r"[、，,]", raw)
            for part in parts:
                name = part.strip().rstrip(".")
                if not name or len(name) < 2 or len(name) > 6:
                    continue
                # 过滤 markdown 语法残留（如 **）
                if _re.match(r"^[!@#$%^&*_\-=+\[\]{}|:;<>,.?/~`]+$", name):
                    continue
                # 过滤非人名（Iris、角色标记、数字等）
                if name.lower() in PERSON_EXCLUSIONS or name in PERSON_EXCLUSIONS:
                    continue
                if _re.match(r"^(?:[a-zA-Z]\d+|[一-龥]+\d+)$", name):  # 如 "发言人3"
                    continue
                # 排除组织/角色类名称（以团队/组/部结尾且非单纯人名）
                if _re.search(r"(?:团队|小组|部门|系统|平台|项目)$", name):
                    continue
                if name not in found:
                    found.append(name)
    return found


def is_high_value_title(title: str, page_type: str) -> bool:
    if not title or len(title) < 2:
        return False
    if title in LOW_VALUE_TITLES or title in STRUCTURAL_TITLES:
        return False
    if title.lower() in STOPWORDS:
        return False
    if any(title.startswith(prefix) for prefix in LOW_VALUE_PREFIXES):
        return False
    if ONLY_SECTION_RE.match(title):
        return False
    if page_type == "project" and len(title) < 4:
        return False
    if page_type == "domain":
        if len(title) < 5 and not any(hint in title for hint in HIGH_VALUE_TOPIC_HINTS):
            return False
    return True


def is_high_value_term(term: str) -> bool:
    if len(term) < MIN_TERM_LENGTH:
        return False
    if term.lower() in STOPWORDS:
        return False
    if term in LOW_VALUE_TITLES or term in STRUCTURAL_TITLES:
        return False
    if any(pattern.search(term) for pattern in LOW_VALUE_TERM_PATTERNS):
        return False
    return True


def path_weight(relative_path: str) -> int:
    for keyword, weight in PATH_WEIGHTS.items():
        if keyword in relative_path:
            return weight
    return 1


def build_candidates(counter: Counter[str], evidence_counter: Counter[str],
                     sample_paths: Dict[str, List[str]], page_type: str,
                     evidence_thresholds: Optional[Dict[str, int]] = None) -> list:
    items: list = []
    min_evidence = (evidence_thresholds or CANDIDATE_EVIDENCE_THRESHOLDS).get(page_type, 3)
    for title, score in counter.items():
        evidence_count = evidence_counter[title]
        if evidence_count < min_evidence:
            continue
        if page_type == "concept" and not re.search(r"[A-Z]", title):
            continue  # 概念标题需含英文缩写或术语
        items.append(CandidateItem(title=title, page_type=page_type, query=title,
                                   score=score, evidence_count=evidence_count,
                                   sample_paths=sample_paths.get(title, [])))
    return items


def suppress_path_concentrated_noise(candidates: list) -> list:
    filtered = []
    for item in candidates:
        low_value_path_count = sum(1 for path in item.sample_paths
                                    if any(hint in path for hint in LOW_VALUE_PATH_HINTS))
        if item.page_type != "project" and item.sample_paths and low_value_path_count == len(item.sample_paths) and item.score <= 4:
            continue
        if item.page_type == "concept" and len(item.title) <= 3 and low_value_path_count >= 1:
            continue
        filtered.append(item)
    return filtered


def cluster_and_resolve(candidates: list) -> list:
    merged = []
    candidates = sorted(candidates, key=lambda item: (-item.score, -item.evidence_count, item.title))
    for item in candidates:
        if item.page_type == "concept":
            if item.title in GENERIC_TERM_SUPPRESS or len(item.title) <= 5:
                if any(item.title in other.title and other.page_type != "concept" for other in candidates):
                    continue
        matched_index = None
        for index, existing in enumerate(merged):
            if should_merge(existing, item):
                matched_index = index
                break
        if matched_index is None:
            merged.append(item)
            continue
        merged[matched_index] = merge_candidates(merged[matched_index], item)
    return merged


def should_merge(left, right) -> bool:
    if left.page_type != right.page_type:
        return normalized_key(left.title) == normalized_key(right.title)
    left_key = normalized_key(left.title)
    right_key = normalized_key(right.title)
    if left_key == right_key:
        return True
    if left.page_type == "project":
        return left_key in right_key or right_key in left_key
    if left.page_type == "domain" and (left_key in right_key or right_key in left_key):
        return min(len(left_key), len(right_key)) >= 4
    return False


def merge_candidates(left, right):
    if left.page_type != right.page_type:
        chosen = left if PAGE_TYPE_PRIORITY[left.page_type] >= PAGE_TYPE_PRIORITY[right.page_type] else right
        other = right if chosen is left else left
        return CandidateItem(title=chosen.title, page_type=chosen.page_type, query=chosen.query,
                             score=chosen.score + other.score, evidence_count=chosen.evidence_count + other.evidence_count,
                             sample_paths=merge_paths(chosen.sample_paths, other.sample_paths))
    primary = prefer_candidate_title(left, right)
    return CandidateItem(title=primary.title, page_type=primary.page_type, query=primary.query,
                         score=left.score + right.score, evidence_count=left.evidence_count + right.evidence_count,
                         sample_paths=merge_paths(left.sample_paths, right.sample_paths))


def prefer_candidate_title(left, right):
    if left.page_type == "project":
        return left if len(left.title) <= len(right.title) else right
    if left.page_type == "domain":
        return left if left.score >= right.score else right
    return left if left.evidence_count >= right.evidence_count else right


def merge_paths(left: List[str], right: List[str]) -> List[str]:
    merged = list(left)
    for path in right:
        if path not in merged and len(merged) < 3:
            merged.append(path)
    return merged


def normalized_key(title: str) -> str:
    return re.sub(r"[^A-Za-z0-9一-鿿]", "", title).lower()


def is_wiki_stale(wiki_path: Path, *, hash_index: Optional[Dict[str, Dict[str, str]]] = None) -> bool:
    """检查 Wiki 页面是否过时。

    优先按源文档指纹判定：frontmatter 的 source_fingerprint 中任一源文档
    hash 已变化（或文档已删除）→ 过时；全部未变 → 新鲜（不再重生成，省 LLM 成本）。
    无指纹（旧页面）或未提供 hash_index 时，兜底按生成天数判定（默认 30 天）。
    """
    if hash_index:
        fingerprint = parse_wiki_source_fingerprint(str(wiki_path))
        if fingerprint:
            for rel_path, digest in fingerprint.items():
                current = (hash_index.get(rel_path) or {}).get("hash", "")
                if not current or not current.startswith(digest):
                    return True
            return False
    from ._constants import STALE_DAYS_THRESHOLD
    generated_at = parse_wiki_generated_at(str(wiki_path))
    if generated_at is None:
        return True
    now = datetime.now(generated_at.tzinfo) if generated_at.tzinfo else datetime.now()
    return (now - generated_at).days >= STALE_DAYS_THRESHOLD


def parse_wiki_generated_at(wiki_path: str) -> Optional[datetime]:
    try:
        content = Path(wiki_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    match = re.search(r"^updated:\s*(.+)$", content, re.MULTILINE)
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1).strip())
    except (ValueError, TypeError):
        return None


# ── 源文档指纹（source_fingerprint）────────────────────────────
# frontmatter 格式：
#   source_fingerprint:
#     - "05-会议纪要/2026-07/xxx.md@a1b2c3d4e5f6"
# 记录页面生成时引用的源文档及其内容 hash 前缀，
# 供 is_wiki_stale 做「源文档变化 → 页面过时」的精准判定。

_FINGERPRINT_BLOCK_RE = re.compile(r"^source_fingerprint:[ \t]*\n((?:[ \t]+-[ \t]+.*\n?)*)", re.MULTILINE)


def render_source_fingerprint(fingerprint: Dict[str, str]) -> str:
    """渲染 frontmatter 指纹段（relative_path → hash 前缀），路径排序保证幂等。"""
    if not fingerprint:
        return ""
    lines = ["source_fingerprint:"]
    for rel_path in sorted(fingerprint):
        lines.append(f'  - "{rel_path}@{fingerprint[rel_path]}"')
    return "\n".join(lines)


def strip_source_fingerprint(markdown: str) -> str:
    """移除 frontmatter 中已有的 source_fingerprint 段。"""
    return _FINGERPRINT_BLOCK_RE.sub("", markdown)


def inject_source_fingerprint(markdown: str, fingerprint: Dict[str, str]) -> str:
    """将源文档指纹注入 frontmatter（幂等：已有指纹段先移除再写入）。

    无 frontmatter 时原样返回，不阻塞页面写出。
    """
    if not fingerprint:
        return markdown
    stripped = strip_source_fingerprint(markdown)
    match = re.match(r"^---[ \t]*\n(.*?)\n---", stripped, re.DOTALL)
    if not match:
        return markdown
    block = render_source_fingerprint(fingerprint)
    new_front = match.group(1).rstrip("\n") + "\n" + block
    return stripped[:match.start(1)] + new_front + stripped[match.end(1):]


def parse_wiki_source_fingerprint(wiki_path: str) -> Dict[str, str]:
    """从 Wiki 页面读取 source_fingerprint（relative_path → hash 前缀）。"""
    try:
        content = Path(wiki_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    match = _FINGERPRINT_BLOCK_RE.search(content)
    if not match:
        return {}
    fingerprint: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        item = line.strip().lstrip("-").strip().strip('"').strip("'")
        rel_path, sep, digest = item.rpartition("@")
        if sep and rel_path and digest:
            fingerprint[rel_path] = digest
    return fingerprint
