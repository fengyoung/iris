"""Wiki 批量候选发现服务 — 适配 4 种页面类型。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle
from iris.ingest.chunker import ChunkSlim

from ._constants import get_wiki_dir, get_wiki_prefix
from ._wiki_io import slugify_title
from .discovery_rules import GENERIC_TERM_SUPPRESS, PERSON_PATTERNS
from .discovery_types import CandidateItem
from .discovery_utils import (
    append_sample, normalize_title, find_parent_title, canonicalize_title,
    infer_page_type, is_high_value_title, is_high_value_term,
    path_weight, extract_terms, extract_persons, build_candidates,
    suppress_path_concentrated_noise, cluster_and_resolve,
    is_wiki_stale, parse_wiki_generated_at,
)
from .searcher import WikiSearcher


# 周报模板固定文本/章节标题噪音（出现在内容或 section_path 中的非主题文本，不应成为 Wiki 候选）
_WIKI_NOISE_TITLES = {
    "本内容由AI",
    "邮件信息",
    "周报内容",
    "本周工作",
    "本周工作总结",
    "下周计划",
    "关键指标",
    "关键指标/数据",
    "遇到的问题与风险",
    "需要协调的资源",
}
_EMOJI_PREFIX_NOISE = ("💼", "📋", "📌", "🔔", "⏰", "✅")


def is_noise_candidate(title: str) -> bool:
    """判断候选标题是否为周报模板等固定文本噪音。"""
    t = title.strip()
    if t in _WIKI_NOISE_TITLES:
        return True
    if t.startswith(_EMOJI_PREFIX_NOISE):
        return True
    return False


class CandidateDiscovery:
    """从文档 chunks 中发现潜在的 Wiki 候选（4 种页面类型）。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._metadata_root = config.root / "data" / "metadata"

    def discover(self, *, limit: int = 20, incremental: bool = False) -> List[CandidateItem]:
        chunks = self._load_chunks()
        project_counter: Counter[str] = Counter()
        domain_counter: Counter[str] = Counter()
        concept_counter: Counter[str] = Counter()
        person_counter: Counter[str] = Counter()
        evidence_counter: Counter[str] = Counter()
        sample_paths: Dict[str, List[str]] = defaultdict(list)

        for chunk in chunks:
            titles = [part.strip() for part in chunk.section_path if part.strip()] or [chunk.title]
            chunk_weight = path_weight(chunk.relative_path)
            parent_title = find_parent_title(titles)

            for raw_title in titles:
                title = normalize_title(raw_title)
                if not title:
                    continue
                page_type = infer_page_type(title)
                canonical = canonicalize_title(title, page_type=page_type, parent_title=parent_title)
                if not canonical or not is_high_value_title(canonical, page_type):
                    continue

                score = chunk_weight + (4 if page_type == "project" else 2 if page_type == "domain" else 1)
                if page_type == "project":
                    project_counter[canonical] += score
                elif page_type == "domain":
                    domain_counter[canonical] += score
                evidence_counter[canonical] += 1
                append_sample(sample_paths.setdefault(canonical, []), chunk.relative_path)

            # 概念/人物提取：使用全文（若有），回退到预览
            content = chunk.content or chunk.content_preview
            for term in extract_terms(content):
                term_clean = term.strip().strip("()[]（）【】")
                if not term_clean or not is_high_value_term(term_clean):
                    continue
                # 短词精确匹配，长词/短语允许子串匹配（避免短如 "AI" 误杀 "MAIN" 等合法术语）
                if not any((t == term_clean) if len(t) <= 3 else (t in term_clean)
                          for t in GENERIC_TERM_SUPPRESS) and len(term_clean) >= 2:
                    concept_counter[term_clean] += 1
                    evidence_counter[term_clean] += 1
                    append_sample(sample_paths.setdefault(term_clean, []), chunk.relative_path)

            # 人物提取
            for person in extract_persons(content):
                if len(person) >= 2:
                    person_counter[person] += 1
                    evidence_counter[person] += 1
                    append_sample(sample_paths.setdefault(person, []), chunk.relative_path)

        # 构建各类型候选（优先使用 wiki.json 中的 discovery.evidence_thresholds 配置）
        evidence_thresholds: Optional[Dict[str, int]] = None
        if self._config.wiki:
            evidence_thresholds = (
                self._config.wiki.get("discovery", {}).get("evidence_thresholds")
            ) or None
        candidates: list[CandidateItem] = []
        candidates.extend(build_candidates(project_counter, evidence_counter, sample_paths, "project", evidence_thresholds))
        candidates.extend(build_candidates(domain_counter, evidence_counter, sample_paths, "domain", evidence_thresholds))
        candidates.extend(build_candidates(concept_counter, evidence_counter, sample_paths, "concept", evidence_thresholds))
        candidates.extend(build_candidates(person_counter, evidence_counter, sample_paths, "person", evidence_thresholds))

        candidates = suppress_path_concentrated_noise(candidates)
        candidates = cluster_and_resolve(candidates)
        # 过滤周报模板固定文本/章节标题噪音（如「本内容由AI」「💼 本周工作」）
        candidates = [c for c in candidates if not is_noise_candidate(c.title)]

        # 按类型分层排序，确保每种类型都有展示
        per_type_min = max(limit // 5, 3)
        selected: list[CandidateItem] = []
        seen_keys: set = set()
        for pt in ("domain", "concept", "project", "person"):
            pool = sorted(
                [c for c in candidates if c.page_type == pt],
                key=lambda c: (-c.score, -c.evidence_count, c.title),
            )
            for item in pool[:per_type_min]:
                key = (item.page_type, item.title)
                if key not in seen_keys:
                    seen_keys.add(key)
                    selected.append(item)

        # 用剩余名额补充高分候选
        remaining = sorted(
            [c for c in candidates if (c.page_type, c.title) not in seen_keys],
            key=lambda c: (-c.score, -c.evidence_count, c.title),
        )
        selected.extend(remaining[:limit - len(selected)])
        selected.sort(key=lambda c: (-c.score, -c.evidence_count, c.title))

        # 始终检查已有 Wiki 页面（设置 has_wiki / wiki_stale），
        # 仅在增量模式下过滤掉已有且非 stale 的候选
        selected = self._check_existing_wiki(selected)
        if incremental:
            selected = [c for c in selected if not c.has_wiki or c.wiki_stale]

        return selected[:limit]

    def _check_existing_wiki(self, candidates: List[CandidateItem]) -> List[CandidateItem]:
        """为每个候选检查是否已有 Wiki 页面，设置 has_wiki / wiki_stale 字段。"""
        if not self._config.wiki:
            return candidates
        wiki_root = Path(self._config.wiki["wiki_root"])
        if not wiki_root.exists():
            return candidates
        # 加载源文档 hash 索引（一次），供 source_fingerprint 精准过时判定
        try:
            from iris.ingest.chunker import MarkdownChunker
            hash_index = MarkdownChunker.load_hash_index(self._config)
        except Exception:
            hash_index = {}
        checked = []
        for item in candidates:
            wiki_path = self._find_wiki_path(item)
            if wiki_path:
                item = CandidateItem(title=item.title, page_type=item.page_type, query=item.query,
                                     score=item.score, evidence_count=item.evidence_count,
                                     sample_paths=item.sample_paths, rationale=item.rationale,
                                     has_wiki=True, wiki_stale=is_wiki_stale(wiki_path, hash_index=hash_index),
                                     wiki_path=str(wiki_path))
            checked.append(item)
        return checked

    def _find_wiki_path(self, item: CandidateItem) -> Optional[Path]:
        """在 Wiki 目录中查找候选对应的页面文件（使用与页面创建一致的 slug 化标题）。"""
        if not self._config.wiki:
            return None
        wiki_root = Path(self._config.wiki["wiki_root"])
        subdir = get_wiki_dir(item.page_type)
        prefix = get_wiki_prefix(item.page_type)
        slug = slugify_title(item.title)
        expected = wiki_root / subdir / f"{prefix}{slug}.md"
        if expected.exists():
            return expected
        return None

    def _load_chunks(self):
        from iris.ingest import iter_chunk_items
        chunks = []
        for item in iter_chunk_items(self._metadata_root, self._config.data_source.get("sources", {})):
            try:
                chunks.append(ChunkSlim.from_dict(item))
            except (TypeError, ValueError):
                continue
        return chunks

    def export_jsonl(self, candidates: List[CandidateItem], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for item in candidates:
                f.write(json.dumps(item.to_batch_json(), ensure_ascii=False) + "\n")
        return path

    def export_review_jsonl(self, candidates: List[CandidateItem], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for item in candidates:
                f.write(json.dumps(item.to_review_record(), ensure_ascii=False) + "\n")
        return path

    def export_review_markdown(self, candidates: List[CandidateItem], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Wiki 候选审核清单\n"]
        type_names = {"domain": "领域", "concept": "概念", "project": "项目", "person": "人物"}
        for ptype in ("domain", "concept", "project", "person"):
            items = [c for c in candidates if c.page_type == ptype]
            if not items:
                continue
            lines.append(f"## {type_names[ptype]}")
            for item in items:
                has = "✓" if item.has_wiki else " "
                stale = " ⚠️过时" if item.wiki_stale else ""
                lines.append(f"- [{has}] **{item.title}** (score={item.score}){stale}")
                if item.rationale:
                    lines.append(f"  - 理由：{item.rationale}")
                if item.sample_paths:
                    lines.append(f"  - 来源：{item.sample_paths[0]}")
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
