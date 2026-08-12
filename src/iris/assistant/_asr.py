"""ASR 引擎：FunASR Paraformer PyTorch 本地识别（VAD + ASR + 标点 + 热词）。

使用 funasr.AutoModel + ModelScope 缓存。首次运行下载 ~913MB PyTorch 模型
到 ~/.cache/modelscope/，后续启动秒加载。完全独立于 vocotype（自管理模型）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

_logger = logging.getLogger(__name__)

_MODEL_ID = "iic/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404"
_PUNC_MODEL_DIR = "punc_ct-transformer_zh-cn-common-vocab272727-onnx"


class ASREngine:
    """FunASR Paraformer PyTorch 识别引擎（ContextualParaformer + 热词支持）。"""

    SAMPLE_RATE = 16000
    _MAX_BUFFER_SAMPLES = int(SAMPLE_RATE * 30)
    _SILENCE_FRAMES = 15
    _NOISE_FLOOR_ALPHA = 0.02

    _PUNC_LABEL_MAP = {0: "", 1: "", 2: "，", 3: "。", 4: "？", 5: "！"}

    def __init__(self, model_dir: str = "", hotwords: str = "",
                 device: str = "cpu", energy_threshold: float = 0):
        self._hotwords = hotwords
        self._device = device
        self._base_threshold = energy_threshold
        self._noise_floor = 0.0
        self._buffer: list[np.ndarray] = []
        self._silence_count = 0
        self._is_speaking = False
        self._speech_start_frame = 0
        self._total_frames = 0
        self._model = self._init_model()
        self._punc_session, self._punc_char_to_id = self._init_punc_model(model_dir)

    def _init_model(self):
        from funasr import AutoModel
        _logger.info("加载 Paraformer 模型…")
        model = AutoModel(model=_MODEL_ID, device=self._device, disable_pbar=True)
        _logger.info("Paraformer 就绪（热词 %d 字）", len(self._hotwords))
        return model

    @staticmethod
    def auto_detect_model_dir() -> Optional[str]:
        path = os.path.expanduser(
            "~/.cache/modelscope/hub/models/iic/"
            "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx"
        )
        return str(Path(path).parent) if Path(path).parent.is_dir() else None

    def is_available(self) -> bool:
        return self._model is not None

    @property
    def _effective_threshold(self) -> float:
        if self._noise_floor < 0.0001:
            return max(self._base_threshold, 0.005)
        return max(self._base_threshold, self._noise_floor * 2.0)

    def feed(self, audio: np.ndarray) -> Optional[str]:
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        if rms < 0.05:
            self._noise_floor = (self._NOISE_FLOOR_ALPHA * rms +
                                 (1 - self._NOISE_FLOOR_ALPHA) * self._noise_floor)
        threshold = self._effective_threshold
        self._total_frames += 1

        if rms > threshold:
            if not self._is_speaking:
                self._is_speaking = True
                self._speech_start_frame = self._total_frames
                self._buffer = []
            self._silence_count = 0
            self._buffer.append(audio)
        elif self._is_speaking:
            self._silence_count += 1
            self._buffer.append(audio)
            speech_duration = (self._total_frames - self._speech_start_frame) * 0.04
            if self._silence_count >= self._SILENCE_FRAMES or speech_duration > 15:
                self._is_speaking = False
                return self._transcribe()

        total_len = sum(len(b) for b in self._buffer)
        if total_len > self._MAX_BUFFER_SAMPLES:
            self._buffer = self._buffer[-self._MAX_BUFFER_SAMPLES:]
        return None

    def _transcribe(self) -> Optional[str]:
        if not self._buffer:
            return None
        total = np.concatenate(self._buffer)
        speech_len = len(total) / self.SAMPLE_RATE
        if speech_len < 0.5:
            self._buffer = []
            return None
        rms = float(np.sqrt(np.mean(total.astype(np.float64) ** 2)))
        _logger.info("🎙 转写中… (%.1fs, RMS=%.4f)", speech_len, rms)
        try:
            result = self._model.generate(
                input=total.flatten(),
                hotword=self._hotwords or None,
                batch_size_s=60,
            )
        except Exception as e:
            _logger.warning("ASR 转写异常: %s", e)
            self._buffer = []
            return None
        self._buffer = []
        if result and result[0].get("text"):
            text = result[0]["text"].strip()
            if text:
                punctuated = self._add_punctuation(text)
                _logger.info("📝 识别: %s", punctuated)
                return punctuated
        _logger.debug("ASR 返回空 (%.1fs)", speech_len)
        return None

    # ── 标点恢复（可选，复用 vocotype ONNX 缓存） ─────────

    def _init_punc_model(self, model_dir: str = ""):
        import json
        if model_dir:
            base = Path(model_dir).parent
        else:
            base = Path(self.auto_detect_model_dir() or "")
        punc_dir = base / _PUNC_MODEL_DIR if str(base) != "." else None
        if not punc_dir or not punc_dir.is_dir():
            return None, {}
        try:
            import onnxruntime as ort
            session = ort.InferenceSession(
                str(punc_dir / "model_quant.onnx"),
                providers=['CPUExecutionProvider'],
            )
            with open(punc_dir / "tokens.json") as f:
                token_list = json.load(f)
            char_to_id = {c: i for i, c in enumerate(token_list)}
            _logger.info("标点模型就绪（%d chars）", len(char_to_id))
            return session, char_to_id
        except Exception as e:
            _logger.warning("标点模型加载失败: %s", e)
            return None, {}

    def _add_punctuation(self, text: str) -> str:
        if not self._punc_session or not self._punc_char_to_id:
            return text
        try:
            token_ids = [self._punc_char_to_id.get(c, 0) for c in text]
            inputs = np.array([token_ids], dtype=np.int32)
            lengths = np.array([len(token_ids)], dtype=np.int32)
            outputs = self._punc_session.run(
                None, {"inputs": inputs, "text_lengths": lengths})
            preds = outputs[0][0].argmax(axis=1)
            result = []
            for i, char in enumerate(text):
                result.append(char)
                label = self._PUNC_LABEL_MAP.get(int(preds[i]), "")
                if label:
                    result.append(label)
            return "".join(result)
        except Exception:
            return text
