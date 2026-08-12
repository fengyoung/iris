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

    使用 vocotype 同款模型栈：
    - ASR: speech_paraformer-large-contextual（中文 + 热词偏置）
    - VAD: 自实现 RMS 能量检测
    - 标点: punc_ct-transformer（CT-Transformer 标点恢复）
    """

    SAMPLE_RATE = 16000
    _MAX_BUFFER_SAMPLES = int(SAMPLE_RATE * 5)      # 最长连续语音 5s（超时强制切段）
    _SILENCE_FRAMES = 15                            # 连续静音帧数 → 切段（15×40ms=600ms）
    _NOISE_FLOOR_ALPHA = 0.02                       # 噪声底限平滑系数

    _PUNC_MODEL_NAME = "punc_ct-transformer_zh-cn-common-vocab272727-onnx"

    # CT-Transformer 标点标签映射（FunASR 标准：0=pad 1=_ 2=， 3=。 4=？ 5=！）
    _PUNC_LABEL_MAP = {0: "", 1: "", 2: "，", 3: "。", 4: "？", 5: "！"}

    def __init__(self, model_dir: str, hotwords: str = "", device: str = "cpu",
                 energy_threshold: float = 0):
        """初始化 ASR 引擎。

        Args:
            model_dir: ModelScope 模型缓存目录
            hotwords: 空格分隔的热词
            device: ONNX 推理设备（cpu / mps）
            energy_threshold: RMS 阈值（0=自动校准+调试模式，>0 手动指定，
                              实际阈值 = max(threshold, noise_floor * 2)）
        """
        self._model_dir = Path(model_dir)
        self._hotwords = hotwords
        self._device = device
        self._base_threshold = energy_threshold
        self._noise_floor = 0.0       # 自适应噪声底限（平滑估计）
        self._buffer: list[np.ndarray] = []
        self._silence_count = 0
        self._is_speaking = False
        self._speech_start_frame = 0  # 当前语音段起始帧数
        self._total_frames = 0         # 总帧数（用于超时检测）
        self._model = self._init_model()
        self._punc_session, self._punc_char_to_id = self._init_punc_model()

    @property
    def _effective_threshold(self) -> float:
        """有效阈值 = max(手动阈值, 噪声底限 × 2)。启动初期用较高值防误触发。"""
        if self._noise_floor < 0.0001:
            return max(self._base_threshold, 0.005)  # 冷启动用较高值
        return max(self._base_threshold, self._noise_floor * 2.0)

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

        自适应 VAD：用平滑噪声底限动态计算阈值，环境噪音变化时自动适应。
        最长语音段 10s 强制切段（防持续噪音导致永不识别）。

        Args:
            audio: float32 数组，16kHz 单声道
        Returns:
            转写后的中文文本，或无语音时返回 None
        """
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        # 平滑更新噪声底限（只追踪低能量帧）
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
            # 条件 1：静音达到阈值 → 正常切段
            # 条件 2：语音超过 10s → 强制切段（高噪声环境）
            speech_duration = (self._total_frames - self._speech_start_frame) * 0.04
            if self._silence_count >= self._SILENCE_FRAMES or speech_duration > 5:
                self._is_speaking = False
                return self._transcribe()

        # 缓冲区保护
        total_len = sum(len(b) for b in self._buffer)
        if total_len > self._MAX_BUFFER_SAMPLES:
            self._buffer = self._buffer[-self._MAX_BUFFER_SAMPLES:]

        return None

    def _transcribe(self) -> Optional[str]:
        """将当前缓冲区中的语音段送 ASR 转写 + 标点恢复。"""
        if not self._buffer:
            return None
        total = np.concatenate(self._buffer)
        speech_len = len(total) / self.SAMPLE_RATE
        if speech_len < 0.3:
            _logger.debug("ASR 跳过：语音太短 (%.2fs)", speech_len)
            self._buffer = []
            return None
        rms = float(np.sqrt(np.mean(total.astype(np.float64) ** 2)))
        _logger.info("🎙 转写中… (%.1fs, RMS=%.4f)", speech_len, rms)
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
                punctuated = self._add_punctuation(text)
                _logger.info("📝 识别: %s", punctuated)
                return punctuated
        _logger.debug("ASR 返回空 (%.1fs)", speech_len)
        return None

    # ── 标点恢复 ──────────────────────────────────────────

    def _init_punc_model(self):
        """初始化 CT-Transformer 标点模型（ONNX 推理）。

        返回 (session, char_to_id) 或 (None, {})。
        """
        import json
        punc_dir = self._model_dir.parent / self._PUNC_MODEL_NAME
        if not punc_dir.is_dir():
            _logger.info("标点模型未找到 (%s)，跳过", punc_dir)
            return None, {}
        try:
            import onnxruntime as ort
            session = ort.InferenceSession(
                str(punc_dir / "model_quant.onnx"),
                providers=['CPUExecutionProvider'],
            )
            with open(punc_dir / "tokens.json") as f:
                token_list = json.load(f)
            # tokens.json 是 list，index 即 token ID
            char_to_id = {c: i for i, c in enumerate(token_list)}
            _logger.info("标点模型就绪（%d chars）", len(char_to_id))
            return session, char_to_id
        except Exception as e:
            _logger.warning("标点模型初始化失败（跳过）: %s", e)
            return None, {}

    def _add_punctuation(self, text: str) -> str:
        """对 ASR 输出文本追加标点符号。

        CT-Transformer ONNX 推理 → per-character 标点标签 → 插入标点。
        模型不可用时直接返回原文（不阻塞）。
        """
        if not self._punc_session or not self._punc_char_to_id:
            return text
        try:
            # char → token id
            token_ids = [self._punc_char_to_id.get(c, 0) for c in text]
            inputs = np.array([token_ids], dtype=np.int32)
            lengths = np.array([len(token_ids)], dtype=np.int32)
            outputs = self._punc_session.run(
                None, {"inputs": inputs, "text_lengths": lengths}
            )
            preds = outputs[0][0].argmax(axis=1)

            # 在预测标点位置插入对应符号
            result = []
            for i, char in enumerate(text):
                result.append(char)
                label = self._PUNC_LABEL_MAP.get(int(preds[i]), "")
                if label:
                    result.append(label)
            return "".join(result)
        except Exception as e:
            _logger.warning("标点恢复异常（返回原文）: %s", e)
            return text

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
