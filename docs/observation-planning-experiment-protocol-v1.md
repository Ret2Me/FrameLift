# Frozen observation-planning experiment protocol v1

Status: frozen after a 25-observation API feasibility check and transmitter-count
inspection, but before downloading the evaluation cohort or fitting/evaluating
any planning model. This is therefore a prospective protocol for the full
cohort, not a pristine preregistration of the general research question.

## Pre-analysis amendment log

Recorded 2026-09-02T21:13:00Z, before the complete cohort existed and before any
model was fitted or evaluated:

- the API's mandatory 25-row cursor pagination was made explicit and every
  cursor page became a separately hashed raw artifact;
- time chunks were defined by half-open observation start to prevent a pass
  crossing midnight from falling between chunks;
- an operational server-side known-label filter was abandoned after timeout,
  so the final snapshot transfers all labels and excludes unknowns locally;
- LOSO was tightened to train on the earlier 80% while excluding the held group
  and to test only that group's later records, preventing future-time leakage;
- the scheduling replay gained a natural-conflict-density guard because the
  Network endpoint exposes jobs already admitted by a scheduler. Zero remaining
  conflicts make scheduler gain non-identifiable, not zero.

These changes respond to API semantics and leakage review, not observed model
performance; no cohort relationship or metric had been computed.

Recorded 2026-09-02T21:48:00Z after inspecting partial acquisition counts for
pipeline QA, but still before completing the cohort, fitting a model or
computing a model-performance metric: the conditional-decode promotion gate
now also requires at least five satellite clusters and five station clusters.
This makes the predeclared cluster-bootstrap uncertainty identifiable instead
of allowing a nominal interval based on one group. The target list, interval,
outcomes, features, models, thresholds and performance claims were unchanged.

Recorded 2026-09-02T21:56:00Z before any model fit or performance metric: the
engineering verification gained a 30-day synthetic robustness artifact. It
compares an independent Bernoulli simulation with two fixed correlated-risk
scenarios: `(station-day availability, satellite-day availability,
environment-day severe probability, severe multiplier)` equal to
`(0.98, 0.95, 0.10, 0.70)` and `(0.90, 0.85, 0.25, 0.50)`. These are sensitivity
assumptions, not learned weather/outage estimates, and cannot support an
empirical propagation claim.

Recorded 2026-09-02T22:00:00Z before any model fit or performance metric: a
consistency audit corrected “selected-plan churn” to the quantity the frozen
historical data can actually identify—material replan-trigger counts and
counts inside a TLE-epoch-based freeze proxy. Actual multi-satellite plan churn
cannot be recovered from successive already-scheduled Network observations.

Recorded 2026-09-02T22:18:00Z before any model fit or performance metric: the
existing geometry/full baseline design gained direct paired
`full_logit - geometry_logit` and `full_logit - group_rate` Brier ablations in
all three splits. The same predeclared useful-effect rule (at least 5% relative
reduction and upper 95% cluster-bootstrap endpoint below zero) applies. This
closes an analysis omission without changing a feature, model or cohort.

## Research questions

1. Do observation-cached schedule geometry and historical TLE age improve
   calibrated prediction of a human-confirmed satellite signal over a base-rate
   model?
2. Conditional on a confirmed signal and a packet-capable transmitter, do
   transmitter, receiver and historical-success features improve prediction of
   uploaded demodulated data?
3. Does a probability-aware global scheduler increase expected successful
   sample yield over chronological, maximum-elevation and duration-priority
   scheduling under identical resource conflicts?
4. How often do successive TLEs materially change future visibility windows,
   and how many potential replan triggers fall below a materiality threshold or
   inside a freeze-horizon proxy?

## Cohort and immutable raw snapshot

The cohort is fetched read-only from SatNOGS Network in half-open UTC
observation-start chunks (`start >= lower`, `start < upper`) using explicit
NORAD IDs and an explicit project User-Agent. Every
raw response is retained with request URL, retrieval time, SHA-256 and byte
length. The API data license is recorded as CC BY-SA. Duplicate observation IDs
must be byte-compatible after canonicalization or the build fails.

Unknown waterfall labels are transferred and counted but excluded from both
frozen model tasks. A feasibility attempt at server-side label strata was
abandoned before fitting because the unindexed query timed out; the final
snapshot therefore uses the simpler all-label query and preserves its unknowns.

