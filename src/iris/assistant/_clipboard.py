"""剪贴板采集：轮询监听 vocotype 转写结果，内容特征判定过滤非语音变化。"""

from __future__ import annotations

import logging
import time
from typing import Optional

from iris.wiki.asr._clipboard_io import _read_clipboard
from iris.wiki.asr.corrector import (
    _clipboard_has_rich_text,
    _is_asr_text,
    _looks_like_written_chinese,
)

_logger = logging.getLogger(__name__)


class ClipboardWatcher:
    """轮询剪贴板，返回新的语音段原文。

    判定链（复用 corrector 成熟逻辑）：
    1. 先更新 _last_seen 再判特征 —— 任何变化都消费掉，防止同一内容重复触发
    2. _is_asr_text 通过（中文比 + 长度 + 无代码/Markdown 特征）
    3. 非「书面中文 + 富文本」—— 富文本复制（网页/文档）不是 vocotype 纯文本输出

    增强（v3.23.3）：
    - max_len 可配置（默认 2000，覆盖 120s 长语音场景；默认 500 上限会静默丢弃长段）
    - 首次 poll 只预读剪贴板置 _last_seen（抑制启动幽灵段：上一场残留文本不算本场首段）
    - 限时去重：相同文本仅在 dedup_window 窗口内去重；超窗视为新段（重复说同一句话不再丢失）

    注：不做热键监听窗口门控——助手不碰光标/剪贴板写入，纯内容特征判定更稳。
    """

    # 超过配置上限仍触发警告的硬上限（>5000 必是异常粘贴，不可能是语音段）
    _HARD_MAX_LEN = 5000

    def __init__(
        self,
        poll_interval: float = 0.5,
        *,
        max_len: int = 2000,
        dedup_window_seconds: float = 30.0,
    ):
        self._poll_interval = poll_interval
        self._max_len = max_len
        self._dedup_window = dedup_window_seconds
        self._last_seen = ""
        self._last_seen_at = 0.0
        self._initialized = False  # 首 poll 吞存量（幽灵段抑制）

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    def poll(self) -> Optional[str]:
        """返回新语音段原文；无变化或非 ASR 特征返回 None。"""
        text = _read_clipboard()
        now = time.monotonic()
        if not text:
            return None
        if not self._initialized:
            # 首 poll：只预读存量，不当作本场语音段（启动时剪贴板残留 ≠ 会议发言）
            self._initialized = True
            self._last_seen = text
            self._last_seen_at = now
            return None
        if text == self._last_seen and now - self._last_seen_at < self._dedup_window:
            return None  # 窗口内重复内容（vocotype 重贴/用户复制同一文本）
        self._last_seen = text  # 先记再判：非语音变化也消费掉
        self._last_seen_at = now
        if len(text) > self._HARD_MAX_LEN:
            _logger.warning("剪贴板内容过长，已忽略（非语音段）")
            return None
        if len(text) > self._max_len:
            _logger.warning("语音段超长（>%d 字），已丢弃，请分段说", self._max_len)
            return None
        if not _is_asr_text(text, max_length=self._max_len):
            return None
        if _looks_like_written_chinese(text) and _clipboard_has_rich_text():
            return None
        return text
