"""按固定时间预算发送 macOS 逐字符退格与粘贴事件。"""

from __future__ import annotations

import ctypes
import time

from ._hotkey import _CF, _CG


def _delete_and_paste(count: int) -> bool:
    """逐字符退格，发送目标 ≤0.8s，1s 超时后不再发送删除或粘贴。

    事件按顺序投递到同一系统队列；目标应用实际处理/绘制耗时不在此预算内。
    不在部分删除后重试，以免删除原文前面的内容。
    True 只表示所有事件已投递，并非目标应用已接收；False 可能已部分删除。
    调用方必须在任何失败后禁用自动写回，等待用户检查并显式重启。
    """
    if _CG is None or _CF is None:
        return False
    _CG.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    _CG.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    _CG.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    _CG.CGEventSetFlags.restype = None
    _CG.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    _CG.CGEventPost.restype = None
    _CG.CGPreflightPostEventAccess.argtypes = []
    _CG.CGPreflightPostEventAccess.restype = ctypes.c_bool
    _CF.CFRelease.argtypes = [ctypes.c_void_p]
    _CF.CFRelease.restype = None
    if not _CG.CGPreflightPostEventAccess():
        return False

    events = []
    try:
        for key, down, flags in ((51, True, 0), (51, False, 0),
                                 (9, True, 1 << 20), (9, False, 1 << 20)):
            event = _CG.CGEventCreateKeyboardEvent(None, key, down)
            if not event:
                return False
            events.append(event)
            _CG.CGEventSetFlags(event, flags)
        started = time.monotonic()
        duration = min(count * 0.015, 0.8)
        for index in range(count):
            # 绝对进度避免系统调用耗时逐字累积到人为延时上。
            remaining = started + duration * index / count - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            if time.monotonic() - started >= 1.0:
                return False
            _CG.CGEventPost(0, events[0])
            _CG.CGEventPost(0, events[1])
        if time.monotonic() - started >= 1.0:
            return False
        _CG.CGEventPost(0, events[2])
        _CG.CGEventPost(0, events[3])
        return True
    finally:
        for event in events:
            _CF.CFRelease(event)
