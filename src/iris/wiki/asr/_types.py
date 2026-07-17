"""ASR 子系统共享数据类型 — 无外部依赖，避免循环导入。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


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
