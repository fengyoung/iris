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

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from .._constants import get_all_types, get_wiki_prefix
from .._term_cleaners import _HEADING_RE, _BOLD_RE, _WIKI_LINK_RE, _SKIP_HEADINGS, _NOISE_TERM_RE, is_noise_term, clean_markup, truncate_context
from ..context_loader import WikiPageInfo
from ..discovery_utils import extract_terms, is_high_value_term, normalized_key
from iris.utils.template_loader import load_template as _load_template_file

from ._types import AsrTerm, AsrPromptVersion
from ._progress import ProgressTracker

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from iris.llm.provider import EnvironmentConfiguredLLMProvider


# ── 向后兼容别名 ──────────────────────────────────────────
# _is_noise_term / _clean_markup / _truncate_context 已迁移到 _term_cleaners.py
# 保留模块级别名以兼容旧代码中的 `from iris.wiki.term_extractor import _xxx` 用法
_is_noise_term = is_noise_term
_clean_markup = clean_markup
_truncate_context = truncate_context


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

        tracker = ProgressTracker(total=len(batches), label="误识别生成")
        print(f"[asr] 误识别生成：{len(batches)} 批并发，共 {len(terms)} 术语",
              file=sys.stderr)

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
                filled = sum(1 for t in batch if t.mis_asr)
                tracker.increment(detail=f"第{idx+1}批 {filled}/{len(batch)} 术语已映射")
            except Exception as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                tracker.increment_error(detail=f"第{idx+1}批失败: {exc}")

        _timeout = len(batches) * 90
        from iris.core.thread_pool import shared_pool
        with shared_pool.executor(max_workers=min(len(batches), 8)) as executor:
            try:
                list(executor.map(_run_batch, enumerate(batches), timeout=_timeout))
            except FuturesTimeoutError:
                logger.warning("ASR 误识别生成超时（%ds），已处理完成的批次保留", _timeout)

        total_mappings = sum(len(t.mis_asr) for t in terms)
        print(f"[asr]   ... 误识别生成完成 ({tracker.elapsed():.1f}s): {total_mappings} 映射",
              file=sys.stderr)
        return terms

    def _build_misreadings_prompt(self, terms: List[AsrTerm], domain_context: str = "") -> str:
        """构建批量误识别生成的 LLM prompt。

        针对 paraformer-large-zh-cn-contextual（Conformer + CTC 解码）优化，
        增加质量约束：禁止括号注释、禁止大小写变体、去冲突检测。
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

        # 降级：内联 prompt（已针对 paraformer-large-zh-cn-contextual 优化）
        return f"""你是 paraformer-large-zh-cn-contextual 语音识别模型的专家。该模型基于 Conformer 架构 + CTC 解码，你需要了解它的典型错误模式：

## 模型特征
- Conformer 自注意力机制：对音近词区分能力较弱，zh↔z、ch↔c、sh↔s 等声母易混淆
- CTC 尖峰效应：短词可能被吞并、相邻字被合并、词语边界偏移
- 热词 bias 已启用：已配置的热词识别率较高，无需额外优化

## 领域背景
{ctx}

## 任务
为以下术语列表的每个条目，生成 paraformer 最可能产生的 **最多 3 个**误识别。

## 误识别生成模式（按优先级）
1. **中文人名**：同音/近音字替换，聚焦声母混淆（zh↔z, ch↔c, sh↔s, n↔l, h↔f）、韵母混淆（an↔ang, en↔eng, in↔ing）
2. **中英混排词**（如「Model-v2」「H200」）：
   - 数字读法混淆：3.0→三点零/三零
   - 英文字母音译：H→爱吃/艾尺
3. **英文缩写**（DNN、OCR、MMoE）：逐字母中文音译
4. **中文术语**：同音替换 + CTC 分词错误（「智能化检测」→「智能画检测」）
5. **项目名**：可能的截断或简化

## 质量约束（必须遵守）
- ❌ 禁止生成含括号注释的误识别（如「张啸（误为张笑）」）
- ❌ 禁止生成原词的大小写变体（如 qwen→QWEN）— 这是格式归一问题，不是 ASR 误识
- ❌ 禁止编造冷门变体 — 只生成 paraformer 实际极可能犯的错误
- ✅ 每术语最多 3 个误识别，宁缺毋滥
- ✅ 聚焦音近混淆和 CTC 分词错误
- ✅ 两个不同的正确词不能有相同的误识别（冲突时省去该误识别）
- ✅ 直接输出纯 JSON 数组，不要 Markdown 代码块包裹，不要解释

## 术语列表
{term_items_text}

## 输出格式
[
  {{"term": "张三", "category": "person", "mis_asr": ["张珊", "章三"]}},
  {{"term": "Model-v2", "category": "domain_term", "mis_asr": ["模型v二", "模型2"]}},
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

