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


def _warm(watcher: ClipboardWatcher) -> None:
    """首 poll 吞存量（幽灵段抑制）：用非语音文本暖机，不返回任何段。"""
    with patch(_READ, return_value="warmup-not-a-segment"), \
         patch(_RICH, return_value=False):
        assert watcher.poll() is None


class TestWatcherPoll:
    def test_passes_asr_text(self):
        watcher = ClipboardWatcher()
        _warm(watcher)
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == _ASR_TEXT

    def test_rejects_short_non_asr_text(self):
        watcher = ClipboardWatcher()
        _warm(watcher)
        with patch(_READ, return_value="hello"):
            assert watcher.poll() is None

    def test_rejects_code_text(self):
        watcher = ClipboardWatcher()
        _warm(watcher)
        code = "def foo():\n    return 1\n" * 3
        with patch(_READ, return_value=code):
            assert watcher.poll() is None

    def test_rejects_written_chinese_with_rich_text(self):
        """书面中文 + 富文本 = 从网页/文档复制的，不是 vocotype 输出。"""
        watcher = ClipboardWatcher()
        _warm(watcher)
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=True), \
             patch(_WRITTEN, return_value=True):
            assert watcher.poll() is None

    def test_same_text_triggered_once(self):
        """重复剪贴板内容只触发一次；文本变化后再触发。"""
        watcher = ClipboardWatcher()
        _warm(watcher)
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == _ASR_TEXT
            assert watcher.poll() is None  # 窗口内相同内容不重复触发
        # 文本变化 → 再次触发
        text2 = _ASR_TEXT + "，需要确认一下时间"
        with patch(_READ, return_value=text2), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == text2

    def test_non_asr_change_consumed(self):
        """非语音剪贴板变化也被消费掉，不会在之后文本未变时误触发。"""
        watcher = ClipboardWatcher()
        _warm(watcher)
        with patch(_READ, return_value="short"):
            assert watcher.poll() is None
            assert watcher.poll() is None  # 无新变化


class TestGhostSuppression:
    """启动幽灵段抑制：首 poll 只预读存量，不当作本场语音段。"""

    def test_first_poll_consumes_legacy_content(self):
        watcher = ClipboardWatcher()
        # 启动时剪贴板残留上一场的 ASR 文本 → 首 poll 吞掉，不触发
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() is None
            assert watcher.poll() is None  # 窗口内相同内容仍不触发
        # 新语音段到达 → 正常触发
        text2 = "接下来我们看一下方案评审的进度"
        with patch(_READ, return_value=text2), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == text2


class TestMaxLen:
    """长段支持：max_len 可配置（默认 2000 覆盖 120s 长语音）。"""

    def test_over_max_len_dropped(self):
        watcher = ClipboardWatcher(max_len=20)
        _warm(watcher)
        long_text = "我们今天讨论一下下半年的目标和预算安排需要确认"  # >20 字
        with patch(_READ, return_value=long_text):
            assert watcher.poll() is None

    def test_under_max_len_passes(self):
        watcher = ClipboardWatcher(max_len=200)
        _warm(watcher)
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == _ASR_TEXT

    def test_over_max_len_warns(self):
        import logging
        import io
        # 临时捕获 _clipboard 模块的日志（v3.25 后不传播到 root logger）
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setLevel(logging.WARNING)
        clip_logger = logging.getLogger("iris.assistant._clipboard")
        clip_logger.addHandler(handler)
        try:
            watcher = ClipboardWatcher(max_len=20)
            _warm(watcher)
            long_text = "我们今天讨论一下下半年的目标和预算安排需要确认"
            with patch(_READ, return_value=long_text):
                watcher.poll()
            handler.flush()
            assert "请分段说" in buf.getvalue()
        finally:
            clip_logger.removeHandler(handler)


class TestDedupWindow:
    """限时去重：相同文本仅在窗口内去重，超窗视为新段（重复说同一句不丢）。"""

    def test_dedup_window_zero_repeats(self):
        """窗口为 0（关闭去重）：相同文本可再次触发。"""
        watcher = ClipboardWatcher(dedup_window_seconds=0.0)
        _warm(watcher)
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == _ASR_TEXT
            assert watcher.poll() == _ASR_TEXT  # 无窗口 → 再次触发

    def test_same_text_within_window_suppressed(self):
        watcher = ClipboardWatcher(dedup_window_seconds=30.0)
        _warm(watcher)
        with patch(_READ, return_value=_ASR_TEXT), \
             patch(_RICH, return_value=False):
            assert watcher.poll() == _ASR_TEXT
            assert watcher.poll() is None  # 窗口内重复 → 去重
