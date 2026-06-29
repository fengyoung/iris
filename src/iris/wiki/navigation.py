"""Wiki 导航维护 — 维护 index.md 和 changelog.md。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from iris.config.loader import ConfigBundle

from .searcher import WikiSearcher, _read_wiki_page, _infer_title_from_filename, FRONTMATTER_RE
from ._constants import PAGE_TYPE_CONFIG as _PTC, get_wiki_dir, get_display_name, get_all_types

# 向下兼容别名
PAGE_TYPE_CONFIG = {k: {"dir": v[0], "name": v[2]} for k, v in _PTC.items()}


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
# 超短噪音
NOISE_LINK_PATTERN = re.compile(r"^[.\-#]{1,3}$|^_{2,}$|^\.{2,}$")

# LLM 引用不存在页面的常见模式（业务概念，非 Wiki 页面）
EXTERNAL_CONCEPT_PATTERNS = [
    re.compile(p) for p in [
        r"^C2P",
        r"^AI评",
        r"^鉴定师",
        r"^LV品牌",
        r"^demo网页",
        r"^数据链路追踪",
        r"^门店 AI 需求",
        r"^二手商品货架进销存",
        r"^价格策略与定价",
        r"^集团",
        r"^VOC-CCR",
        r"^主搜算法",
        r"^质检AI应用",
        r"^拍照3\.0某流程优化方案",
        r"^视频审核与质检在线评估",
        r"^项目周会 ·",
    ]
]


def _atomic_write(path: Path, text: str) -> None:
    """原子写入：先写临时文件，再 os.rename（Unix 原子操作），避免崩溃损坏原文件。"""
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(str(tmp), str(path))  # atomic on Unix


def _is_wiki_broken_link(target: str, page_titles: dict) -> str | None:
    """判断一个 [[link]] 是否为真正的断裂 Wiki 链接。

    Returns None 如果不应视为断裂（技术术语、源文档引用、噪音），返回错误标签。
    """
    # 源文档引用（含路径前缀如 "会议纪要/20260518-..."）
    if SOURCE_REF_PATTERN.match(target):
        return None
    # 超短噪音
    if NOISE_LINK_PATTERN.match(target):
        return None
    # 知名技术术语
    if target in KNOWN_TECH_TERMS:
        return None
    # 外部业务概念（不需 Wiki 页面）
    for pattern in EXTERNAL_CONCEPT_PATTERNS:
        if pattern.match(target):
            return None
    # 精确匹配
    if target in page_titles:
        return None
    # 模糊匹配（子串包含）
    for pt in page_titles:
        if target in pt or pt in target:
            return None
    # 前缀匹配（如 "AlphaTeam" → "AlphaTeam2026年目标与规划"）
    if len(target) >= 4:
        for pt in page_titles:
            if pt.startswith(target) or target.startswith(pt):
                return None
    # 去空格/标点后匹配（如 "AgenticCloud与AIAgent" ↔ "Agentic Cloud 与 AI Agent"）
    def _norm(s: str) -> str:
        return re.sub(r"[\s·\-–—，,、。\.\(\)（）【】\[\]]+", "", s)
    target_norm = _norm(target)
    for pt in page_titles:
        pt_norm = _norm(pt)
        if target_norm == pt_norm:
            return None
        if len(target_norm) >= 6 and (
            target_norm in pt_norm or pt_norm in target_norm
        ):
            return None
    # 字符级有序子序列匹配（如 "AlphaProject" → "AlphaProject手机拆修检测项目"）
    if len(target) >= 6:
        for pt in page_titles:
            if _char_sequence_match(target, pt):
                return None
    # 连写数字修复（如 "v10" ↔ "v1.0", "20AI" ↔ "2.0 AI"）
    target_decimal = re.sub(r"(\d+)\.?(\d*)", lambda m: m.group(1) + "." + m.group(2) if m.group(2) else m.group(1), target_norm)
    for pt in page_titles:
        pt_decimal = re.sub(r"(\d+)\.?(\d*)", lambda m: m.group(1) + "." + m.group(2) if m.group(2) else m.group(1), _norm(pt))
        if target_decimal == pt_decimal:
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
    import re
    return NOISE_LINK_PATTERN.sub("", content)


def lint_wiki(wiki_root: Path, data_root: Optional[Path] = None) -> Dict[str, Any]:
    """全面 Wiki 健康检查 + 索引质量检查。"""
    import json as _json
    import re as _re
    from datetime import datetime, timezone

    if not wiki_root.exists():
        return {"error": "Wiki 根目录不存在", "page_count": 0}

    LINK_RE = _re.compile(r"\[\[([^\]]+)\]\]")

    # ── 扫描所有页面 ────────────────────────────────────
    all_links: Dict[str, List[str]] = {}
    page_titles: Dict[str, Path] = {}
    page_info_dict: Dict[str, dict] = {}
    page_count = 0
    no_frontmatter: List[str] = []
    no_summary: List[str] = []
    zero_outbound: List[str] = []
    stale_pages: List[str] = []
    old_pages: List[str] = []
    broken_links: List[str] = []

    from .context_loader import WikiContextLoader
    loader = WikiContextLoader(wiki_root)
    for page_info in loader.load_pages():
        title = page_info.title
        status = page_info.status
        summary = page_info.summary
        md_file = page_info.path

        if not title:
            no_frontmatter.append(str(md_file.relative_to(wiki_root)))

        page_titles[title] = md_file
        page_count += 1

        try:
            content = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # 提取链接
        links = LINK_RE.findall(content)
        actual_links = []
        for link in links:
            actual = link.split("|")[0].strip() if "|" in link else link.strip()
            actual_links.append(actual)
        all_links[title] = actual_links

        if not actual_links:
            zero_outbound.append(title)

        # 摘要检查
        if not summary:
            no_summary.append(str(md_file.relative_to(wiki_root)))

        # 过时检查（90天未更新）
        updated_str = _parse_updated_from_content(content)
        if updated_str:
            try:
                updated = datetime.fromisoformat(updated_str)
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                days = (datetime.now(timezone.utc) - updated).days
                if days > 90:
                    old_pages.append(f"{title} ({days}天)")
            except (ValueError, TypeError):
                pass

        # Draft 状态
        if status == "draft":
            rel = str(md_file.relative_to(wiki_root))
            if rel not in stale_pages:
                stale_pages.append(rel)

        page_info_dict[title] = {
            "path": str(md_file.relative_to(wiki_root)),
                "type": ptype,
                "status": status,
                "outbound_count": len(actual_links),
            }

    # ── 断裂链接（按规则过滤） ──────────────────────────
    raw_broken_count = 0
    for source_title, links in all_links.items():
        for link_title in links:
            if link_title not in page_titles:
                raw_broken_count += 1
                if _is_wiki_broken_link(link_title, page_titles) is not None:
                    broken_links.append(f"{source_title} → [[{link_title}]]")

    # 统计排除的链接
    excluded_links = raw_broken_count - len(broken_links)

    # ── 孤立页 ──────────────────────────────────────────
    linked_titles = set()
    for links in all_links.values():
        for link in links:
            linked_titles.add(link)
    orphan_pages = []
    for title, path in page_titles.items():
        if title not in linked_titles:
            if not any(title in refs for refs in all_links.values()):
                orphan_pages.append(str(path.relative_to(wiki_root)))

    # ── 索引质量检查 ────────────────────────────────────
    index_info: Dict[str, Any] = {}
    if data_root:
        metadata_dir = data_root / "metadata"
        scan_path = metadata_dir / "work_docs_main_scan_summary.json"
        chunk_path = metadata_dir / "work_docs_main_chunk_summary.json"
        vector_dir = metadata_dir / "work_docs_main_vector_index"

        # 扫描信息
        if scan_path.exists():
            scan = _json.loads(scan_path.read_text(encoding="utf-8"))
            index_info["source_documents"] = scan.get("document_count", 0)
            index_info["last_scanned"] = scan.get("scanned_at", "")
        else:
            index_info["source_documents"] = 0

        # 切块信息
        if chunk_path.exists():
            chunk_data = _json.loads(chunk_path.read_text(encoding="utf-8"))
            chunks = chunk_data.get("chunks", [])
            index_info["total_chunks"] = len(chunks)
            # 按来源文件统计
            sources = set(c.get("relative_path", "") for c in chunks)
            index_info["chunked_documents"] = len(sources)
            # chunk 文档覆盖比例
            if index_info.get("source_documents", 0) > 0:
                cov = len(sources) / index_info["source_documents"] * 100
                index_info["chunk_coverage_pct"] = round(cov, 1)
        else:
            index_info["total_chunks"] = 0
            index_info["chunked_documents"] = 0

        # 向量索引信息
        if vector_dir.exists():
            vec_files = list(vector_dir.glob("*"))
            index_info["vector_index_exists"] = True
            index_info["vector_index_files"] = len(vec_files)
            index_info["vector_index_size_kb"] = round(
                sum(f.stat().st_size for f in vec_files if f.is_file()) / 1024, 1
            )
        else:
            index_info["vector_index_exists"] = False

        # Wiki 覆盖度
        source_files = set()
        if chunk_path.exists():
            for c in chunks:
                src = c.get("relative_path", "")
                if src:
                    source_files.add(src.split("/")[0])
        wiki_topics = set(page_titles.keys())
        index_info["wiki_page_count"] = len(wiki_topics)
        index_info["wiki_source_coverage_pct"] = round(len(source_files) / max(index_info.get("source_documents", 1), 1) * 100, 1)

    # ── 内容质量评分 ──────────────────────────────────────
    quality = _compute_content_quality(page_titles, wiki_root)

    return {
        # Wiki 健康
        "page_count": page_count,
        "content_quality": quality,
        "by_type": {
            cfg["name"]: len(list((wiki_root / cfg["dir"]).glob("*.md")))
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
    from datetime import datetime as _dt

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
            second_close = text.find("\n---\n", first_close + 1) if first_close != -1 else -1
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

        # 清理超短噪音链接 [[.]]、[[#]]、[[---]] 等
        cleaned = NOISE_LINK_PATTERN.sub("", text)
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
    from collections import Counter

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

    # 重复检测：计算页面间的 Jaccard 相似度
    duplicates = []
    tokenized = {}
    for p in pages:
        try:
            body_text = Path(p["path"]).read_text(encoding="utf-8")
            if body_text.startswith("---"):
                body_text = body_text.split("---", 2)[-1]
            tokens = set(re.findall(r"[\w一-鿿]{3,}", body_text.lower()))
            tokenized[p["title"]] = tokens
        except Exception:
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
