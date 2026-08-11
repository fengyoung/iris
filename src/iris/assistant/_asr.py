"""ASR 引擎：FunASR Paraformer 本地 ONNX 识别（VAD + ASR + 标点 + 热词）。

使用 funasr_onnx（轻量 ONNX 推理，无需 PyTorch），直接加载 vocotype 已缓存的
ModelScope ONNX 模型文件。完全独立于 iris.wiki.asr。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

_logger = logging.getLogger(__name__)


class ASREngine:
    """FunASR Paraformer ONNX 识别引擎。

    使用 vocotype 同款模型：speech_paraformer-large-contextual（中文 + 热词偏置）。
    VAD 和标点暂时跳过（Paraformer 自身对静音鲁棒，标点后续补充）。
    """

    SAMPLE_RATE = 16000
    _MIN_SPEECH_SAMPLES = int(SAMPLE_RATE * 0.5)   # 至少 0.5s 音频才送入 ASR
    _MAX_BUFFER_SAMPLES = int(SAMPLE_RATE * 60)     # 缓冲区上限 60s

    def __init__(self, model_dir: str, hotwords: str = "", device: str = "cpu"):
        """初始化 ASR 引擎。

        Args:
            model_dir: ModelScope 模型缓存目录（含 model_quant.onnx / config.yaml / tokens.json）
            hotwords: 空格分隔的热词（如 "冯扬 转转 Iris"）
            device: ONNX 推理设备（cpu / mps）
        """
        self._model_dir = Path(model_dir)
        self._hotwords = hotwords
        self._device = device
        self._buffer: list[np.ndarray] = []
        self._model = self._init_model()

    def _init_model(self):
        """初始化 FunASR ONNX Paraformer（延迟导入）。"""
        from funasr_onnx import Paraformer

        model_path = str(self._model_dir)
        device_id = -1 if self._device == "cpu" else 0

        _logger.info("加载 ASR 模型（device=%s）...", self._device)
        model = Paraformer(
            model_dir=model_path,
            batch_size=1,
            device_id=device_id,
            quantize=True,  # 使用 model_quant.onnx
            intra_op_num_threads=4,
        )
        _logger.info("ASR 模型就绪 · 热词 %d 字", len(self._hotwords))
        return model

    def is_available(self) -> bool:
        """检查模型目录和文件是否完整。"""
        required = ["model_quant.onnx", "config.yaml", "tokens.json", "am.mvn"]
        for name in required:
            if not (self._model_dir / name).is_file():
                return False
        return True

    def feed(self, audio: np.ndarray) -> Optional[str]:
        """喂入音频帧；若有语音则返回转写文本，否则返回 None。

        Args:
            audio: float32 数组，16kHz 单声道
        Returns:
            转写后的中文文本，或无语音时返回 None
        """
        self._buffer.append(audio)
        total = np.concatenate(self._buffer)

        # 缓冲区不足 0.5s → 等待更多音频
        if len(total) < self._MIN_SPEECH_SAMPLES:
            return None

        # 调用 ASR
        try:
            result = self._model(total)
        except Exception as e:
            _logger.warning("ASR 转写异常: %s", e)
            self._buffer = []
            return None

        if result and result[0].get("text"):
            text = result[0]["text"].strip()
            if text:
                self._buffer = []
                return text

        # 缓冲区过长（>60s 无语音）→ 清空防止内存膨胀
        if len(total) > self._MAX_BUFFER_SAMPLES:
            self._buffer = []

        return None

    @staticmethod
    def auto_detect_model_dir() -> Optional[str]:
        """自动检测 ModelScope 缓存路径（vocotype 下载的模型）。

        检测顺序：
        1. ~/.cache/modelscope/hub/models/iic
        2. ~/.cache/modelscope/hub/models
        """
        candidates = [
            os.path.expanduser("~/.cache/modelscope/hub/models/iic"),
            os.path.expanduser("~/.cache/modelscope/hub/models"),
        ]
        for path in candidates:
            p = Path(path)
            if p.is_dir() and any(p.iterdir()):
                return str(p)
        return None
