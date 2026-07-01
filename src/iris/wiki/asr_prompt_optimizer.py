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

from .term_extractor import AsrTerm, AsrPromptVersion
from .asr_version import load_version, save_version

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

