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
import logging
import re
import sys
from concurrent.futures import as_completed, TimeoutError as FuturesTimeoutError

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from ._constants import get_all_types, get_wiki_prefix
from .context_loader import WikiPageInfo
from .discovery_utils import extract_terms, is_high_value_term, normalized_key
from iris.utils.template_loader import load_template as _load_template_file

if TYPE_CHECKING:
    from iris.llm.provider import EnvironmentConfiguredLLMProvider


# ── 正则常量 ──────────────────────────────────────────────
_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_WIKI_LINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")

# 排除的章节标题（无 ASR 价值）
_SKIP_HEADINGS = frozenset({
    "摘要", "正文", "概述", "总结", "背景", "目标", "结论",
    "参考来源", "关联页面", "当前结论", "相关依据",
})


# 领域术语噪音模式：纯数字、百分比、数值+单位、单字母等
_NOISE_TERM_RE = re.compile(
    r"^\d+[分%]?$|"              # "100", "100分"
    r"^\d+\.\d+[%+]?$|"         # "0.38", "48.14%", "55%+"
    r"^\d+\s*张图片|"            # "2000 张图片"
    r"^\d+\s*台手机|"            # "2000 台手机"
    r"^\d+\s*(台|个|张|款|套|分|%|款)\s*$|"  # "19 台", "1个"
    r"\d+\s*[/%]\s*\d+[+]?$|"   # "22/33", "55%+"
    r"^[①②③④⑤⑥⑦⑧⑨⑩]|"       # 列表编号开头
    r"^[A-Za-z0-9]{,2}$|"       # "V21", "h1" 等
    r"^V?\d+(?:\.\d+)+$|"       # "1.0.1", "V2.1"
    r"^\d+年\d+月\d+日$|"       # "2026年6月2日" — 日期
    r"^\d+个(百分)?点$|"         # "5个百分点", "1个百分点", "3百分点"
    r"是\s*.*的\s*|"            # "脏污是...的" — 判断句型
    # 新增噪音模式
    r"^\d+/\d+[-–]\d+/\d+$|"    # "6/24-6/27"
    r"^\d+/\d+[（(]\w+[）)]$|"  # "6/26（周五）"
    r"^\d+/\d+\s*前$|"          # "6/30 前"
    r"^\d+[%+]\+$|"             # "55%+"（百分比+加号）
    r"^[A-Za-z]+[,，]\s*\d+$"   # "Week, 10" 类
)


def _is_noise_term(term: str) -> bool:
    """判断术语是否为噪音（数字/百分比/短字母等）。"""
    return bool(_NOISE_TERM_RE.match(term.strip()))


