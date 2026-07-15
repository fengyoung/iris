"""源文档与 Wiki 页面内容级深度评估。

提供两个核心能力：
1. 内容准确性校验：逐条核对 Wiki 引用描述与源文档 chunk 是否一致
2. 内容全面性校验：通过路径相似度发现同主题下未引用的源文件，判断是否遗漏关键信息
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.config.loader import ConfigBundle, load_config_bundle
from iris.llm import LLMService
from iris.llm.provider import LLMProviderError

# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────


@dataclass
class ReferenceEntry:
    """从 Wiki 页面 ## 参考来源 解析出的单条引用。"""
    raw: str
    source_path: str          # 相对路径，如 "2025/方案及汇报/xxx.md"
    line_number: Optional[int]  # 引用行号
    description: str           # 引用描述文本
    resolved_chunk: Optional[str] = None  # 解析到的源 chunk 内容
    resolved_context: Optional[str] = None  # 扩展上下文后的内容


@dataclass
class AccuracyVerdict:
    """单条引用的准确性判定结果。"""
    reference: ReferenceEntry
    verdict: str               # consistent / inconsistent / unverifiable / source_missing
    detail: str = ""


@dataclass
class CoverageGap:
    """全面性评估中发现的遗漏项。"""
    source_path: str
    missing_topic: str
    detail: str = ""


@dataclass
class PageDeepResult:
    """单个 Wiki 页面的深度评估结果。"""
    title: str
    page_type: str
    path: str

    # 准确性
    accuracy_verdicts: List[AccuracyVerdict] = field(default_factory=list)
    accuracy_rate: Optional[float] = None  # consistent / (consistent + inconsistent)

    # 全面性
    coverage_gaps: List[CoverageGap] = field(default_factory=list)
    comprehensiveness_note: str = ""


@dataclass
class DeepEvalResult:
    """深度评估的完整输出。"""
    evaluated_at: str
    total_pages: int

    # 准确性汇总
    total_references: int
    consistent_count: int
    inconsistent_count: int
    unverifiable_count: int
    source_missing_count: int
    overall_accuracy_rate: Optional[float]

    # 全面性汇总
    pages_with_gaps: int
    total_gaps: int
    overall_comprehensiveness_note: str

    # 逐页明细
    page_results: List[PageDeepResult] = field(default_factory=list)

    # 最高不一致页面
    top_inconsistent_pages: List[dict] = field(default_factory=list)

    # 修复方案
    recommendations: List[dict] = field(default_factory=list)


# ──────────────────────────────────────────────
# 源片段定位器
# ──────────────────────────────────────────────