The frozen target list must contain at least five active satellites with
packet-capable transmitters and non-trivial historical good/bad counts. Target
selection uses only transmitter-level counts, not feature/outcome relationships.
Those aggregate counts are a coverage-enrichment screen and may include the
evaluation interval; they are never model features or evaluation weights. The
selected transmitter UUID determines satellite eligibility, while acquisition
retains every transmitter actually observed for the selected NORAD ID and uses
that row's own UUID/mode.
The intended evaluation interval is 2026-03-01 through 2026-09-01 UTC, fetched
as calendar-month chunks. If an endpoint response exceeds the configured size
bound, the chunk is divided before any model result is inspected.

Publication promotion requires at least 1,000 observations with a known
waterfall label across at least five satellites and five stations. The
conditional decode task additionally requires at least 300 packet observations
qualified either by `with-signal` or by a positive demoddata artifact, both
outcome classes, at least five satellites, and at least five stations. Failure
to meet these counts is reported; the evaluation command independently
recomputes the gate and refuses to fit a real-cohort model. Targets or dates are
not expanded after inspecting model performance.

## Outcomes and exclusion rules

Primary outcome `signal_present`:

- `1` only for SatNOGS `waterfall_status == "with-signal"`;
- `0` only for `waterfall_status == "without-signal"`;
- unknown labels are excluded, never converted to zero.

Secondary outcome `decode_success_given_signal`:

- restricted to transmitter modes other than CW, FM, AM, USB and LSB;
- `1` when the observation contains at least one demoddata artifact, because
  the artifact itself establishes detectable signal even when the waterfall
  label is unknown;
- `0` when the confirmed-signal observation contains an explicit empty list;
- unknown when neither a positive artifact nor `with-signal` is present;
- excluded with `malformed_demoddata` when `with-signal` is present but the
  field is missing/not a list; `without-signal` plus a non-empty artifact is
  excluded earlier with `contradictory_signal_and_demoddata` for every mode,
  so it cannot survive as a false negative in the primary signal task.

Failed jobs, missing coordinates, malformed/mismatched TLEs, future jobs and
unconfirmed transmitters are excluded with a counted reason. `status == good`
is not an independent label because Network may set it from either a data
upload or waterfall vetting.

## Features and time boundary

Only information cached on the observation or available no later than its start
may be used by the probability models:

- observation-cached maximum elevation and rise/set azimuth, pass duration and
  embedded-TLE age;
- station identity (not retrieval-time coordinates or altitude);
- transmitter UUID, frequency, modulation, baud and published status;
- trailing success counts/rates from observations whose end is strictly before
  prediction start, for the satellite, station, transmitter and
  satellite-station pair.

No payload, waterfall image, post-observation status, future aggregate or test
fold outcome may appear in a feature. V4 may retain an allowlisted derivative
of public client metadata for capture-location and receiver-configuration audit,
but never copies raw paths/device identifiers and never uses the target
observation's receiver metadata as its own prediction input. Current antenna
metadata is treated as an optional sensitivity analysis because it may not
describe the historical station configuration.

Upstream source inspection at SatNOGS Network revision
`2ef19b8a1937e9143452e799dadeb64c7f26d1ae` showed that the observations API
serializes coordinates from the current `ground_station` relation, despite
cached coordinate columns on the observation model. Those retrieval-time
coordinates and SGP4 range/Doppler derived from them are retained only as
diagnostics and are excluded from both probability models. The observation's
cached `max_altitude`, `rise_azimuth` and `set_azimuth` fields are used instead.
The later V4 source audit at SatNOGS revision
`41e0ab7c1359b1dc18348b9f3bae647ba2b8bf52` found capture-time coordinates in
public `client_metadata`; these now replace current coordinates for diagnostic
SGP4 and make a row eligible for historical terrestrial weather. All
coordinate-derived quantities remain outside probability models.

## Frozen models

- `global_rate`: training-fold event rate with Laplace smoothing.
- `group_rate`: hierarchical Beta shrinkage over prior satellite/station history.
- `geometry_logit`: standardized logistic regression using observation-cached
  schedule elevation/azimuth, duration and embedded-TLE age.
- `full_logit`: geometry plus transmitter metadata and strictly trailing
  history features; missing numeric inputs get a missingness indicator and
  training-fold median imputation.

Regularization values are selected inside the training fold from
`{0.01, 0.1, 1, 10}` by three-block forward temporal validation. No neural or RL
model is added in v1. A later model must beat these frozen baselines on an
untouched successor cohort.

## Splits and metrics

Primary evaluation is a global temporal holdout: the final 20% of timestamps,
with all preprocessing and histories fit using earlier rows only. Transfer
audits are temporally nested leave-one-satellite-out and leave-one-station-out:
training uses only the earlier 80% and excludes the held group, while testing
uses that group's later 20% records. Folds with one outcome class are reported
but omitted from AUROC aggregation.

