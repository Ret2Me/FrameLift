# Source-channel uncertainty: CPU development pilot

This opt-in Rust experiment tests a local predictive metric on post-FM mono
FSK/GMSK audio. It does not change the receiver CLI, progressive checkpoint
policy, frozen campaign executables, or acceptance rules. It is not a coherent
IQ GMSK receiver and is not a GPU implementation.

## Method and limits

The point model is the existing guarded three-tap ridge fit, bitwise unchanged.
With parameters ordered `[bias, left, center, right]`, source residual scores
are grouped into contiguous blocks of at most 32 symbols. A finite-sample
corrected block-sandwich covariance estimates uncertainty of that source fit.
It is PSD by construction and validated again at the detector boundary.

For bipolar local regressor `phi`, gain `g`, source covariance `C`, source
residual variance `R`, and fixed strength `a`, the branch metric is:

```
mu = g * phi' theta
S  = g^2 * (R + a * phi' C phi)
cost = (y - mu)^2 / S + log(S)
```

There are still four states and eight precomputed branch parameter pairs. The
logarithm and covariance quadratic are prepared once per trial, not per symbol.
Full traceback and deterministic tie order are retained. Strength zero and
identically zero covariance use the original detector directly. This is a
local predictive approximation, not exact joint Bayesian marginalization of a
shared uncertain channel. It assumes independent residual blocks, does not
estimate cross-packet channel drift or ridge bias, and is not calibrated on
held-out transmissions. A null result would not test a time-varying or
longer-memory channel model, which is not implemented here.

## Pairing

The reference is the existing `innovation_audio` portfolio without codec
features: adaptive baseline plus guarded white/AR innovations lanes. This is
not the full progressive portfolio with blind/multi-anchor branches, and no
SatNOGS/Dire Wolf re-benchmark is claimed.

New trials use the original adaptive source selection, clock/gain bank and
whole-window 1-second source/target guard within 60 seconds. Source frames are
independently revalidated before fitting. The point replay must reproduce the
original timing parameters bitwise and the complete accepted frame set in
every eligible trial. The matched point lane and both uncertainty lanes share
the same target soft symbols. Output lengths are checked before comparison.
Source statistical rejections are recorded; unexpected errors abort. Failed
uncertainty fits never cause a different nearest anchor to be substituted.

Strength 1 is primary; strength 4 is a predeclared sensitivity analysis. Both
are reported, without selecting a winner from the new results. The additive
union preserves the completed reference by construction; each standalone new
lane also reports its omissions relative to matched point detection. Received
FCS and UI structure are checked without checksum-guided bit search. Additional
frames never become new source anchors.

## Reproduce

Run from the project root, with the Rust toolchain on PATH:

```sh
cargo build --offline --locked --release --example uncertainty_audio_probe \
  --target-dir work/uncertainty-cpu-pilot-v1/target -j 2
cargo test --offline --locked --release --lib uncertainty \
  --target-dir work/uncertainty-cpu-pilot-v1/target -j 2 -- --test-threads=2
work/uncertainty-cpu-pilot-v1/target/release/examples/uncertainty_audio_probe \
  --input work/innovation-benchmark-old20-20260910-v2/acquisition/obs-14967362/capture.ogg \
  --start-seconds 321 --duration-seconds 60 --threads 2 \
  --output /absolute/path/to/a/new-result-directory
```

Exact five-case selection and input SHA-256 values are frozen in
`work/uncertainty-cpu-pilot-v1/selection.json`. Three real 60-second crops were
chosen from previously exposed, anchor-enriched development recordings. Two
18-second synthetic controls contain a legitimate source frame and no target
frame; their expected total is therefore not zero. This is a small feasibility
pilot, not a representative sample or publication holdout.

Each run creates `plan.json` before decoding, then `report.json` and
`summary.json`, or a failure record. Both arms receive the same decoded crop.
Reports retain exact FCS-inclusive frame sets, trial provenance, rejected
sources and absent-anchor coverage. Timings are shared-host elapsed wall time:
reference computation, source preparation and paired replay are separate.
Summed trial wall time is not process CPU time; the pre-output measurement
excludes report serialization/fsync. The harness includes duplicate point
replays for validation and is not an optimized deployment runtime.

## Expanded development follow-up (2026-09-13)

The unchanged executable was subsequently tested on a fixed selection of 40
different real 120-second OGG crops (80 minutes, 33 stations, CANVAS GMSK9600)
and eight existing 18-second synthetic OGG controls. The real result remained
420 → 420 observation/frame pairs (296 → 296 globally distinct frame values).
Both strengths matched the isolated point lane's exact 367-frame per-observation
sets. There were no additional frames or additional PDU bytes.

The method executed on 32 real recordings; the remaining eight had no usable
source anchor and are coverage failures, not executed uncertainty comparisons.
The intentionally sparse historical-yield subgroup of 11 recordings remained
116 → 116 frames. The eight controls remained 17 → 17 known frames; only two
clean target packets were recovered, with no additional impaired target or
unexpected frame. The experiment does not justify enabling this branch by
default. This is development evidence, not a representative publication test.

See [expanded results and exact protocol](../work/uncertainty-cpu-expanded-v1/README.md),
including frozen selection, independent audit and complete per-trial records.
