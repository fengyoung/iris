"""ASR 热键监听 `_hotkey.py` — 单元测试（纯逻辑 + 状态机，不启动 CGEventTap）。

不调用 ``_HotkeyMonitor.start()`` 的真实路径（会触碰 CGEventTap / 辅助功能权限），
仅测试热键字符串解析、掩码换算、状态机翻转与 CG/CF 不可用时的降级分支。
"""

from __future__ import annotations

import json
import threading

import pytest

import iris.wiki.asr._hotkey as hk
from iris.wiki.asr._hotkey import (
    _HotkeyMonitor,
    _check_key,
    _check_modifiers,
    _flags_to_mask,
    _load_vocotype_hotkey,
    _parse_hotkey,
)


class TestParseHotkey:
    """_parse_hotkey：vocotype 热键字符串 → (modifiers_mask, key_code)。"""

    def test_empty_string(self):
        assert _parse_hotkey("") == (0, 0)

    def test_shift_control_keyz(self):
        assert _parse_hotkey("shift+control+KeyZ") == (512 | 4096, 6)

    def test_alt_keyv(self):
        assert _parse_hotkey("alt+KeyV") == (2048, 9)

    def test_right_option_alone(self):
        assert _parse_hotkey("rightOption") == (2048, 0)

    def test_cmd_shift_no_key(self):
        assert _parse_hotkey("cmd+shift") == (256 | 512, 0)

    def test_ctrl_f5_function_key(self):
        """"F5" lower→"f5"，去 "key" 后 capitalize→"F5"，拼 "KeyF5" 命中 96。"""
        assert _parse_hotkey("ctrl+F5") == (4096, 96)

    def test_keyf10_two_digit_function_key(self):
        assert _parse_hotkey("KeyF10") == (0, 109)

    def test_unknown_key_gives_zero_keycode(self):
        assert _parse_hotkey("foo") == (0, 0)
        assert _parse_hotkey("option+foo") == (2048, 0)

    def test_modifier_aliases_map_to_same_mask(self):
        for alias in ("option", "alt", "leftalt", "rightalt", "altright"):
            assert _parse_hotkey(alias) == (2048, 0)
        for alias in ("control", "ctrl", "rightcontrol"):
            assert _parse_hotkey(alias) == (4096, 0)
        for alias in ("command", "cmd", "rightcommand"):
            assert _parse_hotkey(alias) == (256, 0)

    def test_whitespace_around_parts_is_stripped(self):
        assert _parse_hotkey(" shift + KeyC ") == (512, 8)


class TestFlagsToMask:
    """_flags_to_mask：CGEventFlags 位图 → 修饰键掩码。"""

    def test_zero(self):
        assert _flags_to_mask(0) == 0

    def test_shift_flag(self):
        assert _flags_to_mask(0x00020000) == 512

    def test_option_and_command_flags(self):
        assert _flags_to_mask(0x00080000 | 0x00100000) == 2048 | 256

    def test_control_flag(self):
        assert _flags_to_mask(0x00040000) == 4096

    def test_unrelated_bits_ignored(self):
        assert _flags_to_mask(0x1 | 0x00000100 | 0x01000000) == 0


class TestCheckFunctionsWithoutCG:
    """_check_modifiers / _check_key：CoreGraphics 不可用时的降级。"""

    def test_check_modifiers_returns_zero_without_cg(self, monkeypatch):
        monkeypatch.setattr(hk, "_CG", None)
        assert _check_modifiers() == 0

    def test_check_key_returns_false_without_cg(self, monkeypatch):
        monkeypatch.setattr(hk, "_CG", None)
        assert _check_key(9) is False

    def test_check_key_zero_keycode_always_false(self):
        assert _check_key(0) is False


