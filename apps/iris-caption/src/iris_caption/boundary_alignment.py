"""Conservative ownership for timestamp-free ASR with left audio context.

Text equality is not temporal alignment. Even a unique suffix/prefix match can
be a legitimately repeated phrase. Overlap hypotheses are diagnostic only;
countable finals always come from disjoint owned PCM. This avoids counting the
same audio twice, not ASR hallucinations or words damaged by a hard cut.

The caller decodes owned PCM once per contextual FINAL (not per partial). No
previous final needs deferring. Optional overlap-only ASR is not required and
must not be run just for this module. There is no model/network dependency.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecodeWindow:
    """Text decoded from [start, end); ownership is [owned_start, end).

    Sample positions must share one clock. Only left context is supported.
    """

    text: str
    start_sample: int
    end_sample: int
    owned_start_sample: int

    def __post_init__(self):
        positions = (self.start_sample, self.owned_start_sample, self.end_sample)
        if any(type(value) is not int for value in positions):
            raise ValueError("sample positions must be integers")
        if not 0 <= self.start_sample <= self.owned_start_sample <= self.end_sample:
            raise ValueError("expected start <= owned_start <= end")
        if not isinstance(self.text, str):
            raise ValueError("decoded text must be a string")


@dataclass(frozen=True)
class BoundaryResult:
    """Only non-None text is countable. Diagnostics are never transcript text."""

    text: str | None
    status: str
    candidate_trim: int = 0
    needs_owned_decode: bool = False
    uncertain: bool = False
    evidence: str = "none"


def _candidate(previous, window, overlap, max_chars):
    if previous is None:
        return 0, "no_previous"
    if previous.end_sample != window.owned_start_sample:
        return 0, "nonadjacent"
    if previous.start_sample > window.start_sample:
        return 0, "uncovered_overlap"
    left, right = previous.text[-max_chars:], window.text[:max_chars]
    matches = [n for n in range(1, min(len(left), len(right)) + 1)
               if left[-n:] == right[:n]]
    if not matches:
        return 0, "unmatched"
    if len(matches) != 1:
        return 0, "ambiguous_repetition"
    count = matches[0]
    anchor = right[:count]
    # Never normalize away punctuation, spaces or repetitions.
    if count < 2 or any(anchor == anchor[:n] * (count // n)
                        for n in range(1, count) if count % n == 0):
        return 0, "ambiguous_repetition"
    if left.count(anchor) > 1 or right.count(anchor) > 1:
        return 0, "ambiguous_repetition"
    if overlap is not None:
        if (overlap.start_sample != window.start_sample
                or overlap.end_sample != window.owned_start_sample
                or overlap.owned_start_sample != overlap.start_sample):
            return 0, "anchor_wrong_interval"
        if overlap.text != anchor:
            return 0, "anchor_mismatch"
        return count, "anchored_candidate"
    return count, "text_candidate"


def reconcile_final(
    window: DecodeWindow,
    *,
    owned_text: str | None = None,
    previous: DecodeWindow | None = None,
    overlap: DecodeWindow | None = None,
    max_chars: int = 32,
    max_overlap_samples: int = 9600,
) -> BoundaryResult:
    """Select a final without deleting repetitions based on text similarity.

    ``owned_text`` must be an independent decode of exactly
    ``[window.owned_start_sample, window.end_sample)``. None means not decoded;
    an empty string means decoded silence and is an authoritative empty final.
    The caller is responsible for this PCM provenance and final-only invocation.

    Matching searches at most ``max_chars`` on each side, with no future-window
    lookahead. The default overlap bound is 600 ms at 16 kHz; callers using a
    different sample rate must supply the corresponding sample bound. A unique
    match, even anchored by an overlap-only decode, never authorizes deletion.
    """
    if type(max_chars) is not int or not 1 <= max_chars <= 256:
        raise ValueError("max_chars must be between 1 and 256")
    if type(max_overlap_samples) is not int or max_overlap_samples < 1:
        raise ValueError("max_overlap_samples must be a positive integer")
    if owned_text is not None and not isinstance(owned_text, str):
        raise ValueError("owned_text must be a string or None")
    context_samples = window.owned_start_sample - window.start_sample
    if context_samples > max_overlap_samples:
        raise ValueError("left context exceeds overlap bound")
    if not context_samples:
        return BoundaryResult(window.text, "no_overlap")
    trim, evidence = _candidate(previous, window, overlap, max_chars)
    if owned_text is None:
        return BoundaryResult(None, "needs_owned_decode", trim, True, True, evidence)
    agreement = window.text == owned_text or (
        trim > 0 and window.text[trim:] == owned_text
    )
    # Return the independently owned decode even when a candidate agrees.
    return BoundaryResult(
        owned_text, "owned_agreement" if agreement else "owned_fallback",
        trim, False, not agreement, evidence,
    )
