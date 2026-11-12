#!/usr/bin/env python3
"""Benchmark local WAV input with real ASR, never a microphone or cloud ASR.

Example (from repository root):
 PYTHONPATH=apps/iris-caption/src python apps/iris-caption/scripts/benchmark_audio.py \
   --audio public.wav --model /local/contextual --vad /local/fsmn --output report.json

Processing is accelerated, not paced in real time. Compute timings cannot be
interpreted as measured microphone-to-subtitle latency. Output includes text;
use only audio whose transcript you intend to persist in the report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import subprocess
import sys
import time
import wave
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def hardware_info():
    def sysctl(name):
        try:
            return subprocess.check_output(["sysctl", "-n", name], text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            return None
    memory = sysctl("hw.memsize") if sys.platform == "darwin" else None
    return {"platform": platform.platform(), "architecture": platform.machine(),
            "chip": sysctl("machdep.cpu.brand_string") if sys.platform == "darwin" else platform.processor(),
            "physical_memory_bytes": int(memory) if memory else None,
            "python": sys.version, "python_executable": sys.executable}


def peak_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


def read_pcm(path):
    """Read bounded PCM WAV data; no audio conversion files are created."""
    import numpy as np
    with wave.open(str(path), "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        frames = source.getnframes()
        if width != 2 or source.getcomptype() != "NONE":
            raise ValueError("Benchmark input must be uncompressed 16-bit PCM WAV")
        if frames > rate * 600 or not 1 <= channels <= 8:
            raise ValueError("Benchmark input is limited to 10 minutes and 8 channels")
        raw = source.readframes(frames)
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32).reshape(-1, channels)
    return audio.mean(axis=1) / 32768, rate, channels


def percentiles(values):
    if not values:
        return {"p50": None, "p95": None}
    import numpy as np
    return {"p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95))}


def benchmark(audio_path, model_path, vad_path=None, adapter_factory=None, vad_factory=None):
    from iris_caption.audio import ContextualOnnxAdapter, Segmenter, StreamingResampler
    from iris_caption.streaming_vad import StreamingFsmnVad
    audio_path = Path(audio_path)
    audio, rate, channels = read_pcm(audio_path)
    start = time.perf_counter()
    factory = adapter_factory or ContextualOnnxAdapter
    adapter = factory(model_path, vad_model_path=vad_path)
    load_s = time.perf_counter() - start
    segmenter, resampler = Segmenter((vad_factory or StreamingFsmnVad)(vad_path)), StreamingResampler(rate)
    events = []
    processing_start = time.perf_counter()

    def process(windows):
        for window in windows:
            started = time.perf_counter()
            text = adapter.transcribe(window.samples)
            elapsed = time.perf_counter() - started
            events.append({"type": window.kind, "segment_id": window.segment_id,
                           "revision": window.revision, "start_ms": window.start_sample / 16,
                           "end_ms": window.end_sample / 16, "reason": window.reason,
                           "compute_s": elapsed, "processing_elapsed_s": time.perf_counter() - processing_start,
                           "text": text})

    block = max(1, round(rate * .04))
    for offset in range(0, len(audio), block):
        process(segmenter.feed(resampler.feed(audio[offset:offset + block])))
    process(segmenter.feed(resampler.flush()))
    process(segmenter.flush())
    processing_s = time.perf_counter() - processing_start
    duration = len(audio) / rate
    packages = {}
    for name in ("funasr-onnx", "onnxruntime", "numpy", "scipy"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"schema_version": 1, "mode": "accelerated_file_processing",
            "latency_disclaimer": "Compute-only timings; no real-time pacing, microphone, GUI, IPC queue or cloud correction. Not end-to-end subtitle latency.",
            "hardware": hardware_info(), "packages": packages,
            "audio": {"name": audio_path.name, "sha256": hashlib.sha256(audio_path.read_bytes()).hexdigest(),
                      "duration_s": duration, "sample_rate": rate, "channels": channels},
            "model": {"name": Path(model_path).name, "vad": Path(vad_path).name if vad_path else None},
            "model_load_s": load_s, "processing_s": processing_s,
            "processing_real_time_factor": processing_s / duration if duration else None,
            "peak_process_rss_bytes": peak_rss_bytes(),
            "partial_compute_s": percentiles([e["compute_s"] for e in events if e["type"] == "partial"]),
            "final_compute_s": percentiles([e["compute_s"] for e in events if e["type"] == "final"]),
            "transcript": "\n".join(e["text"] for e in events if e["type"] == "final" and e["text"]),
            "events": events,
            "limitations": ["One file is not an accuracy or endurance benchmark; no CER without reference transcript.",
                            "Energy-proposed boundaries with optional offline FSMN verification; forced cuts lack overlap.",
                            "Peak RSS is the whole process high-water mark, not model-only allocation."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--vad", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("--output must be a new file")
    result = benchmark(args.audio, args.model, args.vad)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({key: result[key] for key in ("model_load_s", "processing_s",
                     "processing_real_time_factor", "peak_process_rss_bytes", "transcript")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
