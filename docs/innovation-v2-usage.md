# Experimental innovations receiver v2

This Rust entry point adds guarded multi-anchor codec calibration to the existing
innovations receiver. It is opt-in; the existing CLI defaults and old decoding
lanes are unchanged. Its current scope is mono FSK/GMSK audio and native AX.25
G3RUH reception, not a universal modulation decoder or a replacement for an IQ
station chain.

## Original audio

Build `cargo build --release --example innovation_audio_probe` and run:

```sh
target/release/examples/innovation_audio_probe \
  --input /absolute/path/original.ogg \
  --output /absolute/path/new-result-directory \
  --threads 2 --codec-sideinfo --pooled-codec
```

The source must be original, mono Vorbis with verified decoded sample coordinates.
The output directory must be new. Without the two codec switches the existing
unweighted innovations receiver accepts the audio formats handled by `input`.
The pooled branch cannot improve a recording with no usable source anchors by
itself; it calibrates only from independently CRC-valid received frames.

## Identical-input external comparisons

The benchmark creates a single mono PCM16 WAV from each original OGG:

```sh
ffmpeg -nostdin -v error -n -i ORIGINAL.ogg -map 0:a:0 \
  -c:a pcm_s16le -flags:a +bitexact -fflags +bitexact SHARED.wav
```

The bitexact flags omit the optional container metadata that the installed
Dire Wolf `atest` cannot read. The compatibility smoke independently checks that
these flags do not change the PCM samples. No resampling, downmix, gain adjustment
or hand-selected cropping is allowed. For the improved receiver use:

```sh
target/release/examples/innovation_audio_probe \
  --input SHARED.wav --output NEW_DIRECTORY --threads 2 \
  --codec-sideinfo --pooled-codec --codec-source ORIGINAL.ogg
```

The receiver independently recreates the canonical WAV and requires its complete
file hash and length to match the supplied WAV. Matching duration alone is
insufficient. Original codec metadata is explicitly additional information for
the improved arm; the comparator arms receive the same numeric waveform.

## Result interpretation

`result.json` contains independent received-FCS-validated frames, the old-lane
union, the complete new union, pooled calibration decisions and matched weighted
versus unweighted additions **and losses**. Distinct timing/window detections of
the same PDU are not additional telemetry. `added_pdu_bytes_excluding_fcs` counts
whole AX.25 PDUs excluding FCS, not application-only telemetry bytes.

A failed or rejected calibration is not a recovered frame. A rejected model does
not run a weighted lane. The fixed codec predictive thresholds are unchanged.
Source windows are disjoint from the whole target window with a one-second guard.
However, source frontend normalization and CRC timing were selected using the
whole source window: the fit check is a conditional residual holdout, not an
untouched raw-waveform holdout. This prototype is offline and may use later source
anchors; it does not establish a causal three-second receiver.

For the frozen five-arm experiment, see
[the benchmark protocol](innovation-benchmark-protocol-20260910.md). Original-OGG
OLD20 regression and shared-PCM OLD20 comparison are explicitly development
experiments. Neither may be relabeled as the fresh 500-observation holdout.
Publication readiness, scientific priority and equal-compute superiority require
separate evidence; no such claim follows from implementing this branch.
