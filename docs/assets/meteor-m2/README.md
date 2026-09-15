# Meteor-M2 image comparison

The pair at the top of the project README is a selected detail from a real
Meteor-M2 LRPT recording, decoded during the 2026-09-13 exploratory image study.
It shows MSU-MR channel 1. Both PNGs are unchanged copies of the original audited
1568 × 768 comparison assets, not newly processed or enhanced images.

## What is being compared

| Left | Right |
|---|---|
| SatDump 1.2.2 | SatDump + FrameLift native recovery |
| Original decoded image coverage | Union of the reference and complementary native frames |

Both arms use the same complete original IQ recording and the same unchanged
SatDump 1.2.2 MSU-MR instrument renderer with `fill_missing=false`. FrameLift's
experimental Rust receiver recovers additional frames; SatDump renders the
instrument data in both arms. The right image is an **augmentation result**, not
a standalone FrameLift result or an independent implementation of the renderer.

The full channel-1 output grows from 4,576 to 4,952 rows. Of the 376 additional
rows of coverage, **328 contain nonzero image data**; some remain incomplete.
All 328 appear in this selected detail. Existing nonzero pixels in the common
extent are unchanged. This measures recovered coverage, not increased spatial
resolution, sharpness or ground-truth pixel accuracy.

The standalone native receiver also missed reference frames (6143 versus 6168
unique corrected CADUs; 409 native-only and 434 reference-only). The displayed
union preserves the reference results. This is a selected positive development
example, not an independent holdout or an equal-compute benchmark. It is separate
from the 266-recording CANVAS telemetry study.

## Display and alignment

Alignment uses one constant 376-row translation, supported by all 561 uniquely
matched positive channel timestamps. The detail spans rows 0–767 of the combined
extent. Baseline coverage starts at row 376; that absent upper area is transparent,
so it takes the background color of the README. It does **not** represent decoded
black pixels. Black gaps within covered areas remain exactly as rendered.

The original audit copies grayscale samples to R=G=B and retains their values;
alpha is zero only outside a source image's extent. No interpolation, inpainting,
sharpening, recoloring or synthetic pixels are used. The README browser scales
both images for display; open either PNG for its original dimensions.

## Source and evidence

Recording provided through the
[NOAAM2 SDRplay reception guide](https://voiceoverman.wixsite.com/noaam2/receive-meteor-m-n2-with-sdrplay):
[original IQ WAV](https://www.dropbox.com/s/11h6webppnl0pxr/11-13-00_137874kHz.wav?dl=1).
The recording's acquisition date is not established here.

- Original input: unsigned PCM8 stereo IQ WAV, 222,222 complex samples/s,
  900.510840511 seconds, 400,226,684 bytes.
- Input SHA-256: `047afccdbeaea7c3c9b0997586ebcb84b5b81da5c3e7b0535e4d730c4379a872`.
- [Frozen reference run](reference-run.json): original input and SatDump binary
  hashes, mission profile, settings and successful exit status.
- [Original image audit](image-audit.json): full product hashes, timestamp
  alignment, coverage and exact comparison-PNG hashes. Its channel-1 entry is
  the pair shown here; channels 2 and 5 are not separate recordings. Historical
  absolute paths identify the original working directories, not clone-relative files.
- [Experimental receiver and reproduction guidance](../../meteor-lrpt.md).

The image audit's top-level common-extent description concerns its ordinary
crops. The `coverage_detail` object records the extended-coverage view used here,
including the transparent area and the selection rule.

| PNG | SHA-256 |
|---|---|
| `MSU-MR-1-baseline-coverage-detail.png` | `02fcdb1e0534931ddd7519e73068639ed98b2dc53f6c5cc71f70eb30e394569c` |
| `MSU-MR-1-candidate-coverage-detail.png` | `3aebada005f7348f558d24bac8dc64967602fa9ac55a2eb8b410bc6837a39b7b` |

Only this image pair and its small evidence records are included; the raw IQ and
full decoded product archive are not bundled. Original imagery and third-party
software retain their respective ownership; the repository's software license
does not relicense them.
