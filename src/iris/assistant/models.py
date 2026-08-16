"""实时会议助理数据模型（Pydantic v2）。"""

from __future__ import annotations

import time
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, ClassVar, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field, PrivateAttr, field_validator


# ── 共享常量：置信度图标 + 颜色映射（_panel / _doc_writer 共用，避免重复定义） ──

CONF_ICON: dict[str, str] = {"confirmed": "✅", "proposed": "💬", "tentative": "❓"}
"""决策置信度 → 展示图标。"""

DECISION_FG: dict[str, str] = {
    "confirmed": "fg_ok",
    "proposed": "fg_proposed",
    "tentative": "fg_tentative",
}
"""决策置信度 → 语义色字段名（Theme 属性）。"""


# ── TypedDict：MeetingState 中无类型 dict 列表的结构约束 ──

class TopicRecord(TypedDict, total=False):
    """话题记录（MeetingState.topics 元素）。"""
    label: str
    start_seq: int
    end_seq: int
    summary: str


class SpeakerRecord(TypedDict, total=False):
    """说话人记录（MeetingState.speakers 元素）。"""
    id: str
    segments: int
    role_hint: str


# ── 冲突检测辅助（模块级，避免 Pydantic 序列化） ──

def _shared_bigram_count(a: str, b: str) -> int:
    """两个中文文本共享的 2-gram 数量（关联度度量）。"""
    if len(a) < 4 or len(b) < 4:
        return 0
    a_grams = {a[i:i+2] for i in range(len(a)-1)}
    b_grams = {b[i:i+2] for i in range(len(b)-1)}
    return len(a_grams & b_grams)


# 强否定：明确推翻/否定前文结论（冲突的充分信号）。
# 注意排除歧义词："不能/不可/没有" 常作约束/修饰（"不能有地区差异"、"没有问题"）。
_STRONG_NEGATION = [
    "不行", "不应该", "不用", "不需要", "不是", "并非", "不对",
    "不同意", "否定", "反对", "推翻", "撤销", "取消", "拒绝",
    "不可行", "有问题", "错误", "矛盾", "质疑",
]
# 弱否定：可能是否定也可能是约束/限定（需更强关联才触发）
_WEAK_NEGATION = ["不", "没", "无", "非", "未", "否", "但", "然而", "却"]


def _negation_level(text: str) -> int:
    """否定强度：0=无否定，1=弱否定，2=强否定。"""
    if any(w in text for w in _STRONG_NEGATION):
        return 2
    if any(w in text for w in _WEAK_NEGATION):
        return 1
    return 0


def _is_conflicting(a: str, b: str) -> bool:
    """双向矛盾判定：共享关键词 + 一句明确推翻另一句。

    v3.25.4 修复误报（工程取舍：宁漏报不误报）：
    - 双向检查（此前只查新句）
    - 仅「明确推翻词」触发（不行/不可行/反对/取消/不同意/推翻…）；
      弱否定（不/没/但，可能是约束或修饰，如"不能有地区差异"）不再单独触发
    - 两句都强否定或都无强否定 → 不判冲突
    """
    shared = _shared_bigram_count(a, b)
    if shared < 2:
        return False
    lvl_a = _negation_level(a)
    lvl_b = _negation_level(b)
    # 一句明确推翻（强否定）+ 另一句完全无否定 → 矛盾
    # 另一句有任何否定（弱也算）→ 同持否定立场（补充而非推翻）→ 非矛盾
    return (lvl_a == 2 and lvl_b == 0) or (lvl_b == 2 and lvl_a == 0)


def _topics_are_similar(a: str, b: str) -> bool:
    """两个话题标签是否语义相近（2-gram 重叠判定）。

    防止 LLM 对同一讨论生成不同措辞导致话题碎片化。
    - 共享 ≥2 bigram → 一定相近
    - 共享 1 bigram + 两标签都短（≤6 字）→ 相近（短标签通常只含 1 个主题词）
    - 共享 1 bigram + 重叠比 ≥33% → 相近
    """
    if not a or not b:
        return False
    a_grams = {a[i:i+2] for i in range(len(a)-1)}
    b_grams = {b[i:i+2] for i in range(len(b)-1)}
    if not a_grams or not b_grams:
        return False
    common = len(a_grams & b_grams)
    if common >= 2:
        return True
    if common == 1:
        # 两标签都很短（≤6 字，即 ≤5 bigrams）→ 1 个共享词即足够
        if len(a_grams) <= 5 and len(b_grams) <= 5:
            return True
        min_size = min(len(a_grams), len(b_grams))
        return common / max(min_size, 1) >= 0.33
    return False


