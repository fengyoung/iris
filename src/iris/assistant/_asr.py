"""ASR 引擎：FunASR Paraformer 本地识别（VAD + ASR + 标点 + 热词）。

完全独立于 iris.wiki.asr（零 import 依赖），通过配置文件指定模型路径和热词。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

_logger = logging.getLogger(__name__)


class ASREngine:
    """FunASR Paraformer 流式识别引擎。

    封装 vocotype 同款模型栈：
    - VAD: speech_fsmn_vad（语音活动检测）
    - ASR: speech_paraformer-large-contextual（中文 + 热词偏置）
    - 标点: punc_ct-transformer（自动加标点）
    """

    SAMPLE_RATE = 16000
    _MIN_SPEECH_SAMPLES = int(SAMPLE_RATE * 0.5)   # 至少 0.5s 音频才送入 ASR
    _MAX_BUFFER_SAMPLES = int(SAMPLE_RATE * 60)     # 缓冲区上限 60s（静音清空）

    def __init__(self, model_dir: str, hotwords: str = "", device: str = "cpu"):
        """初始化 ASR 引擎。

        Args:
            model_dir: ModelScope 模型缓存目录
            hotwords: 空格分隔的热词（如 "冯扬 转转 Iris"）
            device: ONNX 推理设备
        """
        self._model_dir = Path(model_dir)
        self._hotwords = hotwords
        self._device = device
        self._buffer: list[np.ndarray] = []
        self._model = self._init_model()

    def _init_model(self):
        """初始化 FunASR AutoModel（延迟导入，剪贴板模式不需要 funasr）。"""
        from funasr import AutoModel  # noqa: F811

        model_path = str(self._model_dir / "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx")
        vad_path = str(self._model_dir / "speech_fsmn_vad_zh-cn-16k-common-onnx")
        punc_path = str(self._model_dir / "punc_ct-transformer_zh-cn-common-vocab272727-onnx")

        _logger.info("加载 ASR 模型（device=%s）...", self._device)
        model = AutoModel(
            model=model_path,
            vad_model=vad_path,
            punc_model=punc_path,
            device=self._device,
            disable_pbar=True,
        )
        _logger.info("ASR 模型就绪 · 热词 %d 字", len(self._hotwords))
        return model

    def is_available(self) -> bool:
        """检查模型目录和文件是否完整。"""
        required = [
            "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx",
            "speech_fsmn_vad_zh-cn-16k-common-onnx",
            "punc_ct-transformer_zh-cn-common-vocab272727-onnx",
        ]
        for name in required:
            if not (self._model_dir / name).is_dir():
                return False
        return True

    def feed(self, audio: np.ndarray) -> Optional[str]:
        """喂入音频帧；若 VAD 检测到完整语音段则返回转写文本，否则返回 None。

        Args:
            audio: float32 数组，16kHz 单声道
        Returns:
            转写后的中文文本（含标点），或无语音时返回 None
        """
        self._buffer.append(audio)
        total = np.concatenate(self._buffer)

        # 缓冲区不足 0.5s → 等待更多音频
        if len(total) < self._MIN_SPEECH_SAMPLES:
            return None

        # 调用 ASR（内部 VAD 自动切段，返回有语音的段落）
        try:
            result = self._model.generate(
                input=total,
                hotword=self._hotwords or None,
                batch_size_s=60,
            )
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
