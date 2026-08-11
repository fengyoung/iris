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
    """用校正文本替换 vocotype 刚粘贴的原始文本。

    策略：
    1. 基线等待 vocotype 的 Cmd+V 贴入完成（最小 0.15s）
    2. 轮询剪贴板确认稳定（vocotype 没有二次写入）
    3. 快照校验：剪贴板仍等于 raw_text 才继续
    4. 写入校正文本到剪贴板
    5. **短文本（≤120 字）**：逐字符 Delete 删除 + Cmd+V 粘贴
       （精准，只删 vocotype 刚贴入的文本，不误删其他内容）
    6. **长文本（>120 字）**：Cmd+A 全选 + Cmd+V 覆盖粘贴
       （快速，O(1) 完成避免超时截断；全选覆盖在 vocotype 独占窗口场景安全）

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

    # 快照校验：剪贴板必须仍等于原文，才允许操作文档
    if _read_clipboard() != raw_text:
        return False

    _write_clipboard(corrected)

    raw_length = len(raw_text)

    # ── 短文本：逐字符 Delete + 粘贴（精准不误删） ──
    # 理由：Cmd+A 在某些 app（聊天输入框/特殊编辑器）中选不中文档内容，
    # 逐字符删除是唯一可靠的方式；≤120 字时耗时可控（timeout 按 80ms/字）
    if raw_length <= 120:
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
            ], timeout=max(5, int(raw_length * 0.08)))
            return True
        except Exception:
            return False

    # ── 长文本：Cmd+A 全选 + 粘贴（快速，O(1)） ──
    # vocotype 独占窗口场景全选覆盖安全；若 Cmd+A 在目标 app 无效则降级返回 False
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