class SpeakerLabel(BaseModel):
    """说话人标识（v3.25.5 LLM 语义推断）。"""
    speaker_id: str = Field(default="", description="说话人标识符，如 speaker_A")
    role_hint: str = Field(default="", description="角色提示：主持人/汇报人/提问者")
    turn_index: int = Field(default=0, description="本场第几次发言切换")
    is_turn_change: bool = Field(default=False, description="是否切换了说话人")


class DecisionItem(BaseModel):
    """带置信度的决策点（v3.25.3 替代纯字符串）。"""
    text: str = Field(description="决策内容")
    confidence: str = Field(default="proposed",
                           description="confirmed=已拍板 / proposed=提议中 / tentative=待定")
    speaker: str = Field(default="", description="谁拍的板（speaker_id）")


class TodoItem(BaseModel):
    """结构化待办（v3.25.3 新增）。"""
    text: str = Field(description="待办内容")
    assignee: str = Field(default="", description="责任人")
    deadline: str = Field(default="", description="时间节点")


class TopicInfo(BaseModel):
    """话题追踪信息（v3.25.3 新增）。"""
    label: str = Field(description="话题名称")
    start_seq: int = Field(description="起始段号")
    end_seq: int = Field(default=0, description="结束段号（0=进行中）")
    summary: str = Field(default="", description="话题讨论摘要")
    started_at: datetime = Field(default_factory=datetime.now)


