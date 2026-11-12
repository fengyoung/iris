"""Opt-in, local CPU speaker inference. No downloads or inference at import time.

Artifacts are provisioned by the application model manager, not this module.
CAM++ features follow sherpa-onnx's global-mean 80-bin Kaldi filterbanks.
Pyannote 3.0 outputs log probabilities over {}, {A}, {B}, {C}, {AB},
{AC}, {BC}; these LOCAL slots must never be used as global identities.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib

import numpy as np

SAMPLE_RATE = 16000
MAX_SECONDS = 10


@dataclass(frozen=True)
class ModelArtifact:
    id: str
    filename: str
    url: str
    sha256: str
    size: int
    license: str
    license_url: str

    def verify(self, path: Path) -> None:
        if path.stat().st_size != self.size:
            raise ValueError(f"Wrong model size: {self.id}")
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if digest != self.sha256:
            raise ValueError(f"Wrong model checksum: {self.id}")


SEGMENTATION = ModelArtifact(
    "pyannote-segmentation-3.0", "segmentation.onnx",
    "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/"
    "9403a6902bb58e3d5ae8c7e77c3422de279db2e0/model.onnx",
    "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
    5992913, "MIT",
    "https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/raw/"
    "9403a6902bb58e3d5ae8c7e77c3422de279db2e0/LICENSE",
)


CAMPPLUS = ModelArtifact(
    "campplus-zh-en-200k", "campplus.onnx",
    "https://huggingface.co/welcomyou/campplus-3dspeaker-200k-onnx/resolve/"
    "6265ff7af2a104d745b4389026ed9815c6c1c6ff/campplus_cn_en_common_200k.onnx",
    "dd1740aa1e1ffa3895f96aef2166b8af2bb2ad09c00769dd275ee36aef6a2a7f",
    28283928, "Apache-2.0",
    "https://raw.githubusercontent.com/modelscope/3D-Speaker/main/LICENSE",
)
MODEL_MANIFEST = (CAMPPLUS, SEGMENTATION)


def validate_audio(audio: np.ndarray) -> np.ndarray:
    """Require finite, normalized mono 16k PCM; never guess/resample channels."""
    x = np.asarray(audio, dtype=np.float32)
    if x.ndim != 1 or not 1 <= len(x) <= SAMPLE_RATE * MAX_SECONDS:
        raise ValueError("Expected 0–10 seconds of mono 16 kHz PCM")
    if not np.isfinite(x).all() or np.max(np.abs(x)) > 1.001:
        raise ValueError("PCM must be finite and normalized to [-1, 1]")
    return x


def _session(path: Path, artifact: ModelArtifact):
    import onnxruntime as ort

    artifact.verify(path)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def unit_vector(value: np.ndarray) -> np.ndarray:
    x = np.asarray(value, dtype=np.float32)
    if x.ndim != 1 or not x.size or not np.isfinite(x).all():
        raise ValueError("Invalid speaker embedding")
    norm = float(np.linalg.norm(x))
    if norm < 1e-8:
        raise ValueError("Empty speaker embedding")
    return x / norm


class CampPlusEmbedding:
    """192-dimensional anonymous voice descriptor; no biometric persistence."""

    def __init__(self, path: Path, artifact: ModelArtifact = CAMPPLUS):
        self.session = _session(path, artifact)
        self.input_name = self.session.get_inputs()[0].name

    def extract(self, audio: np.ndarray) -> np.ndarray:
        import kaldi_native_fbank as knf

        x = validate_audio(audio)
        if len(x) < SAMPLE_RATE * 1.5:
            raise ValueError("At least 1.5 seconds clean speech required")
        options = knf.FbankOptions()
        options.frame_opts.samp_freq = SAMPLE_RATE
        options.frame_opts.dither = 0.0
        options.mel_opts.num_bins = 80
        fbank = knf.OnlineFbank(options)
        fbank.accept_waveform(SAMPLE_RATE, x * 32768.0)
        fbank.input_finished()
        frames = np.stack([fbank.get_frame(i) for i in range(fbank.num_frames_ready)])
        frames -= frames.mean(axis=0, keepdims=True)
        output = self.session.run(None, {self.input_name: frames[None].astype(np.float32)})[0]
        return unit_vector(output[0])


@dataclass(frozen=True)
class SegmentationFrames:
    """Frame centers in seconds and powerset probabilities, NOT identities."""
    centers: np.ndarray
    probabilities: np.ndarray

    @property
    def overlap(self) -> np.ndarray:
        return self.probabilities[:, 4:7].sum(axis=1)

    @property
    def speech(self) -> np.ndarray:
        return 1.0 - self.probabilities[:, 0]


def decode_powerset(log_probabilities: np.ndarray, sample_count: int) -> SegmentationFrames:
    scores = np.asarray(log_probabilities, dtype=np.float32)
    if scores.ndim != 2 or scores.shape[1] != 7 or not np.isfinite(scores).all():
        raise ValueError("Expected finite pyannote 3.0 seven-class log probabilities")
    # Softmax is stable and valid for the exported LogSoftmax output.
    probabilities = np.exp(scores - scores.max(axis=1, keepdims=True))
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    centers = (np.arange(len(scores)) * 270 + 991 / 2) / SAMPLE_RATE
    valid = centers < sample_count / SAMPLE_RATE
    return SegmentationFrames(centers[valid], probabilities[valid])


class PyannoteOverlap:
    """Real overlap segmentation, not loudness/VAD heuristics. 10s context."""

    def __init__(self, path: Path):
        self.session = _session(path, SEGMENTATION)
        meta = self.session.get_modelmeta().custom_metadata_map
        if meta.get("model_type") != "pyannote-segmentation-3.0":
            raise ValueError("Unexpected segmentation model")
        self.input_name = self.session.get_inputs()[0].name

    def analyze(self, audio: np.ndarray) -> SegmentationFrames:
        x = validate_audio(audio)
        # Fixed training context; ignore output corresponding to right padding.
        padded = np.pad(x, (0, SAMPLE_RATE * MAX_SECONDS - len(x)))
        scores = self.session.run(None, {self.input_name: padded[None, None]})[0][0]
        return decode_powerset(scores, len(x))
