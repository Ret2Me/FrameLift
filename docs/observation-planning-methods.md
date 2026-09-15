# Methods: probabilistic satellite reception and global scheduling v1

## Study design

The executable protocol was frozen before full cohort retrieval. A small
25-observation API feasibility sample and transmitter-level aggregate counts
were inspected first, so this is prospective for the full cohort rather than a
pristine preregistration of the research question. The frozen protocol and data
card record every allowed outcome, feature, split, model, threshold and claim
boundary. A later partial-count QA amendment added five-cluster minimums for
the conditional-decode uncertainty calculation before any model was fitted;
the protocol amendment log discloses it.

## Dataset construction

The acquisition client performs HTTPS GET only, enforces same-origin redirects,
caps response size, follows DRF cursor `Link` headers and checkpoints every raw
page. Requests select ten NORAD IDs in half-open monthly observation-start
intervals. The public anonymous request limit is handled sequentially; saved
cursor pages are never requested again on resume.

Network exposes no transactional snapshot token. The closed interval and raw
page hashes make the extract immutable after retrieval, but do not make the
multi-hour cursor walk atomic with respect to late source-side vetting or
backfill; page timestamps preserve this limitation.

Normalization fails closed for failed/future jobs, invalid coordinates,
unconfirmed or missing transmitters, malformed catalog IDs, mismatched TLE
lines or checksums, TLE epochs after observation start, non-positive windows and SGP4
errors. Duplicate observation IDs must have identical canonical content.
Exclusion counts and all source hashes are machine-readable.

SGP4 samples each observed window at 30-second intervals including both
endpoints. Upstream source inspection established that the API's top-level
station fields expose retrieval-time coordinates rather than its cached
historical coordinates. V4 therefore prefers the capture-time latitude,
longitude and elevation preserved in public client metadata and explicitly
flags current-coordinate fallbacks. Only capture-time coordinates are eligible
for historical terrestrial weather. Coordinate-derived range and Doppler remain
diagnostic; probability models use the observation-cached schedule maximum
altitude and rise/set azimuth, duration, and embedded-TLE age.

An allowlist also extracts capture-time receiver software, SoapySDR driver,
receiver RF-gain setting, sample rate, PPM correction and receiver port. Raw
metadata, paths and device identifiers are not copied. These settings are
coverage/audit evidence and are not used as same-observation prediction inputs;
receiver RF gain is not treated as physical antenna gain.

## Probability models

Four frozen models are compared:

- `global_rate`: `(successes + 1)/(observations + 2)` in the training fold;
- `group_rate`: empirical-Bayes shrinkage of prior satellite, station and pair
  histories toward the training-fold global rate;
- `geometry_logit`: L2-regularized logistic regression on cached elevation,
  log duration, log TLE age and circular cached rise/set azimuth;
- `full_logit`: geometry plus frequency, baud, transmitter
  metadata, satellite/station/transmitter categorical identities and strictly
  trailing counts/rates from observations completed before prediction start.

Numeric features receive training-fold median imputation, an explicit missing
indicator and training-fold standardization. Categorical vocabularies are fit
on training data; unseen values are all zero. The logistic objective is mean
binary log loss plus L2 penalty excluding the intercept. Lambda is selected
from `{0.01, 0.1, 1, 10}` by three forward temporal validation blocks inside
the training fold.

The primary split is the final 20% of labeled timestamps. The nominal cut moves
to the nearest viable boundary between distinct start times, so simultaneous
observations stay entirely in training or test; any observation crossing the
cutoff is excluded from both sides. Forward regularization validation also
partitions whole timestamp groups and requires training observations to end
before validation begins. Transfer audits train
only on the earlier 80%, exclude each satellite or station in turn, and test on
that held group's later records without using held-out outcomes for history
features. Primary metric is Brier score; secondary metrics are log loss, AUROC,
equal-count ten-bin ECE, and logistic calibration intercept/slope. The paired
Brier difference uses 2,000 seeded cluster-bootstrap replicates, clustered by
satellite for the temporal holdout and satellite-transfer audit, and by station
for the station-transfer audit. The temporal comparisons are additionally
repeated with station clustering as a crossed-dependence sensitivity analysis.
A usefulness flag is disabled unless the evaluated predictions contain at least
five clusters, even if its numeric confidence interval happens to exclude zero.
The v4 external-domain analyses additionally use one frozen satellite cohort
and one identifier-only 20% station cohort. Training is earlier-period and
group-disjoint; testing is later-period and must contain the predeclared minimum
number of distinct observations plus at least five actually represented unseen
groups (or all groups when the frozen cohort is smaller). Row thresholds count
observations rather than the four model predictions per observation. The
independent audit reconstructs this membership and recomputes the external
metrics and cluster bootstraps from their separately hashed prediction files.
The temporal `signal_present` comparison of `full_logit` against `global_rate`
is the sole confirmatory comparison. Decode, transfer, station-cluster,
alternative-model, and ablation intervals are secondary/sensitivity results;
no family-wise multiplicity claim is made for them.

