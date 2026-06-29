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
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from ._constants import get_all_types, get_wiki_prefix
from .context_loader import WikiPageInfo
from .discovery_utils import extract_terms, is_high_value_term, normalized_key

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
    # 没有句号时按长度截断，不用逗号（会留下"团队成员J是技术研发部成员，"这类半截句子）
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
            项目-拍照30AI外观定级项目.md title="图像采集3.0 AI外观定级项目"
                → 图像采集3.0 AI外观定级项目（保留正确的 3.0）
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

        for start in range(0, len(terms), self._BATCH_SIZE):
            batch = terms[start:start + self._BATCH_SIZE]
            prompt = self._build_misreadings_prompt(batch)

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
                print(
                    f"[warn] 第 {start//self._BATCH_SIZE + 1} 批 ASR 误识别生成失败: {exc}",
                    file=sys.stderr,
                )

        return terms

    def _build_misreadings_prompt(self, terms: List[AsrTerm]) -> str:
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

        return f"""你是语音识别（ASR）误识别专家。你精通 paraformer 等中文 ASR 模型的常见错误模式。

## 领域背景
这是「转转」集团（二手商品交易平台）「技术研发部」的知识库。部门聚焦：
- 二手商品商品质检 AI（某检测项目 拆修检测、图像采集3.0 外观定级、包袋AI评估）
- 搜索推荐算法与工程
- 大模型训练与业务应用

## 任务
为以下术语列表的每个条目，列出 paraformer 语音转写中最可能出现的 3-5 个误识别。

## 误识别生成模式（按优先级）
1. **中文人名**：同音字（张→章、杨→阳）、声母混淆（zh↔z, ch↔c, sh↔s, n↔l, r↔l, h↔f）、
   韵母混淆（an↔ang, en↔eng, in↔ing, ian↔ie, eng↔ong）、近形字（瀚→翰→汉）
2. **中英混排词**（如「图像采集3.0」「H200」「qwen3.5」）：
   - 数字读法混淆：3.0→三点零/三零/30（最危险）
   - 英文字母音译：H→爱吃/艾尺, Q→Q/扣/球
   - 大小写变体：Qwen→qwen/QWEN/Q温
   - 多路径：图像采集3.0→拍照30/拍照三点零/拍照三零/拍照3O
3. **英文缩写**（DNN、OCR、MMoE）：
   - 逐字母读出时的中文音译：字母→对应音（如 D→第/地/狄）
   - 连读误判：全大写→全小写、字母间加空格（DNN→D N N）
4. **中文术语**：同音词/近音词替换，注意分词错误（「质检自动化」→「质检」+「智能化」
   被 ASR 误分割为「质检智能画」）
5. **项目名/长名词**：逐字替换 + 可能的简化（「视频审核与在线评估项目」→「视频审核在线评估」）

## 质量约束
- 误识别必须是真实语音转写中最可能发生的，不能只是随机的同音字
- 考虑 ASR 分词错误：一个字被吃掉、两个字被合并、边界偏移
- 直接输出纯 JSON 数组，不要 Markdown 代码块包裹，不要任何解释

## 术语列表
{chr(10).join(term_items)}

## 输出格式
[
  {{"term": "张三", "category": "person", "mis_asr": ["张珊", "章三", "章山"]}},
  {{"term": "图像采集3.0", "category": "domain_term", "mis_asr": ["拍照三点零", "拍照30", "拍照三零"]}},
  {{"term": "BM25", "category": "concept", "mis_asr": ["bm二十五", "必爱姆25", "必爱慕25"]}},
  {{"term": "质检自动化", "category": "domain_term", "mis_asr": ["质检智能画", "质检制能化", "质检智慧化"]}},
  ...
]

注意：category 必须严格使用 person / concept / project / domain_term 四种之一。"""

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
            import re
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


# ═══════════════════════════════════════════════════════════════════
# Phase 1: LLM 热词提取
# ═══════════════════════════════════════════════════════════════════

_HOTWORD_BATCH_SIZE = 6  # 每批最多 6 个页面


