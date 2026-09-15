# Experimental real-sample Mueller–Muller control

This component is an established decision-directed timing detector, not a new
demodulation algorithm. It is opt-in and does not replace the frozen progressive,
adaptive, generic-IQ or private-campaign receivers.

`rust/mm_clock.rs` accepts finite real soft samples and an explicit, bounded
configuration. It provides linear and four-point cubic Lagrange interpolation.
The timing decisions do not use packet contents, reference bits, CRC success or
satellite identity. It is not a bit-exact GNU Radio implementation: interpolation,
floating-point arithmetic and boundary handling differ.

The diagnostic executable `examples/mm_clock_probe.rs` runs the whole input
through a fixed bank of 24 branches: two interpolators, four initial phases and
three gains. It tries plain NRZI and G3RUH AX.25 deframing. It retains received FCS
bytes and accepts only CRC-valid, structurally valid AX.25 UI frames. It does not
claim to validate every AX.25 form or native CCSDS framing.

```bash
rtk proxy /home/ubuntu/telemetry-yield/work/decoder-readiness-20260911/mm_clock_probe_v2 --input /absolute/mono-soft-samples.wav --output /absolute/new-output-directory --baud 9600
```

The output directory must not already exist. The result records input and
executable SHA256, any OGG conversion, every attempted branch, the full loop
configuration, exact frame bytes, completion and elapsed times. A failed branch
means an incomplete run, not a completed empty result. The executable supports
mono audio with at least 1.1 samples per symbol; it is not an IQ discriminator,
resampler or matched filter. The tested source-compatible SatNOGS pre-clock
input is 19,200 Hz at 9,600 baud. Separate development experiments use 48,000 Hz
audio. These are different receiver/input contracts.

## Current evidence, 2026-09-11

- A source-compatible SatNOGS frontend followed by this Rust timing/deframing
  component recovers the independently archived 264-byte CANVAS #5122 PDU,
  including its actually received FCS. This is a hybrid component control,
  not an end-to-end Rust IQ receiver result.
- A separate, existing end-to-end Rust generic-IQ receiver also recovers the
  same PDU from the complete raw IQ file. That result does not depend on this
  experimental timing component.
- A 60-second deterministic noise input produces no accepted frames. This
  small exposure is not a measured population false-alarm guarantee.
- The same fixed bank recovers the PDU from a reconstructed lossless 48 kHz
  audio branch, including after fixed amplitude scaling. Controlled Vorbis
  round trips at q3 and q4 yield zero accepted frames. The actual archived OGG
  also yields zero. These are development examples, not a final held-out test
  or proof that no possible receiver can recover the compressed signal.

The independent `decoder_readiness_audit` checks a narrower frozen pre-clock
contract. A minimal control-suite pass is not publication or deployment approval.
See `docs/decoder-readiness-audit.md` and the dated audit artifacts.

## Remaining engineering limits

The clock currently uses an in-memory whole-input buffer and does not export
resumable state. It is not yet part of the progressive scheduler, so its elapsed
time cannot be advertised as a quick/deep mode capability. Broader symbol-rate,
offset, fading, interference and protocol tests are needed before promoting it
from an experimental component. Received CRC is useful error-detection evidence,
not transmitter authentication.

Primary reference for the established detector and a comparison implementation:
[GNU Radio 3.10.9.2 clock_recovery_mm_ff](https://raw.githubusercontent.com/gnuradio/gnuradio/v3.10.9.2/gr-digital/lib/clock_recovery_mm_ff_impl.cc).
