"""音频采集单元测试：read/start/stop 生命周期 / 设备检测容错。

sounddevice 依赖通过注入桩模块完全 mock，仅测试 AudioCapture 内部逻辑。
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ── TestAudioCapture ──────────────────────────────────────────

class TestAudioCapture:
    """AudioCapture 生命周期。"""

    @pytest.fixture(autouse=True)
    def _mock_sounddevice(self):
        """注入 sounddevice 桩模块并 mock，防止硬件访问。

        sounddevice 属可选依赖——源码 `_audio.py` 为函数内延迟导入（剪贴板
        模式不需要），CI 环境不安装该包。此处主动注入桩模块而非依赖真实包
        （原先 patch("sounddevice.xxx") 会强制 import 真实模块，CI 因此报
        ModuleNotFoundError），既保留测试覆盖，又不在 CI 引入 PortAudio 依赖。
        """
        stub = types.ModuleType("sounddevice")
        mock_stream_cls = MagicMock()
        mock_stream_cls.return_value = MagicMock()
        stub.InputStream = mock_stream_cls  # type: ignore[attr-defined]
        stub.query_devices = MagicMock(return_value=[  # type: ignore[attr-defined]
            {"name": "Test Mic", "max_input_channels": 2,
             "default_samplerate": 16000},
        ])
        with patch.dict(sys.modules, {"sounddevice": stub}):
            yield

    def test_read_returns_none_when_empty(self):
        """空缓冲区 read() 返回 None。"""
        from iris.assistant._audio import AudioCapture
        cap = AudioCapture()
        cap._stream = MagicMock()  # 跳过 start 的流初始化
        assert cap.read() is None

    def test_read_concatenates_and_drains(self):
        """回调写入后 read() 返回合并数据并清空缓冲区。"""
        from iris.assistant._audio import AudioCapture
        cap = AudioCapture()
        cap._stream = MagicMock()
        # 模拟回调写入两帧
        cap._buffer.append(np.ones(640, dtype=np.float32) * 0.1)
        cap._buffer.append(np.ones(640, dtype=np.float32) * 0.05)
        result = cap.read()
        assert result is not None
        assert len(result) == 1280  # 640 * 2
        assert result.dtype == np.float32
        # 缓冲区已清空
        assert len(cap._buffer) == 0
        assert cap.read() is None

    def test_start_stop_lifecycle(self):
        """start → stop 正确管理 InputStream。"""
        from iris.assistant._audio import AudioCapture
        cap = AudioCapture()
        cap.start()
        assert cap._stream is not None
        stream = cap._stream  # 保存引用（stop() 会置 None）
        stream.start.assert_called_once()
        cap.stop()
        stream.stop.assert_called_once()
        stream.close.assert_called_once()
        assert cap._stream is None

    def test_device_query_failure_non_fatal(self):
        """设备查询失败不影响 start。"""
        from iris.assistant._audio import AudioCapture
        import sounddevice as sd
        with patch.object(sd, "query_devices", side_effect=Exception("无设备")):
            cap = AudioCapture()
            # 不应抛异常
            cap.start()
            assert cap._stream is not None
            cap.stop()

    def test_callback_handles_status_warning(self):
        """回调 status 非空时记录警告日志。"""
        from iris.assistant._audio import AudioCapture
        import logging
        cap = AudioCapture()
        cap._stream = MagicMock()
        # 模拟带 status 的回调
        with patch.object(logging.getLogger("iris.assistant._audio"), "warning") as mock_warn:
            cap._on_audio(np.ones(640, dtype=np.float32), 640, None,
                          "input overflow")
            mock_warn.assert_called_once()
            assert "overflow" in str(mock_warn.call_args)

    def test_device_lost_after_consecutive_errors(self):
        """连续错误超过阈值后标记设备丢失。"""
        from iris.assistant._audio import AudioCapture
        cap = AudioCapture()
        cap._stream = MagicMock()
        # 连续 50 次错误回调
        for _ in range(50):
            cap._on_audio(np.ones(640, dtype=np.float32), 640, None,
                          "device unavailable")
        assert cap._device_lost is True

    def test_device_lost_warning_on_read(self):
        """设备丢失后 read() 日志警告。"""
        from iris.assistant._audio import AudioCapture
        import logging
        cap = AudioCapture()
        cap._stream = MagicMock()
        cap._device_lost = True
        with patch.object(logging.getLogger("iris.assistant._audio"), "warning") as mock_warn:
            cap.read()
            mock_warn.assert_called_once()
            assert "设备" in str(mock_warn.call_args)