def _build_page_batches(pages: List[WikiPageInfo]) -> List[List[WikiPageInfo]]:
    """按类型分组，每批不超过 _HOTWORD_BATCH_SIZE 个页面。

    顺序：person → concept → project → domain，同类型连续。
    """
    order = {"person": 0, "concept": 1, "project": 2, "domain": 3}
    sorted_pages = sorted(pages, key=lambda p: order.get(p.page_type, 9))
    batches: List[List[WikiPageInfo]] = []
    for i in range(0, len(sorted_pages), _HOTWORD_BATCH_SIZE):
        batches.append(sorted_pages[i:i + _HOTWORD_BATCH_SIZE])
    return batches


def _build_hotwords_prompt(batch: List[WikiPageInfo]) -> str:
    """为一批页面构建热词提取 LLM prompt。

    每页传递完整结构信息（摘要、全量章节标题、粗体术语、Wiki 链接），
    确保 LLM 能理解页面全貌而非仅前 600 字符。
    """
    page_descs = []
    for p in batch:
        body = p.body or ""
        summary = p.summary or ""
        # 从完整正文提取结构元素（不限 600 字符）
        headings = [m.group(1) for m in _HEADING_RE.finditer(body)
                     if m.group(1).strip() not in _SKIP_HEADINGS]
        bolds = [m.group(1) for m in _BOLD_RE.finditer(body)]
        wiki_links = [m.group(1).split("|")[0].strip() for m in _WIKI_LINK_RE.finditer(body)]
        # 去重 + 去类型前缀
        unique_links = []
        seen_links = set()
        for link in wiki_links:
            key = link.lower()
            if key not in seen_links:
                seen_links.add(key)
                unique_links.append(link)
        page_descs.append(
            f"### [{p.page_type.upper()}] {p.title}\n"
            f"摘要：{_clean_markup(summary[:300])}\n"
            f"章节：{' / '.join(headings[:15])}\n"
            f"粗体术语：{'、'.join(bolds[:12])}\n"
            f"内部引用：{'、'.join(unique_links[:12])}"
        )

    return f"""你是语音识别（ASR）热词提取专家。你需要为一个专业工作领域的 Wiki 知识库提取热词。

## 领域背景
这是「转转」集团旗下「技术研发部」的知识库。部门聚焦方向：
- 二手商品商品质检 AI 化（某检测项目 拆修检测、图像采集3.0 外观定级、包袋AI评估）
- 搜索推荐算法与工程
- 大模型训练与业务应用
- 视频审核与在线评估

## 任务
从以下 {len(batch)} 个 Wiki 页面中提取最重要的热词，用于 paraformer 等中文 ASR 模型的
热词增强（boosting），让 ASR 更准确地识别这些专有名词。

## 热词类型（按优先级）
1. **人名**：团队成员全名（2-4 字）
2. **项目名/系统名**：内部项目、平台、系统的完整名称
3. **技术术语**：算法名、框架名、技术栈（中文 + 英文）
4. **英文缩写**：DNN、OCR、MMoE、BERT、BM25、H200 等专有缩写
5. **品牌/产品名**：转转、Qwen、阿里云、豆包、千问 等
6. **中英混排**：图像采集3.0、qwen3.5、iPhone16、Node2Vec 等
7. **业务术语**：质检自动化、定级一致性、图像验证、流水线 等

## 质量要求
- 每个热词 2-20 个字符，超过 20 字符的拆分为多个短词
- 必须是 ASR 容易听错或识别困难的专业词汇（通用词如「部门」「工作」不提取）
- 每批至少提取 80 个热词，不足时从章节标题和粗体中补充
- 优先提取：人名全名、英文缩写、中英混排词——这些是 ASR 最大的痛点
- 不要提取完整句子（如「金刚类1.5品类SKU力度价格特征」应拆为「SKU」「价格特征」）
- 直接输出纯 JSON 数组，不要 Markdown 代码块，不要解释

## Wiki 页面
{chr(10).join(page_descs)}

## 输出格式
[{{"term":"团队成员J"}},{{"term":"图像采集3.0"}},{{"term":"MMoE"}},{{"term":"BM25"}},...]"""


def _count_chinese(text: str) -> int:
    """统计文本中的中文字符数。"""
    return sum(1 for c in text if '一' <= c <= '鿿' or '㐀' <= c <= '䶿')


