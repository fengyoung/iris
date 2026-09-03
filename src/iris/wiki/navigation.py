"""Wiki 导航维护 — 维护 index.md 和 changelog.md。"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle

logger = logging.getLogger(__name__)

from .searcher import FRONTMATTER_RE
from ._constants import (
    get_display_name, get_type_config_map,
    LINT_STALE_DAYS,
)

# 向下兼容别名（推荐直接使用 get_type_config_map()）
PAGE_TYPE_CONFIG = get_type_config_map()


@dataclass(frozen=True)
class NavBuildResult:
    nav_path: str
    pages_written: int
    errors: List[str]


class WikiNavigationBuilder:
    """扫描 Wiki 目录并维护 index.md（总索引）。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._wiki_root = Path(config.wiki["wiki_root"]).resolve() if config.wiki else Path()

    def build(self, *, write: bool = True) -> NavBuildResult:
        """扫描 LLM-WIKI 目录，生成 index.md。"""
        if not self._wiki_root.exists():
            return NavBuildResult(nav_path="", pages_written=0, errors=["Wiki 根目录不存在"])

        from .context_loader import WikiContextLoader
        loader = WikiContextLoader(self._wiki_root)

        pages: Dict[str, List[Dict[str, str]]] = {
            "领域": [], "概念": [], "项目": [], "人物": [],
        }
        errors: List[str] = []

        for page_info in loader.load_pages():
            type_name = get_display_name(page_info.page_type)
            pages.setdefault(type_name, []).append({
                "title": page_info.title,
                "path": page_info.relative_path,
                "summary": page_info.summary[:120] if page_info.summary else "",
                "status": page_info.status,
            })

        # 生成 index.md 内容
        lines = ["# LLM-WIKI 索引", f"> 自动维护 | 最后更新：{datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]

        for section_name in ("领域", "概念", "项目", "人物"):
            section_pages = pages.get(section_name, [])
            if not section_pages:
                continue
            lines.append(f"## {section_name}")
            lines.append("")
            for p in section_pages:
                status_icon = {"stable": "", "review": " 🔍", "draft": " ✏️"}.get(p["status"], "")
                summary_text = f" — {p['summary']}" if p["summary"] else ""
                lines.append(f"- [{p['title']}]({p['path']}){status_icon}{summary_text}")
            lines.append("")

        index_content = "\n".join(lines)

        if write:
            index_path = self._wiki_root / "index.md"
            index_path.write_text(index_content, encoding="utf-8")

        total = sum(len(v) for v in pages.values())
        index_str = str(self._wiki_root / "index.md") if write else "(dry-run)"
        return NavBuildResult(nav_path=index_str, pages_written=total, errors=errors)


def append_changelog(wiki_root: Path, entry: str) -> None:
    """向 changelog.md 追加变更记录。"""
    changelog_path = wiki_root / "changelog.md"
    if not changelog_path.exists():
        changelog_path.write_text("# 变更日志\n\n", encoding="utf-8")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with changelog_path.open("a", encoding="utf-8") as f:
        f.write(f"{timestamp} {entry}\n")


# 知名技术术语白名单（LLM 生成的 Wiki 页面中引用，不应视为断裂链接）
KNOWN_TECH_TERMS = frozenset({
    # 深度学习框架 / 推理
    "TensorFlow", "PyTorch", "ONNX", "ONNX Runtime", "TensorRT", "Triton Inference Server",
    "TensorFlow Serving", "Triton", "OpenVINO",
    # 模型架构
    "BERT", "GPT", "ResNet", "YOLOv5", "YOLOv8", "DETR", "ViT", "CLIP", "BLIP",
    "DeepFM", "DIEN", "DIN", "ESMM", "MMoE", "PLE", "PinSage", "EGES", "Node2Vec",
    "GraphSAGE", "DCN", "xDeepFM", "LambdaMART", "GBDT", "XGBoost", "DNN",
    # 基础设施 / 特征工程
    "Feast", "Kafka", "HDFS", "Hive", "Spark", "Flink", "Spark Streaming",
    "A/B 实验", "AB实验",
    # 向量 / 检索
    "Faiss", "Milvus", "近似最近邻搜索（ANN）", "向量检索", "倒排索引", "BM25",
    "TF-IDF", "Word2Vec", "GloVe", "MMR", "DPP", "相关性",
    # 算法 / 方法论
    "Bandit", "MAB", "UCB", "Thompson Sampling", "ε-greedy",
    "强化学习", "联邦学习", "联邦推荐", "差分隐私",
    "协同过滤", "内容推荐", "LTR",
    # 评估指标
    "NDCG", "GAUC", "AUC", "MAP", "MRR", "CTR", "CVR", "LTV",
    # 业务指标
    "用户停留时长", "商品点击率", "收入", "GMV",
    # 数据库 / 存储
    "Redis", "Elasticsearch", "MySQL", "PostgreSQL",
    # 通用技术概念
    "User Embedding", "多模态双塔模型", "Temporal Graph Network",
    "Lambda 架构", "Kappa 架构", "流批一体",
    # 平台 / 产品 / 模型
    "A100", "H100", "LLaMA", "Qwen", "T4", "V100",
    # CV 概念
    "主体检测", "特征提取", "模型推理",
})
# 源文档引用模式（含路径前缀如 "会议纪要/20260518-..."）
SOURCE_REF_PATTERN = re.compile(r"^(?:.*/)?\d{8}-")
# 超短噪音链接目标匹配（用于 lint 判断：[[target]] 中的 target 是否为噪音）
NOISE_LINK_PATTERN = re.compile(r"^[.\-#]{1,3}$|^_{2,}$|^\.{2,}$")
# 噪音 Wiki 链接整体匹配（用于全文替换，不误删 frontmatter 的 --- 分隔符）
NOISE_WIKILINK_PATTERN = re.compile(r"\[\[(?:[.\-#]{1,3}|_{2,}|\.{2,})\]\]")

# LLM 引用不存在页面的常见模式（业务概念，非 Wiki 页面）
# 用于过滤 wiki-lint 中不应被视为断裂链接的 [[引用]]。
# 用户可根据自己的业务领域在此添加需要豁免的模式。
EXTERNAL_CONCEPT_PATTERNS = [
    re.compile(p) for p in [
        r"^demo",
        r"^项目周会 ·",
    ]
]


def _atomic_write(path: Path, text: str, bundle=None) -> None:
    """原子写入：委托给 core.write_guard.safe_write_text，统一全项目写入策略。

    Args:
        path: 目标路径
        text: 写入内容
        bundle: 可选配置对象，未提供时直接写入（用于 lint/fix 等无配置场景）
    """
    if bundle is not None:
        from iris.core.write_guard import safe_write_text
        safe_write_text(path, text, bundle, allow_existing_outside=True)
    else:
        path.write_text(text, encoding="utf-8")


_LINK_NORM_RE = re.compile(r"[\s·\-–—，,、。\.\(\)（）【】\[\]]+")
_LINK_DECIMAL_RE = re.compile(r"(\d+)\.?(\d*)")


def _norm_link(s: str) -> str:
    """去空格/标点（如 "AgenticCloud与AIAgent" ↔ "Agentic Cloud 与 AI Agent"）。"""
    return _LINK_NORM_RE.sub("", s)


def _decimalize(s: str) -> str:
    """连写数字修复（如 "v10" ↔ "v1.0", "20AI" ↔ "2.0 AI"）。"""
    return _LINK_DECIMAL_RE.sub(
        lambda m: m.group(1) + "." + m.group(2) if m.group(2) else m.group(1), s)


def _is_excluded_link(target: str) -> bool:
    """不应视为断裂的链接：源文档引用 / 超短噪音 / 技术术语 / 外部业务概念。"""
    if SOURCE_REF_PATTERN.match(target) or NOISE_LINK_PATTERN.match(target):
        return True
    if target in KNOWN_TECH_TERMS:
        return True
    return any(p.match(target) for p in EXTERNAL_CONCEPT_PATTERNS)


def _matches_any_title(target: str, page_titles: dict) -> bool:
    """按匹配阶梯逐级放宽：精确 → 子串 → 前缀 → 归一化 → 字符子序列 → 数字修复。"""
    if target in page_titles:
        return True
    # 模糊匹配（子串包含）
    if any(target in pt or pt in target for pt in page_titles):
        return True
    # 前缀匹配（如 "AlphaTeam" → "AlphaTeam2026年目标与规划"）
    if len(target) >= 4 and any(
        pt.startswith(target) or target.startswith(pt) for pt in page_titles
    ):
        return True
    # 去空格/标点后匹配
    target_norm = _norm_link(target)
    for pt in page_titles:
        pt_norm = _norm_link(pt)
        if target_norm == pt_norm:
            return True
        if len(target_norm) >= 6 and (target_norm in pt_norm or pt_norm in target_norm):
            return True
    # 字符级有序子序列匹配（如 "AlphaProject" → "AlphaProject手机拆修检测项目"）
    if len(target) >= 6 and any(_char_sequence_match(target, pt) for pt in page_titles):
        return True
    # 连写数字修复
    target_decimal = _decimalize(target_norm)
    return any(target_decimal == _decimalize(_norm_link(pt)) for pt in page_titles)


def _is_wiki_broken_link(target: str, page_titles: dict) -> str | None:
    """判断一个 [[link]] 是否为真正的断裂 Wiki 链接。

    Returns None 如果不应视为断裂（技术术语、源文档引用、噪音、可匹配到页面），
    否则返回错误标签 "broken"。
    """
    if _is_excluded_link(target) or _matches_any_title(target, page_titles):
        return None
    return "broken"


def _char_sequence_match(short: str, long: str) -> bool:
    """判断 short 的所有字符是否在 long 中按顺序出现（字符级子序列匹配）。

    例: "AlphaProject" → "AlphaProject手机拆修检测项目" → True
        "Alpha20AI" → "Alpha2.0 AI项目" → True (20 → 2.0 不在此处理)
    """
    si = 0
    for ch in long:
        if si < len(short) and ch == short[si]:
            si += 1
    return si == len(short)


def _clean_noise_links(content: str) -> str:
    """清理 Wiki 页面中的超短噪音链接（[[.]]、[[#]]、[[---]] 等）。"""
    return NOISE_WIKILINK_PATTERN.sub("", content)


def _discover_index_paths(metadata_dir: Path) -> tuple:
    """自动发现数据源的索引文件路径（替代硬编码 source_name）。

    Returns:
        (scan_path, chunk_path, vector_dir) — 任一可为 None
    """
    scan_path = None
    chunk_path = None
    vector_dir = None
    if not metadata_dir.exists():
        return scan_path, chunk_path, vector_dir
    # 按优先级查找：先找 *_scan_summary.json，再对应 chunk 和 vector
    for f in sorted(metadata_dir.glob("*_scan_summary.json")):
        stem = f.name.replace("_scan_summary.json", "")
        candidate_chunk = metadata_dir / f"{stem}_chunk_summary.json"
        candidate_vector = metadata_dir / f"{stem}_vector_index"
        if candidate_chunk.exists():
            scan_path = f
            chunk_path = candidate_chunk
            vector_dir = candidate_vector if candidate_vector.exists() else None
            break
    # fallback: 即使没有 scan，也尝试找 chunk
    if chunk_path is None:
        for f in sorted(metadata_dir.glob("*_chunk_summary.json")):
            stem = f.name.replace("_chunk_summary.json", "")
            candidate_vector = metadata_dir / f"{stem}_vector_index"
            chunk_path = f
            vector_dir = candidate_vector if candidate_vector.exists() else None
            break
    return scan_path, chunk_path, vector_dir


_LINT_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _page_age_days(content: str) -> Optional[int]:
    """从页面内容解析 updated 时间并返回距今天数；解析失败返回 None。"""
    from datetime import datetime, timezone
    updated_str = _parse_updated_from_content(content)
    if not updated_str:
        return None
    try:
        updated = datetime.fromisoformat(updated_str)
    except (ValueError, TypeError):
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).days


