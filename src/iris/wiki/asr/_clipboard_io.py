"""macOS 剪贴板 I/O 工具函数（vocotype ASR 校正引擎专用）。"""

from __future__ import annotations

import re
import subprocess
import time

from ._keyboard_writer import _delete_and_paste


def _read_clipboard() -> str:
    """读取剪贴板文本内容。"""
    try:
        return subprocess.check_output(["pbpaste"], text=True)
    except Exception:
        return ""


def _write_clipboard(text: str) -> bool:
    """写入文本到剪贴板；只有 pbcopy 正常退出才返回 True。"""
    try:
        result = subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


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
    5. 逐字符 Delete 删除原始文本 + Cmd+V 粘贴校正文本

    逐字符删除是唯一跨 App 可靠的替换方式——Cmd+A 在聊天输入框/浏览器文本框
    等场景中行为不一致（可能全选整个对话而非当前输入），导致原文残留+校正追加=重复。
    原生键盘事件按长度加速，退格发送目标不超过 0.8s，1s 超时停止。
    应用处理及绘制事件的耗时由目标应用决定。

    Returns:
        True 仅表示全部事件已投递，不能确认目标应用实际接收。
        False 可能已经部分删除；调用方必须停止自动写回，不能重试或假定回滚。
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

    if not _write_clipboard(corrected):
        return False

    try:
        return _delete_and_paste(len(raw_text))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
# 剪贴板来源判定：区分「语音输入转写」与「手动复制」
# ═══════════════════════════════════════════════════════════════════

# 富文本剪贴板类型特征标记
_RICH_INDICATORS = (
    "html", "rtf", "rich", "styled",
    "public.html", "public.rtf", "com.apple",
)


def _looks_like_written_chinese(text: str) -> bool:
    """廉价预检查：文本是否更像书面中文而非 ASR 口语转写。

    ASR 口语转写的典型特征：无标点或标点稀疏、同音错字多；
    书面中文则标点规范。

    此函数用于在调用昂贵的 osascript（_clipboard_has_rich_text）之前
    快速过滤掉明显的手动复制文本，减少子进程开销。

    Returns:
        True 如果文本更像书面中文（建议进一步检查富文本格式）
    """
    # 含规范标点（中文句号/逗号/分号/问号）≥2 → 更像书面中文；
    # 否则不阻塞，走后续检查
    punct_count = len(re.findall(r"[。，；？、]", text))
    return punct_count >= 2


def _clipboard_has_rich_text() -> bool:
    """检查剪贴板是否包含富文本格式（HTML/RTF/Styled）。

    vocotype ASR 输出为纯文本（public.utf8-plain-text），不含富文本类型。
    用户从浏览器/文档手动复制的内容通常附带 HTML/RTF 格式，
    以此区分「语音输入转写」和「手动复制」两种剪贴板写入来源。

    Returns:
        True 如果剪贴板含富文本格式（大概率是手动复制），False 如果仅纯文本。
    """
    try:
        result = subprocess.run(
            [
                "osascript", "-e",
                'tell application "System Events" to get the name of every clipboard type',
            ],
            capture_output=True, text=True, timeout=3,
        )
        types_str = result.stdout.strip().lower()
        return any(ind in types_str for ind in _RICH_INDICATORS)
    except Exception:
        # 检测失败时不拦截，避免影响正常校正
        return False