def _exceeds_char_limit(text: str, max_total: int = 20, max_chinese: int = 10) -> bool:
    """检查文本是否超过字符限制（总长度或中文字数）。"""
    if not text:
        return False
    if len(text) > max_total:
        return True
    if _count_chinese(text) > max_chinese:
        return True
    return False


def _clean_text_term(term: str) -> str:
    """清理单个热词：去除控制字符、首尾标点、空白。"""
    t = term.strip().strip('，。；：、！？"\"''「」『』【】《》（）()[]{}')
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
    t = t.replace("�", "")
    return t.strip()


def _is_valid_hotword(term: str) -> bool:
    """过滤不符合热词质量要求的条目。

    - 纯数字/纯标点
    - 长度超限（>20 字符 或 >10 中文字）
    - 明显是句子片段（>12 字且含「的」「是」「在」）
    - 单字
    - 括号不完整
    """
    if not term or len(term) < 2:
        return False
    if _exceeds_char_limit(term):
        return False
    if re.match(r'^[\d\s\.\+\-/%]+$', term):
        return False
    for left, right in (("(", ")"), ("（", "）")):
        if term.count(left) != term.count(right):
            return False
    if len(term) > 12 and re.search(r'[的是在了与和或及]', term):
        return False
    return True


def _parse_hotwords_response(response_text: str, existing: set) -> List[str]:
    """解析 LLM 返回的热词 JSON，去重后返回。"""
    text = response_text.strip()
    # 容错：去掉 Markdown 代码块
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
        m = re.search(r"\[(?:[^\[\]]|\[[^\]]*\])*\]", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return []
        else:
            return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        if not term or len(term) < 2 or _exceeds_char_limit(term):
            continue
        key = term.lower().replace(" ", "")
        if key not in existing:
            existing.add(key)
            result.append(term)
    return result


class LLMHotwordExtractor:
    """LLM 驱动的热词提取器。

    将 wiki 页面分批次送入 LLM，每批提取 ~100 个热词，
    最终去重合并为 ~480 个高质量热词。
    """

    def __init__(self, pages: List[WikiPageInfo]) -> None:
        self._pages = pages

    def extract(
        self,
        provider: "EnvironmentConfiguredLLMProvider",
        max_hotwords: int = 490,
    ) -> List[str]:
        """分批次调用 LLM 提取热词，去重合并。

        Args:
            provider: Iris LLM Provider
            max_hotwords: 最多返回的热词数

        Returns:
            去重后的热词列表，按长度排序
        """
        if not self._pages:
            return []

        from iris.llm import LLMRequest

        batches = _build_page_batches(self._pages)
        all_hotwords: List[str] = []
        seen: set = set()

        print(f"[asr] 热词提取：{len(batches)} 批，共 {len(self._pages)} 页",
              file=sys.stderr)

        for idx, batch in enumerate(batches):
            prompt = _build_hotwords_prompt(batch)
            try:
                response = provider.generate(
                    LLMRequest(
                        prompt=prompt,
                        route_context={
                            "task_type": "asr_hotword",
                            "input_type": "text",
                        },
                    ),
                    temperature=0.3,
                    max_tokens=8192,
                )
                batch_terms = _parse_hotwords_response(response.text, seen)
                # 后处理：质量过滤 + 文本清理
                clean_batch = []
                for t in batch_terms:
                    cleaned = _clean_text_term(t)
                    if not _is_valid_hotword(cleaned):
                        # 从 seen 中移除无效条目，避免废词占用
                        key = cleaned.lower().replace(" ", "")
                        if key in seen:
                            seen.discard(key)
                        continue
                    clean_batch.append(cleaned)
                all_hotwords.extend(clean_batch)
                all_hotwords.extend(batch_terms)
                print(f"  [asr] 第 {idx+1} 批 → {len(batch_terms)} 个候选（过滤后 {len(clean_batch)}）（累计 {len(all_hotwords)}）",
                      file=sys.stderr)
            except Exception as exc:
                print(f"  [warn] 第 {idx+1} 批热词提取失败: {exc}",
                      file=sys.stderr)

        # 最终去重（防御性）
        final = []
        final_seen = set()
        for w in all_hotwords[:max_hotwords * 2]:  # 放宽上限给去重腾空间
            key = w.lower().replace(" ", "")
            if key not in final_seen:
                final_seen.add(key)
                final.append(w)
                if len(final) >= max_hotwords:
                    break

        final.sort(key=lambda x: (len(x), x))
        return final


def hotwords_to_terms(hotwords: List[str], existing_terms: List[AsrTerm]) -> List[AsrTerm]:
    """将 LLM 提取的热词补充为 AsrTerm 条目，用于 Phase 2 误识别生成。

    已有 terms 中的条目不重复添加。新条目归入 domain_term 类别。
    返回合并后的完整 terms 列表。
    """
    existing_names = {t.term.lower().replace(" ", "") for t in existing_terms}
    new_terms = list(existing_terms)  # 浅拷贝

    for hw in hotwords:
        key = hw.lower().replace(" ", "")
        if key in existing_names:
            continue
        existing_names.add(key)
        new_terms.append(AsrTerm(term=hw, category="domain_term", context=""))

    return new_terms


# ═══════════════════════════════════════════════════════════════════
# 输出格式化器
# ═══════════════════════════════════════════════════════════════════

def format_hotwords_file(hotwords: List[str], output_path: str) -> str:
    """将热词列表写入 txt 文件（每行一个，自动去重）。

    Args:
        hotwords: 热词列表
        output_path: 输出文件路径

    Returns:
        写入的文件路径
    """
    # 防御性去重，保持顺序
    seen = set()
    unique = []
    for w in hotwords:
        key = w.lower().replace(" ", "")
        if key not in seen:
            seen.add(key)
            unique.append(w)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(unique) + "\n", encoding="utf-8")
    return str(path)


