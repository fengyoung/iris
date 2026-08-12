"""实时会议助理数据模型（Pydantic v2）。"""

from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field


class SegmentAnalysis(BaseModel):
    """单个语音段的 LLM 分析结果（六类结构化输出）。"""

    key_points: List[str] = Field(default_factory=list, description="关键要点")
    risks: List[str] = Field(default_factory=list, description="风险")
    questions: List[str] = Field(default_factory=list, description="讨论中的问题")
    decisions: List[str] = Field(default_factory=list, description="决策点")
    suggested_questions: List[str] = Field(default_factory=list, description="建议追问的提问")
    resolved_questions: List[str] = Field(default_factory=list,
                                          description="本段已回答/覆盖的旧问题（LLM 语义判定）")

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
    # 耗时元数据（time.monotonic() 时间戳，None=未采集）
    analysis_started_at: Optional[float] = Field(default=None, description="分析开始时刻")
    analysis_done_at: Optional[float] = Field(default=None, description="分析完成时刻")


class MeetingState(BaseModel):
    """本场会议滚动状态（文档/面板渲染的唯一事实源）。"""

    _MAX_DROPPED_TEXTS: ClassVar[int] = 20  # 丢弃原文最多保留条数（防附录膨胀）

    started_at: datetime = Field(default_factory=datetime.now)
    segments: List[VoiceSegment] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list, description="待解决问题")
    dropped_count: int = Field(default=0, description="积压丢弃的段数")
    dropped_texts: List[str] = Field(default_factory=list,
                                    description="被丢弃段的原文（最多 20 条，供事后追溯）")
    summary: str = Field(default="", description="退出时 AI 生成的会议总结（Markdown 文本）")

    _FUZZY_THRESHOLD: ClassVar[float] = 0.85   # 编辑距离相似度阈值（≥0.85 视为重复）
    _FUZZY_MIN_LEN: ClassVar[int] = 8          # 短于此长度的文本仍用精确匹配

    @staticmethod
    def _dedup_append(target: List[str], items: List[str]) -> None:
        """追加并去重：短文本精确匹配，长文本模糊匹配（SequenceMatcher ≥ 0.85）。

        模糊匹配防止 LLM 对同一要点在不同段的措辞变化（如
        「预算需增加 20%」vs「预算需上调 20%」）导致重复条目。
        """
        for item in items:
            text = item.strip()
            if not text:
                continue
            is_dup = False
            for existing in target:
                if len(text) < MeetingState._FUZZY_MIN_LEN or len(existing) < MeetingState._FUZZY_MIN_LEN:
                    # 短文本精确匹配（防误伤，如 "A" vs "B" 不应模糊去重）
                    if text == existing:
                        is_dup = True
                        break
                elif SequenceMatcher(None, text, existing).ratio() >= MeetingState._FUZZY_THRESHOLD:
                    is_dup = True
                    break
            if not is_dup:
                target.append(text)

    def add_analysis(self, segment: VoiceSegment) -> None:
        """追加段并更新四类累计。

        open_questions 关闭逻辑（双路径）：
        1. 优先 LLM 标记的 analysis.resolved_questions（语义判定）+ fuzzy match
        2. 兼容 fallback：本段决策/要点与旧疑问整串相等 → 视为已回答
           （保证旧版无 resolved_questions 字段的段仍能关闭匹配问题）
        """
        if not segment.analysis:
            self.segments.append(segment)
            return
        analysis = segment.analysis
        self._dedup_append(self.key_points, analysis.key_points)
        self._dedup_append(self.risks, analysis.risks)
        self._dedup_append(self.decisions, analysis.decisions)
        self._dedup_append(self.open_questions, analysis.questions)
        # 路径 1：LLM 语义标记 + fuzzy match 关闭
        if analysis.resolved_questions:
            self._remove_answered(analysis.resolved_questions)
        # 路径 2：精确匹配 fallback（兼容旧版 analysis 无 resolved_questions）
        answered = set(analysis.decisions) | set(analysis.key_points)
        if answered:
            self.open_questions = [
                q for q in self.open_questions if q.strip() not in answered
            ]
        self.segments.append(segment)

    def _remove_answered(self, resolved: List[str]) -> None:
        """移除被 LLM 标记为已回答的旧问题（fuzzy match，与 _dedup_append 同阈值）。"""
        remaining = []
        for q in self.open_questions:
            qs = q.strip()
            is_resolved = False
            for r in resolved:
                rs = r.strip()
                if len(qs) < self._FUZZY_MIN_LEN or len(rs) < self._FUZZY_MIN_LEN:
                    if qs == rs:
                        is_resolved = True
                        break
                elif SequenceMatcher(None, qs, rs).ratio() >= self._FUZZY_THRESHOLD:
                    is_resolved = True
                    break
            if not is_resolved:
                remaining.append(q)
        self.open_questions = remaining


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


