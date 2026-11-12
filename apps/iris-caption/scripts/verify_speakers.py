#!/usr/bin/env python3
"""Explicit-path offline smoke/benchmark; never downloads or overwrites output."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from iris_caption.diarization import Diarizer  # noqa: E402
from iris_caption.rolling_speakers import RollingSpeakerAnalyzer  # noqa: E402
from iris_caption.speaker import (  # noqa: E402
    CAMPPLUS, SEGMENTATION, CampPlusEmbedding, PyannoteOverlap, validate_audio,
)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, required=True, help='Directory containing both pinned ONNX files')
    parser.add_argument('--voice1', type=Path, required=True, help='Local single-speaker 16k mono recording >=8s')
    parser.add_argument('--voice2', type=Path, required=True, help='Different public speaker, 16k mono >=8s')
    parser.add_argument('--output', type=Path, required=True, help='NEW JSON result path, never overwritten')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('--output already exists; choose a new path')
    import soundfile as sf

    voices = []
    for path in (args.voice1, args.voice2):
        pcm, rate = sf.read(path, dtype='float32')
        if rate != 16000 or pcm.ndim != 1 or len(pcm) < 128000:
            parser.error('Each fixture must contain >=8 seconds of mono 16000 Hz speech')
        x = validate_audio(pcm[:128000]).copy()
        peak = float(np.max(np.abs(x)))
        if peak < 1e-6:
            parser.error('Silent fixture')
        voices.append(x / peak * .45)
    embedder = CampPlusEmbedding(args.models / CAMPPLUS.filename)
    segmenter = PyannoteOverlap(args.models / SEGMENTATION.filename)
    report = {'schema': 1, 'models': [asdict(CAMPPLUS), asdict(SEGMENTATION)],
              'fixtures': [{'path': str(p.resolve()), 'sha256': digest(p)}
                           for p in (args.voice1, args.voice2)],
              'preprocessing': 'First8s; each peak-normalized0.45; mixture=sum; rolling=first4s+first4s',
              'limits': 'Local synthetic smoke test, not conference accuracy calibration; no identity guarantee.',
              'measurements': {}}
    embeddings = []
    for name, audio in [('voice1', voices[0]), ('voice2', voices[1]), ('mixture', sum(voices))]:
        started = time.perf_counter()
        frames = segmenter.analyze(audio)
        segmentation_seconds = time.perf_counter() - started
        started = time.perf_counter()
        embeddings.append(embedder.extract(audio))
        report['measurements'][name] = {
            'segmentation_seconds': segmentation_seconds,
            'embedding_seconds': time.perf_counter() - started,
            'overlap_fraction': float(np.mean(frames.overlap >= .5)),
            'max_overlap': float(frames.overlap.max()),
        }
    report['different_speaker_cosine'] = float(embeddings[0] @ embeddings[1])
    report['same_speaker_cosines'] = [float(embedder.extract(x[:48000]) @ embedder.extract(x[-48000:]))
                                     for x in voices]
    rolling = RollingSpeakerAnalyzer(segmenter, 'verification', enabled=True)
    stitched = np.concatenate((voices[0][:64000], voices[1][:64000]))
    events = []
    for start in range(0, len(stitched), 8000):
        events.extend(rolling.feed(stitched[start:start + 8000], start))
    report['true_transition_sample'] = 64000
    report['rolling_events'] = [asdict(event) for event in events]
    rolling.close()
    diarizer = Diarizer(embedder, segmenter, enabled=True)
    report['final_results'] = [asdict(diarizer.analyze_final(x, start_sample=i * 128000,
                               session_id='verification'))
                               for i, x in enumerate([*voices, sum(voices)])]
    diarizer.reset()
    # Exclusive creation protects against races too. Do not create parent dirs implicitly.
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(args.output.resolve()), 'rolling_events': report['rolling_events'],
                      'final_results': report['final_results']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
