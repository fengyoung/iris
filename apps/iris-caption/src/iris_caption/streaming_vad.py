"""Fail-closed streaming FSMN bridge for installed funasr-onnx 0.4.2.

The convenience call swallows ONNX errors and has an unbound scorer on tiny
first chunks. Reuse its frontend/infer explicitly, with persistent caches.
"""
from __future__ import annotations

import math
from pathlib import Path


class _FrameHistory:
    """Absolute-indexed bounded history for vendor scorer's decibel[t]."""
    def __init__(self, capacity=4096):
        from collections import deque
        self.values = deque(maxlen=capacity)
        self.total = 0

    def append(self, value):
        self.values.append(value)
        self.total += 1

    def __getitem__(self, index):
        local = index - (self.total - len(self.values))
        if not 0 <= local < len(self.values):
            raise IndexError("expired VAD frame")
        return self.values[local]


class StreamingFsmnVad:
    def __init__(self, model_path, pause_s=.7, onset_s=.15):
        from funasr_onnx.vad_bin import Fsmn_vad_online
        from funasr_onnx.utils.utils import read_yaml
        path = Path(model_path).expanduser()
        quantize = (path / "model_quant.onnx").is_file()
        graph = "model_quant.onnx" if quantize else "model.onnx"
        if any(not (path / name).is_file() for name in (graph, "config.yaml", "am.mvn")):
            raise ValueError("Complete local FSMN ONNX model required")
        config = read_yaml(str(path / "config.yaml"))
        if config["frontend_conf"]["fs"] != 16000:
            raise ValueError("FSMN model must use 16 kHz audio")
        self.model = Fsmn_vad_online(str(path), quantize=quantize,
                                     max_end_sil=round(pause_s * 1000), intra_op_num_threads=2)
        self.model.config["model_conf"] = dict(config["model_conf"],
            sil_to_speech_time_thres=round(onset_s * 1000), max_single_segment_time=60000)
        self.reset()

    def reset(self):
        from funasr_onnx.utils.frontend import WavFrontendOnline
        from funasr_onnx.utils.e2e_vad import E2EVadModel
        self.frontend = WavFrontendOnline(cmvn_file=self.model.cmvn_file,
                                           **self.model.config["frontend_conf"])
        self.scorer = E2EVadModel(self.model.config["model_conf"])
        self.scorer.decibel = _FrameHistory()
        self.cache = self.model.prepare_cache([])
        import numpy as np
        self._pending_waveform = np.empty(0, dtype=np.float32)
        self.position = 0
        self.quiet = False

    def feed(self, samples, final=False):
        import numpy as np
        waveform = np.asarray(samples, dtype=np.float32)
        if waveform.ndim != 1 or not np.isfinite(waveform).all():
            raise ValueError("FSMN requires finite mono audio")
        # Bound frontend, scorer and history working sets even for offline input.
        endpoints = []
        for offset in range(0, max(1, len(waveform)), 1600):
            chunk = waveform[offset:offset + 1600]
            endpoints.extend(self._feed_chunk(chunk, final and offset + 1600 >= len(waveform)))
        return endpoints

    def _feed_chunk(self, waveform, final):
        import numpy as np
        self._pending_waveform = np.concatenate((self._pending_waveform, waveform))
        self.position += len(waveform)
        # Fewer than one 25ms feature frame cannot establish learned speech;
        # vendor finalization otherwise stacks an empty feature cache.
        if final and self.position < 400:
            return []
        try:
            feats, _ = self.model.extract_feat(self.frontend, waveform[None, :], final)
            if not feats.size:
                return []
            scores, self.cache = self.model.infer([feats, *self.cache])
            silence_ids = self.model.config["model_conf"].get("sil_pdf_ids", [0])
            self.quiet = bool(np.mean(scores[0][:, silence_ids].sum(axis=1)) > .8)
            # get_waveforms() includes the frontend's LFR right context again
            # on each call. Passing it directly makes decibel indices drift away
            # from score indices until bounded history evicts live frames.
            # Consume exactly one 10ms shift per score, retaining the 25ms
            # analysis-window overlap in our own PCM queue.
            frame_count = scores.shape[1]
            shift = self.frontend.frame_shift_sample_length
            window = self.frontend.frame_sample_length
            required = (frame_count - 1) * shift + window
            if len(self._pending_waveform) < required:
                if not final:
                    raise ValueError("FSMN scores exceed available waveform")
                self._pending_waveform = np.pad(
                    self._pending_waveform, (0, required - len(self._pending_waveform)))
            scored_waveform = self._pending_waveform[:required]
            self._pending_waveform = self._pending_waveform[frame_count * shift:].copy()
            result = self.scorer(scores, scored_waveform[None, :], is_final=final,
                                 max_end_sil=self.model.max_end_sil, online=True)
            # Vendor retains every emitted segment forever; only its active tail
            # is subsequently consulted. Compact already-consumed endpoints.
            consumed = self.scorer.output_data_buf_offset
            if consumed:
                del self.scorer.output_data_buf[:consumed]
                self.scorer.output_data_buf_offset = 0
            if not result:
                return []
            if len(result) != 1:
                raise ValueError("unexpected batch")
            endpoints = []
            for start, end in result[0]:
                if any(not isinstance(x, (int, float)) or not math.isfinite(x)
                       or x < -1 or x > self.position / 16 + 40 for x in (start, end)):
                    raise ValueError("invalid endpoint")
                endpoints.append((round(start * 16) if start >= 0 else -1,
                                  min(self.position, round(end * 16)) if end >= 0 else -1))
            return endpoints
        except Exception as exc:
            raise RuntimeError("fsmn_inference_failed") from exc
