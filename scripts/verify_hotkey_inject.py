#!/usr/bin/env python3
"""可行性验证：CGEventPost 注入热键序列，模拟 vocotype 热键「按下→保持→松开」。

用途：验证能否不真实按键，仅靠系统事件注入触发 vocotype 开始录音。

用法:
    python3 scripts/verify_hotkey_inject.py                    # 用 vocotype 真实热键，按住 2s
    python3 scripts/verify_hotkey_inject.py --hold 5           # 按住 5s（模拟长语音）
    python3 scripts/verify_hotkey_inject.py --repeat 3         # 连续注入 3 次
    python3 scripts/verify_hotkey_inject.py --keycode 61       # 指定键码（如右 Option）

流程:
    1. 从 vocotype ui_settings.json 读取 recording_hotkey（与 asr-corrector 同源）
    2. 权限预检（辅助功能授权，CGEventPost 前置条件）
    3. 注入完整按键序列：修饰键 down → 主键 down → 保持 → 主键 up → 修饰键 up
    4. 由你观察 vocotype 是否开始录音 / 转写
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import sys
import time
from pathlib import Path

# 复用 Iris 现有热键解析与键码表（避免复制维护）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from iris.wiki.asr.corrector import _load_vocotype_hotkey  # noqa: E402
from iris.wiki.asr.corrector import _KEYCODE_MAP, _MOD_MASKS, _MODIFIER_KEYCODE_VARIANTS  # noqa: E402

# ── CoreGraphics 加载（同 corrector.py 模式） ──────────────────────
_CG = None


def _load_cg():
    global _CG
    if _CG is not None:
        return _CG
    path = ctypes.util.find_library("CoreGraphics")
    if not path:
        path = "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        _CG = ctypes.CDLL(path)
    except OSError:
        _CG = None
    return _CG


# Carbon 掩码 → CGEvent flags（kCGEventFlagMask*）
_CARBON_TO_CG_FLAGS = {
    _MOD_MASKS["shift"]: 0x00020000,
    _MOD_MASKS["control"]: 0x00040000,
    _MOD_MASKS["option"]: 0x00080000,
    _MOD_MASKS["command"]: 0x00100000,
}


def _setup_signatures() -> None:
    """显式设置 ctypes 函数签名（64 位下防止指针/整型截断）。"""
    _CG.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    _CG.CGEventCreateKeyboardEvent.argtypes = [
        ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool,
    ]
    _CG.CGEventSetFlags.restype = None
    _CG.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    _CG.CGEventPost.restype = None
    _CG.CGEventPost.argtypes = [ctypes.c_int, ctypes.c_void_p]
    _CG.CFRelease.restype = None
    _CG.CFRelease.argtypes = [ctypes.c_void_p]


def _post_key(keycode: int, flags: int, key_down: bool) -> bool:
    """注入单键 down/up 事件到 HID 层（最接近真实硬件的注入点）。"""
    ev = _CG.CGEventCreateKeyboardEvent(None, ctypes.c_uint16(keycode), key_down)
    if not ev:
        return False
    if flags:
        _CG.CGEventSetFlags(ev, flags)
    _CG.CGEventPost(0, ev)  # kCGHIDEventTap = 0
    _CG.CFRelease(ev)
    return True


def _inject_sequence(mask: int, keycode: int, hold_seconds: float) -> None:
    """注入完整热键序列：修饰键 down → 主键 down → 保持 → 主键 up → 修饰键 up。"""
    # 从 Carbon 掩码拆出需按下的修饰键及对应 flags
    mods = [(carb_mask, cg_flag) for carb_mask, cg_flag in _CARBON_TO_CG_FLAGS.items()
            if mask & carb_mask]
    all_flags = 0
    for _, cg_flag in mods:
        all_flags |= cg_flag

    print(f"[verify] ⬇ 序列开始: 修饰键 {len(mods)} 个 + 主键 keycode={keycode}"
          f" (flags=0x{all_flags:x}), 保持 {hold_seconds}s")

    # 1. 修饰键依次按下（各带自身 flag，更新系统修饰键状态）
    for carb_mask, cg_flag in mods:
        _post_key(_MODIFIER_KEYCODE_VARIANTS[_mask_name(carb_mask)][0], cg_flag, True)
    print("[verify]   · 修饰键已按下")

    if keycode:
        # 2. 主键按下（带全部修饰 flags）
        _post_key(keycode, all_flags, True)
        print("[verify]   · 主键已按下")

    # 3. 保持（模拟「按住说话」）
    time.sleep(hold_seconds)

    # 4. 主键松开（带全部修饰 flags）
    if keycode:
        _post_key(keycode, all_flags, False)
    print("[verify]   · 主键已松开")

    # 5. 修饰键依次松开
    for carb_mask, cg_flag in reversed(mods):
        _post_key(_MODIFIER_KEYCODE_VARIANTS[_mask_name(carb_mask)][0], cg_flag, False)
    print("[verify] ⬆ 序列完成: 已全部松开 → vocotype 应触发转写")


_MASK_NAMES = {512: "shift", 4096: "control", 2048: "option", 256: "command"}


def _mask_name(carb_mask: int) -> str:
    return _MASK_NAMES.get(carb_mask, "?")


def _describe_hotkey(mask: int, keycode: int) -> str:
    """人类可读描述，如 'option+KeyF1'。"""
    parts = [_mask_name(m) for m in _MASK_NAMES if mask & m]
    if keycode:
        name = next((k for k, v in _KEYCODE_MAP.items() if v == keycode), str(keycode))
        parts.append(name)
    return "+".join(parts) if parts else "无"


def _check_accessibility() -> bool:
    """预检辅助功能权限：CGEventPost 注入的前置条件。"""
    try:
        _CG.CGPreflightPostEventAccess.restype = ctypes.c_bool
        _CG.CGPreflightPostEventAccess.argtypes = []
        ok = bool(_CG.CGPreflightPostEventAccess())
        if not ok:
            print("[verify] ❌ 无辅助功能权限，事件注入会被系统丢弃。")
            print("         请到 系统设置 → 隐私与安全性 → 辅助功能 → 勾选当前终端后重试")
            return False
        print("[verify] ✅ 辅助功能权限正常")
        return True
    except Exception:
        # 旧系统无此 API：不阻塞，注入后观察效果
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 CGEventPost 注入能否触发 vocotype 录音")
    parser.add_argument("--hold", type=float, default=2.0, help="按住时长（秒），默认 2")
    parser.add_argument("--repeat", type=int, default=1, help="注入次数，默认 1")
    parser.add_argument("--keycode", type=int, default=None, help="覆盖主键键码（如右 Option=61）")
    parser.add_argument("--mask", type=int, default=None, help="覆盖修饰键掩码（Carbon 值）")
    args = parser.parse_args()

    if _load_cg() is None:
        print("[verify] ❌ CoreGraphics 加载失败")
        return 1
    _setup_signatures()

    # 1. 读取 vocotype 热键（与 asr-corrector 同源，可覆盖）
    mask, keycode = _load_vocotype_hotkey()
    if args.mask is not None:
        mask = args.mask
    if args.keycode is not None:
        keycode = args.keycode
    print(f"[verify] vocotype 热键: mask={mask}, keycode={keycode}"
          f" → 「{_describe_hotkey(mask, keycode)}」")
    if not (mask or keycode):
        print("[verify] ❌ 未检测到 vocotype 热键（ui_settings.json 缺失或 recording_hotkey 为空）")
        print("         可手动指定: --mask 2048 --keycode 61")
        return 1
    if not mask:
        print("[verify] ⚠ 热键无修饰键（仅主键），vocotype 的 push-to-talk 可能不认，结果仅供参考")
    if mask and not keycode:
        # 2026-08-10 实测：ui_settings 解析为 option(keycode=0) 时注入左 Option 无反应，
        # vocotype 键码级监听右 Option(61)，必须 --keycode 61 才触发
        print("[verify] ⚠ 热键为纯修饰键（keycode=0）：注入走左 Option 变体，vocotype 可能无反应")
        print("         已知事实：vocotype 实绑右 Option 键码 61，建议 --keycode 61 重试")

    # 2. 权限预检
    if not _check_accessibility():
        return 1

    # 3. 循环注入
    for i in range(args.repeat):
        print(f"\n[verify] ════ 第 {i + 1}/{args.repeat} 次注入 ════")
        _inject_sequence(mask, keycode, args.hold)
        if i < args.repeat - 1:
            time.sleep(4)  # 间隔方便观察

    print("\n[verify] ✅ 注入完成。请观察 vocotype 是否开始录音 / 转写。")
    print("         → 若无反应，试 --keycode 61（右 Option 变体）或更换注入层（CGEventPost 到会话层）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
