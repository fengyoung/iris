# Independent local OCR helper

Uses macOS system Vision and AppKit only. No network, screen capture, microphone capture, model download or private resume fixture. This directory is independent of `native/` (audio).

```bash
bash native-ocr/build.sh
native-ocr/.build/iris-ocr-helper --self-test
native-ocr/.build/iris-ocr-helper /path/to/image.png
```

Build with the installed Xcode command-line tools. `--self-test` draws a synthetic English/Chinese image in a UUID temporary directory, recognizes it with Vision, checks English and Chinese results, emits JSON and removes its temporary image. No network API is used. Build artifacts and Swift module cache stay in this directory's `.build/`.

The JSON contains `text`, mean `confidence`, `paragraphs` with text/confidence/normalized Vision bounds (bottom-left origin), and `requiresReview: true`. Vision confidence is not a calibrated correctness probability. Paragraph candidates are Vision reading units joined with blank lines, not guaranteed semantic paragraphs or correct multi-column order. Language correction is disabled to avoid rewriting candidate statements.

Python `iris_interview.ocr.extract_with_ocr(path)` preserves originals, rasterizes PDF pages without text layers through PyMuPDF into temporary PNG files (216dpi, longest side capped at 5000px), and cleans them on success/failure. Text-layer pages retain extracted text. It returns the resume schema plus confidence, paragraph evidence and review status. Limits: 40MB input, 100 PDF pages, 120 seconds per image. `IRIS_INTERVIEW_OCR_HELPER` overrides the helper path for packaging. Missing helper and OCR failures are explicit errors. Empty recognition remains unverified with a visible warning.

Integration is intentionally left to the parent: call the OCR API for image imports and PDF scan pages, retain the raw file in Project storage, and require human comparison before verified status. No changes to `resume.py` are made here.