def _lint_scan_pages(wiki_root: Path) -> Dict[str, Any]:
    """扫描全部页面：出链、frontmatter/summary 缺失、过时、draft。"""
    from .context_loader import WikiContextLoader

    all_links: Dict[str, List[str]] = {}
    page_titles: Dict[str, Path] = {}
    page_count = 0
    no_frontmatter: List[str] = []
    no_summary: List[str] = []
    zero_outbound: List[str] = []
    stale_pages: List[str] = []
    old_pages: List[str] = []

    for page_info in WikiContextLoader(wiki_root).load_pages():
        title = page_info.title
        md_file = page_info.path
        rel = str(md_file.relative_to(wiki_root))

        if not title:
            no_frontmatter.append(rel)
        page_titles[title] = md_file
        page_count += 1

        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # 提取链接（[[title|alias]] 取 title）
        actual_links = [link.split("|")[0].strip() for link in _LINT_LINK_RE.findall(content)]
        all_links[title] = actual_links
        if not actual_links:
            zero_outbound.append(title)
        if not page_info.summary:
            no_summary.append(rel)
        # 过时检查（LINT_STALE_DAYS 天未更新）
        days = _page_age_days(content)
        if days is not None and days > LINT_STALE_DAYS:
            old_pages.append(f"{title} ({days}天)")
        # Draft 状态
        if page_info.status == "draft" and rel not in stale_pages:
            stale_pages.append(rel)

    return {
        "all_links": all_links, "page_titles": page_titles, "page_count": page_count,
        "no_frontmatter": no_frontmatter, "no_summary": no_summary,
        "zero_outbound": zero_outbound, "stale_pages": stale_pages, "old_pages": old_pages,
    }


