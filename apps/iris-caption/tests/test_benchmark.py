"""Benchmark plumbing tests use synthetic files, no hardware or real model."""
import importlib.util
import wave
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_audio.py"
spec = importlib.util.spec_from_file_location("caption_benchmark", _SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_benchmark_measures_and_labels_compute_only(tmp_path):
    import numpy as np
    source = tmp_path / "synthetic.wav"
    data = (np.sin(np.arange(16000) * .1) * 10000).astype("<i2")
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(data.tobytes())

    class Adapter:
        def __init__(self, model_path, vad_model_path=None):
            pass

        def transcribe(self, samples):
            return "test speech"

    class Vad:
        quiet = False
        def __init__(self, path):
            self.position = 0
        def feed(self, samples, final=False):
            first = self.position == 0
            self.position += len(samples)
            return [(0, -1)] if first else []
        def reset(self):
            self.position = 0

    report = benchmark.benchmark(source, "/model", adapter_factory=Adapter, vad_factory=Vad)
    assert report["mode"] == "accelerated_file_processing"
    assert report["audio"]["duration_s"] == 1
    assert report["transcript"] == "test speech"
    assert [e["type"] for e in report["events"]] == ["partial", "final"]
    assert report["partial_compute_s"]["p50"] >= 0
    assert report["events"][-1]["end_ms"] == 1000
    assert "Not end-to-end" in report["latency_disclaimer"]


def test_rejects_non_pcm16(tmp_path):
    source = tmp_path / "eightbit.wav"
    with wave.open(str(source), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(16000)
        output.writeframes(b"\x80" * 100)
    with pytest.raises(ValueError, match="16-bit"):
        benchmark.read_pcm(source)


def test_refuses_to_overwrite_report(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(SystemExit):
        benchmark.main(["--audio", "unused.wav", "--model", "unused", "--output", str(output)])
    assert output.read_text() == "keep"
