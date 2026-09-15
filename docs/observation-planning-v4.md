# Observation planning v4: expanded and prospective protocol

## Current status (2026-09-04 UTC)

The publication workflow is active and the prospective workflow is gated on it:

1. `telemetry-yield-publication-v4.service` is acquiring the frozen 50-target
   historical panel. Acquisition is resumable and rate-limited. After the raw
   snapshot completes, the service normalizes observations, fetches real
   forecast-aligned Open-Meteo and GFZ covariates, evaluates temporal and
   external-domain holdouts,
   runs the independent audit, and writes manuscript artifacts.
2. The outcome-blind pre-start dependency audit superseded v4f before its first
   timer trigger because it still referenced the smaller v3 history and did not
   load a frozen learned model. After the complete v4 snapshot, count gate,
   evaluation and independent audit pass, the historical service runs one
   fail-closed v4g pre-start solve and only then enables
   `telemetry-yield-prospective-v4g.timer`. It runs the primary 50-target,
   13-station read-only shadow planner daily for 31 days, followed by two
   reconciliation-only days for delayed Network results. The dated config is
   materialized once at `work/prospective-v4g/config.json`; its start is the
   first UTC midnight at least 48 hours after all historical gates pass. This
   avoids guessing an acquisition completion date from 25-row cursor pages.
   User lingering keeps the services scheduled without an interactive login.

The frozen historical SatNOGS acquisition keeps the disclosed placeholder
identity with which it started. Once that snapshot is complete, every later
Open-Meteo, GFZ, NOAA, SatNOGS and CelesTrak request is fail-closed
until `work/operations/publication-api-contact.json` contains a user-supplied,
non-placeholder email address or HTTPS project URL. The validated User-Agent
and the contact artifact hash are frozen into the dated campaign config; the
runner rejects any different runtime identity.

The user timer `telemetry-yield-publication-v4-resume.timer` checks that release
gate every five minutes. A missing contact is an intentional no-op; a valid
contact starts the resumable builder, while an already materialized
`work/prospective-v4g/config.json` suppresses further polling. This avoids an
unattended gap between the historical snapshot and covariate acquisition
without weakening the API-identification gate.

Before any post-acquisition dataset build, `run-publication-v4-tests.sh`
creates the canonical source manifest, runs the complete pytest suite and writes
`test-attestation.json`.  The attestation binds the JUnit bytes and timestamps to
that earlier source identity and requires JUnit coverage for every planning test
module present in the manifest.  Regenerating a source manifest after a code
change cannot make an older test report current: readiness independently checks
the manifest, JUnit hash, chronology, command and module coverage.

The input-only thresholds and temporal/provenance rules are separately frozen
in `configs/observation-planning-method-contract-v4.json`. The source-manifest
builder validates that artifact against the exact code-level contract and binds
its bytes into the source identity. Readiness exposes a dedicated fail-closed
check, so changing a coverage threshold, admitting current station coordinates
as historical weather locations, reinterpreting receiver gain as physical
antenna gain, or weakening an end-time split requires an explicit new protocol
and source identity rather than an unnoticed code edit.

Neither workflow may be described as a completed publication result yet. The
historical v4 result exists only after the service finishes and its independent
audit passes. Before any model is fitted, a fail-closed count and diversity gate
requires at least 1,000 labeled signal rows and 300 conditional-decode rows,
representation of all 50 targets, signal labels from at least 40 satellites and
conditional-decode labels from at least 25. Every temporal and external
training split must contain both outcomes; every future test split must contain
both outcomes and at least 30 observations, and each external test must cover
at least five held-out satellites or stations. The independent audit recomputes
these conditions from the normalized rows instead of trusting the dataset or
evaluation summaries. The same future-row, class and group requirements apply
to the directly reported end-to-end packet-reception endpoint. The prospective
result exists only after 30 elapsed days, at
least 25 distinct plan-commit days, and 300 reconciled outcomes with the
predeclared target, station and outcome-class diversity. Those conditions are
machine-enforced in the prospective report.