class TestHotkeyMonitorStateMachine:
    """_HotkeyMonitor：不 start，仅验证状态翻转与时间记录。"""

    def test_initial_state(self):
        m = _HotkeyMonitor(mask=2048, keycode=0)
        assert m.held is False
        assert m.released_at == 0.0
        assert m.hold_duration == 0.0

    def test_set_held_true_records_pressed_at(self):
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._set_held(True)
        assert m.held is True
        assert m._pressed_at > 0
        assert m.released_at == 0.0

    def test_set_held_false_after_true_records_release(self):
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._set_held(True)
        m._set_held(False)
        assert m.held is False
        assert m.released_at > 0
        assert m.hold_duration >= 0.0
        assert m.released_at >= m._pressed_at

    def test_repeated_true_does_not_reset_pressed_at(self):
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._set_held(True)
        first = m._pressed_at
        m._set_held(True)
        assert m._pressed_at == first

    def test_set_held_false_without_press_is_noop(self):
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._set_held(False)
        assert m.held is False
        assert m.released_at == 0.0
        assert m.hold_duration == 0.0

    def test_start_returns_false_without_cg(self, monkeypatch, capsys):
        monkeypatch.setattr(hk, "_CG", None)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        assert m.start() is False
        assert m._thread is None
        assert m._alive is False
        assert "CGEventTap 不可用" in capsys.readouterr().err

    def test_start_returns_false_without_cf(self, monkeypatch, capsys):
        monkeypatch.setattr(hk, "_CF", None)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        assert m.start() is False
        assert m._thread is None
        assert "CGEventTap 不可用" in capsys.readouterr().err

    def test_stop_without_start_does_not_raise(self):
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m.stop()
        assert m._alive is False

    def test_handle_event_first_event_prints_once_and_ignores_unknown_type(self, capsys):
        """未知事件类型不进任何分支；首事件确认只打印一次。"""
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._handle_event(99, None)
        m._handle_event(99, None)
        err = capsys.readouterr().err
        assert err.count("已收到首个事件") == 1
        assert m._first_event is True
        assert m.held is False


class _StubCG:
    """CGEventTapIsEnabled / CGEventTapEnable 的最小桩。

    方法用实例属性（普通函数对象）而非 def 绑定方法——生产代码会给它们
    赋 restype/argtypes，绑定方法对象不可赋属性。

    ``enable_effective=False`` 模拟已失效（invalidated）的 tap：
    ``CGEventTapEnable`` 是 void 调用，对其无效果也不报错。
    """

    def __init__(self, enabled: bool, enable_effective: bool = True):
        self.enabled = enabled
        self.enable_calls: list = []

        def _enable(tap, enable):
            self.enable_calls.append(enable)
            if self.enable_effective:
                self.enabled = bool(enable)

        self.enable_effective = enable_effective
        self.CGEventTapIsEnabled = lambda tap: self.enabled
        self.CGEventTapEnable = _enable