def _clean_markup(text: str) -> str:
    """清理文本中的 Markdown / Wiki 语法标记。

    - **粗体** → 粗体
    - [[链接]] → 链接
    - [[链接|显示名]] → 显示名
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\[\[([^\[\]]+?)\|([^\[\]]+?)\]\]", r"\2", text)
    text = re.sub(r"\[\[([^\[\]]+?)\]\]", r"\1", text)
    return text


def _truncate_context(text: str, max_chars: int = 60) -> str:
    """截断 context 到指定长度（优先在句号处断句）。

    先清理 Markdown/Wiki 标记再截断，确保长度按实际文字计算。
    """
    if not text:
        return ""
    clean = _clean_markup(text.strip())
    if len(clean) <= max_chars:
        return clean
    for sep in ("。", "；"):
        idx = clean.find(sep)
        if 0 < idx <= max_chars:
            return clean[:idx + 1]
    # 没有句号时按长度截断，不用逗号（逗号截断会产生语义不完整的半截句子）
    return clean[:max_chars].rstrip() + "…"


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
    prompt_text: str = ""


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
        term = self._term_for_asr(page, "person")
        if not term:
            return None
        context = (
            self._extract_role_context(page)
            or page.summary
            or ""
        )
        return AsrTerm(term=term, category="person", context=_truncate_context(context, 60))

    def _extract_from_concept(self, page: WikiPageInfo) -> Optional[AsrTerm]:
        """从概念页面提取：文件名去前缀为 term，优先取 ## 定义 段首句为 context。"""
        term = self._term_for_asr(page, "concept")
        if not term:
            return None
        context = (
            self._extract_section_first_line(page.body, "定义")
            or page.summary
            or ""
        )
        return AsrTerm(term=term, category="concept", context=_truncate_context(context, 60))

    def _extract_from_project(self, page: WikiPageInfo) -> Optional[AsrTerm]:
        """从项目页面提取：文件名去前缀为 term，summary 首句为 context。"""
        term = self._term_for_asr(page, "project")
        if not term:
            return None
        return AsrTerm(term=term, category="project", context=_truncate_context(page.summary or "", 60))

    def _extract_from_domain(self, page: WikiPageInfo) -> List[AsrTerm]:
        """从领域页面提取专有名词：提取标题、## 章节标题、**粗体**、[[Wiki链接]]。

        不再使用正文全文的贪婪正则匹配（会导致碎片句子被当作术语），
        改为从结构化元素中提取高价值名词。
        """
        results: List[AsrTerm] = []
        seen: set = set()
        title = page.title or ""
        body = page.body
        ctx = _truncate_context(title, 40)

        # 1. 页面标题本身作为首个术语
        if title and len(title) >= 2:
            results.append(AsrTerm(term=title, category="domain_term", context=ctx))
            seen.add(normalized_key(title))

        # 2. 提取 ## 章节标题
        for m in _HEADING_RE.finditer(body):
            h_text = m.group(1).strip()
            if not h_text or len(h_text) < 2 or h_text in _SKIP_HEADINGS:
                continue
            if _is_noise_term(h_text):
                continue
            key = normalized_key(h_text)
            if key not in seen and is_high_value_term(h_text):
                seen.add(key)
                results.append(AsrTerm(term=h_text, category="domain_term", context=ctx))

        # 3. 提取 **粗体** 内容（通常是关键术语/项目名）
        for m in _BOLD_RE.finditer(body):
            bold = m.group(1).strip()
            if not bold or len(bold) < 2:
                continue
            if _is_noise_term(bold):
                continue
            # 过长的粗体内容（超过 20 字）通常是句子而非术语
            if len(bold) > 20:
                continue
            # 包含"是…的"判断句式的不是术语
            if "是" in bold and len(bold) > 10:
                continue
            key = normalized_key(bold)
            if key not in seen and is_high_value_term(bold):
                seen.add(key)
                results.append(AsrTerm(term=bold, category="domain_term", context=ctx))

        # 4. 提取 [[Wiki 链接]]（出链目标通常是相关概念/项目）
        for m in _WIKI_LINK_RE.finditer(body):
            link = m.group(1).strip()
            if not link or len(link) < 2:
                continue
            # 排除 internal links 格式 [[链接|显示名]] → 取链接部分
            if "|" in link:
                link = link.split("|")[0].strip()
            # 去掉页面类型前缀（如 "概念-BM25" → "BM25"），避免噪音
            for ptype in get_all_types():
                prefix = get_wiki_prefix(ptype)
                if link.startswith(prefix):
                    link = link[len(prefix):]
                    break
            key = normalized_key(link)
            if key not in seen and is_high_value_term(link):
                seen.add(key)
                results.append(AsrTerm(term=link, category="domain_term", context=ctx))

        # 每页最多 15 个术语，防止膨胀
        # 优先级：标题 > 标题段落 > 粗体 > Wiki 链接
        return results[:15]

    def _deduplicate(self, terms: List[AsrTerm]) -> List[AsrTerm]:
        """按 term 去重，保留最先出现的类别。"""
        seen: Dict[str, AsrTerm] = {}
        for t in terms:
            key = normalized_key(t.term)
            if key not in seen:
                seen[key] = t
        return list(seen.values())

    # ── 辅助：从页面提取术语名 ────────────────────────────

    @staticmethod
    def _term_for_asr(page: WikiPageInfo, page_type: str) -> str:
        """获取适合 ASR 的术语名。

        优先使用 page.title（保留 "3.0" 等格式），
        但如果 title 只是在 filename 基础上额外加了空格
        （如 filename=AgenticCloud与AIAgent, title=Agentic Cloud 与 AI Agent），
        则用 filename 版本（无空格，更适合 ASR）。

        示例:
            人物-张三.md title="张三" → 张三（一致，用 title）
            概念-AgenticCloud与AIAgent.md title="Agentic Cloud 与 AI Agent"
                → AgenticCloud与AIAgent（title 仅在 filename 上加空格）
            项目-项目Beta3AI分析.md title="项目Beta 3.0 AI分析"
                → 项目Beta 3.0 AI分析（保留正确的 3.0）
        """
        prefix = get_wiki_prefix(page_type)
        name_from_file = page.path.stem
        if name_from_file.startswith(prefix):
            name_from_file = name_from_file[len(prefix):]

        title = (page.title or "").strip()
        if not title:
            return name_from_file

        # 如果 title 去掉空格后与 filename 一致，用 filename（无空格）
        if title.replace(" ", "").replace(" ", "") == name_from_file:
            return name_from_file

        return title

    # 兼容旧接口
    _term_from_filename = _term_for_asr

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

    _BATCH_SIZE = 50  # 每批最多 50 个术语，确保 JSON 响应不超 max_tokens

    def generate_misreadings(
        self,
        terms: List[AsrTerm],
        provider: "EnvironmentConfiguredLLMProvider",
        domain_context: str = "",
    ) -> List[AsrTerm]:
        """调用 base_model 批量生成所有术语的 ASR 误识别。

        按 _BATCH_SIZE 分批调用，避免 LLM 输出截断导致 JSON 解析失败。
        LLM 调用失败时 mis_asr 保持空列表，不影响后续渲染。

        Args:
            terms: extract_terms() 的结果
            provider: Iris LLM Provider

        Returns:
            填充了 mis_asr 的同一个 terms 列表
        """
        if not terms:
            return terms

        from iris.llm import LLMRequest

        # 分批：每批独立回填自身 AsrTerm 的 mis_asr，批间无共享状态 → 可并发
        batches = [
            terms[start:start + self._BATCH_SIZE]
            for start in range(0, len(terms), self._BATCH_SIZE)
        ]

        def _run_batch(idx_batch):
            idx, batch = idx_batch
            prompt = self._build_misreadings_prompt(batch, domain_context)
            try:
                response = provider.generate(
                    LLMRequest(
                        prompt=prompt,
                        route_context={
                            "task_type": "asr_misreading",
                            "input_type": "text",
                        },
                    ),
                    temperature=0.3,
                    max_tokens=8192,
                )
                self._parse_misreadings_response(response.text, batch)
            except Exception as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                print(
                    f"[warn] 第 {idx + 1} 批 ASR 误识别生成失败: {exc}",
                    file=sys.stderr,
                )

        _timeout = min(len(batches), 8) * 90
        from iris.core.thread_pool import shared_pool
        with shared_pool.executor(max_workers=min(len(batches), 8)) as executor:
            try:
                list(executor.map(_run_batch, enumerate(batches), timeout=_timeout))
            except FuturesTimeoutError:
                logger.warning("ASR 误识别生成超时（%ds），已处理完成的批次保留", _timeout)

        return terms

    def _build_misreadings_prompt(self, terms: List[AsrTerm], domain_context: str = "") -> str:
        """构建批量误识别生成的 LLM prompt。

        增强说明拼音混淆模式、中英混排处理、领域上下文，
        引导 LLM 生成更真实、可用的 ASR 误识别。
        """
        term_items = []
        for t in terms:
            type_label = {
                "person": "人名",
                "concept": "术语",
                "project": "项目名",
                "domain_term": "领域术语",
            }.get(t.category, "术语")
            ctx = f"（{_clean_markup(t.context[:40])}）" if t.context else ""
            term_items.append(f"- [{type_label}] {t.term} {ctx}".strip())

        ctx = domain_context or "这是一个专业团队的知识库。"
        term_items_text = chr(10).join(term_items)

        # 尝试从外部模板加载
        template = self._load_misreadings_template()
        if template:
            return template.replace("{{domain_context}}", ctx).replace("{{term_items}}", term_items_text)

        # 降级：内联 prompt
        return f"""你是语音识别（ASR）误识别专家。你精通 paraformer 等中文 ASR 模型的常见错误模式。

## 领域背景
{ctx}

## 任务
为以下术语列表的每个条目，列出 paraformer 语音转写中最可能出现的 3-5 个误识别。

## 误识别生成模式（按优先级）
1. **中文人名**：同音字（张→章、杨→阳）、声母混淆（zh↔z, ch↔c, sh↔s, n↔l, r↔l, h↔f）、
   韵母混淆（an↔ang, en↔eng, in↔ing, ian↔ie, eng↔ong）、近形字（瀚→翰→汉）
2. **中英混排词**（如「Model-v2」「H200」「qwen3.5」）：
   - 数字读法混淆：3.0→三点零/三零/30（最危险）
   - 英文字母音译：H→爱吃/艾尺, Q→Q/扣/球
   - 大小写变体：Qwen→qwen/QWEN/Q温
   - 多路径：Model-v2→ModelV2/模型v二/模型2
3. **英文缩写**（DNN、OCR、MMoE）：
   - 逐字母读出时的中文音译：字母→对应音（如 D→第/地/狄）
   - 连读误判：全大写→全小写、字母间加空格（DNN→D N N）
4. **中文术语**：同音词/近音词替换，注意分词错误（「智能化检测」→「智能」+「化检测」
   被 ASR 误分割为「智能画检测」）
5. **项目名/长名词**：逐字替换 + 可能的简化（「智能审核与稽查项目」→「智能审核稽查」）

## 质量约束
- 误识别必须是真实语音转写中最可能发生的，不能只是随机的同音字
- 考虑 ASR 分词错误：一个字被吃掉、两个字被合并、边界偏移
- 直接输出纯 JSON 数组，不要 Markdown 代码块包裹，不要任何解释

## 术语列表
{term_items_text}

## 输出格式
[
  {{"term": "张三", "category": "person", "mis_asr": ["张珊", "章三", "章山"]}},
  {{"term": "Model-v2", "category": "domain_term", "mis_asr": ["模型v二", "模型2", "ModelV2"]}},
  {{"term": "BM25", "category": "concept", "mis_asr": ["bm二十五", "必爱姆25", "必爱慕25"]}},
  {{"term": "智能化检测", "category": "domain_term", "mis_asr": ["智能画检测", "智能化建筑", "智慧化检测"]}},
  ...
]

注意：category 必须严格使用 person / concept / project / domain_term 四种之一。"""

    @staticmethod
    def _load_misreadings_template() -> Optional[str]:
        """从 templates/prompt/misreadings.md 加载 ASR 误识别 Prompt 模板。"""
        return _load_template_file("prompt/misreadings.md")

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

        # 如果 JSON 被截断（max_tokens 不足），尝试关闭末尾的数组/对象
        # 防止 LLM 输出 4096 tokens 时被截断在 JSON 中间导致全部解析失败
        if not text.endswith("]"):
            # 找到最后一个完整的 } 对象，补上 ]
            last_close = text.rfind("}")
            if last_close > 0:
                text = text[:last_close + 1] + "]"

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取第一个 JSON 数组
            m = re.search(r"\[(?:[^\[\]]|\[[^\]]*\])*\]", text, re.DOTALL) or re.search(r"\[.*?\]", text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    return
            else:
                return

        if not isinstance(data, list):
            return

        # 构建查找索引（含 category 别名兼容：LLM 可能返回 "domain" 代替 "domain_term"）
        CATEGORY_ALIASES = {"domain": "domain_term"}
        index: Dict[str, AsrTerm] = {}
        for t in terms:
            index[f"{t.term}|{t.category}"] = t

        for item in data:
            if not isinstance(item, dict):
                continue
            raw_cat = item.get("category", "")
            resolved_cat = CATEGORY_ALIASES.get(raw_cat, raw_cat)
            key = f"{item.get('term', '')}|{resolved_cat}"
            term_obj = index.get(key)
            if term_obj is None:
                # 模糊匹配：仅按 term 名称匹配
                for k, t in index.items():
                    if k.startswith(f"{item.get('term', '')}|"):
                        term_obj = t
                        break
            if term_obj:
                mis = item.get("mis_asr", [])
                if isinstance(mis, list):
                    term_obj.mis_asr = [str(x) for x in mis[:5]]


# Phase 1 热词提取逻辑已拆分至 asr_hotwords.py



# ── 向后兼容重导出 ──────────────────────────────────────────
# 以下内容已拆分到独立文件，保留重导出以避免破坏现有调用方
from .asr_hotwords import (       # noqa: E402, F401
    LLMHotwordExtractor,
    hotwords_to_terms,
    _build_page_batches,
    _build_hotwords_prompt,
    _parse_hotwords_response,
)
from .asr_formatter import (      # noqa: E402, F401
    render_asr_prompt,
    format_hotwords_file,
    format_replace_dict,
)
from .asr_prompt_optimizer import (  # noqa: E402, F401
    LLMPromptOptimizer,
)
from .asr_version import (        # noqa: E402, F401
    load_version,
    save_version,
    bump_version,
    determine_new_version,
    compute_fingerprint,
)
