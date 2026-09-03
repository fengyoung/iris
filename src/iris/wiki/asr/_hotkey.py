"""macOS 热键监听（vocotype push-to-talk 检测，零外部依赖）。

两条检测路径：
  - ``_check_modifiers`` / ``_check_key``：CGEventSourceFlagsState 轮询（辅助）
  - ``_HotkeyMonitor``：CGEventTap 系统级事件回调（主路径，
    解决 macOS 输入法体系下右 Option 键对轮询不可见的盲区）

以及 vocotype 热键字符串解析 ``_parse_hotkey`` / 配置读取 ``_load_vocotype_hotkey``。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_DEFAULT_VOCO_DIR = os.path.expanduser("~/Library/Application Support/VocoType")
VOCO_DIR = os.environ.get("IRIS_VOCOTYPE_DIR", _DEFAULT_VOCO_DIR)


# ═══════════════════════════════════════════════════════════════════
# macOS 键盘修饰键检测（通过 CoreGraphics + Carbon，零外部依赖）
# ═══════════════════════════════════════════════════════════════════

def _load_cg() -> Optional[ctypes.CDLL]:
    """加载 CoreGraphics 框架。"""
    path = ctypes.util.find_library("CoreGraphics")
    if not path:
        # macOS 上 CoreGraphics 通常在固定路径
        path = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        return ctypes.CDLL(path)
    except OSError:
        return None


_CG = _load_cg()

# CoreFoundation（CGEventTap 依赖 CFRunLoop / CFMachPort）
_CF = None
try:
    _cf_path = (
        ctypes.util.find_library("CoreFoundation")
        or "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    _CF = ctypes.CDLL(_cf_path)
except OSError:
    pass


# 键码常量（macOS Carbon key codes）
_KEYCODE_MAP = {
    # 修饰键（左右独立键码）
    "shift": 56,          # kVK_Shift (left)
    "rightShift": 60,     # kVK_RightShift
    "control": 59,        # kVK_Control (left)
    "rightControl": 62,   # kVK_RightControl
    "option": 58,         # kVK_Option (left)
    "rightOption": 61,    # kVK_RightOption
    "command": 55,        # kVK_Command (left)
    "rightCommand": 54,   # kVK_RightCommand
    "capsLock": 57,
    # 常用字母/符号键
    "KeyZ": 6,
    "KeyX": 7,
    "KeyC": 8,
    "KeyV": 9,
    # F1-F12 功能键
    "KeyF1": 122,         # kVK_F1
    "KeyF2": 120,         # kVK_F2
    "KeyF3": 99,          # kVK_F3
    "KeyF4": 118,         # kVK_F4
    "KeyF5": 96,          # kVK_F5
    "KeyF6": 97,          # kVK_F6
    "KeyF7": 98,          # kVK_F7
    "KeyF8": 100,         # kVK_F8
    "KeyF9": 101,         # kVK_F9
    "KeyF10": 109,        # kVK_F10
    "KeyF11": 103,        # kVK_F11
    "KeyF12": 111,        # kVK_F12
}

# 每个修饰键类型对应的全部键码（左右两侧）
_MODIFIER_KEYCODE_VARIANTS: Dict[str, List[int]] = {
    "shift": [56, 60],     # left, right
    "control": [59, 62],   # left, right
    "option": [58, 61],    # left, right
    "command": [55, 54],   # left, right
}

# 修饰键掩码
_MOD_MASKS = {
    "shift": 512,     # shiftKey
    "control": 4096,  # controlKey
    "option": 2048,   # optionKey
    "command": 256,   # cmdKey
}


# CGEventFlags → _MOD_MASKS 映射
_CG_FLAGS_TO_MASK = {
    0x00020000: 512,   # kCGEventFlagMaskShift → shiftKey
    0x00040000: 4096,  # kCGEventFlagMaskControl → controlKey
    0x00080000: 2048,  # kCGEventFlagMaskAlternate → optionKey
    0x00100000: 256,   # kCGEventFlagMaskCommand → cmdKey
}


def _flags_to_mask(flags: int) -> int:
    """CGEventFlags 位图 → _MOD_MASKS 掩码。"""
    mask = 0
    for cg_flag, mod_mask in _CG_FLAGS_TO_MASK.items():
        if flags & cg_flag:
            mask |= mod_mask
    return mask


def _check_modifiers() -> int:
    """返回当前按下的修饰键掩码。

    使用 CGEventSourceFlagsState (source=0) 从窗口服务器直接读取修饰键标志位，
    比逐键码轮询 CGEventSourceKeyState 更可靠（特别是右 Option 键）。
    """
    if _CG is None:
        return 0
    try:
        _CG.CGEventSourceFlagsState.restype = ctypes.c_uint64
        _CG.CGEventSourceFlagsState.argtypes = [ctypes.c_int]
        flags = _CG.CGEventSourceFlagsState(0)  # kCGEventSourceStateHIDSystemState
        return _flags_to_mask(flags)
    except Exception:
        return 0


def _check_key(keycode: int) -> bool:
    """检查指定非修饰键是否被按下。

    使用 CGEventSourceStateHIDSystemState (source=0) 反映全局硬件状态。
    """
    if _CG is None or keycode == 0:
        return False
    try:
        _CG.CGEventSourceKeyState.restype = ctypes.c_bool
        _CG.CGEventSourceKeyState.argtypes = [
            ctypes.c_int, ctypes.c_uint16,
        ]
        return bool(_CG.CGEventSourceKeyState(0, ctypes.c_uint16(keycode)))
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
# CGEventTap 热键监听器（解决 CGEventSourceKeyState 右 Option 盲区）
# ═══════════════════════════════════════════════════════════════════

# CGEventType 常量
_EVT_KEY_DOWN = 10        # kCGEventKeyDown
_EVT_KEY_UP = 11          # kCGEventKeyUp
_EVT_FLAGS_CHANGED = 12   # kCGEventFlagsChanged
# CGEventTap 选项
_TAP_HID = 0               # kCGHIDEventTap（最底层，先于输入法处理）
_TAP_HEAD_INSERT = 0       # kCGHeadInsertEventTap
_TAP_LISTEN_ONLY = 1       # kCGEventTapOptionListenOnly
# CGEvent 字段
_FIELD_KEYCODE = 9         # kCGKeyboardEventKeycode
# CFRunLoop 常量
_kCFRunLoopDefaultMode = ctypes.c_void_p.in_dll(_CF, "kCFRunLoopDefaultMode") if _CF else None
_kCFRunLoopCommonModes = ctypes.c_void_p.in_dll(_CF, "kCFRunLoopCommonModes") if _CF else None

# 热键状态回调类型
_CALLBACK_TYPE = ctypes.CFUNCTYPE(
    ctypes.c_void_p,   # CGEventRef (return)
    ctypes.c_void_p,   # CGEventTapProxy
    ctypes.c_int,      # CGEventType
    ctypes.c_void_p,   # CGEventRef
    ctypes.c_void_p,   # void *refcon
)


class _HotkeyMonitor:
    """CGEventTap 热键监听器。

    在主轮询线程之外独立运行一个后台 CFRunLoop 线程，
    通过系统级事件回调（CGEventTap）可靠检测任意键盘组合，
    包括 macOS 输入法体系下被拦截的右 Option 键。

    用法:
        monitor = _HotkeyMonitor(mask=2048, keycode=0)
        if monitor.start():
            # 在主循环中读取 monitor.held / monitor.released_at
            ...
            monitor.stop()
    """

    def __init__(self, mask: int, keycode: int):
        self._mask = mask
        self._keycode = keycode
        self._held = False
        self._released_at: float = 0.0
        self._pressed_at: float = 0.0  # 本次按住开始时刻（长语音窗口计算用）
        self._lock = threading.Lock()
        self._tap: Any = None
        self._source: Any = None
        self._thread: Optional[threading.Thread] = None
        self._alive = False
        self._first_event = False  # 调试：首次事件确认
        # 引用保持，防止 Python 回收回调
        self._callback_ref: Any = None
        # 就绪事件：tap 线程完成初始化（成功或失败）时 set，start 等待它
        self._ready = threading.Event()
        # tap 线程自己的 run loop 引用（stop 唤醒目标，非主线程 loop）
        self._runloop: Any = None

    # ---- 线程安全属性 ----

    @property
    def held(self) -> bool:
        with self._lock:
            return self._held

    @property
    def released_at(self) -> float:
        with self._lock:
            return self._released_at

    @property
    def hold_duration(self) -> float:
        """最近一次按住的持续时长（秒）。从未按下返回 0。"""
        with self._lock:
            if self._pressed_at <= 0:
                return 0.0
            return max(0.0, self._released_at - self._pressed_at)

    # ---- 启动 / 停止 ----

    def start(self) -> bool:
        """启动事件监听线程。返回 False 表示权限不足。"""
        if self._alive:
            return True
        if _CG is None or _CF is None:
            print("[Iris] ⚠ CGEventTap 不可用：CoreGraphics/CoreFoundation 未加载",
                  file=sys.stderr)
            return False

        self._alive = True  # 先置位，防止 _run_loop 立即退出

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="asr-hotkey-tap",
        )
        self._thread.start()

        # 等待 tap 初始化完成（最多 2s）：
        # 用 ready 事件而非 join(timeout)——tap 线程是常驻循环永不退出，
        # 成功启动时 join 必然等到超时白等 2s；事件在初始化完成后立即 set
        self._ready.wait(timeout=2.0)
        if self._tap is None:
            self._alive = False
            return False
        return True

    def stop(self) -> None:
        """停止监听并回收线程。"""
        self._alive = False
        # 唤醒 tap 线程自己的 run loop 使其检查 _alive 标志
        # （CFRunLoopGetCurrent 返回调用线程的 loop，此处须用 tap 线程保存的引用）
        if _CF and self._source:
            try:
                _CF.CFRunLoopSourceSignal(self._source)
                if self._runloop is not None:
                    _CF.CFRunLoopWakeUp(self._runloop)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    # ---- 事件处理回调（C → Python） ----

    def _set_held(self, now_held: bool) -> None:
        """更新按住状态，并在状态翻转时记录按下/释放时刻。"""
        with self._lock:
            was_held = self._held
            self._held = now_held
            if not was_held and now_held:
                self._pressed_at = time.monotonic()
            if was_held and not now_held:
                self._released_at = time.monotonic()

    def _on_flags_changed(self, event: Any) -> None:
        """修饰键变化：读取 flags 判断组合键是否按下。"""
        cur_mask = _flags_to_mask(_CG.CGEventGetFlags(event))
        now_held = (cur_mask & self._mask) == self._mask if self._mask > 0 else False
        # 如果热键还包含非修饰键，额外检查
        if now_held and self._keycode > 0:
            now_held = bool(_CG.CGEventSourceKeyState(0, ctypes.c_uint16(self._keycode)))
        self._set_held(now_held)

    def _on_key_event(self, event_type: int, event: Any) -> None:
        """非修饰键按下/释放（仅对包含字母/功能键的热键组合有意义）。"""
        keycode = _CG.CGEventGetIntegerValueField(event, _FIELD_KEYCODE)
        if keycode != self._keycode:
            return
        if event_type == _EVT_KEY_DOWN:
            cur_mask = _flags_to_mask(_CG.CGEventGetFlags(event))
            if self._mask == 0 or (cur_mask & self._mask) == self._mask:
                with self._lock:
                    self._held = True
                    self._pressed_at = time.monotonic()
        else:  # key up
            self._set_held(False)

    def _handle_event(self, event_type: int, event: Any) -> None:
        """处理键盘事件，更新热键状态（由 C 回调间接调用）。"""
        # 首次收到事件时输出确认（仅一次）
        if not self._first_event:
            self._first_event = True
            type_names = {10: "keyDown", 11: "keyUp", 12: "flagsChanged"}
            print(
                f"[Iris] 🔍 CGEventTap 已收到首个事件"
                f" (type={event_type} {type_names.get(event_type, '?')})",
                file=sys.stderr,
            )
        try:
            if event_type == _EVT_FLAGS_CHANGED:
                self._on_flags_changed(event)
            elif event_type in (_EVT_KEY_DOWN, _EVT_KEY_UP) and self._keycode > 0:
                self._on_key_event(event_type, event)
        except Exception:
            pass  # 回调链中静默吞异常，不干扰事件流

    # ---- Run Loop 线程 ----

    @staticmethod
    def _declare_signatures() -> None:
        """显式设置 CG / CF 函数签名，防止 64 位下指针/整型截断。"""
        # CGEventTapCreate
        _CG.CGEventTapCreate.restype = ctypes.c_void_p
        _CG.CGEventTapCreate.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint64, ctypes.c_void_p, ctypes.c_void_p,
        ]
        # CGEvent 字段读取
        _CG.CGEventGetFlags.restype = ctypes.c_uint64
        _CG.CGEventGetFlags.argtypes = [ctypes.c_void_p]
        _CG.CGEventGetIntegerValueField.restype = ctypes.c_int64
        _CG.CGEventGetIntegerValueField.argtypes = [ctypes.c_void_p, ctypes.c_int]
        # CFMachPort → RunLoopSource
        _CF.CFMachPortCreateRunLoopSource.restype = ctypes.c_void_p
        _CF.CFMachPortCreateRunLoopSource.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
        ]
        # CFRunLoop
        _CF.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        _CF.CFRunLoopGetCurrent.argtypes = []
        _CF.CFRunLoopAddSource.restype = None
        _CF.CFRunLoopAddSource.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        _CF.CFRunLoopRunInMode.restype = ctypes.c_int
        _CF.CFRunLoopRunInMode.argtypes = [
            ctypes.c_void_p, ctypes.c_double, ctypes.c_bool,
        ]
        # 清理
        _CF.CFMachPortInvalidate.restype = None
        _CF.CFMachPortInvalidate.argtypes = [ctypes.c_void_p]
        _CF.CFRunLoopSourceSignal.restype = None
        _CF.CFRunLoopSourceSignal.argtypes = [ctypes.c_void_p]
        _CF.CFRunLoopWakeUp.restype = None
        _CF.CFRunLoopWakeUp.argtypes = [ctypes.c_void_p]

    def _run_loop(self) -> None:
        """后台线程：创建 CGEventTap，运行 CFRunLoop。"""
        # 搭建 C 回调 → Python 方法的桥接：
        # py_object 包装 self → addressof 取 C 指针 → refcon 传递
        # 回调中 POINTER(py_object) 解引用 → .value 取回 Python 对象
        self_ref = ctypes.py_object(self)
        self_ref_addr = ctypes.c_void_p(ctypes.addressof(self_ref))
        self._self_ref = self_ref  # 防止 GC，保持 py_object 存活

        @_CALLBACK_TYPE
        def _c_callback(_proxy, event_type, event, refcon):
            try:
                obj = ctypes.cast(
                    refcon, ctypes.POINTER(ctypes.py_object),
                ).contents.value
                obj._handle_event(event_type, event)
            except Exception:
                pass
            return event  # listen-only：原样返回事件

        self._callback_ref = _c_callback  # 防止 GC
        self._declare_signatures()

        # 事件掩码：key down | key up | flags changed
        event_mask = (1 << _EVT_KEY_DOWN) | (1 << _EVT_KEY_UP) | (1 << _EVT_FLAGS_CHANGED)

        self._tap = _CG.CGEventTapCreate(
            _TAP_HID,            # HID 层（最底层，先于输入法）
            _TAP_HEAD_INSERT,     # 优先于其他 tap
            _TAP_LISTEN_ONLY,     # 仅观察，不修改
            event_mask,
            _c_callback,
            self_ref_addr,        # refcon → py_object 的 C 指针
        )

        if not self._tap:
            self._alive = False
            print(
                "[Iris] ⚠ CGEventTap 创建失败，请授予终端辅助功能权限：\n"
                "    系统偏好设置 → 隐私与安全性 → 辅助功能 → 添加终端",
                file=sys.stderr,
            )
            self._ready.set()  # 失败也唤醒 start 的等待（其检查 _tap is None）
            return

        # 将 tap 包装为 run loop source
        self._source = _CF.CFMachPortCreateRunLoopSource(None, self._tap, 0)

        _CF.CFRunLoopAddSource(
            _CF.CFRunLoopGetCurrent(),
            self._source,
            _kCFRunLoopCommonModes,
        )

        # 保存本线程 run loop 引用（stop 用它唤醒）+ 通知 start 就绪
        self._runloop = _CF.CFRunLoopGetCurrent()
        self._ready.set()

        # 事件循环（每 0.5s 检查一次 _alive 标志）
        while self._alive:
            _CF.CFRunLoopRunInMode(_kCFRunLoopDefaultMode, 0.5, False)

        # 清理
        if self._tap:
            _CF.CFMachPortInvalidate(self._tap)
        self._tap = None
        self._source = None


# ═══════════════════════════════════════════════════════════════════
# vocotype 热键配置
# ═══════════════════════════════════════════════════════════════════

def _parse_hotkey(hotkey_str: str) -> Tuple[int, int]:
    """解析 vocotype 热键字符串 → (modifiers_mask, key_code)。

    格式例如: "shift+control+KeyZ", "alt+ArrowRight"

    非修饰键（如 ArrowRight）无法通过轮询可靠检测，
    此类热键将仅依赖内容特征判定，修饰键检测作为辅助。
    """
    if not hotkey_str:
        return 0, 0

    parts = [p.strip() for p in hotkey_str.lower().split("+")]
    mask = 0
    keycode = 0

    for part in parts:
        # 修饰键别名（含 macOS 左右键变体）
        if part in ("shift", "leftshift", "rightshift"):
            mask |= _MOD_MASKS.get("shift", 0)
        elif part in ("control", "ctrl", "leftcontrol", "rightcontrol"):
            mask |= _MOD_MASKS.get("control", 0)
        elif part in ("option", "alt", "leftoption", "leftalt", "rightoption", "rightalt", "altright", "altleft"):
            mask |= _MOD_MASKS.get("option", 0)
        elif part in ("command", "cmd", "leftcommand", "rightcommand"):
            mask |= _MOD_MASKS.get("command", 0)
        else:
            # 普通键 — 规范化后查找
            key_name = part.replace("key", "").capitalize()
            mapped = f"Key{key_name}"
            keycode = _KEYCODE_MAP.get(
                mapped,
                _KEYCODE_MAP.get(part.capitalize(), 0),
            )

    return mask, keycode


def _load_vocotype_hotkey() -> Tuple[int, int]:
    """从 vocotype 配置文件读取录音热键。

    Returns:
        (modifiers_mask, key_code) 或 (0, 0)
    """
    config_path = Path(VOCO_DIR) / "ui_settings.json"
    if not config_path.exists():
        return 0, 0

    try:
        with open(config_path) as f:
            settings = json.load(f)
        hotkey_str = settings.get("recording_hotkey", "")
        return _parse_hotkey(hotkey_str)
    except Exception:
        return 0, 0
