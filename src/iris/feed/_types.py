"""信息汇聚管道 — 类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal


@dataclass
class RawMessage:
    """原始飞书消息。"""

    msg_id: str
    chat_id: str
    chat_name: str
    chat_type: str  # group / p2p
    sender_id: str
    sender_name: str
    content: str  # 纯文本内容
    raw_content: Dict[str, Any]  # 原始消息结构
    msg_type: str  # text / image / file / …
    send_time: datetime
    has_doc_link: bool = False
    doc_links: List[str] = field(default_factory=list)


@dataclass
class SourceRef:
    """来源引用。"""

    type: Literal["group", "single"]
    name: str  # 群名或联系人名
    msg_count: int


@dataclass
class Quote:
    """原始消息引述。"""

    text: str
    speaker: str
    time: str


@dataclass
class DetectedTopic:
    """检测到的话题。"""

    topic_id: str  # feed-YYYYMMDD-NNN
    title: str
    summary: str  # LLM 生成的核心摘要
    key_status: str = ""
    discussion_points: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    quotes: List[Quote] = field(default_factory=list)
    participants: List[str] = field(default_factory=list)
    messages: List[RawMessage] = field(default_factory=list)
    source_chats: List[SourceRef] = field(default_factory=list)
    is_update: bool = False
    previous_versions: List[str] = field(default_factory=list)
    okr_tags: List[str] = field(default_factory=list)
    okr_match_strength: Literal["strong", "weak", "none"] = "none"


@dataclass
class ConvertedDoc:
    """转换后的本地文档。"""

    original_url: str
    local_path: Path
    relative_path: str
    title: str
    source_chat: str


@dataclass
class PipelineResult:
    """Pipeline 执行结果。"""

    fetched_count: int = 0
    filtered_count: int = 0
    topics: List[DetectedTopic] = field(default_factory=list)
    brief_files: List[Path] = field(default_factory=list)
    converted_docs: List[ConvertedDoc] = field(default_factory=list)
    auto_imported: List[str] = field(default_factory=list)
    pending: List[str] = field(default_factory=list)
    empty_reason: str = ""

    @staticmethod
    def empty(reason: str) -> "PipelineResult":
        return PipelineResult(empty_reason=reason)
