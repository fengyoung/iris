"""Standalone local audio ASR with required streaming FSMN endpoints.

Contextual ASR is non-streaming and has no word timestamps. Forced cuts carry
400ms overlap; conservative local alignment may retain duplicates rather than
delete ambiguous repetitions. No claim of lossless word-boundary alignment.
"""
from __future__ import annotations

import math
import queue
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path

from .segmentation import Segmenter
from .streaming_vad import StreamingFsmnVad

SAMPLE_RATE = 16000
BLOCK_SAMPLES = 640


@dataclass
class AudioWindow:
    kind: str
    segment_id: int
    start_sample: int
    end_sample: int
    samples: array
    revision: int
    reason: str = ""
    overlap_prefix_samples: int = 0



def list_devices():
    """Enumerate input devices without opening a recording stream."""
    import sounddevice as sd
    return [{"index": i, "name": d["name"]} for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0]


def validate_model(model_path):
    """Validate local files without downloads, exports or model-cache writes."""
    path = Path(model_path).expanduser()
    required = ("config.yaml", "tokens.json", "am.mvn")
    if not path.is_dir() or any(not (path / f).is_file() for f in required):
        raise ValueError("Select a complete local contextual Paraformer ONNX directory")
    acoustic = path / "model_quant.onnx"
    if not acoustic.is_file():
        acoustic = path / "model.onnx"
    encoder = path / "model_eb.onnx"
    if not encoder.is_file():
        encoder = path / "model_eb_quant.onnx"
    if not acoustic.is_file() or not encoder.is_file():
        raise ValueError("Contextual acoustic and hotword ONNX graphs are required")
    return path, acoustic, encoder


class FsmnSpeechVerifier:
    """Learned VAD on every bounded candidate window before ASR.

    Energy gating still proposes boundaries; FSMN rejects noise-only windows.
    Installed funasr-onnx's offline Fsmn_vad creates a new endpoint scorer on
    every call and finalizes its last feature chunk. Its result is batch-wrapped
    completed [start_ms, end_ms] intervals, not online [-1, end] / [start, -1]
    updates. Fresh encoder caches prevent earlier speech leaking into silence.
    """

    def __init__(self, model_path):
        from funasr_onnx.vad_bin import Fsmn_vad
        from funasr_onnx.utils.utils import read_yaml
        path = Path(model_path).expanduser()
        quantize = (path / "model_quant.onnx").is_file()
        graph = "model_quant.onnx" if quantize else "model.onnx"
        if any(not (path / name).is_file() for name in (graph, "config.yaml", "am.mvn")):
            raise ValueError("Complete local FSMN ONNX model required")
        config = read_yaml(str(path / "config.yaml"))
        if config["frontend_conf"]["fs"] != SAMPLE_RATE:
            raise ValueError("FSMN model must use 16 kHz audio")
        # Prevalidated files prevent this installed constructor's auto-export.
        self.model = Fsmn_vad(str(path), quantize=quantize, intra_op_num_threads=2)

    def has_speech(self, samples):
        import numpy as np
        waveform = np.asarray(samples, dtype=np.float32)
        if waveform.ndim != 1 or len(waveform) > 30 * SAMPLE_RATE or not np.isfinite(waveform).all():
            raise ValueError("FSMN requires finite mono audio of at most 30 seconds")
        if not len(waveform):
            return False
        if len(waveform) < 400:
            waveform = np.pad(waveform, (0, 400 - len(waveform)))
        result = self.model(waveform, param_dict={"in_cache": []})
        if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], list):
            raise RuntimeError("fsmn_inference_failed")
        speech = False
        for interval in result[0]:
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                raise RuntimeError("fsmn_inference_failed")
            start, end = interval
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in interval):
                raise RuntimeError("fsmn_inference_failed")
            if not 0 <= start <= end <= len(waveform) * 1000 / SAMPLE_RATE:
                raise RuntimeError("fsmn_inference_failed")
            speech = speech or end > start
        return speech


