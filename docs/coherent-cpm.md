# Coherent finite-pulse CPM lane

`rust/coherent_cpm.rs` adds an optional complex-IQ sequence detector. It is not
enabled by existing qualified profiles. Keep the established discriminator lane
and union only frames passing the same protocol and integrity checks.

## Model and interface

The binary symbols are -1 and +1. A rational modulation index `h = p/q`
produces a finite phase state. The implementation uses log-domain sum-product
(BCJR), not hard-decision Viterbi, to return soft bit marginals and extrinsic
LLRs for a separately validated FEC decoder. It retains all phase/history
states: `2q/gcd(p,2q) * 2^(L-1)`.

- Rectangular frequency pulse: `L=1`.
- Gaussian: the repository's sampled, unit-DC, centered +/-2-symbol Gaussian
  filter, convolved with one NRZ symbol. A two-symbol causal delay gives `L=5`
  and four history symbols. GMSK uses `h=1/2`; rational GFSK uses an explicit
  numerator and denominator. The detector does not round arbitrary `h`.
- `prepare(iq, known, config)` estimates a constant complex gain and bounded
  residual carrier using only a contiguous known preamble (at least 16 bits),
  then freezes complex-sample likelihoods.
- `prepare_with_training_noise(iq, known, config, floor)` fits the same pilot
  once and uses `max(pilot_residual_power, floor)` for the likelihood noise.
  It produces the identical metrics to preparing twice to obtain and then
  apply that estimate, without constructing an unused first trellis.
- `PreparedCpm::detect(prior_llr)` reuses these channel-only metrics across FEC
  iterations. Extrinsic information is APP minus incoming prior *before*
  clipping. Known preamble constraints emit zero extrinsic LLR.
- `decode(iq, known, prior_llr, config)` is the one-shot convenience API.

Timing must already be aligned to integer samples per symbol. Noise power is
explicit `E[|w|^2]` in received-IQ units. Fitted gain, residual carrier, training
coherence, residual training noise and observation count appear in diagnostics.
No expected payload or decoded data is used to fit training parameters.

## Boundaries and bounds

`ZeroPaddedBurst` means *zero amplitude* outside the provided symbols, not
binary zero symbols. Initial and flushed trellis states explicitly omit these
nonexistent amplitudes. `UnknownOutsideWindow` retains **every received sample**
and marginalizes exterior symbols. Its initial history states are equiprobable,
with compensating phase states aligned to the same interior preamble reference;
the two Gaussian future symbols are unknown trellis branches, not zero bits.
This prevents the first or last payload bits becoming unobserved merely because
they lie at a supplied window boundary. No samples outside the window are
invented or scored. Phase/gain fitting still uses only interior known symbols,
where unknown exterior history contributes a common phase offset.

Limits are 16–4096 symbols, 2–128 integer samples/symbol, rational numerator and
denominator in 1–16 with `0<h<=2`, Gaussian BT 0.2–1, and 160 million branch
sample evaluations per preparation. The carrier fit remains within its explicit
configured bound (maximum 0.2 radians/sample). Nonfinite inputs, invalid priors,
insufficient known preamble and incoherent training are rejected.

## Scope and verification

The BCJR recursion is exact **conditional on this finite sampled pulse and the
frozen channel model**. The carrier fit is an estimate. The method is not exact
for infinite Gaussian pulses, arbitrary rate/timing error, unknown modulation
index, drifting carrier, or correlated noise introduced by upstream resampling.
It is CPU-only; cached branch metrics do not constitute CUDA acceleration.

Tests use an independently implemented centered-convolution transmitter, noisy
GMSK/GFSK and rectangular CPM, residual carrier and phase offsets, an interior
window with unknown exterior symbols, rejection controls, repeated preparation
parity, exhaustive waveform enumeration for four unknown payload bits, and an
independent 256-path enumeration including unknown preceding/future symbols.
An additional exact-equality test compares the single-fit preparation with the
two-stage reference across both boundary models, three pulses and two noise
floors. Recursion scratch storage is reused without changing the order of
finite-path sums or approximating log-MAP.
Synthetic bit recovery is not a measured advantage over gr-satellites or proof
of additional recovered satellite packets.

## Background

Coherent CPM trellises and rational-index phase states are established methods;
their implementation here is not itself a novelty claim. See the
[CPM demodulation algorithm description](https://www.mathworks.com/help/comm/ref/cpmdemodulatorbaseband.html)
and the research discussion of
[nonlinear trellis descriptions for coded CPM](https://arxiv.org/abs/1205.7031).
