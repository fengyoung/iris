"""ASR 引擎：FunASR Paraformer PyTorch 本地识别（VAD + ASR + 标点 + 热词）。

主模型：funasr.AutoModel（PyTorch ContextualParaformer），首次运行下载 ~913MB
到 ~/.cache/modelscope/。标点模型：CT-Transformer ONNX（独立加载，缺失时降级）。
完全独立于 vocotype（自管理模型、零外部依赖）。
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


# ── funasr 热词日志过滤 ──────────────────────────────────────
# funasr 在 generate() 中使用裸 logging.info() 打印全部热词表（直写 root logger），
# 绕过所有命名 logger。Filter 以 record.pathname 区分来源，比临时 toggle root level
# 更安全（线程安全、零运行时开销、不会误吞其他 root-level 日志）。

class _FunasrLogFilter(logging.Filter):
    """过滤 funasr 源码直写 root logger 的 INFO/DEBUG 日志（热词泄露）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.WARNING:
            p = getattr(record, "pathname", "") or ""
            if p and ("/funasr/" in p or "\\funasr\\" in p):
                return False
        return True


_root = logging.getLogger()
_root.addFilter(_FunasrLogFilter())


class ASREngine:
    """FunASR Paraformer PyTorch 识别引擎（ContextualParaformer + 热词支持）。"""

    SAMPLE_RATE = 16000
    _MAX_BUFFER_SAMPLES = int(SAMPLE_RATE * 30)
    _SILENCE_FRAMES = 35   # 35 帧 × 40ms = 1.4s 静音触发切段（v3.25.1 从 15 放宽）
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
        """自动检测 ModelScope 缓存中的 Paraformer PyTorch 模型目录。

        返回模型缓存父目录（含 Paraformer 子目录和标点 ONNX 子目录）。
        """
        path = os.path.expanduser(
            "~/.cache/modelscope/hub/models/iic/"
            "speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404"
        )
        return str(Path(path).parent) if Path(path).parent.is_dir() else None

    @staticmethod
    def auto_detect_punc_model_dir() -> Optional[str]:
        """自动检测 ModelScope 缓存中的标点 CT-Transformer ONNX 模型目录。"""
        base = ASREngine.auto_detect_model_dir()
        if not base:
            return None
        punc_dir = Path(base) / _PUNC_MODEL_DIR
        return str(punc_dir) if punc_dir.is_dir() else None

    def is_available(self) -> bool:
        return self._model is not None

    @property
    def effective_threshold(self) -> float:
        """当前生效的能量阈值（公开只读，供心跳日志等外部观测使用）。"""
        if self._noise_floor < 0.0001:
            return max(self._base_threshold, 0.005)
        return max(self._base_threshold, self._noise_floor * 2.0)

    # 内部别名：旧代码引用 _effective_threshold 兼容
    _effective_threshold = effective_threshold

    @property
    def noise_floor(self) -> float:
        """当前估计的噪声地板 RMS 值（公开只读）。"""
        return self._noise_floor

    def feed(self, audio: np.ndarray) -> Optional[str]:
        """喂入音频，返回完成转写的文本（若有）。

        v3.25.5 修复尾部丢失：`mic.read()` 可能返回转写阻塞期间累积的
        大块音频（数秒）。此前把整块当一帧平均 RMS 判定——语音被周围静音
        稀释到阈值以下（真实场景阈值≈语音 RMS），整块被静默丢弃，表现为
        "随机尾部内容丢失"。现改为按 40ms 帧切片逐帧判定。
        """
        result = None
        frame_size = self.SAMPLE_RATE // 25  # 40ms = 640 samples
        for i in range(0, len(audio), frame_size):
            frame = audio[i:i + frame_size]
            if len(frame) < frame_size // 2:
                break  # 尾部不足半帧（<20ms）无意义
            out = self._feed_frame(frame)
            if out is not None:
                result = out
        return result

    def _feed_frame(self, audio: np.ndarray) -> Optional[str]:
        """单帧（40ms）VAD 判定。原 feed 逐帧逻辑。"""
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
            # 连续说话超过 15s 也强制切段（此前仅在静音分支检查，连续说话永不触发）
            speech_duration = (self._total_frames - self._speech_start_frame) * 0.04
            if speech_duration > 15:
                self._is_speaking = False
                return self._transcribe()
        elif self._is_speaking:
            self._silence_count += 1
            self._buffer.append(audio)
            speech_duration = (self._total_frames - self._speech_start_frame) * 0.04
            if self._silence_count >= self._SILENCE_FRAMES or speech_duration > 15:
                self._is_speaking = False
                return self._transcribe()

        total_len = sum(len(b) for b in self._buffer)
        if total_len > self._MAX_BUFFER_SAMPLES:
            # 从头部丢弃整块，直到总样本数 ≤ 上限（避免按样本索引切片
            # 把块数当样本数用的 bug）
            while self._buffer and sum(len(b) for b in self._buffer) > self._MAX_BUFFER_SAMPLES:
                self._buffer.pop(0)
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
        # 热词日志由模块级 _FunasrLogFilter 统一过滤（线程安全，零运行时开销）
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
            punc_dir = Path(model_dir).parent / _PUNC_MODEL_DIR
        else:
            punc_path = self.auto_detect_punc_model_dir()
            punc_dir = Path(punc_path) if punc_path else None
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