class ContextualOnnxAdapter:
    """Adapter for inspected funasr-onnx 0.4.2 and local contextual graphs.

    Stock constructor auto-exports mixed quantization pairs; eb_infer passes two
    inputs to a one-input graph. Bypass both using verified named ONNX tensors.
    Reuse the installed frontend/decoder, with no download or file conversion.
    """

    def __init__(self, model_path, hotwords="", vad_model_path=None):
        if not vad_model_path:
            raise ValueError("vad_required")
        try:
            self.vad = FsmnSpeechVerifier(vad_model_path)
        except ImportError:
            raise
        except Exception as exc:
            raise RuntimeError("vad_failed") from exc
        import json
        import numpy as np
        import onnxruntime as ort
        from funasr_onnx.paraformer_bin import ContextualParaformer
        from funasr_onnx.utils.frontend import WavFrontend
        from funasr_onnx.utils.utils import TokenIDConverter, read_yaml

        path, acoustic, encoder = validate_model(model_path)
        config = read_yaml(str(path / "config.yaml"))
        if config.get("model") != "ContextualParaformer" or config["frontend_conf"]["fs"] != SAMPLE_RATE:
            raise ValueError("Only 16 kHz ContextualParaformer is supported")
        with (path / "tokens.json").open(encoding="utf-8") as stream:
            tokens = json.load(stream)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.acoustic = ort.InferenceSession(str(acoustic), options, providers=["CPUExecutionProvider"])
        self.encoder = ort.InferenceSession(str(encoder), options, providers=["CPUExecutionProvider"])
        if [x.name for x in self.acoustic.get_inputs()] != ["speech", "speech_lengths", "bias_embed"]:
            raise ValueError("Unsupported contextual acoustic graph inputs")
        names = [x.name for x in self.encoder.get_inputs()]
        if names not in (["hotword"], ["hotword", "hotword_lengths"]):
            raise ValueError("Unsupported contextual hotword graph inputs")
        self.helper = ContextualParaformer.__new__(ContextualParaformer)
        self.helper.frontend = WavFrontend(cmvn_file=str(path / "am.mvn"), **config["frontend_conf"])
        self.helper.converter = TokenIDConverter(tokens)
        self.helper.pred_bias = config.get("model_conf", {}).get("predictor_bias", 0)
        vocab = {token: i for i, token in enumerate(tokens)}
        terms = hotwords.split() if isinstance(hotwords, str) else list(hotwords)
        if len(terms) > 256 or any(not isinstance(t, str) or not 1 <= len(t) <= 10 for t in terms):
            raise ValueError("Hotwords support at most 256 terms of 1–10 characters")
        if any(c not in vocab for term in terms for c in term):
            raise ValueError("A hotword contains characters outside the model vocabulary")
        rows = [[vocab[c] for c in term] for term in terms] + [[1]]
        lengths = np.asarray([len(row) - 1 for row in rows], dtype=np.int32)
        encoded = np.zeros((len(rows), 10), dtype=np.int32)
        for i, row in enumerate(rows):
            encoded[i, :len(row)] = row
        inputs = {"hotword": encoded}
        if len(names) == 2:
            inputs[names[1]] = lengths
        embedding = self.encoder.run(None, inputs)[0].transpose(1, 0, 2)
        self.bias = embedding[np.arange(len(rows)), lengths][None, :, :]

    def transcribe(self, samples):
        if self.vad is None:
            raise RuntimeError("vad_required")
        if not self.vad.has_speech(samples):
            return ""
        import numpy as np
        from funasr_onnx.utils.postprocess_utils import sentence_postprocess

        waveform = np.asarray(samples, dtype=np.float32)
        if len(waveform) < 400:
            waveform = np.pad(waveform, (0, 400 - len(waveform)))
        feats, lengths = self.helper.extract_feat([waveform])
        outputs = self.acoustic.run(None, {"speech": feats, "speech_lengths": lengths,
                                           "bias_embed": self.bias})
        decoded = self.helper.decode(outputs[0], outputs[1])
        value = sentence_postprocess(decoded[0])
        text = value[0] if isinstance(value, (tuple, list)) else value
        if not isinstance(text, str):
            raise ValueError("Unsupported contextual decoder result")
        return text.strip()