def _lint_broken_links(all_links: Dict[str, List[str]], page_titles: dict) -> tuple[List[str], int]:
    """断裂链接（按规则过滤）→ (broken_links, raw_broken_count)。"""
    raw_broken_count = 0
    broken_links: List[str] = []
    for source_title, links in all_links.items():
        for link_title in links:
            if link_title in page_titles:
                continue
            raw_broken_count += 1
            if _is_wiki_broken_link(link_title, page_titles) is not None:
                broken_links.append(f"{source_title} → [[{link_title}]]")
    return broken_links, raw_broken_count


def _lint_index_quality(data_root: Path, page_titles: dict) -> Dict[str, Any]:
    """索引质量：扫描/切块/向量索引信息 + Wiki 覆盖度。"""
    import json as _json

    info: Dict[str, Any] = {}
    metadata_dir = data_root / "metadata"
    # 自动发现数据源（而非硬编码 "main_source"）
    scan_path, chunk_path, vector_dir = _discover_index_paths(metadata_dir)

    # 扫描信息
    info["source_documents"] = 0
    if scan_path and scan_path.exists():
        scan = _json.loads(scan_path.read_text(encoding="utf-8"))
        info["source_documents"] = scan.get("document_count", 0)
        info["last_scanned"] = scan.get("scanned_at", "")

    # 切块信息
    chunks: list = []
    if chunk_path and chunk_path.exists():
        chunk_data = _json.loads(chunk_path.read_text(encoding="utf-8"))
        chunks = chunk_data.get("chunks", [])
        sources = {c.get("relative_path", "") for c in chunks}
        info["total_chunks"] = len(chunks)
        info["chunked_documents"] = len(sources)
        if info["source_documents"] > 0:
            info["chunk_coverage_pct"] = round(len(sources) / info["source_documents"] * 100, 1)
    else:
        info["total_chunks"] = 0
        info["chunked_documents"] = 0

    # 向量索引信息
    if vector_dir and vector_dir.exists():
        vec_files = list(vector_dir.glob("*"))
        info["vector_index_exists"] = True
        info["vector_index_files"] = len(vec_files)
        info["vector_index_size_kb"] = round(
            sum(f.stat().st_size for f in vec_files if f.is_file()) / 1024, 1)
    else:
        info["vector_index_exists"] = False

    # Wiki 覆盖度
    source_files = {c["relative_path"].split("/")[0] for c in chunks if c.get("relative_path")}
    info["wiki_page_count"] = len(page_titles)
    info["wiki_source_coverage_pct"] = round(
        len(source_files) / max(info["source_documents"], 1) * 100, 1)
    return info