class SourceLocator:
    """从 chunk 摘要索引中定位源文档片段。

    支持多个 chunk 摘要（work_docs_main + xiaolongxia_shared）。
    """

    def __init__(self, chunk_summary_paths: List[str]):
        self._chunk_summary_paths = [Path(p) for p in chunk_summary_paths]
        self._chunks_by_file: Dict[str, List[dict]] = {}
        self._loaded = False

    def load(self) -> None:
        """加载所有 chunk 摘要并建立 relative_path → chunks 索引。"""
        for csp in self._chunk_summary_paths:
            if not csp.exists():
                continue
            with open(csp, "r", encoding="utf-8") as f:
                data = json.load(f)
            chunks = data["chunks"]
            for c in chunks:
                rp = c["relative_path"]
                if rp not in self._chunks_by_file:
                    self._chunks_by_file[rp] = []
                self._chunks_by_file[rp].append(c)
        # 按 line_start 排序
        for rp in self._chunks_by_file:
            self._chunks_by_file[rp].sort(key=lambda x: x["line_start"])
        self._loaded = True

    def lookup(self, relative_path: str, line_number: Optional[int] = None) -> Optional[str]:
        """根据相对路径和行号定位源 chunk 内容。

        若指定行号，定位到该行所在的 chunk。
        若未指定行号，返回该文件第一个 chunk。
        若文件不存在，返回 None。
        """
        if not self._loaded:
            self.load()

        # 标准化路径分隔符
        rp = relative_path.replace("\\", "/")
        chunks = self._chunks_by_file.get(rp)
        if not chunks:
            # 尝试去掉开头的 ./ 或 /
            for prefix in ("/", "./"):
                if rp.startswith(prefix):
                    rp = rp[len(prefix):]
                    break
            chunks = self._chunks_by_file.get(rp)

        if not chunks:
            return None

        if line_number is not None:
            for c in chunks:
                ls = c.get("line_start", 0)
                le = c.get("line_end", 0)
                if ls <= line_number <= le:
                    return c["content"]
            # 行号不在任何 chunk 范围内，返回最近的一个
            return chunks[-1]["content"]

        return chunks[0]["content"] if chunks else None

    def lookup_with_context(self, relative_path: str, line_number: Optional[int] = None,
                            context_extend: int = 1) -> Optional[str]:
        """定位源 chunk 并扩展上下文（前后各 context_extend 个 chunk）。"""
        if not self._loaded:
            self.load()

        rp = relative_path.replace("\\", "/")
        chunks = self._chunks_by_file.get(rp)
        if not chunks:
            for prefix in ("/", "./"):
                if rp.startswith(prefix):
                    rp = rp[len(prefix):]
                    break
            chunks = self._chunks_by_file.get(rp)
        if not chunks:
            return None

        if line_number is not None:
            for idx, c in enumerate(chunks):
                ls = c.get("line_start", 0)
                le = c.get("line_end", 0)
                if ls <= line_number <= le:
                    start = max(0, idx - context_extend)
                    end = min(len(chunks), idx + context_extend + 1)
                    parts = []
                    for i in range(start, end):
                        parts.append(chunks[i]["content"])
                    return "\n\n".join(parts).strip() or None
            return chunks[-1]["content"]

        return chunks[0]["content"] if chunks else None

    def find_sibling_sources(self, relative_path: str, max_count: int = 3) -> List[str]:
        """通过路径相似度发现同目录下的相关源文件。"""
        if not self._loaded:
            self.load()

        # 提取目录路径
        parts = relative_path.split("/")
        if len(parts) <= 1:
            return []

        # 尝试向上取一层目录
        parent_dir = "/".join(parts[:-1])
        siblings = [rp for rp in self._chunks_by_file if rp.startswith(parent_dir) and rp != relative_path]

        # 按文件数量排序（取内容最多的几个）
        siblings.sort(key=lambda rp: sum(len(c["content"]) for c in self._chunks_by_file[rp]), reverse=True)
        return siblings[:max_count]

    def search_sources_by_keywords(self, keywords: List[str], exclude_path: Optional[str] = None,
                                   max_results: int = 3) -> List[str]:
        """通过关键词匹配文件名或路径，发现相关源文件。"""
        if not self._loaded:
            self.load()

        scored: List[Tuple[int, str]] = []
        for rp in self._chunks_by_file:
            if exclude_path and rp == exclude_path:
                continue
            rp_lower = rp.lower()
            score = 0
            for kw in keywords:
                if kw.lower() in rp_lower:
                    score += 1
            if score > 0:
                scored.append((score, rp))

        scored.sort(key=lambda x: -x[0])
        return [rp for _, rp in scored[:max_results]]

    def get_all_source_paths(self) -> List[str]:
        """返回所有已知源文件路径。"""
        if not self._loaded:
            self.load()
        return list(self._chunks_by_file.keys())


# ──────────────────────────────────────────────
# Wiki 参考来源解析器
# ──────────────────────────────────────────────


# ── 参考来源解析模式 ──
# 格式1: [path.md:line] desc
BRACKET_PATTERN = re.compile(
    r"(?:\d+\.)?\s*\[([^\]]+\.md)(?::(\d+))?\](.*)"
)
# 格式2: 1. path.md:line（章节注释）desc
# 捕获章节注释作为 description 的一部分
NUMBERED_PATH_PATTERN = re.compile(
    r"\d+\.\s*([^\s]+\.md):(\d+)(（[^）]*）)?\s*(.*)"
)
# 格式3: 1. path.md desc（无行号）
NUMBERED_PATH_NO_LINE_PATTERN = re.compile(
    r"\d+\.\s*([^\s]+\.md)\s*(.*)"
)
# 格式4: 行号范围 path.md:109-116
RANGE_PATTERN = re.compile(
    r".*(.+\.md):(\d+)-(\d+).*"
)
# 格式5: 内联内容格式 1. 1. 使用语境... （跳过）
INLINE_CONTENT_PATTERN = re.compile(
    r"\d+\.\s+\d+\.\s*使用语境"
)