class TestTapHealth:
    """tap 运行期健康监测：事件时间戳 / enabled 查询 / 被禁用自愈 / 健康判定。

    背景：macOS 会在进程未及时服务事件或睡眠唤醒后主动禁用 event tap，
    此前失效是静默的——released_at 冻结在旧值，所有 ASR 文本被
    「不在监听窗口」跳过，进程照常运行没有任何失败信号。
    """

    def test_last_event_at_initial_zero(self):
        m = _HotkeyMonitor(mask=2048, keycode=0)
        assert m.last_event_at == 0.0

    def test_last_event_at_updated_on_any_event(self):
        """任意事件（含未知类型）都刷新时间戳；时间来源为 monotonic。"""
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._handle_event(99, None)
        assert m.last_event_at > 0

    def test_reenable_count_initial_zero(self):
        assert _HotkeyMonitor(mask=2048, keycode=0).reenable_count == 0

    def test_started_at_records_creation_time(self):
        """创建时刻有值且与 last_event_at 独立（后者初始为 0=从未收到事件）。"""
        m = _HotkeyMonitor(mask=2048, keycode=0)
        assert m.started_at > 0
        assert m.last_event_at == 0

    def test_is_tap_enabled_false_without_cg(self, monkeypatch):
        monkeypatch.setattr(hk, "_CG", None)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._tap = 12345
        assert m.is_tap_enabled() is False

    def test_is_tap_enabled_false_without_tap(self, monkeypatch):
        monkeypatch.setattr(hk, "_CG", _StubCG(enabled=True))
        m = _HotkeyMonitor(mask=2048, keycode=0)
        assert m.is_tap_enabled() is False  # _tap 未创建

    def test_is_tap_enabled_reflects_cg_state(self, monkeypatch):
        monkeypatch.setattr(hk, "_CG", _StubCG(enabled=False))
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._tap = 12345
        assert m.is_tap_enabled() is False
        monkeypatch.setattr(hk, "_CG", _StubCG(enabled=True))
        assert m.is_tap_enabled() is True

    def test_ensure_tap_enabled_reenables_and_counts(self, monkeypatch, capsys):
        """tap 被禁用 → 重新启用 → 回读确认 → 计数 + 告警；恢复后不再重试。"""
        stub = _StubCG(enabled=False)
        monkeypatch.setattr(hk, "_CG", stub)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._tap = 12345
        m._ensure_tap_enabled()
        m._ensure_tap_enabled()  # 已恢复 → 顶部短路，不再发 enable
        assert stub.enable_calls == [True]
        assert m.reenable_count == 1
        err = capsys.readouterr().err
        assert err.count("已自动重新启用") == 1

    def test_ensure_tap_enabled_ineffective_no_spam(self, monkeypatch, capsys):
        """enable 对已失效的 tap 无效果（void 调用不报错）→ 不计数不打日志。

        回归：此前无条件计数+打印，失效 tap 会让 run loop 每 0.5s 打一行
        「已自动重新启用」永远刷屏——错误的成功信号。
        """
        stub = _StubCG(enabled=False, enable_effective=False)
        monkeypatch.setattr(hk, "_CG", stub)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._tap = 12345
        for _ in range(10):  # 模拟 run loop 连续多个 0.5s 周期
            m._ensure_tap_enabled()
        assert len(stub.enable_calls) == 10
        assert m.reenable_count == 0
        assert "已自动重新启用" not in capsys.readouterr().err

    def test_ensure_tap_enabled_noop_when_enabled(self, monkeypatch):
        stub = _StubCG(enabled=True)
        monkeypatch.setattr(hk, "_CG", stub)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._tap = 12345
        m._ensure_tap_enabled()
        assert stub.enable_calls == []
        assert m.reenable_count == 0

    def test_ensure_tap_enabled_noop_without_tap(self, monkeypatch):
        stub = _StubCG(enabled=False)
        monkeypatch.setattr(hk, "_CG", stub)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._ensure_tap_enabled()  # 不抛异常即通过
        assert stub.enable_calls == []

    def test_is_healthy_requires_alive_thread_and_enabled_tap(self, monkeypatch):
        """健康 = 线程存活 且 tap 启用；任一不满足即 False。"""
        stub = _StubCG(enabled=True)
        monkeypatch.setattr(hk, "_CG", stub)
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._tap = 12345
        # 线程未启动
        assert m.is_healthy() is False
        done = threading.Event()
        m._alive = True
        m._thread = threading.Thread(target=done.wait, args=(0.5,))
        m._thread.start()
        try:
            assert m.is_healthy() is True
        finally:
            done.set()
            m._thread.join()
        # 线程结束后不再健康
        assert m.is_healthy() is False

    def test_is_healthy_false_when_tap_disabled(self, monkeypatch):
        """线程存活但 tap 被系统禁用（自愈恰好失败的窗口期）→ 不健康。"""
        monkeypatch.setattr(hk, "_CG", _StubCG(enabled=False))
        m = _HotkeyMonitor(mask=2048, keycode=0)
        m._tap = 12345
        m._alive = True
        done = threading.Event()
        m._thread = threading.Thread(target=done.wait, args=(0.5,))
        m._thread.start()
        try:
            assert m.is_healthy() is False
        finally:
            done.set()
            m._thread.join()


class TestLoadVocotypeHotkey:
    """_load_vocotype_hotkey：读取 VOCO_DIR/ui_settings.json。"""

    @pytest.fixture(autouse=True)
    def _voco_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr(hk, "VOCO_DIR", str(tmp_path))
        self.dir = tmp_path

    def test_missing_settings_file(self):
        assert _load_vocotype_hotkey() == (0, 0)

    def test_valid_settings(self):
        (self.dir / "ui_settings.json").write_text(
            json.dumps({"recording_hotkey": "alt+KeyV"}), encoding="utf-8",
        )
        assert _load_vocotype_hotkey() == (2048, 9)

    def test_settings_without_hotkey_key(self):
        (self.dir / "ui_settings.json").write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        assert _load_vocotype_hotkey() == (0, 0)

    def test_invalid_json(self):
        (self.dir / "ui_settings.json").write_text("{not json", encoding="utf-8")
        assert _load_vocotype_hotkey() == (0, 0)