def format_replace_dict(
    terms: List[AsrTerm],
    output_path: str,
    max_mappings: int = 990,
    max_chars: int = 20,
) -> str:
    """将术语+误识别映射输出为替换词典 JSON。

    replace_map 格式：{{"误识别": "正确写法", ...}}

    过滤规则：错误词和正确词均不超过 20 字符或 10 个中文字。

    Args:
        terms: 已填充 mis_asr 的术语列表
        output_path: 输出文件路径
        max_mappings: 最多映射条数（上限 990）
        max_chars: 误识别和正确词的最大字符数

    Returns:
        写入的文件路径
    """
    replace_map = {}
    added = set()
    for t in terms:
        # 跳過正確詞本身太長的
        if _exceeds_char_limit(t.term, max_total=max_chars, max_chinese=10):
            continue
        for mis in t.mis_asr:
            if not mis:
                continue
            # 错误詞也检查长度
            if _exceeds_char_limit(mis, max_total=max_chars, max_chinese=10):
                continue
            if mis not in added and mis != t.term:
                replace_map[mis] = t.term
                added.add(mis)
                if len(replace_map) >= max_mappings:
                    break
        if len(replace_map) >= max_mappings:
            break
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"replace_map": replace_map}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


# ═══════════════════════════════════════════════════════════════════
# Phase 3: LLM Prompt 优化器
# ═══════════════════════════════════════════════════════════════════