The final `readiness.json` is a unified fail-closed verdict. In v4 mode it also
requires at least 50 unique targets, ten predeclared external satellites, a 20%
external-station split, successful future-only evaluation for both probability
tasks, capture-time client coordinates for at least 85% of normalized rows,
capture-time receiver metadata for at least 75%, historical Open-Meteo coverage
with all five numeric weather fields for at least 95% of rows eligible by those
captured coordinates, at least 95% GFZ coverage, a hash-valid
prospective ledger, and real Open-Meteo/NOAA/SatNOGS antenna inputs on at least
80% of committed planning days. Input presence alone is insufficient: it also
requires hash-valid plan files, a feature-use contract on the selected contacts,
an exact per-assignment binding to the single frozen model and training dataset,
an explicit active-or-evaluation-only disposition for terrestrial weather and
Kp, and active antenna-frequency constraints, plus a fitted weather ablation for
both outcomes on an untouched temporal tail. Every plan must also
bind unique immutable blocker and target-rule snapshots; readiness independently
rejects blocked assignments and mismatched calendar, priority, yield or
transmitter-physics overrides. It is regenerated after the historical build and
by every subsequent prospective run.

## Expanded cohort

The frozen configuration is
`configs/observation-planning-publication-v4.json`.

- 50 satellites, increased from 10 in v3.
- Every satellite has the same outcome-blind UTC exposure: four 14-day
  seasonal development windows (September, December, March and June) plus the
  complete final month of August 2026. This 87-day panel was frozen before any
  v4 model fit or performance evaluation. It retains every transmitter and
  every observation status inside the windows, while preventing a prolific
  satellite or obsolete transmitter from consuming months of API quota merely
  because it has more observations outside the common sampling frame.
- The start of the final interval, `2026-08-01T00:00:00Z`, is the exact frozen
  cutoff for temporal, leave-one-group-out, covariate-ablation and external
  validation. The legacy 20% row-count split is ignored for v4 and remains only
  as a compatibility fallback for configurations without a sampling panel.
- 336,068 lifetime Network observations across the selected transmitters at
  selection time (a sampling-frame count, not the eventual v4 row count).
- 14 represented frequency-band x modulation-family strata.
- One transmitter per satellite was selected using active/alive/confirmed
  status, packet capability and total observation volume only to construct the
  outcome-blind target pool. Acquisition itself is NORAD-wide and retains every
  transmitter and every outcome inside the common UTC intervals, preventing
  the sampling-frame transmitter from silently filtering the evaluation set.
- The completed input-only acquisition found four frozen targets with zero rows
  in all five common intervals (NORAD 38760, 39215, 48256 and 53106). Before the
  final full-cohort fit they were replaced by NORAD 57174, 38251, 41948 and
  63834, respectively. Each replacement preserves the vacated frequency-band x
  modulation-family stratum and is the highest-ranked otherwise eligible
  candidate with at least one August-2026 observation. Reception labels and
  exploratory model results were not used. The exact preflight responses,
  hashes, zero-row evidence and deterministic holdout recomputation are frozen
  in `reports/observation-planning-publication-v4/cohort-zero-exposure-amendment-20260905T123906Z.json`.
- Exploratory shadow fits existed before this zero-exposure amendment. The
  historical full-cohort evaluation is therefore reported as a locked
  post-exploratory evaluation, not as an untouched confirmatory test. The
  subsequently frozen month-long prospective campaign remains the independent
  validation boundary.
- Good/bad rates are retained for audit but are not used for eligibility,
  ranking, stratification or holdout assignment.
- Ten satellite IDs are frozen as an external validation cohort. They never
  enter training. A reported external result must contain later-period rows
  from at least five of those satellites and at least 30 observations with both
  outcome classes for each modeled task.
- Twenty percent of observed station IDs are selected by a seeded SHA-256 rank
  and held out as a second external domain. The split uses identifiers, not
  labels or performance. The same minimum of five actually represented test
  stations, 30 future observations and both outcome classes applies.
- `minimum_external_test_rows` counts distinct held-out observations, not the
  four model predictions emitted for each observation. The independent audit
  reconstructs the expected future-only membership from the normalized data,
  verifies the complete four-model grid, group coverage, hashes, probabilities,
  metrics and bootstrap artifacts.
