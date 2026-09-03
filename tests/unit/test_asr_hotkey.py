"""ASR 热键监听 `_hotkey.py` — 单元测试（纯逻辑 + 状态机，不启动 CGEventTap）。

不调用 ``_HotkeyMonitor.start()`` 的真实路径（会触碰 CGEventTap / 辅助功能权限），
仅测试热键字符串解析、掩码换算、状态机翻转与 CG/CF 不可用时的降级分支。
"""

from __future__ import annotations

import json

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