def parse_references(wiki_content: str) -> List[ReferenceEntry]:
    """从 Wiki 页面内容中解析 ## 参考来源 下的所有引用条目。

    支持多种引用格式：
    - [path.md:line] desc
    - 1. path.md:line（章节注释）desc
    - 1. path.md:line desc
    - 1. path.md desc（无行号）
    - 内联格式（跳过，不计入校验）
    """
    ref_m = re.search(r"## 参考来源\n(.*?)(?=\n## |\Z)", wiki_content, re.DOTALL)
    if not ref_m:
        return []

    ref_text = ref_m.group(1).strip()
    entries = []
    for line in ref_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # 跳过内联内容格式
        if INLINE_CONTENT_PATTERN.match(line):
            continue

        e = None

        # 尝试格式1: [path.md:line]
        m = BRACKET_PATTERN.match(line)
        if m:
            source_path = m.group(1).strip()
            line_str = m.group(2)
            line_number = int(line_str) if line_str else None
            description = m.group(3).strip() if m.group(3) else ""
            e = ReferenceEntry(raw=line, source_path=source_path,
                               line_number=line_number, description=description)

        # 尝试格式2: 1. path.md:line（章节注释）
        if not e:
            m = NUMBERED_PATH_PATTERN.match(line)
            if m:
                source_path = m.group(1).strip().lstrip("[")
                line_number = int(m.group(2))
                chapter_note = (m.group(3) or "").strip()
                desc = (m.group(4) or "").strip()
                # 合并章节注释和描述
                description = desc
                if chapter_note and not description:
                    # 只有章节注释，用作描述
                    description = chapter_note.strip("（）")
                elif chapter_note and description:
                    description = f"{chapter_note} {description}"
                e = ReferenceEntry(raw=line, source_path=source_path,
                                   line_number=line_number, description=description)

        # 尝试格式3: 1. path.md（无行号）
        if not e:
            m = NUMBERED_PATH_NO_LINE_PATTERN.match(line)
            if m:
                source_path = m.group(1).strip().lstrip("[")
                description = m.group(2).strip() if m.group(2) else ""
                e = ReferenceEntry(raw=line, source_path=source_path,
                                   line_number=None, description=description)

        # 尝试格式4: 行号范围 path.md:109-116（取起始行）
        if not e:
            m = RANGE_PATTERN.match(line)
            if m:
                source_path = m.group(1).strip().lstrip("[")
                line_number = int(m.group(2))
                description = ""
                e = ReferenceEntry(raw=line, source_path=source_path,
                                   line_number=line_number, description=description)

        if e:
            entries.append(e)
        # 不记录无法解析的行

    return entries


def extract_page_title(content: str, filename: str) -> str:
    """从 frontmatter 或文件名提取页面标题。"""
    title_m = re.search(r'title:\s*(.+)', content)
    return title_m.group(1).strip() if title_m else filename.replace(".md", "")


# ──────────────────────────────────────────────
# 准确性校验
# ──────────────────────────────────────────────

def _load_prompt_template(name: str) -> Optional[str]:
    """从 templates/prompt/ 加载评估用 Prompt 模板，不存在返回 None。"""
    templates_dir = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "prompt"
    tmpl_path = templates_dir / name
    if tmpl_path.exists():
        return tmpl_path.read_text(encoding="utf-8")
    return None


# 内联 fallback 模板（保留以保证模板文件缺失时仍可工作）
_ACCURACY_PROMPT_FALLBACK = """你正在审核 Wiki 内容是否与原始工作文档一致。

【源文档片段】
{source_content}

【Wiki 描述】
{wiki_description}

请判定：Wiki 的描述是否与源文档一致？
- consistent: 完全一致，没有事实错误
- inconsistent: 存在数字错误、归因偏差、时间错误、过度概括、虚构内容等
- unverifiable: 源文档没有足够信息来支撑或验证该描述

仅输出一行，格式：判定结果|简要说明
示例：consistent|描述与源文档完全一致
示例：inconsistent|源文档中该比例为 48.14%，Wiki 写作 45%
示例：unverifiable|源文档未涉及此描述的相关信息"""

_PAGE_ACCURACY_FALLBACK = """你正在审核 Wiki 页面内容是否与原始工作文档一致。

【源文档片段】
{source_content}

【Wiki 页面标题】
{wiki_title}

【Wiki 页面关键内容（摘要）】
{wiki_content_snippet}

请判定：Wiki 页面中与上述源文档片段相关的内容，是否与源文档一致？
- consistent: 完全一致，没有事实错误
- inconsistent: 存在数字错误、归因偏差、时间错误、过度概括、虚构内容等
- unverifiable: 源文档与该 Wiki 页面的主题关联不明确，无法验证

仅输出一行，格式：判定结果|简要说明
示例：consistent|Wiki 描述与源文档一致
示例：inconsistent|源文档中提到的准确率是 95.2%，Wiki 写成了 92.5%
示例：unverifiable|源文档内容与 Wiki 主题不直接相关"""


def _get_accuracy_prompt() -> str:
    """获取准确性校验 Prompt（优先模板文件，fallback 内联）。"""
    return _load_prompt_template("accuracy_check.md") or _ACCURACY_PROMPT_FALLBACK


def _get_page_accuracy_prompt() -> str:
    """获取页面级准确性校验 Prompt（优先模板文件，fallback 内联）。"""
    return _load_prompt_template("page_accuracy_check.md") or _PAGE_ACCURACY_FALLBACK


