"""Optional bounded speaker inference, independent of capture and ASR."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import threading
import time


class SpeakerRuntime:
    """One daemon owns models; bounded finals outrank latest rolling audio."""

    def __init__(self, diarizer, rolling, session_id, emit, *, capacity=3):
        if not 2 <= capacity <= 4:
            raise ValueError("speaker capacity must be 2..4")
        self.diarizer, self.rolling = diarizer, rolling
        self.session_id, self.emit = session_id, emit
        self.capacity = capacity
        self._condition = threading.Condition(threading.RLock())
        self._finals = deque()
        self._pending = {}
        self._pcm = None
        self._boundaries = deque(maxlen=8)
        self._cut = self._epoch = 0
        self._suspended = self._closed = self._failed = False
        self._thread = threading.Thread(target=self._run, name="caption-speaker", daemon=True)
        self._thread.start()

    @classmethod
    def create(cls, config, emit):
        """Load hash-verified models before capture; invalid optional models degrade."""
        if config.get("speakers_enabled", False) is not True:
            return None
        try:
            from .speaker import CampPlusEmbedding, PyannoteOverlap
            from .diarization import Diarizer
            from .rolling_speakers import RollingSpeakerAnalyzer
            path = Path(config.get("speaker_model_path", "")).expanduser()
            embedder = CampPlusEmbedding(path / "campplus.onnx")
            segmentation = PyannoteOverlap(path / "segmentation.onnx")
            runtime = cls(Diarizer(embedder, segmentation, enabled=True),
                          RollingSpeakerAnalyzer(segmentation, config["session_id"], enabled=True),
                          config["session_id"], emit)
        except Exception:
            emit({"type": "speaker_status", "session_id": config.get("session_id", ""),
                  "state": "unavailable", "code": "speaker_model_unavailable",
                  "text": "说话人区分暂不可用，普通字幕继续；请检查模型安装。"})
            return None
        runtime._status("ready", "speaker_ready")
        return runtime

    def _status(self, state, code):
        self.emit({"type": "speaker_status", "session_id": self.session_id,
                   "state": state, "code": code,
                   "text": "说话人区分已就绪（试验）" if state == "ready" else
                   "说话人区分暂不可用，普通字幕继续；请检查模型安装。"})

    def _result(self, window, result=None, reason="unavailable"):
        start = window.start_sample + window.overlap_prefix_samples
        label = result.label if result else None
        overlap = getattr(result, "overlap", ())
        self.emit({"type": "speaker", "session_id": self.session_id,
                   "segment_id": str(window.segment_id), "audio_revision": window.revision,
                   "revision": window.revision, "speaker_id": label, "label": label,
                   "speaker_state": "overlap" if overlap else "confirmed" if label else "unknown",
                   "reason": result.reason if result else reason,
                   "start_ms": start * 1000 // 16000, "end_ms": window.end_sample * 1000 // 16000,
                   "overlap_intervals": [[round(start / 16 + a * 1000), round(start / 16 + b * 1000)]
                                         for a, b in overlap]})

    def submit_final(self, window):
        """Call only after ASR final entered its output queue."""
        with self._condition:
            if self._closed or self._failed or len(self._pending) >= self.capacity:
                self._result(window, reason="unavailable" if self._failed or self._closed else "overloaded")
                return False
            key = (window.segment_id, window.revision)
            self._pending[key] = window
            self._finals.append((key, window))
            self._condition.notify()
            return True

    def feed(self, samples, start_sample):
        import numpy as np
        if not len(samples):
            return
        with self._condition:
            if self._suspended or self._closed or self._failed:
                return
            pcm = np.asarray(samples, dtype=np.float32).copy()
            if self._pcm is not None:
                old, start = self._pcm
                if start + len(old) == start_sample:
                    pcm = np.concatenate((old, pcm))
                    start_sample = start
            if len(pcm) > 16000:
                start_sample += len(pcm) - 16000
                pcm = pcm[-16000:].copy()
            self._pcm = (pcm, start_sample)
            self._condition.notify()

    def poll_boundary(self, position):
        with self._condition:
            accepted = False
            while self._boundaries:
                epoch, event = self._boundaries.popleft()
                if (epoch == self._epoch and not self._suspended and
                        event.start_sample >= self._cut and event.detected_at_sample <= position):
                    accepted = True
            return accepted

    def acknowledge_cut(self, sample):
        with self._condition:
            self._cut = max(self._cut, sample)
            self._boundaries.clear()

    def suspend(self, timeout=2.0):
        """Bounded final drain; terminal unknowns and disable on timeout."""
        deadline = time.monotonic() + max(0, timeout)
        with self._condition:
            self._suspended = True
            self._epoch += 1
            self._pcm = None
            self._boundaries.clear()
            while self._pending and not self._failed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            if self._pending:
                self._failed = True
                for window in self._pending.values():
                    self._result(window, reason="timeout")
                self._pending.clear()
                self._finals.clear()
                self._status("unavailable", "speaker_timeout")

    def resume(self, position):
        with self._condition:
            self._epoch += 1
            self._cut = position
            self._suspended = False
            self._condition.notify()

    def close(self, timeout=2.0):
        self.suspend(timeout)
        with self._condition:
            self._closed = True
            self._condition.notify()

    def _run(self):
        expected = None
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._failed or self._finals or self._pcm is not None)
                    if self._closed or self._failed:
                        return
                    epoch, cut = self._epoch, self._cut
                    if self._finals:
                        key, window = self._finals.popleft()
                        pcm = None
                    else:
                        pcm, self._pcm = self._pcm, None
                        window = None
                if window is not None:
                    import numpy as np
                    start = window.start_sample + window.overlap_prefix_samples
                    samples = np.asarray(window.samples[window.overlap_prefix_samples:], dtype=np.float32)
                    result = self.diarizer.analyze_final(samples, start_sample=start, session_id=self.session_id,
                                                        interrupted=False)
                    with self._condition:
                        if key in self._pending:
                            self._result(window, result)
                            del self._pending[key]
                            self._condition.notify_all()
                else:
                    audio, start = pcm
                    if expected != (epoch, start):
                        self.rolling.reset(self.session_id, start_sample=start)
                    self.rolling.acknowledge_cut(cut)
                    results = self.rolling.feed(audio, start_sample=start)
                    expected = (epoch, start + len(audio))
                    with self._condition:
                        if epoch == self._epoch and not self._suspended:
                            self._boundaries.extend((epoch, event) for event in results if event.kind == "boundary")
        except Exception:
            with self._condition:
                self._failed = True
                for window in self._pending.values():
                    self._result(window, reason="inference-failed")
                self._pending.clear()
                self._finals.clear()
                self._pcm = None
                self._status("unavailable", "speaker_inference_failed")
                self._condition.notify_all()
        finally:
            self.diarizer.reset()
            self.rolling.reset(self.session_id)


def create_speaker_runtime(config, emit):
    return SpeakerRuntime.create(config, emit)
