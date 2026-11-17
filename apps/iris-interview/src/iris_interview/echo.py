"""Conservative single-path reference echo attenuation, not validated room AEC.

16 kHz mono caller input only. Source timestamps must share the same clock.
A delayed one-tap least-squares filter is fitted only to strongly correlated
blocks. Ambiguous/double-talk blocks are returned unchanged. This is deliberately
not a claim of robust reverberation cancellation or speaker identification.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .exceptions import InterviewError


@dataclass(frozen=True)
class EchoResult:
    samples: np.ndarray
    state: str
    delay_samples: int | None = None
    confidence: float = 0.0
    attribution_uncertain: bool = True


class ReferenceEchoCanceller:
    """Bounded timestamp-aligned remote reference and conservative attenuation.

    feed_remote(samples, start_ns) must precede process_local for corresponding
    audio. Missing/late reference bypasses, never queues or removes microphone
    audio. Caller should reset after gaps, route changes, pauses and resampling
    discontinuities. No files, devices, worker threads or persistence.
    """
    sample_rate = 16000

    def __init__(self, *, max_delay_ms=200, history_seconds=2.0,
                 minimum_correlation=.985, max_gain=2.0):
        if not 0 <= max_delay_ms <= 500 or not 1 <= history_seconds <= 10:
            raise InterviewError("invalid_echo_history")
        if not .95 <= minimum_correlation <= 1 or not 0 < max_gain <= 4:
            raise InterviewError("invalid_echo_gate")
        self.delay_limit = round(max_delay_ms * 16)
        self.capacity = round(history_seconds * self.sample_rate)
        self.minimum_correlation = minimum_correlation
        self.max_gain = max_gain
        self.reset()

    def reset(self):
        self.reference = np.empty(0, dtype=np.float32)
        self.reference_start = 0
        self.gain = None
        self.delay = None

    @property
    def buffered_samples(self):
        return len(self.reference)

    @staticmethod
    def _waveform(samples):
        result = np.asarray(samples, dtype=np.float32)
        if result.ndim != 1 or len(result) > 16000 or not np.isfinite(result).all():
            raise InterviewError("echo_requires_finite_mono_block_at_most_one_second")
        return result

    @staticmethod
    def _index(timestamp_ns):
        if isinstance(timestamp_ns, bool) or not isinstance(timestamp_ns, int) or timestamp_ns < 0:
            raise InterviewError("invalid_echo_timestamp")
        return round(timestamp_ns * 16000 / 1_000_000_000)

    def feed_remote(self, samples, start_ns):
        values = self._waveform(samples)
        start = self._index(start_ns)
        if not len(values):
            return
        end = self.reference_start + len(self.reference)
        if not len(self.reference) or start != end:
            # A missing interval must never be represented as valid silence.
            self.reset()
            self.reference_start = start
        self.reference = np.concatenate((self.reference, values))
        if len(self.reference) > self.capacity:
            trim = len(self.reference) - self.capacity
            self.reference = self.reference[trim:].copy()
            self.reference_start += trim

    def process_local(self, samples, start_ns):
        values = self._waveform(samples)
        start = self._index(start_ns)
        unchanged = values.copy()
        if len(values) < 320:
            return EchoResult(unchanged, "bypass_short_block")
        low = max(self.reference_start, start - self.delay_limit)
        high = min(start, self.reference_start + len(self.reference) - len(values))
        if high < low:
            return EchoResult(unchanged, "bypass_missing_reference")
        segment = self.reference[low-self.reference_start:high-self.reference_start+len(values)].astype(float)
        local = values.astype(float)
        # Mean-centered normalized correlation permits harmless microphone DC.
        centered = local - local.mean()
        local_energy = float(centered @ centered)
        if local_energy < len(values) * 1e-10:
            return EchoResult(unchanged, "bypass_quiet")
        ones = np.ones(len(values))
        sums = np.convolve(segment, ones, mode="valid")
        energy = np.convolve(segment * segment, ones, mode="valid") - sums*sums/len(values)
        dot = np.correlate(segment, centered, mode="valid")
        normalized = np.abs(dot) / np.sqrt(np.maximum(energy * local_energy, 1e-30))
        match = int(normalized.argmax())
        confidence = min(1., float(normalized[match]))
        delay = start - (low + match)
        if energy[match] < len(values) * 1e-8:
            return EchoResult(unchanged, "bypass_quiet_reference", delay, confidence)
        if confidence < self.minimum_correlation:
            return EchoResult(unchanged, "bypass_doubletalk_or_mismatch", delay, confidence)
        reference = segment[match:match+len(values)]
        centered_reference = reference - reference.mean()
        estimate = float(dot[match] / energy[match])
        if abs(estimate) > self.max_gain:
            return EchoResult(unchanged, "bypass_gain_limit", delay, confidence)
        # Adapt only on high-confidence, remote-dominant blocks. Strongly changing
        # delay/gain bypasses this block instead of subtracting an old path.
        if self.gain is not None and (abs(delay-self.delay) > 8 or
                                     abs(estimate-self.gain) > max(.1, abs(self.gain)*.5)):
            self.gain, self.delay = None, None
            return EchoResult(unchanged, "bypass_path_change", delay, confidence)
        self.gain = estimate if self.gain is None else .75*self.gain + .25*estimate
        self.delay = delay
        filtered = (local - self.gain*centered_reference).astype(np.float32)
        # Never amplify a block due to an inaccurate reference fit.
        if float(filtered @ filtered) > float(local @ local) * 1.01:
            return EchoResult(unchanged, "bypass_energy_guard", delay, confidence)
        return EchoResult(filtered, "attenuated_uncertain", delay, confidence)