class AsrLocalConfig(BaseModel):
    """本地 FunASR Paraformer ONNX 模型配置（assistant.asr.local）。"""

    model_dir: str = Field(default="", description="模型缓存目录（vocotype 已下载的 ModelScope 路径）")
    device: str = Field(default="cpu", description="ONNX 推理设备（cpu / mps）")
    sample_rate: int = Field(default=16000, gt=0, description="音频采样率")
    batch_size_s: int = Field(default=60, gt=0, description="单次 VAD+ASR 最大音频长度（秒）")
    energy_threshold: float = Field(default=0.002, ge=0, le=0.1,
                                    description="VAD 能量阈值（0=调试模式显示电平不识别，0.001-0.05 正常）")


class AsrRemoteConfig(BaseModel):
    """云端 ASR 配置（assistant.asr.remote，未来扩展）。"""

    provider: str = Field(default="", description="云服务提供商")
    api_base: str = Field(default="", description="API 端点")
    api_key: str = Field(default="", description="API 密钥")
    model: str = Field(default="", description="模型名称")


class AsrConfig(BaseModel):
    """ASR 配置段（config/app.json["assistant"]["asr"]）。

    mode: "local" = 本地 Paraformer；"remote" = 云端 API（预留）。
    hotwords_file / replace_dict_file 为 assistant 专属数据文件，
    独立于 asr-corrector 和 vocotype。
    """

    mode: str = Field(default="local", description="ASR 模式：local | remote")
    local: AsrLocalConfig = Field(default_factory=AsrLocalConfig)
    remote: AsrRemoteConfig = Field(default_factory=AsrRemoteConfig)
    hotwords_file: str = Field(default="data/assistant/asr_hotwords.txt",
                               description="热词文件路径（每行一个词条）")
    replace_dict_file: str = Field(default="data/assistant/asr_replace_dict.json",
                                   description="音近词→正确词映射文件（JSON）")
    llm_correct_enabled: bool = Field(default=True, description="启用 LLM 深度校正")
    llm_correct_timeout_ms: int = Field(default=8000, gt=0, description="LLM 校正超时（毫秒）")

    @classmethod
    def from_app_config(cls, cfg: Dict[str, Any]) -> "AsrConfig":
        """从 bundle.app.get("assistant", {}).get("asr", {}) 构造。

        缺失字段使用模型默认值（不覆盖），实现零配置启动。
        """
        if not isinstance(cfg, dict):
            return cls()
        local_cfg = cfg.get("local", {})
        remote_cfg = cfg.get("remote", {})
        # 只传用户显式配置的字段，其余用模型默认值
        kwargs: Dict[str, Any] = {}
        for key in ("mode", "hotwords_file", "replace_dict_file",
                     "llm_correct_enabled", "llm_correct_timeout_ms"):
            if key in cfg:
                kwargs[key] = cfg[key]
        return cls(
            local=AsrLocalConfig(**{k: v for k, v in local_cfg.items()
                                    if k in AsrLocalConfig.model_fields}),
            remote=AsrRemoteConfig(**{k: v for k, v in remote_cfg.items()
                                      if k in AsrRemoteConfig.model_fields}),
            **kwargs,
        )
