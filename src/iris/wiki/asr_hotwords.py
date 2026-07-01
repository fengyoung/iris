"""ASR 热词提取 — LLM 驱动的术语热词表生成（Phase 2）。

从 Wiki 页面批量提取语音识别热词，支持分批调用和去重合并。
"""

from __future__ import annotations

import json
import re
import sys
from typing import Dict, List, Optional, Set

from ._constants import get_wiki_prefix
from .context_loader import WikiPageInfo
from .term_extractor import (
    AsrTerm,
    _BOLD_RE,
    _clean_markup,
    _HEADING_RE,
    _is_noise_term,
    _SKIP_HEADINGS,
    _WIKI_LINK_RE,
)

# 每批最多处理的 Wiki 页面数
_HOTWORD_BATCH_SIZE = 20

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