- SatNOGS temporary 98xxx identifiers are excluded because the operational TLE
  provider cannot reliably refresh them.

The acquisition service is intentionally slow. It respects the anonymous
Network request interval, hashes every response, checkpoints after every page,
and resumes after a transient failure instead of restarting.

While acquisition is live, the operations monitor independently rehashes every
raw page already listed in the checkpoint and computes provisional metadata
coverage from unique observation IDs. That check accesses only `id` and
`client_metadata`; its report records an empty outcome-field access list and
never reads signal/decode labels. It is an early warning against a collapsing
capture-location or receiver-metadata fraction, not a replacement for the final
normalized-cohort readiness calculation.

An earlier unbounded full-year acquisition was stopped before normalization or
model fitting after an input-only audit showed that its first satellite had
14,865 observations, 14,851 from a transmitter other than the one used to
construct the sampling frame. At that point the checkpoint contained 817 pages
and 20,133 rows. Its config, manifest and raw pages are retained under
`*-superseded-unbounded-20260904T024336Z` names. The amendment did not use a
model score or success-rate comparison: it replaced unequal observation-volume
exposure with equal time exposure and a predeclared future tail. The exact
reason, hashes and process boundary are recorded in the amendment audit.

Each response is retained once as a raw JSON page with a separate hash-bound
sidecar containing its cursor and manifest entry. The publication acquisition
path does not retain the client's additional encoded HTTP-cache copy: the raw
page/sidecar pair is already sufficient for exact resume, and avoiding the
duplicate reduces storage pressure during a multi-day cohort fetch. The legacy
`--cache-dir` argument remains accepted so older launch scripts keep the same
interface.

SatNOGS Network's current implementation fixes observation pages at 25 rows
and defaults anonymous clients to 60 requests/hour. The collector therefore
uses a 61-second interval; even the bounded 50-target panel can require days,
not minutes. This is an upstream service constraint and is not bypassed with
parallel anonymous clients. Historical acquisition and prospective outcome
reconciliation share one `fcntl`-locked timestamp file, so independent systemd
processes cannot accidentally exceed that same IP-level observation quota.

```console
systemctl --user status telemetry-yield-publication-v4.service
journalctl --user -u telemetry-yield-publication-v4.service -f
```

## Real environmental data

Historical analysis uses Open-Meteo Historical Forecast API hourly data (air
temperature, relative humidity, surface pressure, 10 m wind and precipitation)
and GFZ Potsdam three-hour Kp including source status (`def`, `pre`, etc.). The
historical weather endpoint exposes the same variable contract as the live
Open-Meteo Forecast API and is intended for ML training on operational model
output; this avoids an unmeasured NASA-to-Open-Meteo deployment-domain shift.

Terrestrial weather is requested and joined only where the observation's
`client_metadata` preserves latitude, longitude and elevation from the actual
capture. Upstream source inspection showed that the public observation
serializer otherwise substitutes the station's current coordinates. Such
fallback coordinates are retained with an explicit provenance flag for
diagnostics but are ineligible for historical weather. Readiness requires at
least 85% capture-location coverage and then 95% complete weather coverage
within that eligible subset; it does not lower the weather-completeness rule or
silently backdate a moved station.

The same allowlisted client metadata yields historical receiver software,
SoapySDR driver, receiver RF-gain setting, sample rate, PPM correction and
receiver port. File paths, device serials and unrestricted metadata are
discarded during normalization. The RF-gain setting and receiver port are not
mislabelled as physical antenna gain or type and are not used as features for
their own observation. The public current antenna type and frequency ranges
remain real prospective feasibility constraints; measured antenna gain/noise
remain optional local declarations with evidence hashes.

For the expanded cohort, nearby observation dates are coalesced into weather
requests across gaps of at most seven missing days, while the immutable archive
retains only UTC hours that contain an actual cohort observation. This changes
neither feature values nor row coverage; it prevents sparse stations from
materializing months of unused hourly records. On v3 the same rule would reduce
the weather archive from 279,408 to at most 68,976 station-hours.

The completed v3 enrichment contains 4,789/4,789 rows with terrestrial weather
and 4,788/4,789 rows with a containing Kp interval. The archive contains
279,408 station-hours and 2,894 Kp intervals; raw HTTP responses are cached and
SHA-256 bound.

