# Iris-caption logo v1.0

Date: 2026-11-05. Part of the Iris design archive. Approved concept: **B**. Exact product wordmark: **Iris-caption**.

## Deliverables

All filenames begin `2026-11-05_iris-caption-logo_v1.0_`.

- `icon-primary.svg` / `.png`: approved B, black rounded tile and light wing/waveform. SVG is byte-identical to the approved source, including its historical draft title.
- `icon-inverse.svg` / `.png`: white rounded tile; original Iris inverse gradient (#7a7a7a → #383838 → #000000), inverse waveform #2b2b2b.
- `lockup-primary.svg` / `.png`: primary icon plus black **Iris-caption** outlines, for light backgrounds.
- `lockup-inverse.svg` / `.png`: inverse icon plus white **Iris-caption** outlines, for dark backgrounds.
- `app.icns`: macOS multiresolution icon generated from the 1024 px primary PNG with Pillow; largest representation 1024 px.
- `contact-sheet.png` / `.html`: positive/negative lockups and native 16, 24, 32, 48, 64 and 128 px icon previews. Preview backgrounds are presentation only.
- `verification.json`: source/font hashes and geometry checks.
- `generate.py`: generation source; requires fontTools, Pillow, CairoSVG and native Cairo. Its temporary dependency location and native Cairo location reflect the generation host.

SVG and PNG canvases are transparent; the approved opaque rounded tile remains part of the artwork. Icon PNGs are 1024 × 1024 RGBA. Lockup PNGs are 3072 px wide. No export requires an installed font or external URL. Font files are not distributed.

## Provenance and preservation

Approved icon source: `apps/iris-caption/design/logo/iris-caption-B.svg`.

Brand references: `data/iris-logo/2026-11-05_iris-logo_v1.4_README.md`, `..._icon-inverse.svg` and `..._lockup-primary.svg`. The three wing path strings and small waveform path string are unchanged in all four SVGs. Gradient, tile and waveform geometry are unchanged in the primary; only colors invert in the inverse.

Typography: locally installed `/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf` (Arial Rounded MT Bold). The four original Iris glyph outlines are copied directly from v1.4. The suffix `-caption` is converted to paths from the same font, with matching 36.864 font-unit tracking, 42-unit round-join stroke and geometric +8° right slant (SVG skew coefficient −tan(8°)). The entire wordmark is uniformly scaled to 96 units high including the **p** descender; this preserves the v1.4 icon-height alignment without clipping. Icon geometry is never slanted or scaled independently within its 96-unit tile.

Primary source SHA-256: `ec8d8523b50cb8fc4ded9b895be702826d013c36ca462af38050c3184c6f0d47`.

## Verification

XML parsing, no SVG text nodes, identical first four path strings across variants, byte equality of approved B and primary SVG, 1024 × 1024 RGBA PNGs with transparent corners, and ICNS decoding were checked. Native-size renders were reviewed; the approved waveform becomes subtle at 16 px, and no small-size redraw was introduced.

App-ready copies are placed under `apps/iris-caption/assets/logo/`. `iris-caption-icon.png` and `iris-caption.icns` are the parent integration entry points. All four SVG/PNG variants are included there to keep app assets tracked even when the data archive is ignored. App wiring and packaging specification belong to the parent task; no app source or existing original Iris assets were modified.
