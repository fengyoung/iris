"""实时会议助理数据模型（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field


class SegmentAnalysis(BaseModel):
    """单个语音段的 LLM 分析结果（五类结构化输出）。"""

    key_points: List[str] = Field(default_factory=list, description="关键要点")
    risks: List[str] = Field(default_factory=list, description="风险")
    questions: List[str] = Field(default_factory=list, description="讨论中的问题")
    decisions: List[str] = Field(default_factory=list, description="决策点")
    suggested_questions: List[str] = Field(default_factory=list, description="建议追问的提问")

    @property
    def has_content(self) -> bool:
        """是否含任何非空字段（用于面板/文档判断是否展示分析区）。"""
        return any(
            v for v in (self.key_points, self.risks, self.questions,
                        self.decisions, self.suggested_questions)
        )


class VoiceSegment(BaseModel):
    """一个语音段（vocotype 一次按住-松开 = 一段）。"""

    # 分析状态：pending（进行中/未分析）· done（已完成）· failed（LLM 失败）
    # · skipped（短反馈/快速模式，有意跳过）
    ANALYSIS_PENDING: ClassVar[str] = "pending"
    ANALYSIS_DONE: ClassVar[str] = "done"
    ANALYSIS_FAILED: ClassVar[str] = "failed"
    ANALYSIS_SKIPPED: ClassVar[str] = "skipped"

    seq: int = Field(description="段序号（1-based，submit 时递增）")
    started_at: datetime = Field(description="检测时刻")
    raw_text: str = Field(description="剪贴板原文")
    corrected_text: str = Field(default="", description="校正后文本（词典/LLM）")
    analysis: Optional[SegmentAnalysis] = Field(default=None, description="分析结果，None=不可用/未完成")
    analysis_status: str = Field(default="pending", description="分析状态（pending/done/failed/skipped）")


class MeetingState(BaseModel):
    """本场会议滚动状态（文档/面板渲染的唯一事实源）。"""

    started_at: datetime = Field(default_factory=datetime.now)
    segments: List[VoiceSegment] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list, description="待解决问题")
    dropped_count: int = Field(default=0, description="积压丢弃的段数")
    summary: str = Field(default="", description="退出时 AI 生成的会议总结（Markdown 文本）")

    @staticmethod
    def _dedup_append(target: List[str], items: List[str]) -> None:
        """追加并去重（strip 后整串相等即跳过）。"""
        seen = {s.strip() for s in target}
        for item in items:
            text = item.strip()
            if text and text not in seen:
                target.append(text)
                seen.add(text)

    def add_analysis(self, segment: VoiceSegment) -> None:
        """追加段并更新四类累计。

        open_questions 启发式：本段新疑问并入；若本段决策/要点完整覆盖了某旧疑问
        （整串相等），视为已回答移出。
        """
        if not segment.analysis:
            self.segments.append(segment)
            return
        analysis = segment.analysis
        self._dedup_append(self.key_points, analysis.key_points)
        self._dedup_append(self.risks, analysis.risks)
        self._dedup_append(self.decisions, analysis.decisions)
        self._dedup_append(self.open_questions, analysis.questions)
        # 被回答的旧疑问：本段决策/要点中出现的整串视为已回答
        answered = set(analysis.decisions) | set(analysis.key_points)
        if answered:
            self.open_questions = [
                q for q in self.open_questions if q.strip() not in answered
            ]
        self.segments.append(segment)


class AssistantConfig(BaseModel):
    """assistant 配置段（config/app.json["assistant"]）。"""

    output_dir: str = Field(default="", description="过程文档输出目录，空=默认 data/meeting-live/")
    top_k: int = Field(default=5, gt=0, description="知识库检索条数")
    llm_model: str = Field(default="", description="分析 LLM 模型，空=走全局路由")
    poll_interval: float = Field(default=0.5, gt=0, description="剪贴板轮询间隔（秒）")
    doc_rewrite_every: int = Field(default=1, gt=0, description="每 N 段重写文档（1=每段）")
    fast_only: bool = Field(default=False, description="仅词典校正模式：跳过所有 LLM（deep 校正/检索/分析）")
    short_segment_chars: int = Field(default=15, gt=0,
                                     description="短段门控：校正后短于该长度视为确认语，跳过 LLM 深度校正/检索/分析")
    max_segment_chars: int = Field(default=2000, gt=0,
                                   description="单段长度上限（超长丢弃并警告，默认 2000 覆盖 120s 长语音场景）")
    dedup_window_seconds: float = Field(default=30.0, ge=0,
                                        description="相同文本去重窗口：窗口内重复不触发，超窗视为新段")
    suggest_every: int = Field(default=3, gt=0,
                               description="建议提问生成间隔：每 N 段生成一次（省 token 减重复噪音）")
    summary_enabled: bool = Field(default=True, description="退出时生成 AI 会议总结（失败自动跳过）")

    @classmethod
    def from_app_config(cls, cfg: Dict[str, Any]) -> "AssistantConfig":
        """从 bundle.app.get("assistant", {}) 构造；缺失段安全。"""
        if not isinstance(cfg, dict):
            cfg = {}
        return cls(**{k: v for k, v in cfg.items() if k in cls.model_fields})
