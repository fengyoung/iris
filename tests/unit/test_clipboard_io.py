"""ASR 剪贴板 I/O — 单元测试（mock subprocess）。"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from iris.wiki.asr._clipboard_io import (
    _read_clipboard,
    _write_clipboard,
    _paste,
    _replace_text_in_place,
)


class TestReadClipboard:
    def test_returns_text(self):
        with patch("subprocess.check_output", return_value="hello world"):
            assert _read_clipboard() == "hello world"

    def test_returns_empty_on_exception(self):
        with patch("subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "pbpaste")):
            assert _read_clipboard() == ""

    def test_returns_empty_on_oserror(self):
        with patch("subprocess.check_output", side_effect=OSError):
            assert _read_clipboard() == ""

    def test_handles_unicode(self):
        with patch("subprocess.check_output", return_value="你好世界"):
            assert _read_clipboard() == "你好世界"


class TestWriteClipboard:
    def test_calls_pbcopy_with_text(self):
        with patch("subprocess.run", return_value=subprocess.CompletedProcess("pbcopy", 0)) as mock_run:
            assert _write_clipboard("test text") is True
            mock_run.assert_called_once_with(
                ["pbcopy"], input="test text", text=True, check=True, timeout=3
            )

    def test_silently_handles_exception(self):
        with patch("subprocess.run", side_effect=OSError):
            assert _write_clipboard("test") is False


class TestPaste:
    def test_simulates_cmd_v(self):
        with patch("subprocess.run") as mock_run:
            _paste()
            args = mock_run.call_args[0][0]
            assert "keystroke" in " ".join(args)
            assert "command down" in " ".join(args)

    def test_handles_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 3)):
            _paste()  # 不应抛出异常


class TestReplaceTextInPlace:
    def test_writes_corrected_to_clipboard(self):
        with patch("iris.wiki.asr._clipboard_io._write_clipboard", return_value=True) as mock_write, \
             patch("iris.wiki.asr._clipboard_io._read_clipboard", return_value="原始文本"), \
             patch("iris.wiki.asr._clipboard_io._delete_and_paste", return_value=True), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 0.2, 1.0]):
            ok = _replace_text_in_place("校正文本", "原始文本")
            assert ok is True
            mock_write.assert_called_once_with("校正文本")

    def test_short_and_long_text_use_native_backspace(self):
        for count in (4, 140, 500, 2000):
            raw = "文" * count
            with patch("iris.wiki.asr._clipboard_io._write_clipboard", return_value=True), \
                 patch("iris.wiki.asr._clipboard_io._read_clipboard", return_value=raw), \
                 patch("iris.wiki.asr._clipboard_io._delete_and_paste", return_value=True) as writer, \
                 patch("time.sleep"), \
                 patch("time.monotonic", side_effect=[0, 0.2, 1.0]):
                assert _replace_text_in_place("校正文本", raw)
                writer.assert_called_once_with(count)

    def test_snapshot_mismatch_returns_false(self):
        """快照校验：剪贴板已不等于原文（新句到达/用户其他复制）→ 不写不贴。"""
        with patch("iris.wiki.asr._clipboard_io._write_clipboard", return_value=True) as mock_write, \
             patch("iris.wiki.asr._clipboard_io._read_clipboard", return_value="其他内容"), \
             patch("subprocess.run") as mock_run, \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 0.2, 1.0]):
            ok = _replace_text_in_place("校正文本", "原始文本")
            assert ok is False
            mock_write.assert_not_called()
            mock_run.assert_not_called()

    def test_native_writer_exception_returns_false(self):
        """系统异常（超时/无权限）→ 返回 False，调用方告警并禁用后续写回。"""
        with patch("iris.wiki.asr._clipboard_io._write_clipboard", return_value=True), \
             patch("iris.wiki.asr._clipboard_io._read_clipboard", return_value="原始文本"), \
             patch("iris.wiki.asr._clipboard_io._delete_and_paste", side_effect=OSError), \
             patch("time.sleep"), \
             patch("time.monotonic", side_effect=[0, 0.2, 1.0]):
            ok = _replace_text_in_place("校正文本", "原始文本")
            assert ok is False

    def test_polling_waits_for_stable_clipboard(self):
        """剪贴板不稳定时轮询等待，稳定后才粘贴。"""
        call_count = [0]
        def unstable_then_stable():
            call_count[0] += 1
            if call_count[0] < 5:
                return f"changing_{call_count[0]}"
            return "stable"
        with patch("iris.wiki.asr._clipboard_io._write_clipboard", return_value=True), \
             patch("iris.wiki.asr._clipboard_io._read_clipboard", side_effect=unstable_then_stable), \
             patch("iris.wiki.asr._clipboard_io._delete_and_paste", return_value=True), \
             patch("time.sleep"), \
             patch("time.monotonic", return_value=0):
            ok = _replace_text_in_place("校正文本", "stable")
            assert ok is True
            assert call_count[0] >= 3  # 至少轮询了几次
