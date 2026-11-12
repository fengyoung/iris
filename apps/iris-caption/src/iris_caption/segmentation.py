"""Learned endpoint segmentation; sample clock excludes pauses.

Forced cuts retain 400ms acoustic overlap, never globally deduplicate text.
Soft cuts prefer 120ms posterior silence after 11s; hard cap is 19s.
"""
from array import array
import math


class Segmenter:
    def __init__(self, vad, pause_s=.7, max_segment_s=11., partial_s=1.,
                 hard_segment_s=19., pre_roll_s=.4, overlap_s=.4,
                 on_speech_chunk=None):
        for value in (pause_s, max_segment_s, partial_s, hard_segment_s, pre_roll_s, overlap_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("positive finite segment timing required")
        if not .3 <= pre_roll_s <= .5 or not .3 <= overlap_s <= .6:
            raise ValueError("pre-roll 300-500ms and overlap 300-600ms required")
        if not max_segment_s <= hard_segment_s <= 20:
            raise ValueError("soft cap must not exceed hard cap <=20s")
        self.vad = vad
        self.soft = round(max_segment_s * 16000)
        self.hard = round(hard_segment_s * 16000)
        self.pre = round(pre_roll_s * 16000)
        self.overlap = round(overlap_s * 16000)
        self.partial = round(partial_s * 16000)
        self.on_speech_chunk = on_speech_chunk
        self.position = self.origin = self.base = 0
        self.segment_id = self.revision = 0
        self.paused = False
        self.audio = array('f')
        self.pending = array('f')
        self.start = None
        self.last_partial = self.quiet_samples = 0
        self.overlap_prefix_samples = 0
        self.last_owned_end = 0
        self.requested_boundary = None

    def request_boundary(self, sample):
        """Request a future speaker cut; never rewrite an already emitted window."""
        if sample < self.position:
            return False
        self.requested_boundary = int(sample)
        return True

    @property
    def buffered_samples(self):
        return len(self.audio) + len(self.pending)

    def feed(self, samples):
        if self.paused:
            return []
        result = []
        for sample in samples:
            value = float(sample)
            if not math.isfinite(value):
                raise ValueError("non-finite audio sample")
            self.pending.append(value)
            if len(self.pending) == 640:
                frame, self.pending = self.pending, array('f')
                result.extend(self._frame(frame))
        return result

    def _frame(self, frame, final=False):
        self.audio.extend(frame)
        self.position += len(frame)
        endpoints = self.vad.feed(frame, final=final)
        result = []
        for start, end in endpoints:
            if start >= 0 and self.start is None:
                self.start = max(self.base, self.last_owned_end, self.origin + start - self.pre)
                self.segment_id += 1
                self.revision = 0
                self.last_partial = self.start
            if end >= 0 and self.start is not None:
                stop = max(self.start + self.overlap_prefix_samples,
                           min(self.position, self.origin + end))
                result.append(self._window('final', stop, 'silence'))
                self.start = None
                self.overlap_prefix_samples = 0
        if self.start is not None:
            self.quiet_samples = self.quiet_samples + len(frame) if self.vad.quiet else 0
            duration = self.position - self.start
            reason = ('limit' if duration >= self.hard else
                      'soft_pause' if duration >= self.soft and self.quiet_samples >= 1920 else '')
            if self.requested_boundary is not None and self.position >= self.requested_boundary:
                reason = 'speaker'
                self.requested_boundary = None
            if reason:
                result.append(self._window('final', self.position, reason))
                self.start = max(self.base, self.position - self.overlap)
                self.overlap_prefix_samples = self.position - self.start
                self.segment_id += 1
                self.revision = 0
                self.last_partial = self.position
            elif self.position - self.last_partial >= self.partial:
                result.append(self._window('partial', self.position))
                self.last_partial = self.position
        # Idle history covers delayed onset plus pre-roll (400ms + 400ms).
        keep = self.start if self.start is not None else max(self.base, self.position - self.pre - 6400)
        trim = max(0, keep - self.base)
        del self.audio[:trim]
        self.base += trim
        return result

    def _window(self, kind, end, reason=''):
        from .audio import AudioWindow
        self.revision += 1
        window = AudioWindow(kind, self.segment_id, self.start, end,
                             self.audio[self.start-self.base:end-self.base], self.revision, reason,
                             self.overlap_prefix_samples)
        if kind == 'final':
            self.last_owned_end = end
        if self.on_speech_chunk:
            self.on_speech_chunk(window)
        return window

    def flush(self, reason='stop'):
        if self.paused:
            return []
        frame, self.pending = self.pending, array('f')
        result = self._frame(frame, final=True)
        if self.start is not None:
            result.append(self._window('final', self.position, reason))
        for window in result:
            if window.kind == 'final':
                window.reason = reason
        self.start = None
        self.audio = array('f')
        self.base = self.origin = self.position
        self.overlap_prefix_samples = self.quiet_samples = 0
        self.requested_boundary = None
        self.vad.reset()
        return result

    def pause(self):
        result = self.flush('pause')
        self.paused = True
        return result

    def resume(self):
        self.paused = False