class AccuracyVerifier:
    """准确性校验器：逐条审核 Wiki 引用是否与源文档一致。"""

    def __init__(self, llm_service: LLMService,
                 source_locator: SourceLocator):
        self._llm = llm_service
        self._locator = source_locator

    def verify(self, entries: List[ReferenceEntry],
               wiki_title: str = "",
               wiki_content: str = "") -> List[AccuracyVerdict]:
        """校验一组引用。

        对于有描述内容的引用，直接校验描述与源文档的一致性。
        对于描述为空的引用，尝试页面级抽样校验（将 Wiki 页面内容与源文档对比）。
        """
        verdicts = []
        for entry in entries:
            verdict = self._verify_one(entry)
            # 对于描述为空导致未验证的，尝试页面级校验
            if verdict.verdict == "unverifiable" and "描述为空" in verdict.detail:
                if wiki_content and self._has_relevant_wiki_content(wiki_content, entry):
                    page_verdict = self._verify_page_level(entry, wiki_title, wiki_content)
                    if page_verdict.verdict in ("consistent", "inconsistent"):
                        verdict = page_verdict
            verdicts.append(verdict)
        return verdicts

    def _has_relevant_wiki_content(self, wiki_content: str, entry: ReferenceEntry) -> bool:
        """粗略判断 Wiki 页面是否有与引用相关的内容。"""
        # 检查 Wiki 页面是否非空且有实质性内容
        content = wiki_content.strip()
        if len(content) < 200:
            return False
        # 如果引用有章节注记，检查相应内容存在
        if entry.description and len(entry.description) > 5:
            # 只要 Wiki 有实质内容就行
            return True
        return False

    def _verify_one(self, entry: ReferenceEntry) -> AccuracyVerdict:
        """校验单条引用。"""
        # 定位源
        source_content = self._locator.lookup_with_context(
            entry.source_path, entry.line_number
        )
        if not source_content:
            return AccuracyVerdict(
                reference=entry,
                verdict="source_missing",
                detail=f"源文件未找到: {entry.source_path}",
            )

        if not entry.description.strip():
            return AccuracyVerdict(
                reference=entry,
                verdict="unverifiable",
                detail="Wiki 引用的描述为空，无法校验",
            )

        # 调用 LLM
        prompt = _get_accuracy_prompt().format(
            source_content=source_content.strip(),
            wiki_description=entry.description.strip(),
        )

        try:
            result_line = self._llm.generate(
                prompt,
                route_context={
                    "input_type": "text",
                    "task_type": "analysis",
                    "complexity": "simple",
                },
                temperature=0.1,
            ).text.strip().split("\n")[0]
        except LLMProviderError as e:
            return AccuracyVerdict(
                reference=entry,
                verdict="unverifiable",
                detail=f"LLM 调用失败: {e}",
            )

        # 解析 LLM 输出
        verdict_str = "unverifiable"
        detail = ""
        if "|" in result_line:
            verdict_str, detail = result_line.split("|", 1)
            verdict_str = verdict_str.strip().lower()
            detail = detail.strip()
        else:
            # fallback: 直接匹配关键词
            for v in ("consistent", "inconsistent", "unverifiable"):
                if v in result_line.lower():
                    verdict_str = v
                    break
            detail = result_line

        if verdict_str not in ("consistent", "inconsistent", "unverifiable"):
            verdict_str = "unverifiable"

        return AccuracyVerdict(
            reference=entry,
            verdict=verdict_str,
            detail=detail,
        )

    def _verify_page_level(self, entry: ReferenceEntry,
                           wiki_title: str, wiki_content: str) -> AccuracyVerdict:
        """页面级准确性校验：当引用描述为空时，将 Wiki 页面内容与源文档对比。"""
        source_content = self._locator.lookup_with_context(
            entry.source_path, entry.line_number
        )
        if not source_content:
            return AccuracyVerdict(
                reference=entry,
                verdict="source_missing",
                detail=f"源文件未找到: {entry.source_path}",
            )

        # 取 Wiki 页面中和该引用相关的部分（前 600 字）
        wiki_snippet = wiki_content[:600].strip()

        prompt = _get_page_accuracy_prompt().format(
            source_content=source_content.strip()[:800],
            wiki_title=wiki_title,
            wiki_content_snippet=wiki_snippet,
        )

        try:
            result_line = self._llm.generate(
                prompt,
                route_context={
                    "input_type": "text",
                    "task_type": "analysis",
                    "complexity": "simple",
                },
                temperature=0.1,
            ).text.strip().split("\n")[0]
        except LLMProviderError as e:
            return AccuracyVerdict(
                reference=entry,
                verdict="unverifiable",
                detail=f"LLM 调用失败（页面级校验）: {e}",
            )

        verdict_str = "unverifiable"
        detail = ""
        if "|" in result_line:
            verdict_str, detail = result_line.split("|", 1)
            verdict_str = verdict_str.strip().lower()
            detail = detail.strip()
        else:
            for v in ("consistent", "inconsistent", "unverifiable"):
                if v in result_line.lower():
                    verdict_str = v
                    break
            detail = result_line

        if verdict_str not in ("consistent", "inconsistent", "unverifiable"):
            verdict_str = "unverifiable"

        return AccuracyVerdict(
            reference=entry,
            verdict=verdict_str,
            detail=f"[页面级] {detail}",
        )