Direct paired ablations compare `full_logit` with `geometry_logit` and with
`group_rate` in every split using the same cluster bootstrap and 5%/upper-CI
usefulness rule; temporal ablations also include the station-cluster
sensitivity.

## Scheduling replay

Every temporal-holdout observation becomes one opportunity worth one nominal
sample. Each ground-station ID is an independent unit-capacity receiver; windows
are half-open. The same conflicts are given to chronological, maximum-elevation
and longest-duration greedy rules, a `full_logit` probability MILP, and an
oracle MILP restricted to successful held-out opportunities. The oracle is an
upper bound only. HiGHS must prove MILP optimality.

Reported outcomes are realized successful observations, predicted expected
observations, selected count and per-satellite successful coverage. Seeded
paired day bootstrap compares the probability MILP with each non-oracle greedy
baseline. Since Network exposes already scheduled jobs rather than every
counterfactual pass, natural same-station conflict density is reported. A
zero-conflict replay is explicitly non-informative for scheduler gain.

Implementation exactness is checked separately on 100 weighted single-receiver
interval instances (40 candidates each) against classic dynamic programming and
50 crossed three-station/five-satellite contention instances (10 candidates
each) against exhaustive enumeration. These synthetic checks validate the MILP
implementation; they are not empirical yield evidence.

## Dynamic TLE and risk analyses

Successive distinct embedded TLEs for a satellite are propagated over the next
observation's station window. A predicted pass must fall within 20 minutes of
the observed job midpoint, preventing an adjacent orbit from being mistaken for
the same pass. The older and newer predictions are compared for window
addition/removal, start shift and duration shift at 5, 15, 30 and 60 seconds.
If neither propagation yields a pass inside the matching tolerance, the
transition is reported separately as `indeterminate_unmatched_both`; it is not
silently treated as an immaterial change.
If either historical element set cannot be propagated (for example SGP4 error
code 6 after orbital decay), the transition is reported separately as a
`propagation_error` and excluded from material-shift denominators rather than
terminating the study or being treated as zero drift.
Because Network ingestion time is absent, TLE epoch is explicitly
reported as a proxy in freeze-horizon sensitivity results; drift is relative to
the newer planning geometry, not physical truth.

The production planner regenerates opportunities after material TLE, blocker,
weather or history changes while preserving a configurable near-term freeze
horizon. Its correlated Monte Carlo sensitivity shares station-day outages,
satellite-day transmit outages and severe-environment degradation. Those risk
parameters remain scenarios until calibrated locally and are not presented as
weather forecasts. The publication artifact applies the two fixed, disclosed
scenario tuples in the protocol to one seeded, 30-day synthetic conflict plan;
the environment shock is shared within each simulated day.

## Reproducibility and audit

Artifacts include the frozen config, raw response manifest, raw pages,
normalized JSONL and manifest, fold predictions, metrics, every bootstrap draw,
scheduling assignments, TLE drift records, environment capture and test output.
An independent module re-reads JSONL, matches every prediction/replay row back
to its immutable label and identity, checks the complete four-model fold grid
and conflict-free replay selections, and recomputes hashes, label counts, Brier,
log loss, AUROC, equal-count ECE, calibration intercept/slope, pooled and
per-fold transfer summaries, every seeded cluster/day bootstrap, replay totals, and every seeded
independent/correlated-risk Monte Carlo result (including per-satellite means)
without importing model, evaluation, or simulation code. External release
additionally requires human attribution, source-permission and secret scan
checks.
