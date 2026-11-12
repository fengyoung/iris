# Optional local speaker diarization: models, provenance and verification

Speaker diarization is **optional and default off**. It produces anonymous session-local labels, not names, biometric authentication, or proof of identity. Audio processing and embeddings are local CPU inference. Downloading public model weights is a separate network operation; inference does not require cloud audio upload.

## Exact inference artifacts

| Artifact | Bytes | License | SHA-256 |
|---|---:|---|---|
| `campplus.onnx` | 28,283,928 | Apache-2.0 as documented by exporter | `dd1740aa1e1ffa3895f96aef2166b8af2bb2ad09c00769dd275ee36aef6a2a7f` |
| `segmentation.onnx` | 5,992,913 | MIT, CNRS | `220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079` |

Total **34,276,841 bytes (~34.28 MB / 32.69 MiB)** excluding licenses and runtime libraries. Both files are verified against size and SHA-256 before inference. The backend manifest has fields `id`, `filename`, `url`, `sha256`, `size`, `license`, `license_url`; the application model manager has its own installation manifest schema.

### CAM++ embeddings

- [Exact pinned model download](https://huggingface.co/welcomyou/campplus-3dspeaker-200k-onnx/resolve/6265ff7af2a104d745b4389026ed9815c6c1c6ff/campplus_cn_en_common_200k.onnx)
- [Pinned exporter model card](https://huggingface.co/welcomyou/campplus-3dspeaker-200k-onnx/blob/6265ff7af2a104d745b4389026ed9815c6c1c6ff/README.md)
- [Upstream 3D-Speaker project and Apache-2.0 license](https://github.com/modelscope/3D-Speaker)
- [Original Chinese/English model](https://www.modelscope.cn/models/iic/speech_campplus_sv_zh_en_16k-common_advanced)

This is a **third-party documented ONNX export**, not an official Alibaba-hosted ONNX artifact. Its model card declares Apache-2.0 inherited from 3D-Speaker and describes its conversion. Preserve applicable upstream notices and Apache license when distributing. No legal guarantee beyond the cited declarations is implied.

Actual inspected input: `feats` float32 `[batch,time,80]`; output: `embs` `[batch,192]`. Features are 80-bin Kaldi filterbanks, 16 kHz waveform scaled by 32768, zero dither, per-feature temporal mean subtraction. Embeddings are L2-normalized. Existing `onnxruntime`, `kaldi_native_fbank`, and NumPy suffice; no PyTorch/pyannote training stack installation is required.

### True overlap segmentation

- [Exact pinned model download](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/resolve/9403a6902bb58e3d5ae8c7e77c3422de279db2e0/model.onnx)
- [Pinned MIT license](https://huggingface.co/csukuangfj/sherpa-onnx-pyannote-segmentation-3-0/blob/9403a6902bb58e3d5ae8c7e77c3422de279db2e0/LICENSE)
- [Official sherpa conversion documentation](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html)
- [Upstream model](https://huggingface.co/pyannote/segmentation-3.0)

The public sherpa ONNX conversion was downloadable without authentication. Actual tensor input is `[batch,1,samples]`; output has seven log-probability classes: none, A, B, C, AB, AC, BC. Overlap comes from pair-class probability mass, **not amplitude/loudness**. Receptive field 991 samples, step270 samples, nominal10-second context. Short inputs are right-padded; this does not impose10 seconds of wall-clock lookahead.

These three speaker slots are **local to each inference window**, can permute between windows, and are not meeting identities. Global clustering separately defaults to8 speakers, configurable1–16. The powerset models at most two simultaneous speakers; three-way overlap is not reliably represented.

## Storage and privacy

On macOS the managed speaker installation root is:

```text
~/Library/Application Support/Iris Caption/models/speaker/
```

The model manager selects a verified active generation under that root. The active artifact directory must contain both ONNX filenames above. An explicitly configured local artifact directory can also be used. Validation artifacts in this checkout are under `apps/iris-caption/output/model-validation/`; they are not a bundled installation.

Session embeddings and rolling PCM are held in memory only by this backend. Close/reset clears owned vectors and buffers best-effort; Python/ONNX Runtime cannot promise secure memory erasure. The broader app's transcript/audio storage settings are separate from this backend's ephemeral embedding policy. CPU inference uses one intra-op and one inter-op thread; the integration must serialize model calls and bound its work queue.

**Network scope:** direct HTTPS downloads of both pinned Hugging Face artifacts succeeded and checksums were verified. This does not establish end-to-end reachability through the app model manager, its redirect allowlist, every network environment, or an installed release. Those paths need separate integration verification. This document does not claim they have passed.

## Conservative behavior and limitations

- Short (<1.5 seconds clean speech), interrupted, ambiguous and overlapped evidence is unknown and excluded from cluster updates.
- A new label needs at least two consistent disjoint clean observations. Final utterances up to20 seconds are inspected in disjoint chunks up to4 seconds; every chunk must pass before any enrollment. Rolling overlapping windows do not count as independent identity evidence.
- Labels remain stable within the session and do not merge or renumber. They are not guaranteed correct; distant microphones, room echo, similar voices and code-switching can degrade results.
- A four-second rolling buffer is analyzed at most once per second. Same-window stable local-slot transitions can request cuts; estimated transition timestamps and actual delayed capture cuts are different. The parent acknowledges actual cuts to prevent repeated retrospective cuts.
- There is **no source separation and no promise to recover all words during overlapping speech**. Mark overlap/unknown instead of inventing participants or dialogue.
- Conservative thresholds may reject true single-speaker speech as unknown. Thresholds are provisional, not calibrated confidence scores.

## Reproducible offline verification

The script requires existing local models and local public speech fixtures. It does not download, record microphone audio, upload audio, or overwrite an output file. It needs existing `soundfile` only for reading fixtures.

Run from the standalone app directory:

```bash
.venv/bin/python scripts/verify_speakers.py \
  --models output/model-validation \
  --voice1 output/model-validation/voice1.wav \
  --voice2 output/model-validation/voice2.wav \
  --output output/model-validation/reproduced-speakers.json
```

Choose a new output filename for every run. Each fixture must be mono16 kHz and at least8 seconds. The script takes the first8 seconds, peak-normalizes each to0.45, tests individual and additive mixture audio, and constructs a4s+4s stitched speaker change. JSON records fixture hashes, model manifest, overlap fraction, embedding cosine similarities, timing, rolling events and conservative final results.

Original local validation sources:

- [FunASR VAD example](https://huggingface.co/funasr/fsmn-vad/resolve/main/example/vad_example.wav), saved as `speaker1.wav`; `voice1.wav` derives from original samples16000:144000.
- [Narsil ASR fixture](https://huggingface.co/datasets/Narsil/asr_dummy/resolve/main/1.flac), saved as `speaker2.flac`; `voice2.wav` derives from samples0:128000.

The normalized local clips were written as PCM16 WAV. Original fixture redistribution licenses were not independently cleared: **do not bundle or redistribute these recordings**. Use your own appropriately licensed fixtures if sharing verification artifacts.

### Observed development results, not an accuracy benchmark

On the development machine,8-second individual clips had0% marked-overlap frames; their additive mixture had90.27%. Cross-speaker cosine was−0.150; disjoint same-speaker3-second excerpts gave0.781 and0.811. Single-thread inference was approximately38ms segmentation plus113ms embedding per8-second input.

For one stitched transition at sample64000, the rolling detector emitted one estimate at68405, delivered at sample96000: **275ms timestamp error and2s detection delay**. One clean8-second voice was nevertheless conservatively rejected by final analysis as `multiple-speakers`; the other confirmed labelA. The mixed audio was marked overlap without learning. These small synthetic checks establish execution and useful behavior, **not conference diarization accuracy**, universal thresholds, or guaranteed speaker identity.
