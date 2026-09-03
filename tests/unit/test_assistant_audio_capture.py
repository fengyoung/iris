"""实时会议助理 — 音频侧纯逻辑单元测试（噪音门控 / MergeBuffer / 热词拼接 / RMS）。

MergeBuffer 用显式 `now` 浮点时间驱动，不依赖真实时钟。
"""

from __future__ import annotations

import numpy as np

from iris.assistant._audio_capture import (
    _MERGE_MAX_CHARS,
    Flush,
    MergeBuffer,
    hotwords_from_lines,
    is_noise,
    rms_of,
)


# ── is_noise ───────────────────────────────────────────────────


class TestIsNoise:
    def test_empty_and_whitespace(self):
        assert is_noise("")
        assert is_noise("   ")
        assert is_noise("\n\t")

    def test_single_char_repeat_six_or_more(self):
        assert is_noise("不不不不不不")
        assert is_noise("据据据据据据据据")
        # 嵌在句子里也拦
        assert is_noise("然后呃呃呃呃呃呃呃我们")

    def test_repeat_below_six_not_flagged_by_repeat_rule(self):
        # 5 次重复不触发重复规则；短句但含中文且 ≥2 字 → 走后续规则，均不命中
        assert not is_noise("不不不不不好意思刚才卡了一下")

    def test_one_char_or_less(self):
        assert is_noise("好")
        assert is_noise(" a ")
        assert is_noise("。")

    def test_zero_cjk_short_english(self):
        assert is_noise("yeah")
        assert is_noise("OK")
        assert is_noise("ststeteding")

    def test_zero_cjk_long_english_passes(self):
        # 零中文且 15~30 字 → 放行；>30 字零中文会再命中规则 5（占比 <0.1），
        # 即长英文整句实际也会被拦——这是当前行为，纯英文会议不在设计场景内
        assert not is_noise("A normal English line")
        assert is_noise("This is a normal English sentence")

    def test_sparse_cjk_in_long_text(self):
        # cjk≤2 且 >30 字且占比 <0.1 → 疑似代码/日志误识别
        text = "def foo(): return bar 的 baz qux quux corge grault"
        assert len(text) > 30
        assert is_noise(text)

    def test_normal_chinese_sentence(self):
        assert not is_noise("我们下周把方案评审排一下")
        assert not is_noise("好的")

    def test_mixed_chinese_english_sentence(self):
        assert not is_noise("这个 API 的 QPS 需要再压测一轮")
        assert not is_noise("把 Wiki 页面和 index 一起更新")


# ── MergeBuffer ────────────────────────────────────────────────


class TestMergeBufferBasics:
    def test_first_push_no_flush_and_pending(self):
        buf = MergeBuffer()
        assert not buf.pending
        assert buf.push("第一句", 100.0) == []
        assert buf.pending

    def test_second_push_within_window_merges(self):
        buf = MergeBuffer()
        buf.push("第一句", 100.0)
        assert buf.push("第二句", 102.0) == []  # gap 2.0 ≤ 3.0 且 ≤ 强信号阈值
        flushes = buf.drain()
        assert len(flushes) == 1
        assert flushes[0].texts == ["第一句", "第二句"]
        assert flushes[0].speaker_change_signal is False
        assert not buf.pending

    def test_drain_empty(self):
        assert MergeBuffer().drain() == []

    def test_drain_clears_buffer(self):
        buf = MergeBuffer()
        buf.push("一句", 1.0)
        buf.drain()
        assert buf.drain() == []


class TestMergeBufferSpeakerBoundary:
    def test_strong_gap_flushes_with_signal_and_opens_new_window(self):
        buf = MergeBuffer()
        buf.push("前一个人说的话", 100.0)
        flushes = buf.push("后一个人说的话", 102.5)  # gap 2.5 > 2.0 强信号
        assert len(flushes) == 1
        assert flushes[0] == Flush(["前一个人说的话"], speaker_change_signal=True)
        # 新句已开启新窗口
        assert buf.pending
        assert buf.drain()[0].texts == ["后一个人说的话"]

    def test_weak_gap_with_two_accumulated_flushes(self):
        buf = MergeBuffer()
        buf.push("句一", 100.0)
        buf.push("句二", 100.5)
        flushes = buf.push("句三", 102.0)  # gap 1.5 ∈ (0.8, 2.0]，已累积 2 句
        assert len(flushes) == 1
        assert flushes[0].texts == ["句一", "句二"]
        assert flushes[0].speaker_change_signal is True
        assert buf.drain()[0].texts == ["句三"]

    def test_weak_gap_with_single_accumulated_merges(self):
        buf = MergeBuffer()
        buf.push("句一", 100.0)
        flushes = buf.push("句二", 101.5)  # gap 1.5 弱信号，但只累积 1 句 → 不刷新
        assert flushes == []
        assert buf.drain()[0].texts == ["句一", "句二"]

    def test_gap_below_weak_threshold_does_not_trigger(self):
        buf = MergeBuffer()
        buf.push("句一", 100.0)
        buf.push("句二", 100.5)
        assert buf.push("句三", 101.25) == []  # gap 0.75 ≤ 0.8，不算边界（用可精确表示的浮点）
        assert buf.drain()[0].texts == ["句一", "句二", "句三"]


