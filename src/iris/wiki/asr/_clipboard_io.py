"""macOS 剪贴板 I/O 工具函数（vocotype ASR 校正引擎专用）。"""

from __future__ import annotations

import subprocess
import time


def _read_clipboard() -> str:
    """读取剪贴板文本内容。"""
    try:
        return subprocess.check_output(["pbpaste"], text=True)
    except Exception:
        return ""


def _write_clipboard(text: str) -> None:
    """写入文本到剪贴板。"""
    try:
        subprocess.run(["pbcopy"], input=text, text=True)
    except Exception:
        pass


def _paste() -> None:
    """模拟 Cmd+V 粘贴。"""
    try:
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ], timeout=3)
    except Exception:
        pass


def _replace_text_in_place(corrected: str, raw_length: int) -> None:
    """用校正文本替换 vocotype 刚粘贴的原始文本。

    策略：
    1. 基线等待 vocotype 的 Cmd+V 贴入完成（最小 0.15s）
    2. 轮询剪贴板确认稳定（剪贴板仍为原文，vocotype 没有二次写入）
    3. 写入校正文本到剪贴板
    4. 按 raw_length 次 Delete 键删除原始文本，粘贴校正文本
    """
    # 基线等待：vocotype 的 Cmd+V 至少需要 0.15s 完成
    _BASELINE_WAIT = 0.15
    time.sleep(_BASELINE_WAIT)

    # 轮询确认剪贴板稳定（此时剪贴板仍含原文，检测 vocotype 是否二次写入）
    _POLL_MAX_EXTRA = 1.0
    _POLL_STABLE_CYCLES = 3
    stable_count = 0
    last_clip = _read_clipboard()
    t_deadline = time.monotonic() + _POLL_MAX_EXTRA

    while time.monotonic() < t_deadline:
        time.sleep(0.05)
        current = _read_clipboard()
        if current == last_clip:
            stable_count += 1
            if stable_count >= _POLL_STABLE_CYCLES:
                break
        else:
            stable_count = 0
            last_clip = current

    # 剪贴板稳定后写入校正文本，紧接着执行删除 + 粘贴
    _write_clipboard(corrected)

    try:
        subprocess.run([
            "osascript", "-e",
            f'''
            tell application "System Events"
                repeat {raw_length} times
                    key code 51  -- Delete / Backspace
                end repeat
                delay 0.05
                keystroke "v" using command down
            end tell
            ''',
        ], timeout=5)
    except Exception:
        pass
