"""剪贴板采集：轮询监听 vocotype 转写结果，内容特征判定过滤非语音变化。"""

from __future__ import annotations

from typing import Optional

from iris.wiki.asr._clipboard_io import _read_clipboard
from iris.wiki.asr.corrector import (
    _clipboard_has_rich_text,
    _is_asr_text,
    _looks_like_written_chinese,
)


class ClipboardWatcher:
    """轮询剪贴板，返回新的语音段原文。

    判定链（复用 corrector 成熟逻辑）：
    1. 先更新 _last_seen 再判特征 —— 任何变化都消费掉，防止同一内容重复触发
    2. _is_asr_text 通过（中文比 + 长度 + 无代码/Markdown 特征）
    3. 非「书面中文 + 富文本」—— 富文本复制（网页/文档）不是 vocotype 纯文本输出

    注：不做热键监听窗口门控——助手不碰光标/剪贴板写入，纯内容特征判定更稳。
    """

    def __init__(self, poll_interval: float = 0.5):
        self._poll_interval = poll_interval
        self._last_seen = ""

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    def poll(self) -> Optional[str]:
        """返回新语音段原文；无变化或非 ASR 特征返回 None。"""
        text = _read_clipboard()
        if not text or text == self._last_seen:
            return None
        self._last_seen = text  # 先记再判：非语音变化也消费掉
        if not _is_asr_text(text):
            return None
        if _looks_like_written_chinese(text) and _clipboard_has_rich_text():
            return None
        return text