class StreamingResampler:
    """Stateful FIR low-pass plus interpolation and integer sample clock.

    Downsampling uses a 127-tap Kaiser FIR at 90% of target Nyquist. Its causal
    delay (63 source samples) is bounded and included in segment timing; no
    filter tail is added to recording duration. State resets across pauses.
    """

    def __init__(self, source_rate, target_rate=SAMPLE_RATE):
        if not 8000 <= source_rate <= 192000:
            raise ValueError("Unsupported input sample rate")
        self.source_rate = int(source_rate)
        self.target_rate = int(target_rate)
        self.consumed = 0
        self.produced = 0
        self.previous = None
        self._filter = None
        self._filter_state = None
        if source_rate > target_rate:
            from scipy.signal import firwin
            self._filter = firwin(127, .9 * target_rate / 2, fs=source_rate, window=("kaiser", 8.0))

    def feed(self, samples):
        if self._filter is not None:
            import numpy as np
            from scipy.signal import lfilter, lfilter_zi
            samples = np.asarray(samples, dtype=float)
            if not len(samples):
                return array("f")
            if self._filter_state is None:
                self._filter_state = lfilter_zi(self._filter, [1.]) * samples[0]
            samples, self._filter_state = lfilter(self._filter, [1.], samples, zi=self._filter_state)
        result = array("f")
        for sample in samples:
            current = float(sample)
            index = self.consumed
            if self.previous is None:
                self.previous = current
            while self.produced * self.source_rate <= index * self.target_rate:
                phase = self.produced * self.source_rate / self.target_rate
                fraction = phase - (index - 1)
                result.append(self.previous + fraction * (current - self.previous))
                self.produced += 1
            self.previous = current
            self.consumed += 1
        return result

    def flush(self):
        result = array("f")
        if self.previous is not None:
            while self.produced * self.source_rate < self.consumed * self.target_rate:
                result.append(self.previous)
                self.produced += 1
        self.previous = None
        self._filter_state = None
        return result


class CaptureBuffer:
    """Bounded callback queue; overflow is fatal and detectable, never silent."""

    def __init__(self, capacity=100, block_samples=BLOCK_SAMPLES):
        if not 1 <= capacity <= 750:
            raise ValueError("audio_queue_blocks must be between 1 and 750")
        self.queue = queue.Queue(maxsize=capacity)
        self.block_samples = block_samples
        self.failed = threading.Event()
        self.reason = ""

    def callback(self, indata, frames, timestamp, status):
        if status or frames > self.block_samples:
            self.reason = "audio_device_overflow" if status else "audio_block_too_large"
            self.failed.set()
            return
        try:
            self.queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            self.reason = "audio_backpressure"
            self.failed.set()


def _event(config, kind, text="", **fields):
    return {"type": kind, "session_id": config.get("session_id", ""),
            "segment_id": "", "revision": 0, "start_ms": 0, "end_ms": 0,
            "text": text, **fields}


def _emit(events, event):
    try:
        events.put(event, timeout=0.5)
    except queue.Full as exc:
        raise RuntimeError("event_backpressure") from exc


