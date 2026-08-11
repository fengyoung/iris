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


def _replace_text_in_place(corrected: str, raw_text: str) -> bool:
    """用校正文本替换 vocotype 刚粘贴的原始文本（Cmd+A 全选覆盖 + 快照校验）。

    策略：
    1. 基线等待 vocotype 的 Cmd+V 贴入完成（最小 0.15s）
    2. 轮询剪贴板确认稳定（剪贴板仍为原文，vocotype 没有二次写入）
    3. 快照校验：剪贴板仍等于 raw_text 才继续——剪贴板未被其他操作改写，
       说明文档内容就是这段转写，此时 Cmd+A 全选覆盖是安全的
       （此校验同时拦截跨句竞态：新句到达时剪贴板已是新文本，直接返回 False）
    4. 写入校正文本到剪贴板
    5. Cmd+A 全选 → Cmd+V 覆盖粘贴（替代逐字符删除，长文本毫秒级完成，
       不再受 5s 超时截断）

    Returns:
        True 成功；False 快照不符/系统异常（调用方负责告警与状态回滚）
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

    # 快照校验：剪贴板必须仍等于原文，才允许全选覆盖
    if _read_clipboard() != raw_text:
        return False

    # 写入校正文本后执行「全选 → 粘贴」覆盖（删除 + 粘贴由系统完成）
    _write_clipboard(corrected)

    try:
        subprocess.run([
            "osascript", "-e",
            '''
            tell application "System Events"
                keystroke "a" using command down
                delay 0.05
                keystroke "v" using command down
            end tell
            ''',
        ], timeout=5)
        return True
    except Exception:
        return False