# ──────────────────────────────────────────────
# 全面性校验
# ──────────────────────────────────────────────

_COMPREHENSIVENESS_FALLBACK = """你正在审核 Wiki 页面是否遗漏了源文档中的关键信息。

【Wiki 页面标题】
{wiki_title}

【Wiki 页面核心内容】
{wiki_content_snippet}

【候选源文档片段】
{candidate_source}

请判断：候选源文档中是否包含与 Wiki 主题相关、但 Wiki 页面未覆盖的关键信息？
- has_gap: 候选源中有关键信息遗漏
- no_gap: 候选源的内容已被 Wiki 覆盖，或与主题不直接相关

仅输出一行，格式：判定结果|简要说明遗漏了什么
示例：no_gap|已覆盖
示例：has_gap|提到了 XXX 的具体时间节点和责任人，Wiki 未收录"""


def _get_comprehensiveness_prompt() -> str:
    """获取全面性校验 Prompt（优先模板文件，fallback 内联）。"""
    return _load_prompt_template("comprehensiveness_check.md") or _COMPREHENSIVENESS_FALLBACK


class ComprehensivenessVerifier:
    """全面性校验器：发现同主题源文件中可能遗漏的关键信息。"""

    def __init__(self, llm_service: LLMService,
                 source_locator: SourceLocator):
        self._llm = llm_service
        self._locator = source_locator

    def find_gaps(self, wiki_title: str, wiki_content: str,
                  referenced_sources: List[str]) -> List[CoverageGap]:
        """通过同主题源文件发现可能遗漏的信息。"""
        # 从路径和标题提取关键词
        keywords = self._extract_keywords(wiki_title, wiki_content)

        # 搜索相关源文件（排除已引用的）
        candidates = self._locator.search_sources_by_keywords(
            keywords,
            exclude_path=referenced_sources[0] if referenced_sources else None,
            max_results=3,
        )

        gaps = []
        for candidate_path in candidates:
            # 取候选源内容（首部 800 字）
            content = self._locator.lookup(candidate_path)
            if not content:
                continue

            wiki_snippet = wiki_content[:600]

            prompt = _get_comprehensiveness_prompt().format(
                wiki_title=wiki_title,
                wiki_content_snippet=wiki_snippet.strip(),
                candidate_source=content[:800].strip(),
            )

            try:
                result_line = self._llm.generate(
                    prompt,
                    route_context={
                        "input_type": "text",
                        "task_type": "analysis",
                        "complexity": "simple",
                    },
                    temperature=0.1,
                ).text.strip().split("\n")[0]
            except LLMProviderError:
                continue

            is_gap = "has_gap" in result_line.lower()
            detail = result_line.split("|", 1)[1].strip() if "|" in result_line else result_line

            if is_gap:
                gaps.append(CoverageGap(
                    source_path=candidate_path,
                    missing_topic=detail[:100] if detail else "未明确说明",
                    detail=detail,
                ))

        return gaps

    def _extract_keywords(self, title: str, content: str) -> List[str]:
        """提取用于发现相关源文件的关键词。"""
        keywords = []

        # 从标题分词（中英文混合）
        title_clean = re.sub(r'[^\w一-鿿]', ' ', title)
        for w in title_clean.split():
            w = w.strip()
            if len(w) >= 2 and w not in ("Wiki", "的", "与", "和"):
                keywords.append(w)

        # 从前两段提取关键词
        for line in content.split("\n")[:15]:
            line = line.strip().strip("#").strip()
            if len(line) > 4 and line not in keywords:
                # 取第一个有意义的词组
                words = re.findall(r'[一-鿿]{2,}', line)
                for w in words[:2]:
                    if w not in keywords:
                        keywords.append(w)
                        break

        return keywords[:6]


# ──────────────────────────────────────────────
# 主评估器
# ──────────────────────────────────────────────