def _recognize(windows, adapter, events, config, on_final=None):
    for window in windows:
        text = adapter.transcribe(window.samples)
        alignment = "none"
        if window.kind == "final" and window.overlap_prefix_samples:
            from .boundary_alignment import DecodeWindow, reconcile_final
            owned_start = window.start_sample + window.overlap_prefix_samples
            owned = adapter.transcribe(window.samples[window.overlap_prefix_samples:])
            decision = reconcile_final(DecodeWindow(text, window.start_sample,
                                        window.end_sample, owned_start), owned_text=owned)
            text, alignment = decision.text, decision.status
        if not text and window.kind == "partial":
            continue
        kind = "discard" if not text else window.kind
        _emit(events, _event(config, kind, text,
                            segment_id=str(window.segment_id), revision=window.revision,
                            start_ms=(window.start_sample + window.overlap_prefix_samples) * 1000 // SAMPLE_RATE,
                            end_ms=window.end_sample * 1000 // SAMPLE_RATE,
                            reason=window.reason, boundary_alignment=alignment,
                            recognition_start_ms=window.start_sample * 1000 // SAMPLE_RATE))
        if kind == "final" and on_final is not None:
            on_final(window)


def _capture_loop(config, events, commands, adapter, segmenter, sd):
    from .recognition_scheduler import RecognitionScheduler
    speakers = None
    if config.get("speakers_enabled", False) is True:
        from .speaker_runtime import create_speaker_runtime
        def emit_speaker(event):
            # Optional metadata must never backpressure capture or recognition.
            try:
                events.put_nowait(event)
            except queue.Full:
                pass
        speakers = create_speaker_runtime(config, emit_speaker)
    def speaker_final(window):
        if speakers:
            speakers.submit_final(window)
        elif config.get("speakers_enabled", False) is True:
            emit_speaker({"type": "speaker", "session_id": config["session_id"],
                          "segment_id": str(window.segment_id), "audio_revision": window.revision,
                          "speaker_id": None, "label": None, "speaker_state": "unknown",
                          "reason": "unavailable", "overlap_intervals": []})

    scheduler = RecognitionScheduler(lambda w: _recognize(
        [w], adapter, events, config, speaker_final))
    try:
        _capture_scheduled(config, events, commands, segmenter, sd, scheduler, speakers)
    finally:
        scheduler.close(cancel=True)
        if speakers:
            speakers.close()


def _feed_capture(segmenter, scheduler, samples, speakers=None):
    if speakers:
        if segmenter.start is not None and speakers.poll_boundary(segmenter.position):
            segmenter.request_boundary(segmenter.position)
        speakers.feed(samples, segmenter.position + len(segmenter.pending))
    windows = segmenter.feed(samples)
    if speakers:
        for window in windows:
            if window.kind == "final":
                speakers.acknowledge_cut(window.end_sample)
                segmenter.requested_boundary = None
    scheduler.submit(windows)


def _capture_scheduled(config, events, commands, segmenter, sd, scheduler, speakers=None):
    device = config.get("device")
    rate = round(sd.query_devices(device, kind="input")["default_samplerate"])
    resampler = StreamingResampler(rate)
    block_samples = max(1, round(rate * .04))
    capture = CaptureBuffer(config.get("audio_queue_blocks", 100), block_samples)
    with sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                        blocksize=block_samples, device=device,
                        callback=capture.callback) as stream:
        _emit(events, _event(config, "status", "Recording", state="recording",
                            vad="streaming_fsmn+fsmn_verifier",
                            boundary_context="400ms_conservative_overlap"))
        paused = False
        while True:
            scheduler.raise_if_failed()
            if capture.failed.is_set():
                raise RuntimeError(capture.reason)
            try:
                command = commands.get_nowait()
            except queue.Empty:
                command = None
            if command is not None:
                kind = command.get("type") if isinstance(command, dict) else None
                if kind in ("pause", "stop"):
                    stream.stop()
                    while True:
                        try:
                            audio = capture.queue.get_nowait()
                        except queue.Empty:
                            break
                        _feed_capture(segmenter, scheduler, resampler.feed(audio), speakers)
                    _feed_capture(segmenter, scheduler, resampler.flush(), speakers)
                    windows = segmenter.pause() if kind == "pause" else segmenter.flush("stop")
                    if speakers:
                        speakers.acknowledge_cut(segmenter.position)
                    scheduler.submit(windows)
                    scheduler.drain()
                    if speakers:
                        speakers.suspend(timeout=2.0)
                    paused = True
                    if kind == "stop":
                        return
                    _emit(events, _event(config, "status", "Paused", state="paused"))
                elif kind == "resume" and paused:
                    segmenter.resume()
                    if speakers:
                        speakers.resume(segmenter.position)
                    stream.start()
                    paused = False
                    _emit(events, _event(config, "status", "Recording", state="recording"))
                elif kind not in ("pause", "resume", "stop"):
                    _emit(events, _event(config, "error", "Unknown audio command", code="invalid_command"))
            if not paused and not stream.active:
                raise RuntimeError("audio_device_stopped")
            try:
                audio = capture.queue.get(timeout=0.05)
            except queue.Empty:
                continue
            _feed_capture(segmenter, scheduler, resampler.feed(audio), speakers)


def run_audio_worker(config: dict, events, commands):
    """Spawn target with dict config and multiprocessing queues.

    Required: session_id, model_path. device selects sounddevice input, not GPU.
    Defaults: pause_s=.7, max_segment_s=11 (soft), hard_segment_s=19, partial_s=1.
    Required vad_model_path enables local FSMN streaming endpoints and verification;
    omission fails before opening input. Downsampling requires scipy.
    Use bounded queues and drain events continuously. Monitor process exit too:
    an output queue that stays full makes error delivery impossible and exits
    nonzero. Stop/pause wait for the current bounded-window inference to finish.
    """
    stage = "configuration"
    try:
        if not config.get("session_id") or not config.get("model_path"):
            raise ValueError("session_id and local model_path are required")
        if not config.get("vad_model_path"):
            raise ValueError("vad_required")
        stage = "model"
        vad = StreamingFsmnVad(config["vad_model_path"], pause_s=config.get("pause_s", .7))
        segmenter = Segmenter(vad, **{k: config[k] for k in
                                ("pause_s", "max_segment_s", "partial_s", "hard_segment_s") if k in config})
        _emit(events, _event(config, "status", "Loading local ASR model", state="loading"))
        stage = "model"
        adapter = ContextualOnnxAdapter(config["model_path"], config.get("hotwords", ""),
                                        config.get("vad_model_path"))
        import sounddevice as sd
        stage = "capture"
        _capture_loop(config, events, commands, adapter, segmenter, sd)
    except Exception as exc:
        # Vendor exceptions can contain hotwords or paths; never relay them.
        code = str(exc) if str(exc) in {"audio_backpressure", "event_backpressure", "recognition_backpressure", "recognition_timeout",
                                       "audio_device_overflow", "audio_block_too_large",
                                       "audio_device_stopped", "vad_required", "vad_failed",
                                        "fsmn_inference_failed"} else f"{stage}_failed"
        if isinstance(exc, ImportError):
            code = "dependency_missing"
        messages = {
            "vad_required": "A local FSMN speech detector is required; select its model folder in Settings",
            "vad_failed": "Local FSMN speech detector is missing or incompatible; check its model folder in Settings",
            "fsmn_inference_failed": "Speech detector returned invalid output; recording stopped before recognition",
            "configuration_failed": "Invalid audio configuration; check session ID and timing limits",
            "model_failed": "Local contextual ONNX model is missing or incompatible; check graph files and hotwords",
            "capture_failed": "Audio capture or inference failed; check input device and 16 kHz support",
            "dependency_missing": "ASR dependencies unavailable; use an environment with sounddevice, numpy, funasr-onnx and onnxruntime",
        }
        _emit(events, _event(config, "error", messages.get(code, "Audio buffer or device failure; recording stopped"),
                            code=code, error_class=type(exc).__name__))
    finally:
        _emit(events, _event(config, "stopped", state="stopped"))