Prospective planning captures Open-Meteo hourly forecasts and NOAA SWPC
planetary K-index predictions before passes. Values outside a provider's
forecast horizon remain missing until a later daily replan; they are never
extrapolated to fill a month. The audited v4f pre-start input contains 4,680
forecast station-hours, 17 predicted Kp intervals and 13 current hardware
snapshots.

Open-Meteo reports surface pressure in hPa and defaults wind speed to km/h.
The frozen client now requests m/s explicitly, validates every returned unit,
and converts hPa to kPa before writing `WeatherRecord`. The 2026-09-03
pre-start diagnostic archive predates this correction and is retained rather
than rewritten; terrestrial weather was evaluation-only in that baseline plan.
All in-campaign daily captures start with the corrected unit contract.
The retained NASA POWER auxiliary client also validates response metadata. Its
hourly `PRECTOTCORR` rate is reported in mm/hour and is retained without
rescaling. It is not the V4 training source; a different unit label fails
closed. Archive hashes bind values and the normalized-unit contract.

Weather/Kp are joined using station ID plus the containing UTC interval.
Numeric missingness is represented explicitly in model preprocessing. A
covariate absent from the whole training fold becomes a zero column rather than
a duplicate intercept.

All temporal splits and historical-rate features use observation end times,
not only start times. An observation crossing a train/test cutoff is discarded
from both sides, and its eventual label cannot enter the history of an
overlapping pass.

The first exploratory v3 ablation does **not** show a Brier-score improvement
from weather on the small old cohort:

| task | operational Brier | + weather/Kp Brier | test rows |
|---|---:|---:|---:|
| signal present | 0.05495 | 0.05794 | 322 |
| decode given signal | 0.10187 | 0.10299 | 77 |

The satellite- and station-cluster bootstrap intervals cross zero. Therefore
weather is retained for the predeclared larger/prospective evaluation but is
not advertised as proven beneficial and is not given an invented effect in the
planner. Real Kp is consumed by the conservative v3 estimator. In v4, Kp and
terrestrial values enter probability scoring only if the predeclared held-out
gate selects a weather-bearing family; otherwise they remain explicitly
evaluation-only in every immutable feature-use contract.

Every planned assignment now carries `probability-feature-use-v1`. It separates
`active_probability_features`, `evaluation_only_features` and
`active_feasibility_constraints`. Thus the publication audit cannot equate a
captured Open-Meteo field with an active scoring coefficient. The v4 readiness
gate accepts weather/Kp as active only when the frozen selection chose them, or
as evaluation-only when a non-empty rejection reason is recorded. It also
requires real fitting and held-out scoring of the weather model; a worse
held-out score is a valid negative result and blocks any claim that weather
improved prediction, rather than motivating an after-the-fact coefficient.

V4 also evaluates the operational quantity that a user actually asks for:

`P(packet reception) = P(reviewed signal) * P(demodulation artifact | reviewed signal)`.

The endpoint is defined only for packet-capable transmitter modes. A reviewed
`without-signal` observation is a failed packet reception; an unreviewed
waterfall or a non-packet mode is missing rather than silently negative. Every
held-out prediction stores both probability factors and their product. The
report includes Brier score, AUROC, calibration and a frozen 0.5-threshold
confusion matrix, including accuracy, sensitivity, specificity and predictive
values. It is calculated for the future temporal tail and independently for
the unseen-satellite and unseen-station cohorts. The audit reconstructs the
endpoint from normalized rows, verifies exact test membership, recomputes every
metric and confirms that each reported end-to-end probability is the product of
the two stored factors.
The scheduling replay uses this same product as its objective weight, so the
operational comparison counts received demodulation artifacts rather than only
visible signals. The older signal-only replay remains a secondary diagnostic.
Because public SatNOGS jobs have already passed through admission and scheduling,
either replay remains explicitly non-informative for scheduler gain when the
held-out observations contain no natural same-station conflicts.
This end-to-end extension is secondary, not confirmatory. It was frozen during
the documented input-level hardening phase, before any V4 model fit or V4
performance evaluation; the dated acquisition-amendment audit preserves that
chronology and the original signal-present comparison remains the sole
confirmatory hypothesis.
The weather candidate is eligible for a separately frozen successor campaign
only if it improves Brier by at least 5%, has at least five independent groups,
and the upper 95% paired-bootstrap bound is below zero under both satellite and
station clustering. All 2,000 bootstrap draws and their hashes are retained.