def lint_wiki(wiki_root: Path, data_root: Optional[Path] = None) -> Dict[str, Any]:
    """全面 Wiki 健康检查 + 索引质量检查。"""
    if not wiki_root.exists():
        return {"error": "Wiki 根目录不存在", "page_count": 0}

    # ── 扫描所有页面 ────────────────────────────────────
    scan = _lint_scan_pages(wiki_root)
    page_titles: Dict[str, Path] = scan["page_titles"]

    # ── 断裂链接（按规则过滤） ──────────────────────────
    broken_links, raw_broken_count = _lint_broken_links(scan["all_links"], page_titles)
    excluded_links = raw_broken_count - len(broken_links)  # 统计排除的链接

    # ── 孤立页（使用 BacklinkBuilder 统一检测） ──────────
    from iris.wiki.backlink import BacklinkBuilder
    backlink_builder = BacklinkBuilder(wiki_root)
    backlink_index = backlink_builder.build()
    orphan_pages = sorted(
        str(page_titles[t].relative_to(wiki_root))
        for t in backlink_index.orphans
        if t in page_titles
    )
    # 持久化反向引用索引（供 graph 等模块使用）
    if data_root:
        backlink_builder.save(data_root / "graph" / "backlink_index.json")

    # ── 索引质量检查 ────────────────────────────────────
    index_info = _lint_index_quality(data_root, page_titles) if data_root else {}

    # ── 内容质量评分 ──────────────────────────────────────
    quality = _compute_content_quality(page_titles, wiki_root)

    stale_pages = scan["stale_pages"]
    no_frontmatter = scan["no_frontmatter"]
    no_summary = scan["no_summary"]
    zero_outbound = scan["zero_outbound"]
    old_pages = scan["old_pages"]
    return {
        # Wiki 健康
        "page_count": scan["page_count"],
        "content_quality": quality,
        "by_type": {
            cfg["name"]: len([p for p in (wiki_root / cfg["dir"]).glob("*.md")
                              if ".bak." not in p.stem])
            for ptype, cfg in PAGE_TYPE_CONFIG.items()
            if (wiki_root / cfg["dir"]).exists()
        },
        "orphan_pages": orphan_pages[:20],
        "broken_links": broken_links[:50],
        "stale_pages": stale_pages[:20],
        "no_frontmatter": no_frontmatter[:20],
        "no_summary": no_summary[:20],
        "zero_outbound": zero_outbound[:20],
        "old_pages": old_pages[:20],
        "orphan_count": len(orphan_pages),
        "broken_count": len(broken_links),
        "excluded_broken": excluded_links,       # 排除的非 Wiki 链接数
        "raw_broken_count": raw_broken_count,     # 原始总和
        "stale_count": len(stale_pages),
        "no_frontmatter_count": len(no_frontmatter),
        "no_summary_count": len(no_summary),
        "zero_outbound_count": len(zero_outbound),
        "old_page_count": len(old_pages),
        # 索引质量
        "index_quality": index_info,
    }