class LLMPromptOptimizer:
    """LLM 驱动的 ASR 校正提示词优化器。

    设计原则：
    - 替换词典（asr-replace-dict）负责确定性词→词映射
    - LLM prompt 负责语境消歧、流畅润色、输出规范
    - 不在 prompt 中重复列全部 400+ 术语，只给出关键样例和策略指引
    """

    @staticmethod
    def build_optimize_prompt(
        hotwords: List[str],
        terms: List[AsrTerm],
    ) -> str:
        """构建提示词——让 LLM 生成策略型校正 prompt，而非术语列表。"""
        persons = [t for t in terms if t.category == "person"]
        concepts = [t for t in terms if t.category == "concept"]
        projects = [t for t in terms if t.category == "project"]
        domain_terms = [t for t in terms if t.category == "domain_term"]

        # 按 term 长度分级：短词（≤4字）优先作为样例
        short_domain = [t for t in domain_terms if len(t.term) <= 8][:30]
        all_person_names = "、".join(t.term for t in persons)
        all_project_names = "、".join(t.term for t in projects[:8])
        all_concept_names = "、".join(t.term for t in concepts)
        sample_domain_terms = "、".join(t.term for t in short_domain)
        hotword_sample = "、".join(hotwords[:60]) if hotwords else ""

        # 统计摘要
        summary = (
            f"共 {len(persons)} 位成员、{len(concepts)} 个概念、"
            f"{len(projects)} 个项目、{len(domain_terms)} 个领域术语、"
            f"{len(hotwords)} 个热词"
        )

        return f"""你是 ASR 校正提示词专家。你需要为语音转写（ASR）后处理生成一份 LLM 校正系统提示词。

## 背景
这是「转转」集团「技术研发部」的内部语音场景（会议讨论、项目沟通），
主要涉及方向：二手商品商品质检 AI、搜索推荐算法、大模型训练与应用、视频审核。

## 校正资源说明
系统已配备一份**替换词典**（{len(terms)} 术语 × 平均 3-5 条误识别映射），
词典负责词级别的确定性替换。你的 prompt 不需要重复列这个词表，
而是聚焦于：
1. **语境消歧**：告诉 LLM 如何根据上下文从多个候选词中选正确的
2. **流畅润色**：修正 ASR 输出中的不自然停顿、重复、碎片
3. **格式规范**：数字、日期、英文大小写、标点的统一规则

## 校正策略（需要在 prompt 中体现）
1. 先检查替换词典覆盖的词，优先应用词典映射
2. 对于词典未覆盖的词，根据领域背景和上下文推断正确写法
3. 人名必须匹配已知成员列表（{all_person_names}）
4. 项目名/术语以完整性优先（如「图像采集3.0 AI外观定级」不能截断为「图像采集3.0」）
5. 数字格式：技术语境用阿拉伯数字，口语语境保留中文数字
6. 英文缩写保持全大写（DNN、OCR、MMoE），中英混排间加空格

## 润色规则（需要在 prompt 中体现）
1. 修正 ASR 常见的口语填充（嗯、啊、那个、这个）
2. 合并 ASR 错误拆分的短句片段
3. 保留说话人的语气和风格，不过度书面化
4. 会议场景保留"我们""咱们"等口语化表达
5. 纠正常见标点错误（中文句号/英文句号混用）

## 领域参考数据（用于 prompt 中的关键术语样例，不是全量列表）
- 成员：{all_person_names}
- 概念：{all_concept_names}
- 项目：{all_project_names}
- 领域术语样例：{sample_domain_terms}
- 热词样例：{hotword_sample[:300]}
- 术语统计：{summary}

## 输出规范
1. 角色设定开头：「你是 ASR 语音转写后处理校正助手。」
2. 分三个小节：「校正策略」「润色规则」「输出格式」
3. 总长度不超过 1200 汉字，紧凑高效
4. 不要列全量术语表——这是替换词典的职责
5. 不要写"以下是常见误识别映射"——那个在替换词典里
6. 直接输出提示词文本，不要 Markdown 代码块包裹

## 示例结构（供参考，不要照抄）
你是 ASR 语音转写后处理校正助手。
[2-3句话的校正策略指引]
[3-5条润色规则]
[输出格式说明]"""

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理 LLM 返回文本中的乱码和不可打印字符。"""
        text = text.replace("�", "")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def optimize(
        hotwords: List[str],
        terms: List[AsrTerm],
        provider: "EnvironmentConfiguredLLMProvider",
    ) -> str:
        """LLM 调用一次，生成策略型校正 prompt。"""
        from iris.llm import LLMRequest

        prompt = LLMPromptOptimizer.build_optimize_prompt(hotwords, terms)
        try:
            response = provider.generate(
                LLMRequest(
                    prompt=prompt,
                    route_context={
                        "task_type": "asr_prompt_optimize",
                        "input_type": "text",
                    },
                ),
                temperature=0.3,
                max_tokens=4096,
            )
            return LLMPromptOptimizer._clean_text(response.text)
        except Exception as exc:
            print(f"[warn] Prompt 优化生成失败: {exc}", file=sys.stderr)
            return _render_standard(terms, AsrPromptVersion(
                version="0.0.0", generated_at="",
                wiki_page_count=0, term_count=0, fingerprint="",
            ))

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
            prompt_text=data.get("prompt_text", ""),
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
        "prompt_text": version.prompt_text,
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
