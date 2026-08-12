"""ASR 引擎单元测试：VAD 状态机 / 能量阈值 / 标点恢复 / 热词注入。

硬件依赖（funasr / onnxruntime）全部 mock，仅测试纯逻辑路径。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from iris.assistant._asr import ASREngine


# ── 工具函数 ──────────────────────────────────────────────────

def _make_engine(hotwords: str = "测试 热词", energy_threshold: float = 0,
                 with_punc: bool = False):
    """构造 ASREngine（mock 掉 funasr/onnxruntime 加载）。"""
    mock_model = MagicMock()
    mock_model.generate.return_value = [{"text": "测试文本"}]
    if with_punc:
        mock_punc_session = MagicMock()
        mock_punc_session.run.return_value = (np.array([[[0.1] * 6] * 4]),)
        char_to_id = {"测": 0, "试": 1, "文": 2, "本": 3}
    else:
        mock_punc_session = None
        char_to_id = {}
    with patch.object(ASREngine, "_init_model", return_value=mock_model), \
         patch.object(ASREngine, "_init_punc_model", return_value=(mock_punc_session, char_to_id)):
        engine = ASREngine(hotwords=hotwords, energy_threshold=energy_threshold)
    return engine


def _noisy_frame(rms: float = 0.001, samples: int = 640) -> np.ndarray:
    """低能量噪声帧。"""
    return np.full(samples, rms, dtype=np.float32)


def _speech_frame(rms: float = 0.1, samples: int = 640) -> np.ndarray:
    """高能量语音帧。"""
    return np.full(samples, rms, dtype=np.float32)


def _feed_silence(engine: ASREngine, count: int) -> None:
    """连续喂入静音帧。"""
    for _ in range(count):
        result = engine.feed(_noisy_frame(0.001))
        if result is not None:
            raise AssertionError(f"静音帧意外触发转写: {result}")


def _feed_speech(engine: ASREngine, count: int, rms: float = 0.1) -> None:
    """连续喂入语音帧。"""
    for _ in range(count):
        result = engine.feed(_speech_frame(rms))
        if result is not None:
            raise AssertionError(f"语音帧意外触发转写: {result}")


# ── TestVAD ───────────────────────────────────────────────────

class TestVAD:
    """VAD 状态机：语音检测 / 静音切段 / 最大时长 / 短段丢弃 / 缓冲上限。"""

    def test_speech_detection(self):
        """RMS > 阈值时进入说话状态。"""
        engine = _make_engine(energy_threshold=0.01)
        assert engine._is_speaking is False
        engine.feed(_speech_frame(0.05))
        assert engine._is_speaking is True

    def test_noise_floor_adapts(self):
        """低 RMS 帧更新 noise_floor；高 RMS 帧不更新。"""
        engine = _make_engine()
        initial_nf = engine._noise_floor
        # 低 RMS 帧更新噪声基底
        engine.feed(_noisy_frame(0.001))
        assert engine._noise_floor != initial_nf
        updated = engine._noise_floor
        # 高 RMS 帧不更新噪声基底
        engine.feed(_speech_frame(0.1))
        assert engine._noise_floor == pytest.approx(updated)

    def test_silence_triggers_transcribe(self):
        """连续 35 帧静音（1.4s）后结束语音段并转写。"""
        engine = _make_engine(energy_threshold=0.01)
        engine._model.generate.return_value = [{"text": "你好世界"}]
        # 先触发语音开始
        engine.feed(_speech_frame(0.1))
        assert engine._is_speaking is True
        # 喂 34 帧极低噪声 → 不触发（base_threshold 防止噪声地板误升）
        for _ in range(34):
            result = engine.feed(_noisy_frame(0.00001))
            assert result is None
        assert engine._is_speaking is True
        # 第 35 帧静音（1.4s） → 触发转写
        result = engine.feed(_noisy_frame(0.00001))
        assert result == "你好世界"
        assert engine._is_speaking is False

    def test_max_duration_triggers_transcribe(self):
        """说话超过 15 秒强制切段（即使无静音）。"""
        engine = _make_engine(energy_threshold=0.01)
        engine._model.generate.return_value = [{"text": "长段文本"}]
        # speech_duration = (total_frames - speech_start_frame) * 0.04
        # 需要 > 15s → need > 375 frames after start → 1 + 376 + 1
        engine.feed(_speech_frame(0.1))  # frame 1: speech_start_frame=1
        for _i in range(375):            # frames 2-376: speech_duration = (376-1)*0.04 = 15.0
            result = engine.feed(_speech_frame(0.1))
            if result is not None:
                break  # 边界提前触发
        else:
            # frame 377: speech_duration = (377-1)*0.04 = 15.04 > 15
            result = engine.feed(_speech_frame(0.1))
            assert result == "长段文本"

    def test_short_segment_discarded(self):
        """< 0.5 秒的语音段在 _transcribe 中被丢弃（通过直接调用验证）。"""
        engine = _make_engine()
        engine._model.generate.return_value = [{"text": "嗯"}]
        # 直接构造不足 0.5s 的 buffer（正常 VAD 流最少 1.4s，但安全网保留）
        engine._buffer = [_speech_frame(0.1) for _ in range(5)]  # 5 × 640 / 16000 = 0.2s
        result = engine._transcribe()
        assert result is None  # 段长不足 0.5s，丢弃

    def test_buffer_capped_at_30s(self):
        """缓冲区超过 30 秒截断保留最新数据。"""
        engine = _make_engine(energy_threshold=0.01)
        engine._is_speaking = True
        # 30s = 750 帧 @ 40ms/帧 = 480,000 samples
        engine._buffer = [np.zeros(500_000, dtype=np.float32)]
        engine._buffer_total = 500_000  # v3.26.1 增量追踪
        engine.feed(_speech_frame(0.1))
        total = sum(len(b) for b in engine._buffer)
        assert total <= ASREngine._MAX_BUFFER_SAMPLES


# ── TestEnergyThreshold ───────────────────────────────────────

class TestEnergyThreshold:
    """能量阈值：base_threshold / noise_floor / 下限。"""

    def test_base_threshold_respected(self):
        """energy_threshold 参数被有效阈值采纳。"""
        engine = _make_engine(energy_threshold=0.03)
        # noise_floor 极低时，effective = max(base, 0.005) = 0.03
        engine._noise_floor = 0.00005
        assert engine.effective_threshold == 0.03

    def test_noise_floor_doubled(self):
        """noise_floor > 0.0001 时 effective = max(base, nf * 2)。"""
        engine = _make_engine(energy_threshold=0.005)
        engine._noise_floor = 0.01
        assert engine.effective_threshold == 0.02

    def test_zero_noise_floor_floors_at_0_005(self):
        """noise_floor 极低时 effective 不低��� 0.005。"""
        engine = _make_engine(energy_threshold=0)
        engine._noise_floor = 0.0
        assert engine.effective_threshold == 0.005

    def test_effective_threshold_readonly(self):
        """effective_threshold 是只读属性（公开 API）。"""
        engine = _make_engine()
        engine._noise_floor = 0.02
        val = engine.effective_threshold
        assert val == 0.04
        # noise_floor 是公开只读属性
        assert engine.noise_floor == 0.02


# ── TestPunctuation ───────────────────────────────────────────

class TestPunctuation:
    """标点恢复：正常插入 / 模型缺失降级 / 异常降级。"""

    def test_punctuation_inserted(self):
        """标点模型正常工作时插入中文标点。"""
        engine = _make_engine(with_punc=True)
        # mock 输出 label 序列：假设第 1 字后加逗号（label=2）
        engine._punc_session.run.return_value = (
            np.array([[[0, 0, 1, 0, 0, 0],   # 测 → 逗号（索引 2）
                       [0, 0, 0, 0, 0, 0],   # 试 → 无
                       [0, 0, 0, 1, 0, 0],   # 文 → 句号（索引 3）
                       [0, 0, 0, 0, 0, 0]]], dtype=np.float32),  # 本 → 无
        )
        result = engine._add_punctuation("测试文本")
        assert "测" in result
        assert "，" in result
        assert "。" in result

    def test_no_punc_model_falls_back(self):
        """无标点模型时返回原文。"""
        engine = _make_engine(with_punc=False)
        assert engine._add_punctuation("测试文本") == "测试文本"

    def test_punc_exception_falls_back(self):
        """ONNX 异常时返回原文。"""
        engine = _make_engine(with_punc=True)
        engine._punc_session.run.side_effect = RuntimeError("ONNX error")
        assert engine._add_punctuation("测试文本") == "测试文本"


# ── TestHotwords ──────────────────────────────────────────────

class TestHotwords:
    """热词注入：hotwords 参数传给 generate()。"""

    def test_hotwords_passed_to_generate(self):
        """构造时热词被传入 model.generate()。"""
        engine = _make_engine(hotwords="图像 算法 模型")
        # 触发转写
        engine._model.generate.return_value = [{"text": "测试"}]
        engine._is_speaking = True
        engine._buffer = [_speech_frame(0.1) for _ in range(int(0.6 * ASREngine.SAMPLE_RATE / 640) + 1)]
        engine._transcribe()
        call_kwargs = engine._model.generate.call_args
        assert call_kwargs is not None
        assert call_kwargs[1].get("hotword") == "图像 算法 模型"

    def test_empty_hotwords_passed_as_none(self):
        """空热词映射为 None 传入 generate()。"""
        engine = _make_engine(hotwords="")
        engine._model.generate.return_value = [{"text": "测试"}]
        engine._is_speaking = True
        engine._buffer = [_speech_frame(0.1) for _ in range(int(0.6 * ASREngine.SAMPLE_RATE / 640) + 1)]
        engine._transcribe()
        call_kwargs = engine._model.generate.call_args
        assert call_kwargs is not None
        assert call_kwargs[1].get("hotword") is None


# ── TestRootLoggerFilter ──────────────────────────────────────

class TestRootLoggerFilter:
    """root logger Filter 正确过滤 funasr 日志。"""

    def test_funasr_path_filtered(self):
        """funasr 路径的 INFO 日志被过滤。"""
        from iris.assistant._asr import _FunasrLogFilter
        f = _FunasrLogFilter()
        import logging
        record = logging.LogRecord(
            "root", logging.INFO, "/path/to/funasr/model.py", 100,
            "Hotword list: ...", (), None,
        )
        assert f.filter(record) is False

    def test_non_funasr_path_passes(self):
        """非 funasr 路径的 INFO 日志通过。"""
        from iris.assistant._asr import _FunasrLogFilter
        f = _FunasrLogFilter()
        import logging
        record = logging.LogRecord(
            "root", logging.INFO, "/path/to/iris/assistant/_asr.py", 100,
            "ASR 识别完成", (), None,
        )
        assert f.filter(record) is True

    def test_warning_always_passes(self):
        """即使 funasr 路径，WARNING 及以上日志不拦截。"""
        from iris.assistant._asr import _FunasrLogFilter
        f = _FunasrLogFilter()
        import logging
        record = logging.LogRecord(
            "root", logging.WARNING, "/path/to/funasr/model.py", 100,
            "Model warning", (), None,
        )
        assert f.filter(record) is True


# ── v3.25.5 修复: 转写阻塞期间音频不丢失 ──────────────

class TestChunkSlicing:
    """feed() 按帧切片：大块音频（转写阻塞期间累积）不被整块平均稀释丢弃。"""

    def test_speech_in_large_chunk_retained(self):
        """0.5s 语音混合在 2s 大 chunk 中，帧级判定保留语音（修复回归）。"""
        engine = _make_engine(energy_threshold=0.046)
        chunk = np.concatenate(
            [_noisy_frame(0.001)] * 12 +   # 0.48s 静音
            [_speech_frame(0.05)] * 12 +   # 0.48s 语音
            [_noisy_frame(0.001)] * 26     # 1.04s 静音
        )
        engine.feed(chunk)
        # 语音帧应触发 speaking，且语音保留在 buffer
        assert engine._is_speaking is True
        total = sum(len(b) for b in engine._buffer)
        assert total >= 640 * 12  # 至少包含 0.48s 语音

    def test_quiet_chunk_no_false_positive(self):
        """纯静音大 chunk 不触发语音（不误报）。"""
        engine = _make_engine(energy_threshold=0.046)
        chunk = np.concatenate([_noisy_frame(0.001)] * 50)  # 2s 静音
        engine.feed(chunk)
        assert engine._is_speaking is False
        assert not engine._buffer

    def test_mixed_silence_speech_transcribes(self):
        """大 chunk 中语音 + 后续静音 → 正确切段转写。"""
        engine = _make_engine(energy_threshold=0.046)
        engine._model.generate.return_value = [{"text": "新段内容"}]
        chunk = np.concatenate(
            [_noisy_frame(0.001)] * 12 +
            [_speech_frame(0.05)] * 12 +
            [_noisy_frame(0.001)] * 26
        )
        engine.feed(chunk)
        # 继续喂静音直到切段
        result = None
        for _ in range(35):
            r = engine.feed(_noisy_frame(0.001))
            if r:
                result = r
        assert result == "新段内容"
