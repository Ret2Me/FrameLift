# Decoder-assisted PSK synchronization

`rust/recovery_tracking.rs` provides an optional, bounded refinement of an
already acquired PSK burst. It does not replace acquisition, framing, error
correction, or integrity validation. A rejected refinement leaves the original
geometry intact. Receivers must retain the original decode branch even when
the waveform fit improves: lower signal residual does not guarantee more
correct frames.

## Inputs and supported waveforms

- Received complex IQ, sample rate, and an initial burst geometry.
- BPSK, QPSK or OQPSK with a rectangular or explicitly specified RRC pulse.
- Known sync/pilot expectations of `-1` or `+1`, followed in actual wire order
  by soft decoder **extrinsic** expectations `tanh(LLR / 2)`.
- Explicit search limits and an aggregate sample/pulse-work limit.

The decoder must undo interleaving/randomization on its feedback before
constructing the on-air expectations. It must not use reference payloads,
guessed application fields, CRC-directed bit changes, or decoder posterior
LLRs presented as independent observations. An unknown bit has expectation
zero, not an arbitrarily selected hard value. Soft means need not describe a
CRC-valid codeword; this permits a new synchronization attempt after an
unsuccessful decode without claiming its data are correct.

GMSK/GFSK/FSK and AFSK are explicitly rejected by this module. Their nonlinear
phase evolution requires a different likelihood; passing the average bit into
a nonlinear modulator is not the expected waveform. Their separate receiver
implementations remain available.

## Fit and guards

The model estimates fractional start, samples per symbol, carrier frequency,
linear carrier drift and a complex gain. Carrier phase at sample `n` is

```text
t = (n - carrier_reference_sample) / sample_rate
phase = 2*pi * (carrier_hz*t + 0.5*carrier_drift_hz_per_s*t*t)
IQ_model[n] = complex_gain * expected_PSK_waveform[n] * exp(j*phase)
```

For each trial geometry, complex gain is fitted by weighted least squares on
alternating source-symbol intervals. The optimizer uses normalized training
residual only. A bounded coordinate search evaluates up to four offsets for
each of the four geometry parameters per sweep; its steps halve each sweep.
Clock changes are coupled to a compensating timing shift, and frequency-drift
changes to a compensating carrier shift, around the fixed interval midpoint.
This reduces the strong start/clock and carrier/drift correlations while
respecting every original parameter bound. If the compensating parameter is
fixed by its configuration, its value is preserved instead.
This is a deterministic local refinement, not a guarantee of the global
maximum-likelihood solution.

The source interval, train/holdout split and uncertainty weights are frozen
before the search. Every hypothesis is evaluated on the common interior
supported by all permitted geometries. A trial cannot improve its score by
cropping difficult samples or moving low-confidence masks. RRC uncertainty is
computed from the pulse-weighted independent bit variance `1 - mean^2`; this
is a working approximation because decoder messages can be correlated.

Only the training winner is checked on held-out intervals. It must improve
the original fitted model by the configured relative amount and explain a
minimum fraction of held-out received energy. No alternative candidate is
selected after looking at a failed held-out check. No CRC, payload identity,
accepted-frame count, or known reference result enters this fit.

This is a **conditional waveform-consistency guard**, not an independent
false-alarm probability measurement. Initial acquisition and decoder messages
may have used the held-out samples. Final accepted frames still require the
normal protocol-specific integrity checks, and false acceptance must be
measured on separate negative recordings.

## Integration contract

`refine_psk` returns a report with a reason, the selected geometry, fitted gain,
work count and before/after residuals. Rejected reports retain the original
geometry and original fitted gain. `corrected_iq` refuses rejected reports.

For an accepted report, `corrected_iq` removes fitted carrier phase/drift and
divides by complex gain. It does **not** change sample positions, remove noise,
or replace any data with a synthesized waveform. The output is baseband:
subsequent filtering and sampling use the selected `start_sample` and
`samples_per_symbol`, without a second carrier removal. OQPSK arm delay and
conjugation remain part of the symbol extraction contract.

The normal branch and refined branch must be independently decoded and
unioned by validated frame identity. Tracking output alone is never a decoded
frame or proof of telemetry recovery. The library does not assert improvement
over Dire Wolf, gr-satellites or any previous FrameLift release.

## Bounds

The implementation accepts 32–8192 wire-bit expectations, 2–128 samples per
symbol and at most 1,048,576 IQ samples. At least 128 common interior source
samples and 32 sufficiently reliable samples in each partition are required.
RRC span is limited to 2–16 symbols. Searches that cross Nyquist, leave the
supported symbol-step range, or exceed the configured work bound are rejected
before the search. Work accounting covers rendering and scoring rather than
claiming a wall-clock deadline.

The module is CPU-only. Shared frontend caching or GPU implementations must
preserve the same evidence and acceptance contracts; GPU capability elsewhere
in the repository does not imply this search has a CUDA implementation.