After the v4 evaluation, `planning-build-probability-model` freezes one model
per task. It chooses the lowest held-out Brier score only among optional models
that passed both cluster gates; when none passes, it freezes the operational
model without weather/hardware. It then refits that already selected family on
all historical rows. The artifact contains the complete encoder (medians,
scales, missingness flags and category vocabulary), coefficients, empirical
validation error envelope, training boundary, and SHA-256 bindings to the
dataset, dataset manifest, evaluation and prediction rows. A self-hash detects
coefficient or metadata tampering.

The runtime estimator reproduces the publication preprocessing exactly. A
parity test compares its serialized prediction to the in-memory training
pipeline for the full weather/Kp/hardware family. The campaign runner rejects a
model when its training-data hash differs from the supplied history or its
training boundary falls after campaign start. The superseded v4f ledger remains
an audit record of the conservative baseline; the dynamically dated v4g
successor is materialized only after the learned v4 artifact passes every
historical gate, so prospective outcomes cannot leak back into their own
predictions.

Campaign materialization also recomputes the dataset-manifest and evaluation
hashes referenced by both the model and the independent audit. During the
campaign, readiness independently requires every committed plan to contain
exactly one copy of the frozen model and its training dataset in the hashed
input list. Every assignment's immutable feature lists, selected task families,
model version, model hashes and training-data hash must exactly match that
artifact; only the explicitly enumerated antenna/modulation feasibility fields
may vary by opportunity. A copied, edited, duplicated or unrelated model can
therefore neither register nor satisfy the prospective publication gate.

```console
PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-build-probability-model \
  --dataset work/observation-planning-publication-v4/normalized-enriched.jsonl \
  --dataset-manifest reports/observation-planning-publication-v4/dataset-manifest.json \
  --evaluation reports/observation-planning-publication-v4/analysis/evaluation.json \
  --output reports/observation-planning-publication-v4/deployment/probability-model.json
```

`planning-write-publication-results` emits `table-deployment-model.csv`, and
the v4 readiness report reloads the artifact, verifies its self-hash, checks
the exact training dataset and evaluation identities, and confirms that the
training boundary precedes campaign start.

## Antenna truth boundary

SatNOGS station detail data provide real antenna type and supported frequency
ranges. Those ranges are used as hard compatibility constraints before an
opportunity reaches the optimizer. Ten band-complete configurations and three
redundant backup configurations were captured on 2026-09-03; every selected
transmitter frequency has at least two compatible campaign stations.

The public API does not provide calibrated antenna gain or receiver system-noise
temperature. These remain `null`; the implementation does not manufacture
values from antenna names. A local station owner can supply a versioned record
with measured gain/noise. It is joined only when both `effective_from` and
`known_at` are no later than the observation. A configuration captured today is
never backfilled into last year's training rows.
The pinned upstream model/serializer/view audit and exact allowed claim boundary
are recorded in
`reports/observation-planning-publication-v4/historical-antenna-truth-audit-20260903T232159Z.json`.

The declaration template is
`configs/prospective/station-hardware-declarations.example.json`. Replace every
fixture value, including the calibration-document SHA-256, then build the
validated archive with:

```console
PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-build-hardware-archive \
  --input configs/prospective/station-hardware-declarations.json \
  --output work/prospective-v4g/local-hardware.json
```

Pass that archive to `planning-fetch-forecast --hardware-archive ...`. The
daily runner will then inject measured gain/noise into the link-budget features;
otherwise both values correctly remain missing.

## External-domain validation

Three evaluations have distinct meanings:

- temporal: predict the last 20% of time from earlier observations;
- leave-one-satellite/station-out: repeat cold-start transfer over each future
  group;
- predeclared external cohort: train once without any held-out satellite or
  seeded held-out station and predict only that domain in the future tail.