def fix_wiki(wiki_root: Path) -> Dict[str, Any]:
    """自动修复 Wiki 常见问题。"""
    import re as _re

    if not wiki_root.exists():
        return {"error": "Wiki 根目录不存在", "actions": 0}

    actions: Dict[str, List[str]] = {
        "frontmatter_fixed": [],
        "status_updated": [],
        "title_fixed": [],
        "noise_links_cleaned": [],
    }

    for md_file in sorted(wiki_root.rglob("*.md")):
        if md_file.name in ("index.md", "changelog.md") or ".bak." in md_file.name:
            continue
        text = md_file.read_text(encoding="utf-8")

        # 修复缺少结束 --- 的 frontmatter
        if text.startswith("---"):
            first_close = text.find("\n---\n", 1)
            text.find("\n---\n", first_close + 1) if first_close != -1 else -1
            if first_close == -1:
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if i > 0 and line.startswith("#") and not line.startswith("---"):
                        lines.insert(i, "---")
                        text = "\n".join(lines)
                        actions["frontmatter_fixed"].append(md_file.name)
                        break

        # 修复 draft → review
        text = text.replace("\nstatus: draft\n", "\nstatus: review\n")
        text = text.replace("\nstatus: 'draft'\n", "\nstatus: review\n")

        # 修复 title 被 LLM 改写成 "XX - 个人 Wiki" 格式
        title_fix = _re.sub(
            r'^title:\s*["\']?(.+?)\s*[-–—]\s*(?:个人\s*Wiki|Wiki\s*页面)["\']?\s*$',
            lambda m: f"title: {m.group(1).strip()}",
            text,
            flags=_re.MULTILINE,
        )
        if title_fix != text:
            text = title_fix
            actions["title_fixed"].append(md_file.name)

        # 清理超短噪音链接 [[.]]、[[#]]、[[---]] 等（用 NOISE_WIKILINK_PATTERN 避免误删 frontmatter ---)
        cleaned = NOISE_WIKILINK_PATTERN.sub("", text)
        # 删除因清理产生的空 [[]]
        cleaned = _re.sub(r"\[\[\]\]", "", cleaned)
        if cleaned != text:
            text = cleaned
            actions["noise_links_cleaned"].append(md_file.name)

        _atomic_write(md_file, text)

    # 检查 status 是否被修复
    for md_file in sorted(wiki_root.rglob("*.md")):
        if md_file.name in ("index.md", "changelog.md") or ".bak." in md_file.name:
            continue
        text = md_file.read_text(encoding="utf-8")
        if "\nstatus: draft\n" in text or "\nstatus: 'draft'\n" in text:
            text = text.replace("\nstatus: draft\n", "\nstatus: review\n")
            text = text.replace("\nstatus: 'draft'\n", "\nstatus: review\n")
            _atomic_write(md_file, text)
            if md_file.name not in actions["status_updated"]:
                actions["status_updated"].append(md_file.name)

    total = sum(len(v) for v in actions.values())
    return {"actions_taken": total, "details": actions}


