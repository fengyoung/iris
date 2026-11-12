"""Bounded pure rolling detector; call on a serialized background worker only.

No identity clustering here: pyannote slots are local to ONE inference window.
Parent owns asynchronous backpressure, result queue and generation filtering.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .diarization import Segmenter, _intervals
from .speaker import SAMPLE_RATE, SegmentationFrames, validate_audio


@dataclass(frozen=True)
class SpeakerEvent:
    kind: str
    session_id: str
    start_sample: int
    end_sample: int
    detected_at_sample: int


class RollingSpeakerAnalyzer:
    """4s ring, 1s hop; ~0.2s clean dwell either side of a transition.

    feed accepts <=1s PCM; start_sample is an absolute monotonic capture clock.
    Gaps clear context, never compare local slots across windows. Detection
    latency includes the 1s hop and is not a sample-accurate source separation.
    acknowledge_cut must receive the ACTUAL parent cut, especially if late.
    """

    def __init__(self, segmenter: Segmenter, session_id: str, *, enabled: bool = False):
        self.segmenter = segmenter
        self.enabled = enabled
        self.reset(session_id)

    def reset(self, session_id: str, start_sample: int = 0) -> None:
        if start_sample < 0:
            raise ValueError("Negative capture clock")
        if hasattr(self, '_buffer'):
            self._buffer.fill(0)
        self.session_id = session_id
        self._buffer = np.empty(0, dtype=np.float32)
        self._end = start_sample
        self._last_tick = start_sample
        self._boundary_watermark = start_sample
        self._overlap_watermark = start_sample

    @property
    def retained_samples(self) -> int:
        return len(self._buffer)

    def acknowledge_cut(self, sample: int) -> None:
        if sample < 0:
            raise ValueError("Negative cut clock")
        self._boundary_watermark = max(self._boundary_watermark, sample)

    def feed(self, audio: np.ndarray, start_sample: int) -> tuple[SpeakerEvent, ...]:
        if not self.enabled:
            return ()
        x = validate_audio(audio)
        if len(x) > SAMPLE_RATE or start_sample < 0:
            raise ValueError("feed requires <=1s PCM and nonnegative clock")
        if start_sample < self._end:
            return ()  # Replayed/out-of-order audio cannot change the ring.
        if start_sample != self._end:
            self._buffer.fill(0)
            self._buffer = np.empty(0, dtype=np.float32)
            self._last_tick = start_sample
        previous = self._buffer
        self._buffer = np.concatenate((previous, x))[-4 * SAMPLE_RATE:].copy()
        previous.fill(0)
        self._end = start_sample + len(x)
        if len(self._buffer) < 2 * SAMPLE_RATE or self._end - self._last_tick < SAMPLE_RATE:
            return ()
        self._last_tick = self._end
        frames = self.segmenter.analyze(self._buffer)
        return self._events(frames, self._end - len(self._buffer), self._end)

    def _events(self, frames: SegmentationFrames, start: int, end: int) -> tuple[SpeakerEvent, ...]:
        events = []
        for a, b in _intervals(frames.overlap >= .5, frames.centers, (end - start) / SAMPLE_RATE):
            left, right = start + int(a * SAMPLE_RATE), start + int(b * SAMPLE_RATE)
            left = max(left, self._overlap_watermark)
            if right > left:
                events.append(SpeakerEvent('overlap', self.session_id, left, right, end))
                self._overlap_watermark = right
        scores = frames.probabilities[:, 1:4]
        slots = np.argmax(scores, axis=1)
        confident = (scores.max(axis=1) >= .75) & (frames.overlap < .1)
        slots = np.where(confident, slots, -1)
        # Run boundaries are all derived from this ONE window; no cross-window slot matching.
        edges = np.r_[0, np.flatnonzero(np.diff(slots) != 0) + 1, len(slots)]
        runs = [(a, b, slots[a]) for a, b in zip(edges[:-1], edges[1:])
                if slots[a] >= 0 and b - a >= 12]
        for (a, old_end, old_slot), (new_start, b, new_slot) in zip(runs[:-1], runs[1:]):
            if old_slot == new_slot:
                continue
            # Transition posteriors can briefly be uncertain. Permit <=0.6s
            # only with clean, different stable endpoints and NO suspected
            # overlap anywhere in the gap. Never use uncertainty as identity.
            gap = new_start - old_end
            if gap * .016875 > .6 or np.any(frames.overlap[old_end:new_start] >= .1):
                continue
            boundary = (old_end + new_start) // 2
            relative = float(frames.centers[boundary])
            sample = start + int(relative * SAMPLE_RATE)
            if relative < .25 or (end - sample) < int(.25 * SAMPLE_RATE):
                continue
            # Dedupe sliding-window jitter and keep final utterances useful (>=1.5s).
            if sample <= self._boundary_watermark + int(1.5 * SAMPLE_RATE):
                continue
            events.append(SpeakerEvent('boundary', self.session_id, sample, sample, end))
            self._boundary_watermark = sample
        return tuple(sorted(events, key=lambda event: (event.start_sample, event.kind)))

    def close(self) -> None:
        self.enabled = False
        self._buffer.fill(0)
        self._buffer = np.empty(0, dtype=np.float32)