The final model accepts missing numeric values using a training-fold median plus
a missing flag. An unseen category is all-zero. Consequently an unknown
satellite, station, antenna, gain or power does not prevent a probability; it
removes information rather than inventing it.

## Prospective planner

The first registered protocol failed before its start because five old v3
objects no longer had a current CelesTrak TLE. That failure is retained in the
original hash-chained ledger. Protocol v4a was frozen before the campaign start
and keeps all five targets with a valid current TLE. Its config records the
parent config/ledger hashes and the outcome-blind amendment reason.

The pre-start v4a plan covers the entire 31-day horizon:

- 4,711 feasible TLE-derived opportunities;
- 1,385 selected assignments;
- 297,982.62 expected unique sample-seconds under the current conservative
  model;
- SciPy/HiGHS MILP status `optimal`, MIP gap `0.0`;
- dry-run only: no SatNOGS jobs were submitted.

V4a remains the five-target operational smoke campaign. The 50-target protocol
then went through a fully preserved pre-start audit lineage:

- v4b replaced eight targets without a current TLE using the frozen,
  outcome-blind candidate order. Its solve produced 15,772 opportunities and
  6,140 assignments, but an exact antenna audit showed that only 23/50 targets
  had a compatible receiver and only 22 were assigned.
- v4c added stations 2830, 1433 and 657 by deterministic frequency set-cover.
  All 50 frequencies became compatible and its optimal plan contained 31,827
  opportunities and 9,878 assignments, but pure expected-yield maximization
  assigned only 45 targets.
- v4d required one assignment per target. It correctly failed as infeasible:
  continuously visible geostationary ALPHASAT was represented as one
  indivisible 31-day contact that would monopolize the sole L/S receiver.
- v4e split allowed visibility intervals longer than 900 seconds into
  independently selectable sessions. Its first optimal plan assigned all 50,
  but the independent audit found that segmented GEO sessions still looked up
  weather at the whole-pass culmination. A second run also observed station 657
  changing to disconnected. Both findings were fixed before campaign start.
- final v4f retains at most 900-second sessions, adds high-volume backup stations 1698,
  4690 and 432 by outcome-blind frequency multicover, and tolerates a currently
  disconnected station. The frozen set has at least two frequency-compatible
  stations per target.

An additional outcome-blind pre-start audit found that naive 900-second
segmentation could leave sub-minute tail fragments: 368 selected assignments
were shorter than one minute and ten were shorter than one second. The campaign
now requires every selectable session to last at least 60 seconds, one SGP4
sampling step. Shorter blocker- or segmentation-created remnants are discarded
before optimization. The prospective report independently enforces both the
60-second minimum and 900-second maximum on every committed assignment. The
authoritative final plan counts and solver gap are retained in the hash-chained
ledger rather than copied into this protocol before the replacement solve.

This is the primary prospective publication campaign; v4a continues as the
smaller reliability check. All intermediate failures and amendments remain in
the hash-chained ledger rather than being rewritten.

CelesTrak requests use three bounded retries with exponential backoff after a
transient timeout or retryable HTTP status. Every committed plan embeds the
exact name, two TLE lines, epoch, retrieval time, source URL and fingerprint
for every one of the 50 registered targets. Final readiness reparses checksums
and catalog IDs, reproduces every fingerprint, and requires complete 50-target
coverage on every in-campaign plan event. Thus a fingerprint alone cannot
stand in for reproducible orbit evidence.

Every daily run:

1. reconciles ended recommendations against naturally scheduled public Network
   observations;
2. captures new station, antenna, weather and Kp inputs;
3. refreshes every TLE and rebuilds the remaining monthly schedule;
4. solves all receiver and satellite contention globally;
5. captures the canonical source/config/test/protocol inventory immediately
   before solving, verifies every file against the running checkout, and binds
   that manifest together with the immutable plan, other input hashes and TLE
   fingerprints to the hash-chain;
6. regenerates the prospective gate report.

