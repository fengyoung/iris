"""ASR 校正 Prompt 优化器 — LLM 驱动的策略型 Prompt 生成（Phase 3）。

生成语境消歧 + 流畅润色 + 输出规范的策略指引 Prompt（非术语列表），
与替换词典互补。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from .formatter import _render_standard
from ._types import AsrTerm, AsrPromptVersion
from .version import load_version, save_version

if TYPE_CHECKING:
    from iris.llm.provider import EnvironmentConfiguredLLMProvider

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
        domain_context: str = "",
        top_n_mappings: int = 30,
    ) -> str:
        """构建 LLM 优化提示词 — 规则式 Prompt（V2）。

        核心转变：从"指南式"（告诉 LLM 怎么纠错）改为"规则式"
        （直接内嵌 top N 高频易错映射 + 精简规则），目标 ~800 字。

        Args:
            hotwords: 热词列表
            terms: 已填充 mis_asr 的 AsrTerm 列表
            domain_context: 领域上下文描述
            top_n_mappings: 内嵌到 Prompt 的高频映射数（默认 30）
        """
        persons = [t for t in terms if t.category == "person"]
        projects = [t for t in terms if t.category == "project"]
        concepts = [t for t in terms if t.category == "concept"]
        domain_terms = [t for t in terms if t.category == "domain_term"]

        # 精选 top N 高频易错映射：首选有 mis_asr 的短词（≤8字），按 mis_asr 数量排序
        candidates = [t for t in terms if t.mis_asr and len(t.term) <= 8]
        candidates.sort(key=lambda t: -len(t.mis_asr))

        embedded_mappings: List[str] = []
        for t in candidates[:top_n_mappings]:
            for mis in t.mis_asr[:2]:  # 每术语最多取 2 个误识别
                if len(embedded_mappings) >= top_n_mappings:
                    break
                embedded_mappings.append(f"「{mis}」→「{t.term}」")
            if len(embedded_mappings) >= top_n_mappings:
                break

        mappings_text = "、".join(embedded_mappings) if embedded_mappings else "（暂无）"

        # 统计摘要
        summary = (
            f"共 {len(persons)} 位成员、{len(concepts)} 个概念、"
            f"{len(projects)} 个项目、{len(domain_terms)} 个领域术语"
        )

        # 关键人名（仅列前 8 个高频出现的，避免硬编码全量）
        top_persons = "、".join(t.term for t in persons[:8]) if persons else ""

        return f"""你是 ASR 校正助手。对 paraformer 语音转写结果进行校正，仅输出校正后文本，不解释。

## 内置映射（直接替换）
以下为高频易错映射，命中时直接替换无需判断：
{mappings_text}

## 通用规则
1. 技术缩写全大写（dnn→DNN、ocr→OCR、mmoe→MMoE）
2. 中英文间加空格（Qwen大模型→Qwen 大模型）
3. 版本号用阿拉伯数字（三点零→3.0、v二→v2）
4. 合并 ASR 误拆的短句碎片，去除口语填充词（嗯、啊、那个）
5. 中文全角标点（，。？“”），英文缩写保留半角点

## 未覆盖的词
- 人名优先匹配已知成员（{top_persons or '参考系统名单'}），音近校正
- 技术术语根据上下文推断，保持音近原则
- 不确定时不改，保留原文

## 领域背景
{domain_context or "专业团队工作场景"}（{summary}）

仅输出校正后文本。"""

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
        domain_context: str = "",
    ) -> str:
        """直接渲染规则式校正 Prompt（V2）。

        不使用 LLM 生成——LLM 输出不稳定，容易产生元评论或角色混淆。
        改为 Python 模板直接渲染，确定性、零延迟。
        """
        # 使用旧的 LLM 方式作为后备（保留接口兼容性，但默认跳过）
        return LLMPromptOptimizer._render_v2(hotwords, terms, domain_context)

    @staticmethod
    def _render_v2(
        hotwords: List[str],
        terms: List[AsrTerm],
        domain_context: str = "",
        top_n_mappings: int = 30,
    ) -> str:
        """直接渲染 V2 规则式校正 Prompt。"""
        persons = [t for t in terms if t.category == "person"]
        projects = [t for t in terms if t.category == "project"]
        concepts = [t for t in terms if t.category == "concept"]
        domain_terms = [t for t in terms if t.category == "domain_term"]

        # 精选 top N 易错映射
        candidates = [t for t in terms if t.mis_asr and len(t.term) <= 8]
        candidates.sort(key=lambda t: -len(t.mis_asr))

        embedded: List[str] = []
        for t in candidates:
            for mis in t.mis_asr[:2]:
                if len(embedded) >= top_n_mappings:
                    break
                if mis and mis != t.term:
                    embedded.append(f"「{mis}」→「{t.term}」")
            if len(embedded) >= top_n_mappings:
                break

        mappings_text = "、".join(embedded) if embedded else "（暂无）"
        top_persons = "、".join(t.term for t in persons[:8]) if persons else ""

        summary = (
            f"共 {len(persons)} 位成员、{len(concepts)} 个概念、"
            f"{len(projects)} 个项目、{len(domain_terms)} 个领域术语"
        )

        domain_bg = domain_context or "专业团队工作场景"

        # 领域保护名单：项目名 + 概念名（来自 Wiki，确保 LLM 不改错）
        protected_terms = "、".join(
            t.term for t in (projects + concepts) if len(t.term) <= 12
        )[:60] or "暂无"

        return (
            "你是 ASR 语音转写编辑助手。\n\n"
            "## 核心规则（违反将导致输出被丢弃）\n"
            "1. **直接输出校正后文本，不要输出任何其他内容**。不解释、不分析、不描述修改过程\n"
            "2. 如果不需要修改，原样输出输入文本\n"
            "3. 输出必须是完整的一段中文文本，不能只有半句\n\n"
            "## 校正\n"
            "首先检查以下映射，命中直接替换：\n"
            f"{mappings_text}\n\n"
            "未命中时，根据音近原则推断：\n"
            "- 「大冒险」在技术语境可能是「大模型」（韵母混淆）\n"
            "- 「交叉」可能是「矫正」（声母混淆）\n"
            "- 如果某词听感相近但语境不符，尝试同音/近音替换为领域术语\n\n"
            "## 润色\n"
            "- 合并口语化碎片和重复表述（「包括…包括…」→ 精简表达）\n"
            "- 补充缺失的标点符号，按语义合理分句\n"
            "- 去除口语填充词（嗯、啊、那个、就是）\n"
            "- 保持说话人的语气和原意，不过度书面化\n\n"
            "## 格式\n"
            "- 技术缩写全大写：llm→LLM、dnn→DNN、ocr→OCR\n"
            "- 中英文间加空格：LLM训练→LLM 训练\n"
            "- 中文全角标点：，。？「」\n"
            "- wiki 等技术名词保持小写\n\n"
            "## 领域保护名单（这些词是真实存在的，绝不要修改）\n"
            f"{protected_terms}\n"
            f"人名：{top_persons or '参考系统名单'}\n"
            f"领域：{domain_bg or '专业团队'}\n\n"
            "现在开始。输入文本："
        )

