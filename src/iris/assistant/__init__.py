"""实时会议助理（iris meeting-live-assistant）。

实时监听 vocotype 语音转写，逐段做 ASR 校正 → 知识库检索 → LLM 分析
（要点/风险/问题/决策点/建议提问），输出到终端面板 + Markdown 过程文档。
"""

from __future__ import annotations

from ._logging import teardown_session_logger
from .live import MeetingLiveAssistant, _probe_running
from .models import (
    CONF_ICON,
    DECISION_FG,
    AsrConfig,
    AsrLocalConfig,
    AsrRemoteConfig,
    AssistantConfig,
    MeetingState,
    SegmentAnalysis,
    SpeakerRecord,
    TopicRecord,
    VoiceSegment,
)
from ._session import MeetingSession

__all__ = [
    "AsrConfig",
    "AsrLocalConfig",
    "AsrRemoteConfig",
    "AssistantConfig",
    "CONF_ICON",
    "DECISION_FG",
    "MeetingLiveAssistant",
    "MeetingSession",
    "MeetingState",
    "SegmentAnalysis",
    "SpeakerRecord",
    "TopicRecord",
    "VoiceSegment",
    "_probe_running",
    "teardown_session_logger",
]