class TestMergeBufferRelaxedWindow:
    """gap ∈ (3, 6] 放宽窗口：注意 >2.0 已是强说话人信号，这里的判定
    只在强信号刷新之后、对空缓冲不生效，故通过间接路径观察不到合并；
    但 _should_merge 的短段逻辑对 `push` 之外也无入口，这里直接验证。"""

    def test_short_current_segment_keeps_waiting(self):
        buf = MergeBuffer()
        buf.push("前面说了一段比较长的内容超过十五字", 100.0)
        assert buf._should_merge(4.0, cur_len=5, prev_total=18) is True

    def test_long_current_with_long_prev_stops_merging(self):
        buf = MergeBuffer()
        buf.push("前面说了一段比较长的内容超过十五字", 100.0)
        assert buf._should_merge(4.0, cur_len=10, prev_total=18) is False

    def test_long_current_with_short_prev_keeps_waiting(self):
        buf = MergeBuffer()
        buf.push("短", 100.0)
        assert buf._should_merge(4.0, cur_len=10, prev_total=1) is True

    def test_beyond_relaxed_window_never_merges(self):
        buf = MergeBuffer()
        buf.push("短", 100.0)
        assert buf._should_merge(6.5, cur_len=1, prev_total=1) is False

    def test_empty_buffer_never_merges(self):
        assert MergeBuffer()._should_merge(0.1, cur_len=1, prev_total=0) is False

    def test_push_with_relaxed_gap_flushes_old_segment_without_signal_path(self):
        """通过 push 观察：gap 4.0 属强说话人信号 → 旧段带 signal=True 刷出，
        新句独立开窗（放宽窗口的合并让位于说话人边界判定）。"""
        buf = MergeBuffer()
        buf.push("前面说了一段比较长的内容超过十五字", 100.0)
        flushes = buf.push("这句话长度超过八个字了", 104.0)
        assert len(flushes) == 1
        assert flushes[0].texts == ["前面说了一段比较长的内容超过十五字"]
        assert flushes[0].speaker_change_signal is True
        assert buf.drain()[0].texts == ["这句话长度超过八个字了"]


class TestMergeBufferMaxChars:
    def test_exceeding_max_chars_flushes_old_then_opens_new(self):
        buf = MergeBuffer()
        long_text = "字" * (_MERGE_MAX_CHARS - 2)
        buf.push(long_text, 100.0)
        flushes = buf.push("再来三个字", 100.5)  # 498 + 5 > 500
        assert len(flushes) == 1
        assert flushes[0].texts == [long_text]
        assert flushes[0].speaker_change_signal is False
        assert buf.drain()[0].texts == ["再来三个字"]

    def test_exactly_max_chars_still_merges(self):
        buf = MergeBuffer()
        buf.push("字" * 490, 100.0)
        assert buf.push("字" * 10, 100.5) == []
        assert sum(len(t) for t in buf.drain()[0].texts) == _MERGE_MAX_CHARS


class TestMergeBufferOnSilence:
    def test_silence_beyond_window_flushes_with_signal(self):
        buf = MergeBuffer()
        buf.push("说完了", 100.0)
        flushes = buf.on_silence(103.5)
        assert flushes == [Flush(["说完了"], speaker_change_signal=True)]
        assert not buf.pending

    def test_silence_within_window_keeps(self):
        buf = MergeBuffer()
        buf.push("说完了", 100.0)
        assert buf.on_silence(103.0) == []  # 恰好 3.0 不超
        assert buf.pending

    def test_silence_without_pending(self):
        assert MergeBuffer().on_silence(999.0) == []


# ── hotwords_from_lines ────────────────────────────────────────


class TestHotwordsFromLines:
    def test_within_limit_joins_as_is(self):
        assert hotwords_from_lines(["转转", "质检", "数据智能"], 100) == "转转 质检 数据智能"

    def test_exact_limit_not_truncated(self):
        s = "转转 质检"
        assert hotwords_from_lines(["转转", "质检"], len(s)) == s

    def test_over_limit_truncates_by_whole_lines(self):
        lines = ["转转", "质检", "数据智能部", "搜推体验"]
        # "转转 质检" = 5 字；下一行 "数据智能部" 需 5+1 → 11 > 8 → 截断
        result = hotwords_from_lines(lines, 8)
        assert result == "转转 质检"
        assert "数据智" not in result  # 不截半行

    def test_single_line_over_limit_returns_empty(self):
        assert hotwords_from_lines(["这一行本身就超过了限制长度"], 5) == ""

    def test_empty_lines(self):
        assert hotwords_from_lines([], 10) == ""


# ── rms_of ─────────────────────────────────────────────────────


class TestRmsOf:
    def test_all_zero(self):
        assert rms_of(np.zeros(1600, dtype=np.float32)) == 0.0

    def test_constant_half(self):
        assert rms_of(np.full(1600, 0.5, dtype=np.float32)) == 0.5

    def test_sign_independent(self):
        assert rms_of(np.full(8, -0.25, dtype=np.float32)) == 0.25

    def test_int16_input_promoted(self):
        # astype(float64) 保证整型输入不溢出
        chunk = np.array([30000, -30000], dtype=np.int16)
        assert rms_of(chunk) == 30000.0