The nominal 80/20 cut is moved to the nearest viable boundary between distinct
start timestamps (ties prefer the earlier boundary), with at least ten training
rows. Observations sharing an exact start timestamp therefore never straddle a
training/test boundary, and observations whose end crosses the boundary are
excluded from both sides. Forward regularization validation likewise partitions
whole timestamp groups and requires training observations to finish before the
validation block starts. The independent audit reconstructs temporal membership,
counts and bounds from normalized rows and requires every transfer fold to cover
the same later-period observations with the declared held group.

Primary probability metric is Brier score. Secondary metrics are log loss,
AUROC, calibration intercept/slope, expected calibration error with ten
equal-count bins, and 90% interval coverage where available. Uncertainty for
paired Brier differences uses 2,000 deterministic cluster-bootstrap replicates,
resampling satellites for the temporal test and satellite audit, and stations
for the station audit. The temporal comparisons also report a station-cluster
sensitivity analysis. Any usefulness flag requires at least five clusters in
the evaluated predictions.

The predeclared useful-effect threshold for a model over `global_rate` is both:

- at least 5% relative Brier reduction; and
- a two-sided 95% cluster-bootstrap interval for the paired Brier difference
  whose upper endpoint is below zero.

Failing this threshold is a valid negative result and does not authorize a
post-hoc model or cohort change.

The single confirmatory comparison is temporal-holdout `full_logit` versus
`global_rate` for `signal_present`. Conditional decode, leave-one-group-out
transfer, station-cluster sensitivity, alternative models, and direct
ablations are secondary or sensitivity analyses. Their raw confidence
intervals are reported without a family-wise multiplicity claim; they cannot
replace a failed confirmatory result.

The same paired threshold is separately reported for `full_logit` versus
`geometry_logit` (increment from transmitter/station/history features) and
versus `group_rate` (increment from geometry and metadata beyond trailing group
history). These are the predeclared ablations.

## Scheduling replay

Only holdout observations are converted to opportunities. Competing schedulers
receive identical half-open windows, receiver conflicts and sample values:

- chronological first-fit;
- greedy maximum elevation;
- greedy longest duration;
- MILP using predicted probability;
- oracle MILP using the held-out binary outcome, reported only as an upper
  bound.

The primary probability scheduler uses `full_logit`; each Network observation
is one nominal sample regardless of baud or duration, because the API does not
expose a comparable count of unique payload samples. This choice was fixed
before the scheduling replay was run.

Metrics are realized successful samples, predicted expected samples, number of
scheduled observations and per-satellite coverage. A paired
day-level bootstrap compares probability-MILP with each non-oracle baseline.
Because Network observations are themselves already scheduled, the replay also
reports the number of natural same-station conflict pairs. If this is zero, the
scheduler comparison is declared non-informative rather than treated as
evidence of equality or improvement; synthetic conflict tests remain
engineering validation, not empirical yield evidence.

## TLE refresh audit

For successive historical TLEs of the same satellite, the older element set is
used to predict a later observation window and the observation-embedded TLE is
the scheduling-reference geometry. This measures schedule drift relative to
Network's updated planning state, not physical ephemeris truth. Start/end shift,
window addition/removal and material trigger counts are reported over
thresholds `{5, 15, 30, 60}` seconds. A candidate pass must culminate within
20 minutes of the observed job midpoint; if neither TLE yields such a pass, the
transition is counted as indeterminate rather than immaterial. An SGP4
propagation failure is a separate indeterminate state and never a zero-shift
transition. Counts inside freeze horizons
`{0, 15, 30, 60}` minutes use TLE epoch as the disclosed availability-time
proxy; they are not measurements of actual selected-plan churn.

## Reproducibility and claim boundary

The publication package requires raw-response hashes, a normalized dataset
manifest, frozen configuration, environment capture, deterministic seeds,
machine-readable fold predictions, metrics, bootstrap samples, scheduling
assignments, exclusion counts, source/license metadata, methods, limitations
and an independent arithmetic audit.

Count-gated successor configs are retained rather than overwriting this base
protocol. v1 failed only the conditional-decode row threshold at 217; v2
prospectively enriched the largest count-only contributor and reached 237; v3
prospectively added filtered preceding-period windows for the next two largest
contributors and passed at 382. No real-cohort model was fitted and no
performance metric was inspected before v3 was frozen. Exact parent artifact
hashes and selection reasons are part of the v2/v3 configs.

The study may support a calibrated scheduling claim. It cannot establish that
TLE is physical truth, that missing SatNOGS data means no transmission, that the
result transfers to proprietary stations, or that the existing fail-closed
frame decoder has improved.
