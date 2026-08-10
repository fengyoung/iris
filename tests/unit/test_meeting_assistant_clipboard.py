"""实时会议助理 — 剪贴板采集单元测试（mock subprocess，仿 test_clipboard_io.py）。"""

from __future__ import annotations

from unittest.mock import patch

from iris.assistant._clipboard import ClipboardWatcher

# 注意：_clipboard.py 在模块导入时绑定函数引用，patch 必须打在
# iris.assistant._clipboard 命名空间（而非源头模块）
_READ = "iris.assistant._clipboard._read_clipboard"
_RICH = "iris.assistant._clipboard._clipboard_has_rich_text"
_WRITTEN = "iris.assistant._clipboard._looks_like_written_chinese"

_ASR_TEXT = "我们今天讨论一下下半年的目标和预算安排"  # 中文为主，无代码特征


class TestWatcherPoll:
    def test_passes_asr_text(self):
        watcher = ClipboardWatcher()
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == _ASR_TEXT

    def test_rejects_short_non_asr_text(self):
        watcher = ClipboardWatcher()
        with patch(_READ, return_value="hello"):
            assert watcher.poll() is None

    def test_rejects_code_text(self):
        watcher = ClipboardWatcher()
        code = "def foo():\n    return 1\n" * 3
        with patch(_READ, return_value=code):
            assert watcher.poll() is None

    def test_rejects_written_chinese_with_rich_text(self):
        """书面中文 + 富文本 = 从网页/文档复制的，不是 vocotype 输出。"""
        watcher = ClipboardWatcher()
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=True), \
             patch(_WRITTEN, return_value=True):
            assert watcher.poll() is None

    def test_same_text_triggered_once(self):
        """重复剪贴板内容只触发一次；文本变化后再触发。"""
        watcher = ClipboardWatcher()
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == _ASR_TEXT
            assert watcher.poll() is None  # 相同内容不重复触发
        # 文本变化 → 再次触发
        text2 = _ASR_TEXT + "，需要确认一下时间"
        with patch(_READ, return_value=text2), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == text2

    def test_non_asr_change_consumed(self):
        """非语音剪贴板变化也被消费掉，不会在之后文本未变时误触发。"""
        watcher = ClipboardWatcher()
        with patch(_READ, return_value="short"):
            assert watcher.poll() is None
            assert watcher.poll() is None  # 无新变化