class DeepEvaluator:
    """深度评估主控器：协调准确性 + 全面性校验。"""

    def __init__(self, config: ConfigBundle):
        self._config = config
        self._wiki_root = Path(config.wiki["wiki_root"])

        # 初始化 LLM 服务（走 base_model，纯文本低 cost）
        self._llm = LLMService(config)

        # 初始化源定位器（iris3: 主数据源 chunk 摘要）
        data_root = Path(config.root) / "data" / "metadata"
        chunk_summary_paths = [
            str(data_root / "work_docs_main_chunk_summary.json"),
        ]
        self._locator = SourceLocator(chunk_summary_paths)

        # 校验器
        self._acc_verifier = AccuracyVerifier(self._llm, self._locator)
        self._comp_verifier = ComprehensivenessVerifier(self._llm, self._locator)

    def evaluate(self, page_filter: Optional[str] = None,
                 sample_rate: Optional[float] = None) -> DeepEvalResult:
        """执行深度评估。

        Args:
            page_filter: 可选，只评估标题匹配的页面（支持子串匹配）
            sample_rate: 可选，抽样比例（0.0~1.0）
        """
        # 收集所有 Wiki 页面
        pages = self._collect_pages()
        if page_filter:
            pages = [p for p in pages if page_filter.lower() in p["title"].lower()]
            if not pages:
                print(f"  警告: 未找到标题包含 '{page_filter}' 的页面")

        # 抽样
        if sample_rate and sample_rate < 1.0:
            import random
            random.shuffle(pages)
            take = max(1, int(len(pages) * sample_rate))
            pages = pages[:take]
            print(f"  抽样模式: {take}/{len(pages) if not page_filter else '?'} 页")

        # 先加载源定位器
        print("  加载源文档索引...")
        self._locator.load()
        print(f"    已索引 {len(self._locator.get_all_source_paths())} 个源文件")

        # 逐页评估
        page_results: List[PageDeepResult] = []
        total_refs = 0
        consistent = 0
        inconsistent = 0
        unverifiable = 0
        source_missing = 0
        total_gaps = 0
        pages_with_gaps = 0

        print(f"\n  开始评估 {len(pages)} 个页面...")
        for idx, p in enumerate(pages):
            print(f"    [{idx+1}/{len(pages)}] {p['title']}")
            result = self._evaluate_page(p)

            page_results.append(result)
            total_refs += len(result.accuracy_verdicts)

            for v in result.accuracy_verdicts:
                if v.verdict == "consistent":
                    consistent += 1
                elif v.verdict == "inconsistent":
                    inconsistent += 1
                elif v.verdict == "unverifiable":
                    unverifiable += 1
                else:
                    source_missing += 1

            if result.coverage_gaps:
                pages_with_gaps += 1
                total_gaps += len(result.coverage_gaps)

        # 汇总
        verifiable = consistent + inconsistent
        accuracy_rate = round(consistent / verifiable, 3) if verifiable > 0 else None

        # 找出最不一致的页面
        inconsistent_pages = []
        for pr in page_results:
            inc_count = sum(1 for v in pr.accuracy_verdicts if v.verdict == "inconsistent")
            if inc_count > 0:
                inconsistent_pages.append({
                    "title": pr.title,
                    "inconsistent": inc_count,
                    "total": len(pr.accuracy_verdicts),
                })
        inconsistent_pages.sort(key=lambda x: -x["inconsistent"])

        # 全面性说明
        comp_note = f"发现 {total_gaps} 处可能的遗漏，涉及 {pages_with_gaps} 页"

        result = DeepEvalResult(
            evaluated_at=datetime.now().isoformat(),
            total_pages=len(pages),
            total_references=total_refs,
            consistent_count=consistent,
            inconsistent_count=inconsistent,
            unverifiable_count=unverifiable,
            source_missing_count=source_missing,
            overall_accuracy_rate=accuracy_rate,
            pages_with_gaps=pages_with_gaps,
            total_gaps=total_gaps,
            overall_comprehensiveness_note=comp_note,
            page_results=page_results,
            top_inconsistent_pages=inconsistent_pages[:10],
        )
        result.recommendations = self._generate_recommendations(result)
        return result

    def _collect_pages(self) -> List[dict]:
        """收集 Wiki 根目录下的所有页面（iris3: 4 种类型）。"""
        pages = []
        for subdir, type_name in [
            ("01-领域", "domain"),
            ("02-概念", "concept"),
            ("03-项目", "project"),
            ("04-人物", "person"),
        ]:
            d = self._wiki_root / subdir
            if not d.exists():
                continue
            for f in sorted(d.iterdir()):
                if not f.name.endswith(".md") or ".bak." in f.name:
                    continue
                content = f.read_text(encoding="utf-8")
                title = extract_page_title(content, f.name)
                pages.append({
                    "title": title,
                    "type": type_name,
                    "path": f"{subdir}/{f.name}",
                    "content": content,
                })
        return pages

    # ── 修复方案生成 ──

    def _generate_recommendations(self, result: DeepEvalResult) -> List[dict]:
        """根据深度评估结果生成结构化修复方案。"""
        recs = []

        # ── 类别1：内容不一致页面（需 regenerate 或人工审核）──
        inconsistent_pages = []
        for pr in result.page_results:
            inc_issues = [v for v in pr.accuracy_verdicts if v.verdict == "inconsistent"]
            if inc_issues:
                inconsistent_pages.append((pr, inc_issues))

        if inconsistent_pages:
            pages_list = "\n".join(
                f"    - {pr.title}（{len(issues)} 条）"
                for pr, issues in inconsistent_pages
            )
            recs.append({
                "priority": "P0",
                "category": "内容不一致",
                "problem": f"{len(inconsistent_pages)} 个页面存在 {result.inconsistent_count} 条内容不一致",
                "detected_pages": [pr.title for pr, _ in inconsistent_pages],
                "suggestion": "逐个页面复审并修正。对数字错误类（如算法能力评估中样本数有误），直接用源文档校正；"
                             "对描述完全偏离的（如逐层深入讨论、智能问答服务），建议用 `build-wiki` 命令重新生成 "
                             "（python scripts/run_cli.py build-wiki --title \"页面标题\" --overwrite）。",
                "estimated_effort": f"约 {len(inconsistent_pages)} 个页面，每页 5-15 分钟",
                "automation": "可执行" if len(inconsistent_pages) <= 3 else "建议人工复核",
            })

        # ── 类别2：引用描述为空（信息缺失）──
        empty_desc_count = sum(
            1 for pr in result.page_results
            for v in pr.accuracy_verdicts
            if v.verdict == "unverifiable" and "描述为空" in v.detail
        )
        if empty_desc_count > 0:
            recs.append({
                "priority": "P1",
                "category": "参考来源描述缺失",
                "problem": f"{empty_desc_count} 条引用的描述字段为空，无法校验",
                "detected_pages": [
                    pr.title for pr in result.page_results
                    if any("描述为空" in v.detail for v in pr.accuracy_verdicts)
                ][:10],
                "suggestion": "在 Wiki 页面生成时增强参考来源的描述质量。对已有页面，可运行 `fix_noise_refs.py` "
                             "清理噪音引用，然后考虑批量重新生成引用描述不完整的主题/项目页面。",
                "estimated_effort": "若批量重新生成约 30 分钟，若逐页补充约 2-3 小时",
                "automation": "可部分自动化（重新生成 Wiki 页面时新模板会包含完整描述）",
            })

        # ── 类别3：源文件缺失（chunk 索引过期）──
        if result.source_missing_count > 0:
            recs.append({
                "priority": "P1",
                "category": "源文件缺失",
                "problem": f"{result.source_missing_count} 条引用的源文件在 chunk 索引中不存在",
                "detected_pages": [
                    pr.title for pr in result.page_results
                    if any(v.verdict == "source_missing" for v in pr.accuracy_verdicts)
                ][:10],
                "suggestion": "可能原因：(1) 文件已被删除/重命名；(2) chunk 索引需要重建。"
                             "运行 `build-chunks` 重建索引：python scripts/run_cli.py build-chunks --source work_docs_main。"
                             "若重建后仍缺失，检查源文件是否已被移至 obsidian 的其他目录。",
                "estimated_effort": "重建索引约 5 分钟",
                "automation": "可自动化",
            })

        # ── 类别4：全面性遗漏──
        if result.pages_with_gaps > 0:
            gap_pages = [
                pr for pr in result.page_results if pr.coverage_gaps
            ]
            recs.append({
                "priority": "P2",
                "category": "关键信息遗漏",
                "problem": f"{result.pages_with_gaps} 个页面可能存在 {result.total_gaps} 处信息遗漏",
                "detected_pages": [pr.title for pr in gap_pages],
                "suggestion": "审查候选源文件内容，确认是否确实遗漏。确认后使用 `build-wiki` 命令增量更新 Wiki 页面，"
                             "或手动补充遗漏内容。建议优先处理智能问答服务页面。",
                "estimated_effort": "每页审查 10-15 分钟",
                "automation": "需人工判断",
            })

        # ── 类别5：整体准确率偏低──
        if result.overall_accuracy_rate is not None and result.overall_accuracy_rate < 0.8:
            recs.append({
                "priority": "P2",
                "category": "整体准确率偏低",
                "problem": f"内容准确率为 {result.overall_accuracy_rate:.0%}，低于 80%",
                "detected_pages": [],
                "suggestion": "建议在 Wiki 生成流程（`build-wiki` / `wiki-pipeline`）中增加引用描述校验步骤："
                             "生成 Wiki 页面时，要求 LLM 在 `## 参考来源` 中为每条引用附上 10-30 字的事实断言描述，"
                             "而非仅罗列文件路径。同时考虑在 `wiki.json` 配置中增加引用格式规范约束。",
                "estimated_effort": "配置变更约 30 分钟，后续增量生成自动遵循",
                "automation": "可自动化（修改 Wiki 生成模板）",
            })

        return recs

    def _evaluate_page(self, page: dict) -> PageDeepResult:
        """评估单个 Wiki 页面。"""
        result = PageDeepResult(
            title=page["title"],
            page_type=page["type"],
            path=page["path"],
        )

        # 解析引用
        entries = parse_references(page["content"])
        if not entries:
            result.comprehensiveness_note = "页面无参考来源"
            return result

        # 准确性校验（传递 Wiki 页面内容用于无描述引用的页面级 fallback）
        verdicts = self._acc_verifier.verify(
            entries, wiki_title=page["title"], wiki_content=page["content"]
        )
        result.accuracy_verdicts = verdicts

        consistent_count = sum(1 for v in verdicts if v.verdict == "consistent")
        inconsistent_count = sum(1 for v in verdicts if v.verdict == "inconsistent")
        verifiable = consistent_count + inconsistent_count
        result.accuracy_rate = round(consistent_count / verifiable, 2) if verifiable > 0 else None

        # 全面性校验（只对 accuracy_rate < 1.0 或引用数 >= 3 的页面做，控制成本）
        referenced_sources = list(set(e.source_path for e in entries))
        if len(referenced_sources) >= 1 and (result.accuracy_rate is None or result.accuracy_rate < 1.0):
            try:
                gaps = self._comp_verifier.find_gaps(
                    page["title"], page["content"], referenced_sources
                )
                result.coverage_gaps = gaps
            except (LLMProviderError, ValueError, TypeError, OSError) as e:
                result.comprehensiveness_note = f"全面性校验异常: {e}"

        return result


