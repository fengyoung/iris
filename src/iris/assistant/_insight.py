"""洞察推送引擎：关键时刻主动推送，从被动展示变为主动参谋。

v3.25.3 新增。InsightFeed 维护滚动推送历史，与 PanelRenderer 协作渲染
分屏面板（上半屏固定区 + 下半屏推送滚动区）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class InsightEvent:
    """一条洞察推送。"""

    TYPE_ICONS = {
        "decision_confirmed": "✅ 决策",
        "decision_proposed": "💬 提议",
        "topic_change": "📌 话题",
        "risk": "⚠ 风险",
        "conflict": "🔥 冲突",
        "todo": "📋 待办",
        "speaker_turn": "🗣 说话人",
    }

    event_type: str           # 见 TYPE_ICONS
    text: str                 # 推送文本
    timestamp: str = ""       # HH:MM:SS

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%H:%M:%S")

    @property
    def line(self) -> str:
        """单行渲染（面板推送区显示）。"""
        icon = self.TYPE_ICONS.get(self.event_type, "🔔")
        return f"🔔 {self.timestamp}  {icon}  {self.text}"


class InsightFeed:
    """滚动推送历史（环形缓冲，最多 50 条；面板显示最近 8 条）。"""

    MAX_HISTORY = 50
    VISIBLE_LINES = 8

    def __init__(self):
        self._events: list[InsightEvent] = []

    def push(self, event: InsightEvent) -> None:
        """追加一条洞察。超过容量时淘汰最旧的。"""
        self._events.append(event)
        if len(self._events) > self.MAX_HISTORY:
            self._events = self._events[-self.MAX_HISTORY:]

    def push_decision(self, text: str, confidence: str) -> None:
        """推送决策洞察。"""
        etype = f"decision_{confidence}" if confidence in ("confirmed", "proposed") else "decision_proposed"
        self.push(InsightEvent(event_type=etype, text=text))

    def push_topic_change(self, topic: str) -> None:
        """推送话题切换洞察。"""
        self.push(InsightEvent(event_type="topic_change", text=f"进入「{topic}」讨论"))

    def push_risk(self, text: str) -> None:
        """推送风险洞察。"""
        self.push(InsightEvent(event_type="risk", text=text))

    def push_conflict(self, text: str) -> None:
        """推送冲突洞察。"""
        self.push(InsightEvent(event_type="conflict", text=text))

    @property
    def visible(self) -> list[InsightEvent]:
        """最近 N 条（面板展示）。"""
        return self._events[-self.VISIBLE_LINES:]

    @property
    def empty(self) -> bool:
        return len(self._events) == 0

    def render_lines(self, width: int) -> list[str]:
        """渲染可见推送为面板行列表。"""
        lines = []
        for event in self.visible:
            # 截断到面板宽度
            line = event.line
            if len(line) > width:
                line = line[:width - 2] + "…"
            lines.append(line)
        return lines
