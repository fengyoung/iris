"""ASR 校正引擎 — 单元测试（Aho-Corasick + AsrCorrector）。"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from iris.wiki.asr._types import AsrCorrection
from iris.wiki.asr.corrector import (
    _AhoCorasick,
    _is_asr_text,
    _parse_hotkey,
    _count_chinese,
    correct_text_static,
)


class TestAhoCorasick:
    def test_basic_replacement(self):
        ac = _AhoCorasick({"检测板": "剪切板"})
        result, applied = ac.replace_all("我写到检测板里头")
        assert "剪切板" in result
        assert "检测板" not in result
        assert len(applied) == 1

    def test_longest_match_first(self):
        """最长匹配优先：避免短模式先覆盖长模式。"""
        ac = _AhoCorasick({
            "数据湖": "数据仓库",
            "数据湖工程": "数据仓库工程",
        })
        result, applied = ac.replace_all("数据湖工程团队")
        # 应该先匹配 "数据湖工程"（更长），而非 "数据湖"
        assert "数据仓库工程" in result
        assert "数据仓库数据仓库" not in result  # 防止双重匹配

    def test_multiple_replacements(self):
        ac = _AhoCorasick({
            "李雷": "李蕾",
            "检测板": "剪切板",
            "智能画检测": "智能化检测",
        })
        result, applied = ac.replace_all("李雷在检测板上做智能画检测")
        assert "李蕾" in result
        assert "剪切板" in result
        assert "智能化检测" in result

    def test_no_match(self):
        ac = _AhoCorasick({"张三": "李四"})
        result, applied = ac.replace_all("今天天气真好")
        assert result == "今天天气真好"
        assert len(applied) == 0

    def test_empty_text(self):
        ac = _AhoCorasick({"A": "B"})
        result, applied = ac.replace_all("")
        assert result == ""

    def test_overlapping_patterns(self):
        """重叠模式：匹配一个后不应破坏后续匹配。"""
        ac = _AhoCorasick({"AB": "12", "BC": "34"})
        result, applied = ac.replace_all("ABC")
        # 匹配 "AB" 后从 "C" 继续，不应匹配 "BC"
        assert result == "12C"


class TestAsrTextDetection:
    def test_valid_chinese_short_text(self):
        assert _is_asr_text("我写到检测板里头")

    def test_valid_medium_text(self):
        assert _is_asr_text("我们今天讨论一下数据仓库的算法优化方案")

    def test_too_short(self):
        assert not _is_asr_text("好")

    def test_too_long(self):
        assert not _is_asr_text("长文本" * 200)

    def test_code_text(self):
        assert not _is_asr_text("def main(): print('hello world')")

    def test_url_text(self):
        assert not _is_asr_text("请访问 https://example.com 查看详情")

    def test_markdown_text(self):
        assert not _is_asr_text("# 标题\n这是一段 markdown 文本")

    def test_english_dominant(self):
        assert not _is_asr_text("hello world this is a test of english text")


class TestHotkeyParsing:
    def test_shift_control_z(self):
        mask, keycode = _parse_hotkey("shift+control+KeyZ")
        assert mask > 0  # 应有修饰键
        assert keycode > 0  # 应有键码

    def test_simple_shift(self):
        mask, keycode = _parse_hotkey("shift+KeyX")
        assert mask == 512  # shiftKey
        assert keycode > 0

    def test_empty(self):
        mask, keycode = _parse_hotkey("")
        assert mask == 0
        assert keycode == 0

    def test_case_insensitive(self):
        mask1, key1 = _parse_hotkey("Shift+Control+KeyZ")
        mask2, key2 = _parse_hotkey("shift+control+keyz")
        assert mask1 == mask2
        assert key1 == key2


class TestChineseCount:
    def test_pure_chinese(self):
        assert _count_chinese("你好世界") == 4

    def test_mixed(self):
        assert _count_chinese("hello世界123") == 2

    def test_no_chinese(self):
        assert _count_chinese("hello world") == 0

    def test_empty_string(self):
        assert _count_chinese("") == 0

    def test_punctuation_not_counted(self):
        assert _count_chinese("你好。世界！") == 4

    def test_kana_not_counted(self):
        assert _count_chinese("これはテストです") == 0


class TestCorrectTextStatic:
    def test_fast_mode_only(self):
        result, applied = correct_text_static(
            "我写到检测板里头",
            {"检测板": "剪切板"},
        )
        assert "剪切板" in result
        assert "检测板" not in result

    def test_no_dict_returns_original(self):
        result, applied = correct_text_static(
            "今天天气真好",
            {},
        )
        assert result == "今天天气真好"
        assert applied == []

    def test_with_mock_provider_success(self):
        """LLM provider 返回正确结果时使用 LLM 输出。"""
        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "修正后的文本"
        mock_provider.generate.return_value = mock_response

        result, applied = correct_text_static(
            "原始文本",
            {"原始": "修正"},
            llm_prompt="测试 prompt",
            provider=mock_provider,
        )
        assert "修正" in result

    def test_with_mock_provider_failure_falls_back(self):
        """LLM provider 异常时降级为词典结果。"""
        mock_provider = MagicMock()
        mock_provider.generate.side_effect = Exception("API error")

        result, applied = correct_text_static(
            "我写到检测板里头",
            {"检测板": "剪切板"},
            llm_prompt="测试 prompt",
            provider=mock_provider,
        )
        assert "剪切板" in result
        assert "检测板" not in result


class TestLLMSimilarityGate:
    """v3.24: LLM 输出相似度门槛——幻觉/答非所问降级为词典结果。"""

    def _make_corrector(self, mock_service):
        from iris.wiki.asr.corrector import AsrCorrector
        corrector = AsrCorrector(
            replace_dict={"检测板": "剪切板"},
            llm_prompt="你是 ASR 校正助手。输入文本：",
            mode="full",
        )
        corrector.set_llm_service(mock_service)
        return corrector

    def test_hallucination_falls_back_to_dict(self):
        """输出与输入相似度过低（<0.5）→ 视为幻觉，降级为词典结果。"""
        mock_service = MagicMock()
        mock_service.generate.return_value = SimpleNamespace(
            text="今天天气真好啊我们去公园散步吧",
            provider="mock", model="mock",
        )
        result, _ = self._make_corrector(mock_service).correct_full("我写到检测板里头")
        assert "剪切板" in result  # 词典结果保留
        assert "公园" not in result  # 幻觉内容未写入

    def test_normal_rewrite_passes(self):
        """相似润色（补标点/顺句）通过门槛。"""
        mock_service = MagicMock()
        mock_service.generate.return_value = SimpleNamespace(
            text="我写到剪切板里头，检查一下。",
            provider="mock", model="mock",
        )
        result, _ = self._make_corrector(mock_service).correct_full("我写到检测板里头")
        assert "检查一下" in result

    def test_length_bomb_still_blocked(self):
        """超长推理过程仍被长度启发式拦截（先于相似度检查）。"""
        mock_service = MagicMock()
        mock_service.generate.return_value = SimpleNamespace(
            text="让我分析一下这个文本的含义，首先我看到...然后继续分析..." * 20,
            provider="mock", model="mock",
        )
        result, _ = self._make_corrector(mock_service).correct_full("我写到检测板里头")
        assert "剪切板" in result


class TestAsrCorrectionType:
    def test_dataclass_defaults(self):
        c = AsrCorrection()
        assert c.timestamp == ""
        assert c.mode == "full"

    def test_dataclass_full_fields(self):
        c = AsrCorrection(
            timestamp="2026-01-01T00:00:00Z",
            raw_text="原始文本",
            fast_corrected="快速修正",
            full_corrected="完整修正",
            mode="full",
            corrections_applied=["A→B"],
            llm_time_ms=150,
        )
        assert c.raw_text == "原始文本"
        assert c.llm_time_ms == 150


class TestAsrTextDetectionEdgeCases:
    def test_empty_text(self):
        assert not _is_asr_text("")

    def test_whitespace_text(self):
        assert not _is_asr_text("   ")

    def test_mixed_chinese_english(self):
        """中文为主的混合文本应通过。"""
        assert _is_asr_text("我们今天讨论一下LLM的训练策略")

    def test_braces_single_char_passes(self):
        """单个花括号不应触发代码检测（需≥2个）。"""
        assert _is_asr_text("用花括号{}测试") or not _is_asr_text("{测试")

    def test_double_semicolons_blocked(self):
        """多个分号应被拦截。"""
        assert not _is_asr_text("a; b; c; d; e; f; g; h")


class TestListenWindow:
    """监听窗口：基础 3s + 长语音按按住时长放宽（上限 120s）。

    vocotype 为「松开热键后才开始转写」，1 分钟语音的转写+写剪贴板
    耗时远超固定 3s 窗口，因此窗口与说话时长挂钩。
    """

    def _win(self, hold):
        from iris.wiki.asr.corrector import _listen_window_sec
        return _listen_window_sec(hold)

    def test_no_hold_baseline(self):
        assert self._win(0.0) == 3.0

    def test_short_hold_keeps_baseline(self):
        assert self._win(1.5) == 3.0

    def test_long_hold_scales_with_speech(self):
        assert self._win(60.0) == 60.0

    def test_hold_capped_at_max(self):
        assert self._win(300.0) == 120.0

    def test_hold_exact_baseline(self):
        assert self._win(3.0) == 3.0


class TestHotkeyMonitorHoldDuration:
    """热键按住时长计算（纯字段逻辑，不依赖 CGEventTap）。"""

    def _make(self):
        from iris.wiki.asr.corrector import _HotkeyMonitor
        return _HotkeyMonitor(0, 0)

    def test_never_pressed(self):
        assert self._make().hold_duration == 0.0

    def test_pressed_but_not_released(self):
        m = self._make()
        with m._lock:
            m._pressed_at = 100.0
        assert m.hold_duration == 0.0

    def test_hold_duration_after_release(self):
        m = self._make()
        with m._lock:
            m._pressed_at = 100.0
            m._released_at = 160.0
        assert m.hold_duration == 60.0

    def test_release_without_press(self):
        m = self._make()
        with m._lock:
            m._released_at = 50.0
        assert m.hold_duration == 0.0


class TestListenWindowGateFallback:
    """热键监听器不可用（CGEventTap 启动失败）时降级为内容特征判定，不跳过。

    回归：此前 start() 失败只打印警告未置空 _hotkey_monitor，
    _tick 门控仍按配置 mask 判定 → in_listen_window 恒 False →
    所有剪贴板变化（含真实 ASR 输出）一律被「不在监听窗口」跳过。
    """

    def _make_corrector(self):
        from iris.wiki.asr.corrector import AsrCorrector
        return AsrCorrector({}, mode="fast")

    def test_monitor_none_does_not_skip(self, monkeypatch, capsys):
        import iris.wiki.asr.corrector as corrector_mod
        c = self._make_corrector()
        # 模拟：热键已配置但 CGEventTap 启动失败 → _hotkey_monitor 被置空
        c._hotkey_mask = 0x20000  # Shift，非零即可
        c._hotkey_monitor = None
        c._last_text = ""
        c._last_corrected = ""

        monkeypatch.setattr(
            corrector_mod, "_read_clipboard",
            lambda: "嗯就是那个然后我们继续做下去",
        )
        monkeypatch.setattr(corrector_mod, "_looks_like_written_chinese", lambda t: False)
        monkeypatch.setattr(corrector_mod, "_clipboard_has_rich_text", lambda: False)

        c._tick()
        err = capsys.readouterr().err
        assert "跳过" not in err  # 未被「不在监听窗口」拦截
        assert c._last_text == "嗯就是那个然后我们继续做下去"  # 已进入校正流程

    def test_monitor_available_and_out_of_window_still_skips(self, monkeypatch, capsys):
        """对照：监听器可用且超出窗口时仍应跳过（门控未被废掉）。

        time.monotonic 固定为 1000.0（消除对开机时长的依赖）：
        释放于 100.0 → elapsed=900s，超出任何窗口 → 必须跳过。
        """
        import iris.wiki.asr.corrector as corrector_mod
        from iris.wiki.asr.corrector import _HotkeyMonitor
        c = self._make_corrector()
        c._hotkey_mask = 0x20000
        monitor = _HotkeyMonitor(0x20000, 0)
        with monitor._lock:
            monitor._held = False
            monitor._pressed_at = 100.0
            monitor._released_at = 100.0  # 释放时刻距今远超窗口
        c._hotkey_monitor = monitor
        c._last_text = ""
        c._last_corrected = ""

        monkeypatch.setattr(corrector_mod.time, "monotonic", lambda: 1000.0)
        monkeypatch.setattr(
            corrector_mod, "_read_clipboard",
            lambda: "嗯就是那个然后我们继续做下去",
        )
        monkeypatch.setattr(corrector_mod, "_looks_like_written_chinese", lambda t: False)
        monkeypatch.setattr(corrector_mod, "_clipboard_has_rich_text", lambda: False)

        c._tick()
        err = capsys.readouterr().err
        assert "跳过：不在监听窗口" in err
        # _last_text 在门控前更新（防抖：跳过的内容标记为已见，避免下轮重复处理），
        # 未进入校正流程的信号是：没有词典处理输出
        assert "词典无命中" not in err and "✅" not in err

    def test_long_hold_keeps_window_open(self, monkeypatch, capsys):
        """长语音：1 分钟按住 → 窗口放宽到 60s，释放后 30s 剪贴板变化仍被处理。"""
        import iris.wiki.asr.corrector as corrector_mod
        from iris.wiki.asr.corrector import _HotkeyMonitor
        c = self._make_corrector()
        c._hotkey_mask = 0x20000
        monitor = _HotkeyMonitor(0x20000, 0)
        with monitor._lock:
            monitor._held = False
            monitor._pressed_at = 100.0
            monitor._released_at = 160.0  # 按住 60s，30s 前释放
        c._hotkey_monitor = monitor
        c._last_text = ""
        c._last_corrected = ""

        monkeypatch.setattr(corrector_mod.time, "monotonic", lambda: 190.0)
        monkeypatch.setattr(
            corrector_mod, "_read_clipboard",
            lambda: "嗯就是那个然后我们继续做下去",
        )
        monkeypatch.setattr(corrector_mod, "_looks_like_written_chinese", lambda t: False)
        monkeypatch.setattr(corrector_mod, "_clipboard_has_rich_text", lambda: False)

        c._tick()
        err = capsys.readouterr().err
        assert "跳过" not in err  # 30s < 窗口 60s → 放行
        assert c._last_text == "嗯就是那个然后我们继续做下去"

    def test_long_hold_window_expired_after_120s(self, monkeypatch, capsys):
        """长语音窗口上限 120s：释放后 130s（>120s）剪贴板变化仍被跳过。"""
        import iris.wiki.asr.corrector as corrector_mod
        from iris.wiki.asr.corrector import _HotkeyMonitor
        c = self._make_corrector()
        c._hotkey_mask = 0x20000
        monitor = _HotkeyMonitor(0x20000, 0)
        with monitor._lock:
            monitor._held = False
            monitor._pressed_at = 100.0
            monitor._released_at = 160.0
        c._hotkey_monitor = monitor
        c._last_text = ""
        c._last_corrected = ""

        monkeypatch.setattr(corrector_mod.time, "monotonic", lambda: 290.0)
        monkeypatch.setattr(
            corrector_mod, "_read_clipboard",
            lambda: "嗯就是那个然后我们继续做下去",
        )
        monkeypatch.setattr(corrector_mod, "_looks_like_written_chinese", lambda t: False)
        monkeypatch.setattr(corrector_mod, "_clipboard_has_rich_text", lambda: False)

        c._tick()
        err = capsys.readouterr().err
        assert "跳过：不在监听窗口" in err
        assert "词典无命中" not in err and "✅" not in err


class TestPidAlive:
    """对称互斥探测（v3.23.3）：asr-corrector 启动前检查 meeting-live-assistant。"""

    def _mod(self):
        import iris.wiki.asr.corrector as m
        return m

    def test_no_pid_file(self, tmp_path):
        assert self._mod()._pid_alive(tmp_path / "meeting-live-assistant.pid") is False

    def test_alive_pid(self, tmp_path):
        import os
        pid_file = tmp_path / "meeting-live-assistant.pid"
        pid_file.write_text(str(os.getpid()))
        # v3.24: 含 ps 命令行校验（防 PID 复用误判），mock 通过
        with patch("subprocess.run", return_value=SimpleNamespace(
                stdout="python /Users/dev/myproject/src/iris/app/main.py")):
            assert self._mod()._pid_alive(pid_file) is True

    def test_pid_reused_by_unrelated_process(self, tmp_path):
        """PID 被无关进程复用：存活但命令行不含 iris → 视为无实例。"""
        import os
        pid_file = tmp_path / "meeting-live-assistant.pid"
        pid_file.write_text(str(os.getpid()))
        with patch("subprocess.run", return_value=SimpleNamespace(
                stdout="/usr/bin/some-unrelated-daemon")):
            assert self._mod()._pid_alive(pid_file) is False

    def test_dead_pid(self, tmp_path):
        pid_file = tmp_path / "meeting-live-assistant.pid"
        pid_file.write_text("999999999")
        assert self._mod()._pid_alive(pid_file) is False

    def test_corrupt_pid(self, tmp_path):
        pid_file = tmp_path / "meeting-live-assistant.pid"
        pid_file.write_text("not-a-pid")
        assert self._mod()._pid_alive(pid_file) is False