# ──────────────────────────────────────────────
# 序列化工具
# ──────────────────────────────────────────────


def deep_eval_result_to_json(result: DeepEvalResult) -> dict:
    """将 DeepEvalResult 序列化为 JSON 友好字典。"""
    return {
        "evaluated_at": result.evaluated_at,
        "total_pages": result.total_pages,
        "accuracy": {
            "total_references": result.total_references,
            "consistent": result.consistent_count,
            "inconsistent": result.inconsistent_count,
            "unverifiable": result.unverifiable_count,
            "source_missing": result.source_missing_count,
            "overall_rate": result.overall_accuracy_rate,
        },
        "comprehensiveness": {
            "pages_with_gaps": result.pages_with_gaps,
            "total_gaps": result.total_gaps,
            "summary": result.overall_comprehensiveness_note,
        },
        "top_inconsistent_pages": result.top_inconsistent_pages,
        "per_page_details": [
            {
                "title": pr.title,
                "type": pr.page_type,
                "path": pr.path,
                "accuracy_rate": pr.accuracy_rate,
                "issues": [
                    {
                        "ref": v.reference.raw,
                        "source": v.reference.source_path,
                        "type": v.verdict,
                        "detail": v.detail,
                    }
                    for v in pr.accuracy_verdicts
                    if v.verdict != "consistent"
                ],
                "coverage_gaps": [
                    {
                        "source": g.source_path,
                        "topic": g.missing_topic,
                    }
                    for g in pr.coverage_gaps
                ],
            }
            for pr in result.page_results
        ],
        "recommendations": result.recommendations,
    }


