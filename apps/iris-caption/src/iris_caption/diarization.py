"""Optional session-local diarization prototype, deliberately not wired into UI.

Input must be a complete, ordered, single-turn utterance (<=10s, 16k mono).
No words are generated, corrected or split here. Short/interrupted, uncertain,
multi-speaker and overlapping utterances cannot update voice clusters.
A–P are session-local anonymous labels, never real-person identification.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from typing import Protocol

import numpy as np

from .speaker import SAMPLE_RATE, SegmentationFrames, unit_vector, validate_audio


@dataclass(frozen=True)
class SpeakerResult:
    label: str | None = None
    reason: str = "disabled"
    overlap: tuple[tuple[float, float], ...] = ()
    similarity: float | None = None


class Embedder(Protocol):
    def extract(self, audio: np.ndarray) -> np.ndarray: ...


class Segmenter(Protocol):
    def analyze(self, audio: np.ndarray) -> SegmentationFrames: ...


class StableClusters:
    """Conservative cosine clustering; uncertain gaps neither create nor update.

    Thresholds are provisional, NOT calibrated confidence/probabilities.
    A new voice needs two consistent clean observations before receiving a label.
    Existing labels never renumber/merge. No serialisation or disk writes.
    """

    def __init__(self, match: float = .72, novel: float = .40, margin: float = .12,
                 max_speakers: int = 8):
        if not 0 < novel < match < 1 or not 0 < margin < 1:
            raise ValueError("Invalid cosine thresholds")
        if isinstance(max_speakers, bool) or not isinstance(max_speakers, int) or not 1 <= max_speakers <= 16:
            raise ValueError("max_speakers must be 1..16")
        self.max_speakers = max_speakers
        self.match, self.novel, self.margin = match, novel, margin
        self._centroids: list[np.ndarray] = []
        self._candidate: np.ndarray | None = None

    @property
    def count(self) -> int:
        return len(self._centroids)

    def reset(self) -> None:
        # Best effort overwrite of owned vectors; Python/ORT cannot guarantee secure erasure.
        for vector in self._centroids:
            vector.fill(0)
        if self._candidate is not None:
            self._candidate.fill(0)
        self._centroids.clear()
        self._candidate = None

    def assign(self, embedding: np.ndarray) -> SpeakerResult:
        vector = unit_vector(embedding)
        if self._centroids and vector.shape != self._centroids[0].shape:
            raise ValueError("Embedding dimensions changed")
        scores = np.array([float(vector @ center) for center in self._centroids])
        if scores.size:
            order = np.argsort(scores)
            best = float(scores[order[-1]])
            runner_up = float(scores[order[-2]]) if len(scores) > 1 else -1.0
            if best >= self.match and best - runner_up >= self.margin:
                i = int(order[-1])
                # Slow adaptation only on strong, unambiguous matches.
                if best >= .85:
                    self._centroids[i] = unit_vector(.95 * self._centroids[i] + .05 * vector)
                self._candidate = None
                return SpeakerResult(chr(65 + i), "matched", similarity=best)
            if best > self.novel:
                self._candidate = None
                return SpeakerResult(reason="ambiguous", similarity=best)
        if len(self._centroids) >= self.max_speakers:
            return SpeakerResult(reason="capacity")
        if self._candidate is not None and float(vector @ self._candidate) >= self.match:
            self._centroids.append(unit_vector(self._candidate + vector))
            self._candidate = None
            return SpeakerResult(chr(64 + len(self._centroids)), "confirmed")
        self._candidate = vector.copy()
        return SpeakerResult(reason="unconfirmed")


def _intervals(mask: np.ndarray, centers: np.ndarray, duration: float) -> tuple[tuple[float, float], ...]:
    # Use actual 16.875ms model frame spacing, not utterance-length interpolation.
    edges = np.diff(np.r_[False, mask, False].astype(int))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return tuple((max(0., float(centers[a]) - .0084375),
                  min(duration, float(centers[b - 1]) + .0084375)) for a, b in zip(starts, ends))


class Diarizer:
    """Owned by one serialized worker. Explicit enable is required."""

    def __init__(self, embedder: Embedder, segmenter: Segmenter, *, enabled: bool = False,
                 max_speakers: int = 8):
        self.embedder, self.segmenter = embedder, segmenter
        self.enabled = enabled
        self.clusters = StableClusters(max_speakers=max_speakers)
        self._session_id: str | None = None
        self._enrolled_through = 0

    def reset(self) -> None:
        self.clusters.reset()
        self._session_id = None
        self._enrolled_through = 0

    def analyze_final(self, audio: np.ndarray, *, start_sample: int, session_id: str,
                      interrupted: bool = False) -> SpeakerResult:
        """Ordered disjoint finals <=20s; inspect all chunks before learning.

        Session changes require explicit reset, preventing stale callbacks from
        switching identities back into an old meeting. Unknown finals consume
        their range too, so retries cannot become independent enrollment evidence.
        """
        if not self.enabled:
            return SpeakerResult()
        x = np.asarray(audio, dtype=np.float32)
        if x.ndim != 1 or not 1 <= len(x) <= SAMPLE_RATE * 20 or start_sample < 0:
            raise ValueError("Expected <=20s mono final and nonnegative start")
        if self._session_id is not None and self._session_id != session_id:
            return SpeakerResult(reason="stale-session")
        if start_sample < self._enrolled_through:
            return SpeakerResult(reason="duplicate-range")
        # Validate entire PCM before consuming the final's range.
        chunks = [validate_audio(c) for c in np.array_split(x, max(1, int(np.ceil(len(x) / 64000))))]
        self._session_id = session_id
        self._enrolled_through = start_sample + len(x)
        vectors, overlaps, reasons = [], [], []
        offset = 0
        for chunk in chunks:
            result, vector = self._inspect(chunk, interrupted=interrupted)
            overlaps.extend((a + offset / SAMPLE_RATE, b + offset / SAMPLE_RATE) for a, b in result.overlap)
            reasons.append(result.reason)
            if vector is not None:
                vectors.append(vector)
            offset += len(chunk)
        if overlaps:
            return SpeakerResult(reason="overlap", overlap=tuple(overlaps))
        if len(vectors) != len(chunks):
            return SpeakerResult(reason=next(reason for reason in reasons if reason != "clean"))
        # Local slots cannot be compared across windows; compare actual embeddings.
        if any(float(a @ b) < self.clusters.match for i, a in enumerate(vectors) for b in vectors[i + 1:]):
            return SpeakerResult(reason="multiple-speakers")
        # Learn only after ALL disjoint chunks pass consistency/overlap checks.
        result = SpeakerResult(reason="unconfirmed")
        for vector in vectors:
            result = self.clusters.assign(vector)
        return result

    def analyze(self, audio: np.ndarray, *, interrupted: bool = False) -> SpeakerResult:
        if not self.enabled:
            return SpeakerResult()
        result, vector = self._inspect(validate_audio(audio), interrupted=interrupted)
        return self.clusters.assign(vector) if vector is not None else result

    def _inspect(self, x: np.ndarray, *, interrupted: bool) -> tuple[SpeakerResult, np.ndarray | None]:
        frames = self.segmenter.analyze(x)
        duration = len(x) / SAMPLE_RATE
        overlap = _intervals(frames.overlap >= .5, frames.centers, duration)
        if overlap:
            return SpeakerResult(reason="overlap", overlap=overlap), None
        if interrupted or duration < 1.5:
            return SpeakerResult(reason="interrupted" if interrupted else "short"), None
        # Even suspected overlap excludes learning; no need to invent two labels.
        if np.any(frames.overlap >= .25):
            return SpeakerResult(reason="uncertain-overlap"), None
        clean = (frames.probabilities[:, 1:4].max(axis=1) >= .7) & (frames.overlap < .1)
        if clean.sum() * .016875 < 1.5:
            return SpeakerResult(reason="insufficient-clean-speech"), None
        active_slots = np.argmax(frames.probabilities[:, 1:4], axis=1)[clean]
        if len(np.unique(active_slots)) != 1:
            return SpeakerResult(reason="multiple-speakers"), None
        # Only embed a contiguous clean region, never concatenate interrupted speech.
        regions = _intervals(clean, frames.centers, duration)
        start, end = max(regions, key=lambda item: item[1] - item[0])
        if end - start < 1.5:
            return SpeakerResult(reason="fragmented-speech"), None
        embedding = self.embedder.extract(x[int(start * SAMPLE_RATE):int(end * SAMPLE_RATE)])
        return SpeakerResult(reason="clean"), embedding


class DiarizationWorker:
    """One CPU worker, at most two owned PCM buffers (running + queued).

    submit returns None immediately on overload; caller labels unknown, never
    waits on the capture/UI thread. Results carry no text; parent must associate
    futures with session/utterance IDs and discard stale results on reset.
    close requests shutdown without blocking; completion signals erased clusters.
    """

    def __init__(self, diarizer: Diarizer):
        self.diarizer = diarizer
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="caption-speaker")
        self._slots = BoundedSemaphore(2)
        self._lock = Lock()
        self._closed = False
        self.completion: Future | None = None

    def submit(self, audio: np.ndarray, *, interrupted: bool = False) -> Future | None:
        with self._lock:
            if self._closed or not self._slots.acquire(blocking=False):
                return None
            try:
                owned = validate_audio(audio).copy()
                future = self._pool.submit(self._run, owned, interrupted)
            except BaseException:
                self._slots.release()
                raise
            def finished(_):
                owned.fill(0)  # Also erase if caller cancelled before worker execution.
                self._slots.release()

            future.add_done_callback(finished)
            return future

    def _run(self, audio: np.ndarray, interrupted: bool) -> SpeakerResult:
        try:
            if self._closed:
                return SpeakerResult(reason="closed")
            return self.diarizer.analyze(audio, interrupted=interrupted)
        finally:
            audio.fill(0)

    def close(self, timeout: float = 0) -> bool:
        """Request shutdown immediately; optional bounded wait, never kill ORT.

        completion resolves after queued buffers are erased and vectors reset.
        Callers must discard results as soon as closing starts.
        """
        if timeout < 0:
            raise ValueError("timeout must be nonnegative")
        with self._lock:
            if not self._closed:
                self._closed = True
                self.completion = self._pool.submit(self.diarizer.reset)
                self._pool.shutdown(wait=False)
            completion = self.completion
        try:
            completion.result(timeout=timeout)
            return True
        except TimeoutError:
            return False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
