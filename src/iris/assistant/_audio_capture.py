"""音频侧纯逻辑：ASR 后置噪音门控 + 连续短句合并缓冲（MergeBuffer）。

从 live.py 抽出，不依赖线程/设备，便于单元测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

# merge buffer 参数
_MERGE_WINDOW = 3.0        # 3 秒内连续语音合并
_MERGE_MAX_CHARS = 500     # 合并总长上限（防内存膨胀）
_MERGE_RELAXED_WINDOW = 6.0  # 放宽窗口：短段继续等待的上限
# v3.25.5 说话人间隙门控：VAD 间隙超过阈值时不合并（优先保证不跨人）
_SPEAKER_GAP = 0.8         # > 0.8s 可能是说话人切换
_SPEAKER_GAP_STRONG = 2.0  # > 2.0s 几乎一定是切换

_REPEAT_RE = re.compile(r"(.)\1{5,}")
_CJK_RE = re.compile(r"[一-鿿]")


def is_noise(text: str) -> bool:
    """ASR 后置噪音检测：拦截幻觉/键盘噪音/英文碎片，不进入管线。

    以下模式视为噪音：
    1. 单字符连续重复 ≥6 次（"不不不不不不…"、"据据据据…"）
    2. 无有效内容（纯标点/空白）
    3. 极短文本（≤1 字符）
    4. 零中文字符且总长 <15（"yeah"、"OK"、"ststeteding"）
    5. 少量中文 + 大量英文（疑似代码/日志误识别，v3.26.1）
    """
    if not text or not text.strip():
        return True
    # 单字符重复（ASR 幻觉：电流噪音/键盘撞击被当成语音）
    if _REPEAT_RE.search(text):
        return True
    stripped = text.strip()
    if len(stripped) <= 1:
        return True
    # 纯英文/拼音碎片：零中文字符且不长
    cjk = len(_CJK_RE.findall(stripped))
    if cjk == 0 and len(stripped) < 15:
        return True
    # 混合文本噪音判定
    if cjk <= 2 and len(stripped) > 30 and cjk / len(stripped) < 0.1:
        return True
    return False


@dataclass
class Flush:
    """一次待提交的合并段。"""

    texts: List[str]
    speaker_change_signal: bool = False


@dataclass
class MergeBuffer:
    """连续短句合并缓冲。

    v3.25.2：说话人自然停顿（思考、看数据）期间 VAD 可能切段，
    将间隔 ≤ _MERGE_WINDOW 的连续短句合并为一个段再提交，减少碎片化。
    v3.25.5：VAD 间隙超过说话人阈值时不合并，并携带 speaker_change_signal。

    调用方在音频循环中：
        for flush in buf.push(text, now): submit(flush)
        for flush in buf.on_silence(now):  submit(flush)
        for flush in buf.drain():          submit(flush)
    """

    _texts: List[str] = field(default_factory=list)
    _time: float = 0.0

    @property
    def pending(self) -> bool:
        return bool(self._texts)

    def _take(self, speaker_change_signal: bool = False) -> Flush:
        flush = Flush(self._texts, speaker_change_signal)
        self._texts = []
        return flush

    def on_silence(self, now: float) -> List[Flush]:
        """静音期间检查缓冲是否过期。静音 >3s 是最强的说话人切换信号 → 标记。"""
        if self._texts and (now - self._time) > _MERGE_WINDOW:
            return [self._take(speaker_change_signal=True)]
        return []

    def drain(self) -> List[Flush]:
        """退出前刷新剩余内容。"""
        return [self._take()] if self._texts else []

    def _speaker_changed(self, gap: float) -> bool:
        """间隙过大 → 说话人边界：强信号直接判定；弱信号 + 已有累积 → 保守刷新。"""
        if gap <= _SPEAKER_GAP:
            return False
        return gap > _SPEAKER_GAP_STRONG or len(self._texts) >= 2

    def _should_merge(self, gap: float, cur_len: int, prev_total: int) -> bool:
        """内容感知合并：正常窗口内直接合并；放宽窗口内短段继续等。"""
        if not self._texts:
            return False
        if gap <= _MERGE_WINDOW:
            return True
        if gap <= _MERGE_RELAXED_WINDOW:
            # 短段（<8字）或前段也短（<15字）继续等
            return cur_len < 8 or prev_total < 15
        return False

    def push(self, text: str, now: float) -> List[Flush]:
        """接收一句 ASR 输出，返回本次需要提交的段（0-2 个）。"""
        flushes: List[Flush] = []
        gap = now - self._time if self._texts else float("inf")

        # ── 说话人边界：间隙过大时不合并 ──
        if self._speaker_changed(gap):
            flushes.append(self._take(speaker_change_signal=True))
            gap = float("inf")

        # ── 内容感知合并 ──
        cur_len = len(text)
        prev_total = sum(len(t) for t in self._texts)
        if self._should_merge(gap, cur_len, prev_total):
            if prev_total + cur_len <= _MERGE_MAX_CHARS:
                self._texts.append(text)
                self._time = now
                return flushes
            flushes.append(self._take())
        elif self._texts:
            flushes.append(self._take())

        # 启动新合并窗口
        self._texts = [text]
        self._time = now
        return flushes


def rms_of(chunk) -> float:
    """音频块均方根电平（供心跳日志 / 面板 VU 条）。"""
    import numpy as np
    return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))


def hotwords_from_lines(lines: List[str], max_chars: int) -> str:
    """热词行 → 空格拼接字符串，超长按行截断（v3.26.1 热词总长校验）。"""
    hotwords = " ".join(lines)
    if len(hotwords) <= max_chars:
        return hotwords
    truncated: List[str] = []
    current = 0
    for line in lines:
        if current + len(line) + 1 > max_chars:
            break
        truncated.append(line)
        current += len(line) + 1
    return " ".join(truncated)


__all__ = ["Flush", "MergeBuffer", "is_noise", "rms_of", "hotwords_from_lines"]
