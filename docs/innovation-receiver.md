# Experimental residual / codec-conditioned receiver

Build with `cargo build --release --example innovation_audio_probe`, then run:

```sh
target/release/examples/innovation_audio_probe \
  --input original.ogg --output new-experiment-directory \
  --threads 4 --codec-sideinfo
```

For mono WAV input, omit `--codec-sideinfo`. The side-information path requires
the original mono Vorbis OGG; it cannot recover discarded codec evidence from
a converted WAV. This experiment defaults to 9600 baud and accepts an explicit
`--baud`; this does not qualify every rate/modulation. Native DSP is Rust;
OGG decoding and side-information extraction use bounded FFmpeg/FFprobe.

The output directory must not exist. It contains a source/executable-bound
plan, optional packet metadata, and a result or explicit failure. The report
separates the retained adaptive baseline from matched-white, innovations and
codec-innovations lanes. Rejected codec calibration does not mean a missing
or failed observation: it disables the optional weighted lane while keeping
the matched-white and innovations experiments.

This is an opt-in library/example, not a newly qualified default and not yet
a checkpointable task in the quick/deep/full progressive bank. The additive
union here preserves the existing adaptive interface, not the additional
blind/multi-anchor stages of the complete progressive receiver. Do not replace
that receiver with this experimental example based on a partial comparison.

See the [frozen development protocol](innovation-audio-protocol.md). Predictive
colored-noise sequence detection is established prior art. The codec-specific
variance feature is an unqualified proposed contribution, not evidence that
the receiver knows the compression error or is scientifically novel.
