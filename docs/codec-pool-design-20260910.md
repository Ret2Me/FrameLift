# Guarded multi-anchor codec calibration (experimental v1)

This is a bounded evidence-pooling wrapper around the existing Rust
`codec_reliability::fit_variance`, not a new demodulator or a loosened predictive
gate. Its purpose is to test whether multiple independently recovered source
frames provide enough codec-feature diversity to calibrate a transferable local
variance metric. Packet size and decoded frame duration remain codec proxies,
not measured quantization error. No scientific-priority claim is made here.

## Input contract and target exclusion

`codec_pool::fit_pool(metadata, sources, frontend, target_bounds, sample_rate)`
accepts only source-bound, original untrimmed PCM coordinates. Every `PoolSource`
contains the original recording SHA256, native received full-frame SHA256 values,
its complete frontend window, and separately guarded training/check residual
innovation blocks. The caller must independently verify the received CRCs and
frame spans and must have fitted signal/noise models without the codec check
suffix. The pool API cannot authenticate a supplied provenance claim.

The module requires matching recording SHA256, matching frontend and sample rate,
valid contained coordinates, a one-second exclusion zone around the *whole*
target window, and source window starts at most 60 seconds from the target start.
The latter matches existing adaptive anchor geometry. Target samples, target
CRC results and reference payloads are not arguments to the fitter.

## Deterministic evidence selection

Eligible sources are ordered by absolute source/target start distance, source
window start, source window end, then source ID. Selection never ranks by residual
likelihood, codec feature variation, model quality, or target decoding results.
Selected source windows cannot overlap. Full received-frame hashes cannot be
reused across selected sources: identical retransmitted payloads are also
conservatively excluded because a byte hash alone cannot establish independent
physical reception. Duplicate source IDs exclude every ambiguous copy.

At least two selected windows and two distinct native received-frame hashes are
required. At most 4,096 input sources, 32 combined training/check blocks and
131,072 samples per pooled partition are allowed. Every block contains at least
32 samples; each pooled partition contains at least 128. Capacity handling skips
whole sources in deterministic order; it does not subsample or retry subsets
after seeing a fit result. Every exclusion is recorded with a source ID, window
and reason. Structural invalidity causes a source rejection; invalid global
recording/target geometry is an error, not an implicit fallback.

## Scale normalization and validation

Each selected source has one RMS scale computed from **its training residuals
only**. Both its training and untouched check residuals are divided by this
same scale. No heldout statistic affects normalization. The original absolute
training-energy floor of 1e-14 is enforced before normalization, so tiny errors
cannot be rescaled into valid evidence. Coordinate order, train/check block
disjointness, finiteness and numerical magnitude limits are checked explicitly.

The normalized source blocks are pooled and passed to `fit_variance` exactly
once. Its fixed ridge regression and all existing gates remain unchanged:
four distinct codec packets, nontrivial encoded-density variation, at least
0.01 nats/sample mean heldout likelihood gain, and no heldout block below
-0.01 nats/sample. A rejected fit exposes no usable model. This is conditional
residual calibration, not a significance test or packet-recovery result.

The returned `PoolFit` retains selected source windows, unique frame hashes,
training RMS values, block coordinate extents, counts, original and normalized
residual hashes, all rejection reasons, and an optional statistical fit. Stable
statuses are `insufficient_sources`, `insufficient_samples`, `gate_accepted` and
`gate_rejected`. `prepared` is internal preparation state, never returned by
successful public `fit_pool`. Each fitted variance model remains bound to the
original OGG SHA256.

## Verification scope

Pure synthetic unit tests exercise predictive and nonpredictive codec proxies,
target and guard exclusions, cross-recording/frontend rejection, duplicate and
overlapping source rejection, input-order invariance, source-ID ambiguity,
training-only normalization under heldout mutation, energy-floor preservation,
and insufficient source evidence. These fixtures are **not actual compressed
audio**, and do not establish better recovery on real OGG. Parent integration
must preserve old decoder outputs, compare matched weighted/unweighted lanes,
and benchmark frozen code on a declared development or heldout cohort.
