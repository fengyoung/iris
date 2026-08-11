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
    _MAX_BUFFER_SAMPLES = int(SAMPLE_RATE * 60)     # 缓冲区上限 60s
    _ENERGY_THRESHOLD = 0.005                       # RMS 能量阈值（低于此值视为静音）
    _SILENCE_FRAMES = 15                            # 连续静音帧数 → 切段（15×40ms=600ms）

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
        self._silence_count = 0
        self._is_speaking = False
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
        """喂入音频帧；检测到完整语音段后返回转写文本，否则返回 None。

        内置能量检测 VAD：RMS > _ENERGY_THRESHOLD 视为语音，连续静音 _SILENCE_FRAMES
        帧后切段送 ASR。Paraformer 接收语音段（不含前后静音）时识别效果最佳。

        Args:
            audio: float32 数组，16kHz 单声道
        Returns:
            转写后的中文文本，或无语音时返回 None
        """
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))

        if rms > self._ENERGY_THRESHOLD:
            # 检测到语音
            if not self._is_speaking:
                self._is_speaking = True
                self._buffer = []  # 丢弃之前的静音
            self._silence_count = 0
            self._buffer.append(audio)
        elif self._is_speaking:
            # 语音中但当前帧静音 → 计数
            self._silence_count += 1
            self._buffer.append(audio)
            # 连续静音达到阈值 → 切段送 ASR
            if self._silence_count >= self._SILENCE_FRAMES:
                self._is_speaking = False
                return self._transcribe()
        # 静音且未在说话 → 丢弃（不累积静音数据）

        # 缓冲区过长保护
        total_len = sum(len(b) for b in self._buffer)
        if total_len > self._MAX_BUFFER_SAMPLES:
            self._buffer = []
            self._is_speaking = False

        return None

    def _transcribe(self) -> Optional[str]:
        """将当前缓冲区中的语音段送 ASR 转写。"""
        if not self._buffer:
            return None
        total = np.concatenate(self._buffer)
        speech_len = len(total) / self.SAMPLE_RATE
        if speech_len < 0.3:  # 太短，可能是噪音
            self._buffer = []
            return None
        try:
            result = self._model(total)
        except Exception as e:
            _logger.warning("ASR 转写异常: %s", e)
            self._buffer = []
            return None
        self._buffer = []
        if result and result[0].get("text"):
            text = result[0]["text"].strip()
            if text:
                return text
        return None

    @staticmethod
    def auto_detect_model_dir() -> Optional[str]:
        """自动检测 ASR 模型目录（vocotype 下载的 Paraformer 中文 contextual 模型）。

        检测顺序：
        1. ~/.cache/modelscope/hub/models/iic/speech_paraformer-large-contextual...onnx
        2. ~/.cache/modelscope/hub/models/iic（搜索含 model_quant.onnx 的子目录）
        """
        candidates = [
            os.path.expanduser(
                "~/.cache/modelscope/hub/models/iic/"
                "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx"
            ),
            os.path.expanduser("~/.cache/modelscope/hub/models/iic"),
        ]
        for path in candidates:
            p = Path(path)
            if p.is_dir():
                # 如果直接就是模型目录 → 直接返回
                if (p / "model_quant.onnx").is_file():
                    return str(p)
                # 如果是父目录 → 搜索子目录
                for sub in sorted(p.iterdir()):
                    if sub.is_dir() and (sub / "model_quant.onnx").is_file():
                        return str(sub)
        return None
