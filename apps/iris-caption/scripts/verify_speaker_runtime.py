#!/usr/bin/env python3
"""Offline genuine ASR + optional speaker runtime + Session verification; no mic/downloads."""
from __future__ import annotations

import argparse
from array import array
import hashlib
import json
from pathlib import Path
from queue import Queue, Empty
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from iris_caption.audio import AudioWindow, ContextualOnnxAdapter, _recognize  # noqa: E402
from iris_caption.session import Session  # noqa: E402
from iris_caption.speaker_runtime import SpeakerRuntime  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--asr', type=Path, required=True)
    parser.add_argument('--vad', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; select a NEW path')
    events = Queue()
    session = Session(id='runtime-verification', speakers_enabled=True)
    config = {'session_id': session.id, 'speakers_enabled': True,
              'speaker_model_path': str(args.models)}
    started = time.perf_counter()
    runtime = SpeakerRuntime.create(config, events.put_nowait)
    assert runtime is not None, 'Pinned local speaker model loading failed'
    speaker_load = time.perf_counter() - started
    started = time.perf_counter()
    adapter = ContextualOnnxAdapter(args.asr, vad_model_path=args.vad)
    asr_load = time.perf_counter() - started
    report = {'schema': 1, 'no_microphone': True, 'speaker_load_seconds': speaker_load,
              'asr_load_seconds': asr_load, 'measurements': {}, 'events': [], 'fixtures': {},
              'limitations': 'Synthetic local fixtures, not meeting accuracy calibration. Chinese ASR on public English fixtures is not a transcript quality test.'}
    position = 0
    try:
        # Voice2 is clean in this pinned fixture and must enroll A using two
        # independent disjoint evidence chunks inside a single eight-second final.
        for number, name in enumerate(('voice2', 'voice1', 'mixture'), 1):
            path = args.models / (name + '.wav')
            pcm, rate = sf.read(path, dtype='float32')
            assert rate == 16000 and pcm.ndim == 1 and len(pcm) >= 128000
            pcm = pcm[:128000]
            assert np.isfinite(pcm).all()
            report['fixtures'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
            runtime.resume(position)
            # Accelerated 40ms capture exercises bounded/coalescing rolling input.
            # This deliberately does NOT claim a real-time boundary latency test.
            for offset in range(0, len(pcm), 640):
                runtime.feed(pcm[offset:offset + 640], position + offset)
            window = AudioWindow('final', number, position, position + len(pcm),
                                 array('f', pcm), number)
            started = time.perf_counter()
            _recognize([window], adapter, events, config, runtime.submit_final)
            asr_seconds = time.perf_counter() - started
            started = time.perf_counter()
            runtime.acknowledge_cut(window.end_sample)
            runtime.suspend(timeout=2)
            drain_seconds = time.perf_counter() - started
            assert not runtime.poll_boundary(window.end_sample), 'Stale boundary survived suspend'
            observed = []
            while True:
                try:
                    event = events.get_nowait()
                except Empty:
                    break
                observed.append(event)
                if event['type'] == 'final':
                    assert session.apply_audio(event) is not None
                elif event['type'] == 'speaker':
                    assert session.apply_speaker(event) is not None, 'Speaker event arrived before matching final'
            relevant = [e for e in observed if e['type'] in ('final', 'speaker', 'discard')]
            assert [e['type'] for e in relevant] == ['final', 'speaker'], relevant
            result = relevant[-1]
            assert result['audio_revision'] == number
            segment = session.segments[str(number)]
            assert segment.speaker_state != 'pending'
            if name == 'voice2':
                assert result['speaker_state'] == 'confirmed' and result['speaker_id'] == 'A', result
            if name == 'mixture':
                assert result['speaker_state'] == 'overlap' and result['speaker_id'] is None, result
                assert result['overlap_intervals']
                assert all(position // 16 <= a < b <= window.end_sample // 16
                           for a, b in result['overlap_intervals'])
            report['events'].extend(observed)
            report['measurements'][name] = {'asr_seconds': asr_seconds,
                'speaker_drain_seconds': drain_seconds, 'audio_seconds': len(pcm) / 16000,
                'speaker_state': segment.speaker_state, 'speaker_id': segment.speaker_id}
            position = window.end_sample
        report['checks'] = {'final_first': True, 'exact_audio_revision': True,
                            'session_applied': True, 'no_stale_after_suspend': True,
                            'factory_checksum_verified': True, 'bounded_terminal_results': True}
    finally:
        runtime.close(timeout=2)
        runtime._thread.join(timeout=2)
    assert not runtime._thread.is_alive(), 'Inference worker did not terminate'
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(args.output), 'measurements': report['measurements'],
                      'checks': report['checks']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