def print_deep_eval_pretty(result: DeepEvalResult) -> None:
    """打印人类可读的深度评估结果。"""
    acc = result.overall_accuracy_rate
    acc_str = f"{acc:.1%}" if acc is not None else "N/A"

    print(f"\n{'='*55}")
    print(f"  深度评估结果")
    print(f"{'='*55}")
    print(f"  评估页面: {result.total_pages}")
    print(f"  总引用数: {result.total_references}")
    print()
    print(f"  ▌ 内容准确性")
    print(f"    一致(consistent):   {result.consistent_count}")
    print(f"    不一致(inconsistent): {result.inconsistent_count}")
    print(f"    无法验证:          {result.unverifiable_count}")
    print(f"    源文件缺失:        {result.source_missing_count}")
    print(f"    准确率:            {acc_str}")
    print()
    print(f"  ▌ 内容全面性")
    print(f"    {result.overall_comprehensiveness_note}")
    print()

    if result.top_inconsistent_pages:
        print(f"  ▌ 不一致最多的页面 (Top {len(result.top_inconsistent_pages)})")
        for ip in result.top_inconsistent_pages:
            print(f"    ❌ {ip['title']}: {ip['inconsistent']}/{ip['total']} 条不一致")
        print()

    # 输出有问题的页面详情
    issues_found = False
    for pr in result.page_results:
        has_issues = any(v.verdict != "consistent" for v in pr.accuracy_verdicts)
        has_gaps = len(pr.coverage_gaps) > 0
        if not has_issues and not has_gaps:
            continue
        issues_found = True

    if not issues_found:
        print(f"  ✅ 所有页面均无问题")
    print()

    # ── 打印修复方案 ──
    if result.recommendations:
        print(f"{'='*55}")
        print(f"  修复方案")
        print(f"{'='*55}")
        for r in result.recommendations:
            icon = {"P0": "🔴", "P1": "🟡", "P2": "🟢"}.get(r["priority"], "⚪")
            print(f"\n  {icon} [{r['priority']}] {r['category']}")
            print(f"    问题: {r['problem']}")
            print(f"    方案: {r['suggestion']}")
            print(f"    工作量: {r['estimated_effort']}")
            print(f"    自动化: {r['automation']}")
        print()