Forecast HTTP caches are namespaced by the exact planning-run capture time.
This is required for mutable endpoints such as NOAA's constant Kp forecast URL:
a byte-for-byte cached response from an earlier day cannot satisfy a later
capture. Repeated files with the same response hash are disambiguated by the
retrieval timestamp bound into the covariate archive and raw-evidence manifest.
An unavailable registered backup station is not treated as a data failure when
it is absent from the plan; weather and current SatNOGS antenna evidence remain
mandatory for every station that is actually assigned.

Planning start and ledger commitment are separate timestamps. The solver
reserves a one-hour lead before the first selectable contact, then records the
actual wall-clock commitment after optimization finishes. A plan is rejected
if even one selected contact has already started (or starts exactly at the
commit instant), and outcome scoring requires a strictly earlier commitment.
The runtime source manifest must be fresh at planning start; it is not made to
look fresh by backdating a long-running solve. This prevents contacts occurring
during optimization from leaking into the prospective score.

The final gate does not accept those declarations at face value. For every
selected assignment it independently repeats the station-plus-exact-UTC-hour
weather join, the containing three-hour Kp join, and the time-visible hardware
selection from the hash-bound forecast archive. It then compares every
serialized value and source label, proves that the planned frequency lies in a
documented range for the named SatNOGS antenna, and verifies that gain/noise are
present only when a time-valid record supplies them. Ambiguous joins, invented
values, a culmination outside the selected interval, or one mismatched
assignment fail the whole planning day. The primary v4g pre-start
reconstruction must pass for every assignment, and the ledger records the
resulting weather, Kp, antenna and public gain/noise counts. All assignments
must also pass both duration bounds, while an independent receiver/satellite
interval sweep must find zero overlaps.
Each archive now has a separate `observation-planning-covariate-evidence-v1`
artifact. It embeds every distinct raw Open-Meteo, GFZ, NOAA and
SatNOGS station-detail HTTP body referenced by the archive, together with its
approved HTTPS origin, retrieval time, byte length and SHA-256. The manifest is
itself bound to the plan (or historical readiness input), and final readiness
re-decodes every body, recomputes all hashes and requires exact agreement with
the per-record source hashes. This prevents a plausible-looking hand-authored
weather or antenna row from satisfying the “real data” gate.

All primary daily full rebuilds share a stable logical plan identifier derived
from `satnogs-network-shadow-v4g-trained-50-target-redundant-YYYYMMDD`. The persistent
SQLite store allocates the next immutable revision before every solve, so
retries and TLE-driven replans append revisions instead of colliding with or
overwriting an earlier plan.

An in-campaign run without that source manifest fails closed and cannot commit
a plan. Publication readiness additionally requires one valid manifest on
every in-campaign plan event, coverage of every committed planning day, one
unchanged source identity across the complete campaign, and equality with the
final independently checked publication source manifest. This makes a code or
protocol change after the prospective start visible as a failed preregistration
gate instead of silently mixing planner versions.

Maintenance and nonstandard high-priority observations are supplied as hard
receiver reservations. The bootstrap seeds the editable control
`work/prospective-v4g/blockers.json` from the empty reviewed template; use
`configs/prospective/blockers.example.json` only as a field-level example. The
next daily run validates the control, copies it to a unique read-only file under
`work/prospective-v4g/blocker-snapshots/`, subtracts those intervals, and commits
the snapshot hash. Older plan evidence therefore remains reproducible after a
later maintenance edit. Final readiness also reconstructs every interval and
rejects any selected assignment that overlaps it.

Satellite power, transmit-antenna gain, yield rate, priority, allowed UTC
weekdays/month-days/hours, and geographic downlink zones use
`work/prospective-v4g/target-overrides.json`, seeded from an empty reviewed
template. `configs/prospective/target-overrides.example.json` documents the
fields. Each daily run freezes a unique copy under
`work/prospective-v4g/target-override-snapshots/` before clipping every
TLE-derived pass to the declared transmitter windows. Final readiness validates
the frozen rule syntax and independently reproduces calendar/time eligibility,
priority, exclusivity, nominal sample yield and supplied transmitter physics.

Natural shadow matches are observational and potentially selected. They test
calibration and operational stability, not the causal gain from executing
planner assignments. A causal yield claim requires an owned station and a
randomized or stepped-wedge execution protocol.

