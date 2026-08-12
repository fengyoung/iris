"""音频采集：sounddevice 麦克风输入（16kHz mono float32）。

回调模式采集，外部轮询 read() 取走累积的音频帧。
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np

_logger = logging.getLogger(__name__)


class AudioCapture:
    """sounddevice 麦克风采集。

    使用 InputStream 回调模式：音频帧到达时自动追加到内部缓冲区，
    主循环通过 read() 取走累积数据。回调在独立的高优先级线程中执行，
    确保音频不丢帧。

    v3.25.1 增加设备热插拔容错：连续错误回调标记 _device_lost，
    read() 检测到设备丢失时日志告警，避免静默无输出。
    """

    SAMPLE_RATE = 16000   # 16kHz
    BLOCK_SIZE = 640      # 40ms @ 16kHz
    _MAX_CONSECUTIVE_ERRORS = 50  # 连续错误阈值（≈2s @ 40ms/帧）

    def __init__(self, sample_rate: int = 16000):
        import sounddevice as sd  # noqa: F811 — 延迟导入，剪贴板模式不需要
        self._sd = sd
        self._sample_rate = sample_rate
        self._stream: Optional[sd.InputStream] = None
        self._buffer: list[np.ndarray] = []
        self._consecutive_errors = 0
        self._device_lost = False

    # ── 公开接口 ──────────────────────────────────────────

    def start(self) -> None:
        """启动音频流（回调线程采集）。"""
        self._buffer = []
        # 检查可用设备
        try:
            devices = self._sd.query_devices()
            input_devices = [d for d in devices if d['max_input_channels'] > 0]
            if not input_devices:
                _logger.warning("未检测到麦克风设备！请检查系统音频设置")
            else:
                default = self._sd.query_devices(kind='input')
                _logger.info("麦克风: %s (%d Hz)",
                             default['name'], int(default['default_samplerate']))
        except Exception:
            pass  # 查询失败不影响启动

        self._stream = self._sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype=np.float32,
            blocksize=self.BLOCK_SIZE,
            callback=self._on_audio,
        )
        self._stream.start()
        self._start_time = time.monotonic()
        _logger.info("麦克风已启动（%d Hz, block %d samples）",
                     self._sample_rate, self.BLOCK_SIZE)

    def read(self) -> Optional[np.ndarray]:
        """取走缓冲区内所有音频帧，返回合并后的 float32 数组。

        无新数据时返回 None（调用方自行 sleep 后重试）。
        设备丢失时日志告警一次（避免洪水日志）。
        """
        if self._device_lost:
            _logger.warning("⚠ 音频设备已断开，请检查麦克风连接")
            self._device_lost = False  # 重置，允许恢复后重新检测
        if not self._buffer:
            return None
        data = np.concatenate(self._buffer)
        self._buffer = []
        # 重置连续错误计数（有数据到达说明设备恢复）
        self._consecutive_errors = 0
        return data.flatten()

    def stop(self) -> None:
        """停止音频流。"""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
            _logger.info("麦克风已停止")

    # ── 回调 ──────────────────────────────────────────────

    def _on_audio(self, indata: np.ndarray, frames: int,
                  timestamp, status) -> None:
        """音频回调（sounddevice 高优先级线程中执行）。"""
        if status:
            _logger.warning("音频采集异常: %s", status)
            self._consecutive_errors += 1
            if self._consecutive_errors >= self._MAX_CONSECUTIVE_ERRORS:
                self._device_lost = True
        else:
            self._consecutive_errors = 0
        self._buffer.append(indata.copy())
