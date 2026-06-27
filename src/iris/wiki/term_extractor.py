"""Wiki 术语提取与 ASR 误识别生成。

从 Wiki 页面中提取人名、术语、项目名等关键术语，调用 LLM 批量生成
paraformer ASR 的常见误识别映射，最终渲染为 vocotype 可用的校正系统提示词。

用法:
    from iris.wiki.term_extractor import TermExtractor, render_asr_prompt
    from iris.wiki.context_loader import WikiContextLoader

    loader = WikiContextLoader(wiki_root)
    pages = loader.load_pages(sort_order=["person", "concept", "project", "domain"])

    extractor = TermExtractor(pages)
    terms = extractor.extract_terms()          # 阶段 1：规则提取（纯本地）
    terms = extractor.generate_misreadings(terms, provider)  # 阶段 2：LLM 批量生成

    prompt = render_asr_prompt(terms, version, output_format="standard")
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from ._constants import get_wiki_prefix
from .context_loader import WikiPageInfo
from .discovery_utils import extract_terms, is_high_value_term, normalized_key

if TYPE_CHECKING:
    from iris.llm.provider import EnvironmentConfiguredLLMProvider


# ═══════════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AsrTerm:
    """ASR 校正用术语条目。

    Attributes:
        term: 正确写法，如 "张三"、"BM25"
        category: 术语类别，person | concept | project | domain_term
        context: 简短说明，如 "算法工程师, Alpha项目"
        mis_asr: 常见 ASR 误识别列表，由 generate_misreadings() 填充
    """
    term: str
    category: str
    context: str
    mis_asr: List[str] = field(default_factory=list)


@dataclass
class AsrPromptVersion:
    """ASR 提示词版本信息。

    Attributes:
        version: 三段式版本号，如 "1.0.0"
        generated_at: ISO 8601 时间戳
        wiki_page_count: 来源 Wiki 页面总数
        term_count: 提取的术语总数
        fingerprint: Wiki 内容指纹（SHA-256 前16位 hex）
    """
    version: str
    generated_at: str
    wiki_page_count: int
    term_count: int
    fingerprint: str


# ═══════════════════════════════════════════════════════════════════
# TermExtractor
# ═══════════════════════════════════════════════════════════════════

class TermExtractor:
    """从 Wiki 页面中提取 ASR 校正术语。

    分两阶段工作：
    1. extract_terms() — 纯规则提取，无 LLM 依赖
    2. generate_misreadings() — 调用 base_model 批量生成误识别
    """

    def __init__(self, pages: List[WikiPageInfo]) -> None:
        self._pages = pages

    # ── 阶段 1：术语提取（纯规则） ──────────────────────────

    def extract_terms(self) -> List[AsrTerm]:
        """遍历 Wiki 页面，按 person → concept → project → domain 顺序提取并去重。

        domain 类型的术语从正文中抽取，可能一页产生多个；其余类型每页一个。
        去重规则：同名术语（规范化后）保留先出现的类别。
        """
        all_terms: List[AsrTerm] = []

        for page in self._pages:
            ptype = page.page_type
            if ptype == "person":
                t = self._extract_from_person(page)
                if t:
                    all_terms.append(t)
            elif ptype == "concept":
                t = self._extract_from_concept(page)
                if t:
                    all_terms.append(t)
            elif ptype == "project":
                t = self._extract_from_project(page)
                if t:
                    all_terms.append(t)
            elif ptype == "domain":
                domain_terms = self._extract_from_domain(page)
                all_terms.extend(domain_terms)

        return self._deduplicate(all_terms)

    def _extract_from_person(self, page: WikiPageInfo) -> Optional[AsrTerm]:
        """从人物页面提取：文件名去前缀为 term，## 角色 段首句为 context。"""
        term = self._term_from_filename(page, "person")
        if not term:
            return None
        context = self._extract_role_context(page) or page.summary or ""
        return AsrTerm(term=term, category="person", context=context)

    def _extract_from_concept(self, page: WikiPageInfo) -> Optional[AsrTerm]:
        """从概念页面提取：文件名去前缀为 term，优先取 ## 定义 段首句为 context。"""
        term = self._term_from_filename(page, "concept")
        if not term:
            return None
        context = (
            self._extract_section_first_line(page.body, "定义")
            or page.summary
            or ""
        )
        return AsrTerm(term=term, category="concept", context=context)

    def _extract_from_project(self, page: WikiPageInfo) -> Optional[AsrTerm]:
        """从项目页面提取：文件名去前缀为 term，summary 首句为 context。"""
        term = self._term_from_filename(page, "project")
        if not term:
            return None
        return AsrTerm(term=term, category="project", context=page.summary or "")

    def _extract_from_domain(self, page: WikiPageInfo) -> List[AsrTerm]:
        """从领域页面提取专有名词：对 body 调用 discovery 的术语提取管线。"""
        results: List[AsrTerm] = []
        candidates = extract_terms(page.body)
        for cand in candidates:
            if not is_high_value_term(cand):
                continue
            # 排除过短或纯标点
            clean = cand.strip()
            if len(clean) < 2:
                continue
            results.append(AsrTerm(
                term=clean,
                category="domain_term",
                context=page.title or "",
            ))
        return results

    def _deduplicate(self, terms: List[AsrTerm]) -> List[AsrTerm]:
        """按 term 去重，保留最先出现的类别。"""
        seen: Dict[str, AsrTerm] = {}
        for t in terms:
            key = normalized_key(t.term)
            if key not in seen:
                seen[key] = t
        return list(seen.values())

    # ── 辅助：从文件名提取术语名 ────────────────────────────

    @staticmethod
    def _term_from_filename(page: WikiPageInfo, page_type: str) -> str:
        """从文件名提取术语名：去掉类型前缀和 .md 后缀。

        示例:
            人物-张三.md → 张三
            概念-BM25.md → BM25
            项目-Alpha.md → Alpha
        """
        prefix = get_wiki_prefix(page_type)
        name = page.path.stem
        if name.startswith(prefix):
            return name[len(prefix):]
        return name

    # ── 辅助：从正文提取上下文 ─────────────────────────────

    @staticmethod
    def _extract_role_context(page: WikiPageInfo) -> str:
        """从人物页面正文提取 ## 角色 段落的首句。"""
        return TermExtractor._extract_section_first_line(page.body, "角色")

    @staticmethod
    def _extract_section_first_line(body: str, section_name: str) -> str:
        """提取指定 ## 章节标题后的第一行非空文本（到句号或换行）。"""
        lines = body.splitlines()
        in_section = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"## {section_name}"):
                in_section = True
                continue
            if in_section:
                if stripped.startswith("##"):
                    break  # 进入下一个章节了
                if stripped:
                    # 取到第一个中文/英文句号
                    for sep in ("。", ". ", "；"):
                        idx = stripped.find(sep)
                        if idx > 0:
                            return stripped[:idx + 1]
                    return stripped[:120]
        return ""

    # ── 阶段 2：LLM 批量误识别生成 ──────────────────────────

    def generate_misreadings(
        self,
        terms: List[AsrTerm],
        provider: "EnvironmentConfiguredLLMProvider",
    ) -> List[AsrTerm]:
        """调用 base_model 批量生成所有术语的 ASR 误识别。

        将所有术语打包为一条 prompt，一次 LLM 调用返回全部结果。
        LLM 调用失败时 mis_asr 保持空列表，不影响后续渲染。

        Args:
            terms: extract_terms() 的结果
            provider: Iris LLM Provider

        Returns:
            填充了 mis_asr 的同一个 terms 列表
        """
        if not terms:
            return terms

        prompt = self._build_misreadings_prompt(terms)

        try:
            from iris.llm import LLMRequest
            response = provider.generate(
                LLMRequest(
                    prompt=prompt,
                    route_context={"task_type": "asr_misreading", "input_type": "text"},
                ),
                temperature=0.3,
                max_tokens=4096,
            )
            self._parse_misreadings_response(response.text, terms)
        except Exception as exc:
            print(
                f"[warn] ASR 误识别 LLM 调用失败，将继续生成不含误识别列的 prompt: {exc}",
                file=sys.stderr,
            )

        return terms

    def _build_misreadings_prompt(self, terms: List[AsrTerm]) -> str:
        """构建批量误识别生成的 LLM prompt。"""
        term_items = []
        for t in terms:
            type_label = {
                "person": "人名",
                "concept": "术语",
                "project": "项目名",
                "domain_term": "领域术语",
            }.get(t.category, "术语")
            ctx = f"（{t.context}）" if t.context else ""
            term_items.append(f"- [{type_label}] {t.term} {ctx}".strip())

        return f"""你是语音识别（ASR）误识别专家。你精通 paraformer 等中文 ASR 模型的常见错误模式。

以下是我工作场景中的人名、术语、项目名列表。请为每个条目列出 paraformer 语音转写中最可能出现的 3-5 个误识别结果。

注意事项：
- 中文人名：考虑同音字替换（如 张→章、伟→玮）、声母混淆（zh→z, sh→s, ch→c, n→l, r→l）、韵母混淆（an→ang, en→eng, in→ing）
- 英文术语：考虑中国人读出英文时的中文音译（如 BM25→必爱姆二十五、MMoE→毛艾）、大小写错误、字母数字混合错误
- 中文术语：考虑同音词/近音词替代
- 英文人名：考虑拼写变异和中文音译
- 直接输出纯 JSON 数组，不要 Markdown 代码块包裹，不要任何解释文字

## 术语列表
{chr(10).join(term_items)}

## 输出格式
[
  {{"term": "张三", "category": "person", "mis_asr": ["张珊", "章三", "章山"]}},
  {{"term": "BM25", "category": "concept", "mis_asr": ["bm二十五", "必爱姆25"]}},
  ...
]"""

    @staticmethod
    def _parse_misreadings_response(response_text: str, terms: List[AsrTerm]) -> None:
        """解析 LLM 返回的 JSON 数组，按 (term, category) 匹配回填 mis_asr。

        容错处理：
        - 去除可能的 ```json ``` 包裹
        - 解析失败则全部 mis_asr 保持空列表
        - 部分 term 未匹配则仅未匹配项保持空列表
        """
        text = response_text.strip()
        # 容错：去掉可能的 Markdown 代码块包裹
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取第一个 JSON 数组
            import re
            m = re.search(r"\[.*\]", text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    return
            else:
                return

        if not isinstance(data, list):
            return

        # 构建查找索引
        index: Dict[str, AsrTerm] = {}
        for t in terms:
            key = f"{t.term}|{t.category}"
            index[key] = t

        for item in data:
            if not isinstance(item, dict):
                continue
            key = f"{item.get('term', '')}|{item.get('category', '')}"
            if key in index:
                mis = item.get("mis_asr", [])
                if isinstance(mis, list):
                    index[key].mis_asr = [str(x) for x in mis[:5]]


# ═══════════════════════════════════════════════════════════════════
# 提示词渲染
# ═══════════════════════════════════════════════════════════════════

def render_asr_prompt(
    terms: List[AsrTerm],
    version: AsrPromptVersion,
    output_format: str = "standard",
) -> str:
    """将术语列表渲染为 ASR 校正系统提示词。

    Args:
        terms: 已填充 mis_asr 的术语列表
        version: 版本信息
        output_format: "standard" (Markdown 表格) 或 "compact" (纯文本)

    Returns:
        完整 prompt 字符串，可直接复制到 vocotype LLM 校正配置中
    """
    if output_format in ("compact",):
        return _render_compact(terms, version)
    return _render_standard(terms, version)


def _render_standard(terms: List[AsrTerm], version: AsrPromptVersion) -> str:
    """标准格式：Markdown 表格，便于人类阅读和微调。"""
    # 按类别分组
    persons = [t for t in terms if t.category == "person"]
    concepts = [t for t in terms if t.category == "concept"]
    projects = [t for t in terms if t.category == "project"]
    domain_terms = [t for t in terms if t.category == "domain_term"]

    lines = [
        "你是 ASR 语音转写后处理校正助手。你的任务是将语音转写文本校正为准确、流畅的书面中文。",
        "严格遵守以下校正规则。仅输出校正后的文本，不要添加任何解释、说明或前缀。",
        "",
    ]

    # 人名词典
    if persons:
        lines.append("## 人名词典")
        lines.append("以下为工作场景中的人名，语音转写中可能被误识别为同音/近音字：")
        lines.append("")
        lines.append("| 正确写法 | 说明 | 常见 ASR 误识别 |")
        lines.append("|---------|------|----------------|")
        for t in persons:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 术语词典
    if concepts:
        lines.append("## 术语词典")
        lines.append("以下为工作领域的专业术语，语音转写中可能被误识别：")
        lines.append("")
        lines.append("| 正确写法 | 说明 | 常见 ASR 误识别 |")
        lines.append("|---------|------|----------------|")
        for t in concepts:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 项目名词典
    if projects:
        lines.append("## 项目名词典")
        lines.append("以下为工作场景中的项目名称：")
        lines.append("")
        lines.append("| 正确写法 | 说明 | 常见 ASR 误识别 |")
        lines.append("|---------|------|----------------|")
        for t in projects:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 领域专有名词
    if domain_terms:
        lines.append("## 领域专有名词")
        lines.append("")
        lines.append("| 正确写法 | 来源领域 | 常见 ASR 误识别 |")
        lines.append("|---------|---------|----------------|")
        for t in domain_terms:
            mis = "、".join(t.mis_asr) if t.mis_asr else "-"
            lines.append(f"| {t.term} | {t.context or '-'} | {mis} |")
        lines.append("")

    # 通用校正规则
    lines.extend([
        "## 通用校正规则",
        "- 技术英文术语保持原写法，不要翻译成中文",
        "- 中文数字与阿拉伯数字：根据上下文判断（技术语境中\"二十五\"→25）",
        "- 中英文混排时，英文前后保留空格",
        "- 代码、命令、文件名等保持原样，不翻译",
        "- 标点符号使用中文全角标点",
        "- 保持原意不变，仅修正转写错误和语序问题",
        "",
        "---",
        f"ASR Prompt v{version.version} | 生成: {version.generated_at} | 来源: LLM-WIKI（{version.wiki_page_count}页, {version.term_count}术语）",
    ])

    return "\n".join(lines)


def _render_compact(terms: List[AsrTerm], version: AsrPromptVersion) -> str:
    """紧凑格式：纯文本分号分隔，最小 token 消耗。"""
    persons = [t for t in terms if t.category == "person"]
    concepts = [t for t in terms if t.category == "concept"]
    projects = [t for t in terms if t.category == "project"]
    domain_terms = [t for t in terms if t.category == "domain_term"]

    blocks = ["你是 ASR 校正助手。校正规则如下，仅输出校正后文本："]

    def _term_str(t: AsrTerm) -> str:
        ctx = f",{t.context}" if t.context else ""
        mis = "/".join(t.mis_asr) if t.mis_asr else ""
        if mis:
            return f"{t.term}={t.category}{ctx}|勿误:{mis}"
        return f"{t.term}={t.category}{ctx}"

    if persons:
        blocks.append("【人名】" + ";".join(_term_str(t) for t in persons))
    if concepts:
        blocks.append("【术语】" + ";".join(_term_str(t) for t in concepts))
    if projects:
        blocks.append("【项目】" + ";".join(_term_str(t) for t in projects))
    if domain_terms:
        blocks.append("【领域】" + ";".join(_term_str(t) for t in domain_terms))

    blocks.append("【规则】英文术语保持原写;代码/命令/文件名不翻译;中英文间加空格;中文全角标点;保持原意仅修正转写错误")

    blocks.append(
        f"---\n"
        f"v{version.version}|{version.generated_at}|{version.wiki_page_count}页{version.term_count}术语"
    )

    return "\n".join(blocks)


# ═══════════════════════════════════════════════════════════════════
# 版本管理
# ═══════════════════════════════════════════════════════════════════

_VERSION_FILE = "asr_prompt_version.json"


def _now_iso() -> str:
    """当前时间的 ISO 8601 字符串（含时区）。"""
    return datetime.now(timezone.utc).isoformat()


def compute_fingerprint(pages: List[WikiPageInfo]) -> str:
    """计算 Wiki 页面内容指纹。

    对每页 (title, type, body[:500]) 拼接后计算 SHA-256，返回前 16 位 hex。
    使用 body[:500] 而非完整正文，避免微小文字改动频繁触发版本变化。
    """
    h = hashlib.sha256()
    for page in sorted(pages, key=lambda p: str(p.path)):
        snippet = f"{page.title}|{page.page_type}|{page.body[:500]}"
        h.update(snippet.encode("utf-8"))
    return h.hexdigest()[:16]


def load_version(data_dir: Path) -> Optional[AsrPromptVersion]:
    """加载版本文件。文件不存在或损坏时返回 None。"""
    path = data_dir / _VERSION_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return AsrPromptVersion(
            version=data.get("version", "0.0.0"),
            generated_at=data.get("generated_at", ""),
            wiki_page_count=data.get("wiki_page_count", 0),
            term_count=data.get("term_count", 0),
            fingerprint=data.get("fingerprint", ""),
        )
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def save_version(data_dir: Path, version: AsrPromptVersion) -> None:
    """持久化版本信息到 JSON 文件。使用 FileLock 保证并发安全。"""
    from iris.core.locks import FileLock

    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / _VERSION_FILE

    payload = {
        "version": version.version,
        "generated_at": version.generated_at,
        "wiki_page_count": version.wiki_page_count,
        "term_count": version.term_count,
        "fingerprint": version.fingerprint,
    }

    with FileLock(str(path)):
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def bump_version(current: str, bump: str) -> str:
    """纯函数：递增三段式版本号。

    Args:
        current: 当前版本号，如 "1.0.0"
        bump: "major" | "minor" | "patch" | "auto"

    Returns:
        新版本号字符串
    """
    try:
        parts = [int(x) for x in current.split(".")]
        while len(parts) < 3:
            parts.append(0)
        major, minor, patch = parts[0], parts[1], parts[2]
    except (ValueError, TypeError):
        major, minor, patch = 0, 0, 0

    if bump == "major":
        return f"{major + 1}.0.0"
    elif bump == "minor":
        return f"{major}.{minor + 1}.0"
    else:  # patch 或 auto
        return f"{major}.{minor}.{patch + 1}"


def determine_new_version(
    pages: List[WikiPageInfo],
    data_dir: Path,
    bump: str = "auto",
) -> AsrPromptVersion:
    """综合判定新版本号。

    auto 模式：
    - 指纹无变化 → 返回旧版本（版本号不变）
    - 指纹有变化 → bump patch

    手动模式（major/minor/patch）：
    - 始终递增，忽略指纹

    Args:
        pages: 已加载的 Wiki 页面列表
        data_dir: 项目 data/ 目录
        bump: "auto" | "major" | "minor" | "patch"

    Returns:
        新版本信息（auto 且指纹不变时返回旧版本）
    """
    fingerprint = compute_fingerprint(pages)
    now = _now_iso()
    old = load_version(data_dir)

    if bump == "auto" and old and old.fingerprint == fingerprint:
        # 指纹不变，返回旧版本
        return old

    if old:
        new_ver = bump_version(old.version, bump)
    else:
        new_ver = bump_version("0.0.0", "patch")  # 首次生成 → 0.0.1

    return AsrPromptVersion(
        version=new_ver,
        generated_at=now,
        wiki_page_count=len(pages),
        term_count=0,  # 由调用方填充
        fingerprint=fingerprint,
    )