class SegmentAnalysis(BaseModel):
    """单个语音段的 LLM 分析结果。"""

    key_points: List[str] = Field(default_factory=list, description="关键要点")
    risks: List[str] = Field(default_factory=list, description="风险")
    questions: List[str] = Field(default_factory=list, description="讨论中的问题")
    decisions: List[DecisionItem] = Field(default_factory=list, description="决策点（含置信度）")
    suggested_questions: List[str] = Field(default_factory=list, description="建议追问的提问")
    resolved_questions: List[str] = Field(default_factory=list,
                                          description="本段已回答/覆盖的旧问题（LLM 语义判定）")
    # ── v3.25.3 话题感知 + 待办 ──
    topic: str = Field(default="", description="当前讨论的话题标签")
    topic_change: bool = Field(default=False, description="是否切换到了新话题")
    topic_summary: str = Field(default="", description="当前话题的一句话摘要")
    todos: List[TodoItem] = Field(default_factory=list, description="结构化待办")
    speaker: SpeakerLabel = Field(default_factory=SpeakerLabel, description="说话人")

    @field_validator("decisions", mode="before")
    @classmethod
    def _coerce_decisions(cls, v: Any) -> Any:
        """向后兼容：纯字符串决策 → DecisionItem(text=..., confidence='proposed')。"""
        if isinstance(v, list):
            return [
                DecisionItem(text=item.strip(), confidence="proposed")
                if isinstance(item, str) else item
                for item in v
            ]
        return v

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
    # · skipped（短反馈/快速模式，有意跳过）· merged（批次合并分析，结果见批次首段）
    ANALYSIS_PENDING: ClassVar[str] = "pending"
    ANALYSIS_DONE: ClassVar[str] = "done"
    ANALYSIS_FAILED: ClassVar[str] = "failed"
    ANALYSIS_SKIPPED: ClassVar[str] = "skipped"
    ANALYSIS_MERGED: ClassVar[str] = "merged"

    seq: int = Field(description="段序号（1-based，submit 时递增）")
    started_at: datetime = Field(description="检测时刻")
    raw_text: str = Field(description="剪贴板原文")
    corrected_text: str = Field(default="", description="校正后文本（词典/LLM）")
    analysis: Optional[SegmentAnalysis] = Field(default=None, description="分析结果，None=不可用/未完成")
    analysis_status: str = Field(default="pending", description="分析状态（pending/done/failed/skipped）")
    # 耗时元数据（time.monotonic() 时间戳，None=未采集）
    analysis_started_at: Optional[float] = Field(default=None, description="分析开始时刻")
    analysis_done_at: Optional[float] = Field(default=None, description="分析完成时刻")
    speaker: SpeakerLabel = Field(default_factory=SpeakerLabel, description="说话人（LLM 后验）")
    speaker_change_signal: bool = Field(default=False, description="VAD 检测到可能切换")
    forced_cut: bool = Field(default=False, description="v3.26.1 ASR 15s 强制切段（非自然停顿）")


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
    mini_summaries: list[str] = Field(default_factory=list,
                                       description="v3.26.1 阶段性总结（每 15 分钟生成一次）")
    todos: List[str] = Field(default_factory=list, description="结构化待办（去重后）")
    # ── v3.25.3 话题追踪 ──
    current_topic: str = Field(default="", description="当前讨论的话题标签")
    topics: List[TopicRecord] = Field(default_factory=list, description="已关闭的话题列表")
    # ── v3.25.5 说话人追踪 ──
    speakers: List[SpeakerRecord] = Field(default_factory=list, description="说话人统计")
    speaker_seq: int = Field(default=0, description="自增说话人编号")
    # ── v3.25.3 冲突检测（PrivateAttr 避免 pydantic 下划线限制）──
    _recent_claims: list = PrivateAttr(default_factory=list)  # list[(str, float)]

    _MAX_CUMULATIVE_ITEMS: ClassVar[int] = 25  # 每类累计项上限（防信息爆炸）
    _MAX_SEGMENTS: ClassVar[int] = 5000         # 段数上限（超限自动合并旧段，v3.26.1）
    _FUZZY_THRESHOLD: ClassVar[float] = 0.85   # 编辑距离相似度阈值（≥0.85 视为重复）
    _FUZZY_MIN_LEN: ClassVar[int] = 8          # 短于此长度的文本仍用精确匹配

    @staticmethod
    def _dedup_append(target: List[str], items: List[str]) -> None:
        """追加并去重：短文本精确匹配，长文本模糊匹配（SequenceMatcher ≥ 0.85）。

        模糊匹配防止 LLM 对同一要点在不同段的措辞变化（如
        「预算需增加 20%」vs「预算需上调 20%」）导致重复条目。

        容量控制：超过 _MAX_CUMULATIVE_ITEMS 时淘汰最旧条目（保留最新）。
        """
        for item in items:
            text = item.strip()
            if not text:
                continue
            is_dup = False
            for existing in target:
                if len(text) < MeetingState._FUZZY_MIN_LEN or len(existing) < MeetingState._FUZZY_MIN_LEN:
                    if text == existing:
                        is_dup = True
                        break
                elif SequenceMatcher(None, text, existing).ratio() >= MeetingState._FUZZY_THRESHOLD:
                    is_dup = True
                    break
            if not is_dup:
                # 容量控制：超限前先腾位（pop 最旧的）
                while len(target) >= MeetingState._MAX_CUMULATIVE_ITEMS:
                    target.pop(0)
                target.append(text)

    def update_topic(self, topic: str, topic_change: bool, topic_summary: str,
                     seg_seq: int) -> Optional[dict]:
        """处理话题变化。返回被关闭的旧话题 info（无变化返回 None）。

        v3.25.4 修复两个状态机缺陷 + 话题粒度去重：
        1. 连续两次 topic_change=True 不再丢话题
        2. 关闭旧话题时保留其自身摘要
        3. 新话题标签与当前话题 2-gram 重叠 ≥50% → 视为同一话题不切换
           （LLM 可能对同一讨论生成不同措辞，如"答疑耗时优化"vs"答疑优化方向"）
        """
        if not topic:
            return None
        # 完全相同 → 不处理
        if topic == self.current_topic:
            return None
        # 语义去重：与当前话题或最近关闭话题高度重叠 → 延续而非新开
        candidates = [self.current_topic] if self.current_topic else []
        # 最近关闭的话题（处理 话题A→话题B→话题A 的回旋）
        for t in reversed(self.topics[-2:]):
            if t.get("end_seq", 0) > 0 and t["label"] not in candidates:
                candidates.append(t["label"])
        for c in candidates:
            if _topics_are_similar(topic, c):
                if self.current_topic and _topics_are_similar(topic, self.current_topic):
                    # 与进行中话题相近 → 更新 label/summary
                    self.current_topic = topic
                    for t in reversed(self.topics):
                        if t.get("end_seq", 0) == 0:
                            t["label"] = topic
                            if topic_summary:
                                t["summary"] = topic_summary
                            break
                else:
                    # 与已关闭话题相近 → 重开该话题
                    if self.current_topic:
                        for t in reversed(self.topics):
                            if t.get("end_seq", 0) == 0:
                                t["end_seq"] = seg_seq
                                break
                    self.current_topic = topic
                    self.topics.append({
                        "label": topic, "start_seq": seg_seq, "end_seq": 0,
                        "summary": topic_summary,
                    })
                return None
        # 真正的话题切换
        if self.current_topic:
            for t in reversed(self.topics):
                if t.get("end_seq", 0) == 0:
                    t["end_seq"] = seg_seq
                    closed = t
                    break
            else:
                closed = None
        else:
            closed = None
        self.current_topic = topic
        self.topics.append({
            "label": topic, "start_seq": seg_seq, "end_seq": 0,
            "summary": topic_summary,
        })
        return closed

    def check_conflict(self, new_points: list[str]) -> list[str]:
        """检测新要点与最近结论的语义冲突（双向否定 + 关键词关联）。

        v3.25.4 修复误报：双向否定方向相反才判冲突（细化/约束/进展不误伤）。
        """
        conflicts = []
        for point in new_points:
            for claim, _ts in self._recent_claims[-10:]:
                if _is_conflicting(point, claim):
                    conflicts.append(f'"{point}" vs 此前"{claim}"')
        # 更新最近结论缓存
        now = time.monotonic()
        for p in new_points:
            self._recent_claims.append((p, now))
        if len(self._recent_claims) > 10:
            self._recent_claims = self._recent_claims[-10:]
        return conflicts

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
        # decisions: DecisionItem → str 用于累计列表
        decision_texts = [d.text for d in analysis.decisions if d.text.strip()]
        self._dedup_append(self.decisions, decision_texts)
        self._dedup_append(self.open_questions, analysis.questions)
        # 路径 1：LLM 语义标记 + fuzzy match 关闭
        if analysis.resolved_questions:
            self._remove_answered(analysis.resolved_questions)
        # 路径 2：精确匹配 fallback（兼容旧版 analysis 无 resolved_questions）
        answered = set(decision_texts) | set(analysis.key_points)
        if answered:
            self.open_questions = [
                q for q in self.open_questions if q.strip() not in answered
            ]
        self.segments.append(segment)
        # v3.26.1 段数上限保护：超限时合并最旧的 500 段为摘要段
        if len(self.segments) > self._MAX_SEGMENTS:
            old = self.segments[:500]
            merged = " ".join(
                s.corrected_text or s.raw_text for s in old if s.corrected_text or s.raw_text
            )
            self.segments = self.segments[500:]
            if merged:
                self._merged_archive = (getattr(self, '_merged_archive', '') +
                                       f"\n[已归档前 500 段] {merged[:2000]}")

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
    poll_interval: float = Field(default=0.5, gt=0,
                                 description="[废弃·v3.25.0] 剪贴板轮询间隔，音频模式不使用")
    doc_rewrite_every: int = Field(default=3, gt=0, description="每 N 段重写文档（3=每3段，降低 I/O）")
    fast_only: bool = Field(default=False, description="仅词典校正模式：跳过所有 LLM（deep 校正/检索/分析）")
    short_segment_chars: int = Field(default=15, gt=0,
                                     description="短段门控：校正后短于该长度视为确认语，跳过 LLM 深度校正/检索/分析")
    max_segment_chars: int = Field(default=2000, gt=0,
                                   description="单段长度上限（超长截断并警告，默认 2000 覆盖 120s 长语音场景）")
    dedup_window_seconds: float = Field(default=30.0, ge=0,
                                        description="[废弃·v3.25.0] 剪贴板去重窗口，音频模式不使用")
    suggest_every: int = Field(default=3, gt=0,
                               description="建议提问生成间隔：每 N 段生成一次（省 token 减重复噪音）")
    summary_enabled: bool = Field(default=True, description="退出时生成 AI 会议总结（失败自动跳过）")
    panel_theme: str = Field(default="dark",
                             description="v3.26.2 面板主题（dark/light，非法值回退 dark）")
    agenda: str = Field(default="", description="预设议题（分号分隔），注入分析 prompt 并用于跑偏检测")
    save_knowledge: bool = Field(default=False,
                                 description="[预留·v3.27.0] 退出时自动回写知识库（决策→Wiki/待办→Trello），当前版本不生效")

    @classmethod
    def from_app_config(cls, cfg: Dict[str, Any]) -> "AssistantConfig":
        """从 bundle.app.get("assistant", {}) 构造；缺失段安全。"""
        if not isinstance(cfg, dict):
            cfg = {}
        return cls(**{k: v for k, v in cfg.items() if k in cls.model_fields})


class AsrLocalConfig(BaseModel):
    """本地 FunASR Paraformer PyTorch 模型配置（assistant.asr.local）。

    主模型使用 PyTorch 推理；标点模型使用 ONNX 推理。
    """

    model_dir: str = Field(default="", description="模型缓存目录（vocotype 已下载的 ModelScope 路径）")
    device: str = Field(default="cpu", description="推理设备（cpu / mps）")
    sample_rate: int = Field(default=16000, gt=0, description="音频采样率")
    batch_size_s: int = Field(default=60, gt=0, description="单次 VAD+ASR 最大音频长度（秒）")
    energy_threshold: float = Field(default=0, ge=0, le=0.1,
                                    description="VAD 能量阈值（RMS；0=自动适应噪声地板；0.001-0.05 固定阈值）")
    debug_mode: bool = Field(default=False, description="调试模式：面板仅显示能量电平，不触发 ASR 识别")


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
