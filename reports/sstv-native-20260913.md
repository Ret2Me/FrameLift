# Experimental native SSTV receiver — 13 September 2026

## Scope and implementation

Added `telemetry-sstv`, a separate opt-in Rust executable in the main Cargo package. No changes to the qualified packet receiver or frozen telemetry campaigns. Supports mono PCM16 audio (12–48 kHz), explicitly selected PD120 and PD180. Does not implement LRIT, SAR focusing, or Terra/Aqua/Aura instrument reconstruction.

Implementation: block FFT analytic-signal FM demodulation; sustained sync detection; global line-clock estimation; piecewise linear clock fits across discontinuities; VIS mode/parity validation for image origin; sync-derived frequency offset correction; Y0/Cr/Cb/Y1 sampling and YCbCr-to-RGB conversion. Missing sync can be bridged by timing prediction, but pixels are always sampled from the audio, never inpainted. Per-pair timestamps, observed/predicted sync status and finite-audio fractions are exported. Finite samples are NOT evidence of correct pixel values. These are established techniques, not asserted novel inventions.

Protocol sources:

- https://www.classicsstv.com/pdmodes.php
- https://www.classicsstv.com/downloads/daytonpaper.pdf

## Reproduce

```sh
cargo test --bin telemetry-sstv
cargo build --bin telemetry-sstv
target/debug/telemetry-sstv --input mono-pcm16.wav --output NEW_DIRECTORY --mode pd180
```

`--local-sync` is an internal ablation: local measured sync timing, no bridging of absent syncs. It is not gr-satellites, SatNOGS, QSSTV or MMSSTV. Output directory must not already exist. Output is an actual RGB PPM plus JSON provenance; PNG conversion is lossless via FFmpeg.

## Real recordings

Seven original WAVs from https://spacecomms.wordpress.com/iss-sstv-audio-recordings/ . Five PD180 and two PD120. ZIP integrity checked; archive SHA256:

`e3f7088fc47f97c9f59af8d2a3486b0e93171943d1b98e48dac27e584e4fd620`

For this pilot, convert each original once with FFmpeg to mono PCM16 at 12 kHz. All decoder arms receive byte-identical converted audio. This resampling is explicit, not claimed lossless. Do not compare these timings to direct processing of high-rate original IQ.

Artifacts: `work/image-diversity-20260913/sstv-final-v5/`. `build-provenance.json` binds a private executable snapshot, and `manifest.json` records commands, input hashes, timings and per-pair diagnostics. Every recording is retained. Earlier v1/v2/v3 directories are development diagnostics, not the final benchmark. In particular v3 used a shared development binary while development continued; exclude it from publication statistics. The v4 runner was terminated with exit 143 during recording 2 for an undetermined reason; its partial files are retained, and v5 is a new complete replay rather than silently merging partial runs.

The first implementation rendered only 304 rows of recording 3 (2016-04-12, first ARISS QSO). Sync-candidate diagnostics showed a timing jump of approximately 6 ms around 118 s, followed by continuing valid line syncs. Piecewise timing and wider association recovered the remaining image span (496 rows). This is an improvement over our own initial implementation, not by itself superiority over established receivers. The exact physical cause of the timing jump is not established.

## Verification and comparator limitations

Final v5 replay and independent artifact verification completed: **7/7 recordings produced 640×496 images**, each with a parity-checked VIS and 247 detected line-pair syncs plus one header-anchored predicted sync. These counts do not certify the analog pixels. Native unoptimized executable runtime was **2.873–4.480 seconds per recording**, excluding audio conversion and PNG rendering. Recording 3 required three timing segments; the other six used one. Input hashes match across native and external-reference arms. Executable SHA256: `0611e471e2c21eda08a5f3ebab2244f456245c2893b26d8c4ecf0cd634e1466b`. See `sstv-final-v5/verification.json` and the actual-image preview `sstv-final-v5/all-decoded.png`.

Nine Rust unit tests cover protocol timing, silence and invalid-input rejection, duplicate-sync rejection, tone-frequency recovery, gray color conversion, clock fit with absent syncs, VIS mode/parity validation, and end-to-end synthetic audio for both modes with an erased sync and a 6 ms timing jump. Synthetic fixtures are implementation tests only, never passed off as real reception evidence.

Independent reference: https://github.com/bashkirtsevich/sstv at commit `8ba3e24b07e6dc6973593e0fb9696e980f2c92b9`, default CLI, system Python, same seven 12 kHz WAVs. Outputs/logs in `reference-v1/`: all seven commands completed and wrote images. Runtime was 46.7–71.9 seconds per recording; native debug runtime is recorded separately. These are single-run implementation-specific timings, not general language or decoder rankings.

The reference uses `WINDOW_FACTOR=30`, strongly smoothing horizontal detail. An explicitly recorded sensitivity check on recording 1 reduces that value to 3 without editing upstream source (`reference-tuned-v1/`); this worsened color quantization. Its PD timing also deserves validation (LINE_TIME omits SYNC_PORCH). Therefore the visually poor reference output is NOT a defensible general claim of superiority over mature SSTV decoders. QSSTV/MMSSTV and a verified native SatNOGS reference remain necessary for a publication-quality benchmark. The installed SatDump lacks the newer SSTV pipeline; the available libsstv project currently implements encoding only.

No CRC exists for these analog pixels. A 640×496 output does not establish all pixels are correct, nor recovery of extra digital frames. No objective PSNR/SSIM or unique-byte gain is reported without appropriate ground truth. Source-author PNGs were not used as our decoder output or as an oracle for choosing timing.

## Outstanding work

- Strong external receiver comparison, input-rate sensitivity, and controlled independent ground-truth image/audio tests.
- Broader noisy corpus and frozen evaluation cohort (these seven files are development data).
- Multi-image handling, wider clock/rate errors and automatic mode selection; current scope is one image segment, maximum 248 pairs.
- LRIT and X-band instrument pipelines are not implemented by this change. The Fengyun download from the search was incomplete and must not be treated as a valid IQ test.
