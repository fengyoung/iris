"""ASR 校正 Prompt 优化器 — LLM 驱动的策略型 Prompt 生成（Phase 3）。

生成语境消歧 + 流畅润色 + 输出规范的策略指引 Prompt（非术语列表），
与替换词典互补。
"""

from __future__ import annotations

from typing import List, TYPE_CHECKING

from ._types import AsrTerm

if TYPE_CHECKING:
    from iris.llm.provider import EnvironmentConfiguredLLMProvider

class LLMPromptOptimizer:
    """LLM 驱动的 ASR 校正提示词优化器。

    设计原则：
    - 替换词典（asr-replace-dict）负责确定性词→词映射
    - LLM prompt 负责语境消歧、流畅润色、输出规范
    - 不在 prompt 中重复列全部 400+ 术语，只给出关键样例和策略指引

    V3 演进：从 LLM 生成 Prompt 改为 Python 模板直渲染（`_render_v2`），
    消除 LLM 输出的不稳定性和角色混淆风险。`optimize()` 直接委托给 `_render_v2`。
    """

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

