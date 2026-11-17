"""Deterministic synthetic echo paths; no microphone or room-quality claims."""
import numpy as np
import pytest

from iris_interview.echo import ReferenceEchoCanceller
from iris_interview.exceptions import InterviewError


def reference():
    return np.random.default_rng(731).normal(0, .1, 16000).astype(np.float32)


def configured():
    remote = reference()
    canceller = ReferenceEchoCanceller()
    canceller.feed_remote(remote, 0)
    return remote, canceller


def test_delayed_attenuated_echo_energy_reduction():
    remote, canceller = configured()
    delay, start, count = 720, 4000, 1600
    echo = .6 * remote[start-delay:start-delay+count]
    result = canceller.process_local(echo, start * 62500)
    assert result.state == "attenuated_uncertain"
    assert result.attribution_uncertain
    assert result.delay_samples == delay
    assert np.mean(result.samples**2) < np.mean(echo**2) * .001
    assert result.confidence > .999


def test_doubletalk_preserves_independent_local_voice():
    remote, canceller = configured()
    start, delay, count = 4000, 480, 1600
    echo = .5 * remote[start-delay:start-delay+count]
    canceller.process_local(echo, start*62500)
    voice = np.random.default_rng(9).normal(0, .08, count).astype(np.float32)
    mixed = echo + voice
    result = canceller.process_local(mixed, start*62500)
    assert result.state == "bypass_doubletalk_or_mismatch"
    np.testing.assert_array_equal(result.samples, mixed)


def test_late_or_missing_reference_is_unchanged():
    c = ReferenceEchoCanceller()
    local = reference()[:640]
    result = c.process_local(local, 0)
    assert result.state == "bypass_missing_reference"
    np.testing.assert_array_equal(result.samples, local)
    c.feed_remote(local, 1_000_000_000)
    np.testing.assert_array_equal(c.process_local(local, 0).samples, local)


def test_reference_history_bounded_and_gap_resets_path():
    c = ReferenceEchoCanceller(history_seconds=1)
    data = reference()
    for second in range(5):
        c.feed_remote(data, second*1_000_000_000)
    assert c.buffered_samples == 16000
    c.gain, c.delay = .5, 100
    c.feed_remote(data[:640], 7_000_000_000)
    assert c.buffered_samples == 640 and c.gain is None


def test_mismatch_and_silence_are_not_removed():
    _, c = configured()
    voice = np.random.default_rng(3).normal(0, .1, 640).astype(np.float32)
    result = c.process_local(voice, 4000*62500)
    np.testing.assert_array_equal(result.samples, voice)
    zeros = np.zeros(640, dtype=np.float32)
    np.testing.assert_array_equal(c.process_local(zeros, 4000*62500).samples, zeros)


def test_rejects_invalid_pcm_and_timestamps():
    c = ReferenceEchoCanceller()
    for values in (np.array([np.nan]), np.ones((3,2)), np.ones(16001)):
        with pytest.raises(InterviewError):
            c.process_local(values, 0)
    with pytest.raises(InterviewError):
        c.feed_remote(np.ones(640), -1)