def _parse_updated_from_content(content: str) -> str:
    """从 frontmatter 中提取 updated 字段。"""
    m = FRONTMATTER_RE.match(content)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        if line.startswith("updated:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def _compute_content_quality(page_titles: dict, wiki_root: Path) -> Dict[str, Any]:
    """计算 Wiki 内容质量：信息密度 + 重复检测。"""
    import re

    pages = []
    for title, path in page_titles.items():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # 去掉 frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            body = parts[2] if len(parts) >= 3 else text
        else:
            body = text
        # 统计
        words = len(body)
        headings = len(re.findall(r"^#{1,4}\s", body, re.MULTILINE))
        sections = max(headings, 1)
        density = round(words / sections) if sections > 0 else words
        pages.append({"title": title, "words": words, "sections": sections, "density": density, "path": str(path)})

    if not pages:
        return {"info_density": {}, "duplicates": []}

    # 信息密度分布
    densities = [p["density"] for p in pages]
    info_density = {
        "avg_words_per_section": round(sum(densities) / len(densities)),
        "min_density": min(densities),
        "max_density": max(densities),
        "thin_pages": [p["title"] for p in pages if p["words"] < 500][:5],
        "dense_pages": [p["title"] for p in pages if p["words"] > 3000][:5],
    }

    # 重复检测：计算页面间的 Jaccard 相似度（跳过内容过短的页面）
    duplicates = []
    tokenized = {}
    MIN_BODY_LENGTH_FOR_DUP = 200  # 少于 200 字符不参与重复比较（避免空模板误报）
    for p in pages:
        try:
            body_text = Path(p["path"]).read_text(encoding="utf-8")
            if body_text.startswith("---"):
                body_text = body_text.split("---", 2)[-1]
            # 跳过正文过短的页面（大多数是人物空模板）
            if len(body_text.strip()) < MIN_BODY_LENGTH_FOR_DUP:
                continue
            tokens = set(re.findall(r"[\w一-鿿]{3,}", body_text.lower()))
            tokenized[p["title"]] = tokens
        except Exception as exc:
            logger.debug("Wiki 页面分词失败 [%s]: %s", p.get("title", "?"), exc)
            continue

    titles = list(tokenized.keys())
    for i in range(len(titles)):
        for j in range(i + 1, len(titles)):
            a, b = tokenized[titles[i]], tokenized[titles[j]]
            if not a or not b:
                continue
            jaccard = len(a & b) / len(a | b)
            if jaccard > 0.3:  # 相似度 > 30% 标记
                duplicates.append({"pair": [titles[i], titles[j]], "similarity": round(jaccard, 2)})

    duplicates.sort(key=lambda d: -d["similarity"])
    return {
        "info_density": info_density,
        "duplicates": duplicates[:10],
        "duplicate_count": len(duplicates),
    }