Every in-campaign plan commitment stores all three distinct probabilities:
`P(signal)`, `P(decode | signal)` and their product `P(end-to-end success)`.
The prospective report computes a separate Brier score for each matching
endpoint. A reconciled row with unknown labels no longer satisfies a scoring
gate. With the registered minimum of 30 reconciliations, publication requires
at least 30 scored signal outcomes and at least 10 conditional-decode outcomes,
in addition to the elapsed-time and plan-coverage gates.
Each outcome also embeds the hash and timestamp of the latest prediction that
was committed before that pass began. A later replan with the same opportunity
identifier cannot overwrite the probability used for prospective scoring.
A later revision of the same full-plan lineage supersedes every older
assignment wholly inside its horizon when it was committed before that
assignment began, even if a TLE refresh or feasibility change produced a new
opportunity identifier. The prior plan remains active only in the lead-time gap
before the newer horizon begins.

Outcome reconciliation keeps a seven-day retry window for delayed Network
processing. Requests are restricted to the exact station–NORAD pairs that have
ended recommendations, and begin at the earliest still-pending pass in each
pair. The matcher uses the assignment's explicit numeric
`satnogs_station_id`; the local `station_id` is an opaque value such as
`satnogs-2968:UX5UL` and is never parsed as an API identifier. A Network
observation ID can be used only once across the complete
ledger. When several TLE-driven plan versions overlap the same natural job, the
latest prediction committed before the pass wins. Rows whose signal label is
still unknown remain pending instead of being frozen before SatNOGS finishes
processing them, unless a non-empty demodulated artifact already provides an
independently scoreable positive decode. That artifact does not promote the
unknown waterfall to a positive primary signal label. An explicit
`without-signal` review combined with a non-empty demodulated artifact is
excluded as contradictory, using the same fail-closed rule as the historical
dataset. Every recorded Network label carries hashes for both the
derived outcome file and its raw API snapshot. The final readiness audit
independently reloads those artifacts, reconstructs the label and latest
eligible prediction, verifies one-time use of each observation ID, and rejects
any broken link in that chain.

```console
systemctl --user status telemetry-yield-prospective-v4g.timer
journalctl --user -u telemetry-yield-prospective-v4g.service -f

PYTHONPATH=.:src .venv/bin/python -m telemetry_yield.cli \
  planning-prospective-report \
  --config work/prospective-v4g/config.json \
  --ledger work/prospective-v4g/ledger.jsonl \
  --output reports/observation-planning-prospective-v4g/status.json
```

## Remaining publication gates

- Wait until the materialized campaign end in `work/prospective-v4g/config.json`;
  elapsed time cannot be simulated or backdated.
- Obtain at least 25 distinct in-campaign planning days, with all 50 targets and
  every observation between 60 and 900 seconds in every committed daily plan.
- Obtain at least 300 scored Network outcomes covering at least 20 registered
  satellites and five registered stations. The frozen gate additionally needs
  at least 30 signal-positive and 30 signal-negative cases, plus at least 15
  successful and 15 failed decodes conditional on signal. An outcome carrying
  probabilities but lacking a prediction committed before its pass contributes
  to none of these counts. If natural shadow matches are too few, move to an
  owned station and freeze a successor execution protocol before inspecting its
  results.
- Supply measured gain/noise and a historical version source for an owned
  station if those effects are to be claimed.
- Replace the placeholder research contact and complete the persistent,
  hash-bound license/contact/attribution file from
  `configs/observation-planning-release-attestation-template.json` before
  external release. Daily prospective runs preserve and revalidate that exact
  decision instead of silently clearing one-shot flags.
- Before post-acquisition processing can start, copy
  `configs/observation-planning-api-contact-template.json` to
  `work/operations/publication-api-contact.json`, replace its contact value,
  and set `open_meteo_free_api_usage.attested` to true only if the work is
  genuinely non-commercial research under Open-Meteo's current terms. The
  five-minute resume timer then starts the builder automatically. This
  operational contact and usage attestation are content-bound into campaign
  registration and the contact must match the User-Agent on every prospective
  request.
- Do not submit SatNOGS jobs without the existing token plus exact export hash
  confirmation workflow.
