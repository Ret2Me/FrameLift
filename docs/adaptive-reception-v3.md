# History-first reception prediction and local planning

This is a **local research/shadow deployment**, not a station deployment and not
a claim that a universally optimal predictor has been found. It evaluates a
bounded, predeclared set of methods and retains a simple history reference when
the more elaborate alternative does not justify replacing it.

## Design

The two tasks are selected independently:

1. Probability of a detectable signal.
2. Probability of a demodulation file, conditional on signal evidence.

The second label is **not verified packet/CRC correctness**. Their product is a
planning proxy, not a measured number of recovered unique packets.

All methods receive availability-gated history from the same implementation.
Unknown labels are omitted; late uploads are unavailable until their receipt;
corrections replace the previous version rather than adding another example.
Local observations can enter the existing `LiveHistoryStore` through captured
`HistoryEvent` records. No live record is silently added to the training cohort.

The 23 candidates are:

- The 15 existing global/satellite/station and regularized pair-history rules.
- Four additional history rules: 7-day, 90-day, last-50 and transmitter-specific
  last-10 histories, with broader-population fallback when evidence is sparse.
- One histogram-tree model with physical/calendar features, 7/30/90-day and
  last-10/last-50 summaries, and the actual history-rule probabilities as inputs.
- Three fixed combinations containing 25%, 50%, or 75% of the tree prediction
  and the remaining weight from the established last-10 reference.

Modulation categories are learned only from the training population, without
hash collisions. Missing geometry/radio values remain missing; they are not
fabricated. A column absent throughout training becomes a frozen constant.

No RL is used. Monte Carlo is not needed to compute the expected-value
scheduling objective. Existing plan-risk simulations remain separate and do not
turn simulated outcomes into reception labels or accuracy evidence.

## Selection and safeguards

For each completed large-history-v2 milestone, v3 uses the **same immutable
cohort prefix, per-fold training IDs and fixed evaluation passes**. All 15 old
rules must reproduce the old predictions to numerical tolerance. Every fold
checkpoint binds its protocol, cohort, task, period and training-population hash.

March chooses one challenger using mean squared probability error (Brier), with
deterministic ties preferring the earlier, simpler candidate. June is then an
explicit **development promotion gate**, not an untouched test. Promotion
requires all of:

- At least 0.001 lower Brier than the last-10 reference.
- No lower decision accuracy at the fixed 0.5 threshold.
- At least 10 day blocks and an exploratory day-cluster bootstrap upper 95%
  bound below zero for the paired Brier difference.

Otherwise the reference remains selected. This gate is conservative but is not
a familywise significance guarantee: March and June have been inspected during
development, and satellites/stations can remain correlated across days.
August and reserved groups are not opened. A separate frozen final evaluation
and prospective reception/plan comparison are still necessary for claims about
generalization and operational gain.

If a history-only method wins, no unnecessary full AI refit is performed. If a
learned or combined method wins, its full research fit uses every eligible label
in the frozen cohort. The selected bundle is hash-bound, reloaded, checked, and
published through an atomic `current.json` pointer only after the local audit.

## Running locally

Project root: `/home/ubuntu/telemetry-yield`.

The service `telemetry-yield-adaptive-method-v3.service` runs the comparison;
`telemetry-yield-adaptive-method-v3.timer` checks for newly completed v2 stages
every 30 minutes. Downloading and the original v2 learning curve continue
independently. This service has its own lock, a 3 GiB memory cap, two-CPU quota
and lower scheduling priority. It makes no external API requests.

For an explicit completed stage:

```bash
rtk proxy env OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  /home/ubuntu/telemetry-yield/.venv/bin/python \
  /home/ubuntu/telemetry-yield/work/operations/adaptive_method_v3.py run --stage 25000
```

For local inference, prepare a JSON object or a list of objects. Only `norad_id`
and a timezone-aware future `start` are required. Optional fields are
`station_id` (SatNOGS ID), `transmitter_uuid`, `transmitter_mode`,
`transmitter_baud`, `frequency_hz`, `max_elevation_deg`, `duration_seconds`,
`tle_age_hours`, `rise_azimuth_deg`, and `set_azimuth_deg`. Target outcomes and
unknown fields are rejected. Missing station identity uses broader history.

The start time and geometry should come from an actual TLE-derived opportunity,
not an invented future pass. The command uses the current issue time unless
`--as-of` is supplied; issue times before model creation or in the future are
rejected.

```bash
rtk proxy /home/ubuntu/telemetry-yield/.venv/bin/python \
  /home/ubuntu/telemetry-yield/work/operations/adaptive_method_v3.py predict \
  --request /absolute/path/to/passes.json --output /absolute/path/to/predictions.json
```

Only use the project's locally generated artifacts. Never load a pickle bundle
provided by an untrusted party. The loader checks report, artifact, source and
runtime identities before loading the local selected bundle.

## Existing TLE/month planner integration

Wrap the existing `OpportunityBuilder` with `AdaptiveOpportunityBuilder`, then
pass that wrapper to `DynamicObservationPlanner` in place of the old builder.
The existing builder still handles TLE geometry, transmitter calendars, antenna
frequency constraints, receiver capabilities and maintenance/high-priority
blockers. The adapter changes only reception probabilities, expected scores and
their provenance. The existing constrained optimizer then chooses compatible
observations by expected yield and target priority.

```python
from datetime import UTC, datetime
from pathlib import Path
from telemetry_yield.planning.adaptive_planner import (
    AdaptiveReceptionPredictor, AdaptiveOpportunityBuilder,
)
from telemetry_yield.planning.engine import DynamicObservationPlanner

root = Path('/home/ubuntu/telemetry-yield')
as_of = datetime.now(UTC)
predictor = AdaptiveReceptionPredictor.from_report(
    root / 'reports/adaptive-method-v3-20260908',
    root / 'work/live-history-v1-20260907/history.sqlite',
)
builder = AdaptiveOpportunityBuilder(
    existing_opportunity_builder, predictor, as_of=as_of,
    allow_extrapolation=True,  # Explicitly tentative month-ahead scores.
)
planner = DynamicObservationPlanner(existing_tle_provider, builder)
# Supply the actual targets, stations, horizon and blockers to planner.plan(...).
```

Recreate the adapter at each new issue time. TLE/blocker updates use the existing
refresh path. New history without a TLE change should request a rebuild via
`force_rebuild_reason='history-update'`; do not reuse a prior issue time.

The tested forecast lead is 2–26 hours. Longer/shorter leads are labeled
extrapolations; the planner adapter refuses them unless explicitly allowed.
Consequently a month plan is tentative and should be regenerated as its dates
approach, not presented as a validated month-ahead forecast.

Default expected-value scheduling (`risk_aversion=0`) uses the estimated mean.
The legacy interval fields carry conservative support bounds `[0, transmission
factor]`, **not calibrated 90% confidence intervals**, and the standard deviation
is an upper bound. Do not interpret them as measured probability precision.

No submitted SatNOGS jobs, existing station plans, or other research services are
changed by the adapter or the comparison service.

## Remaining limitations

- This candidate does not fit weather, transmit-power, antenna-gain or noise
  covariates; its feature-use contract says so. Existing radio feasibility checks
  are preserved but are not equivalent to weather-aware reception probabilities.
- A fresh ingestion time does not imply recent receptions. The protected live
  history bridge currently contains historical data, so outputs expose history
  age and warn about stale or missing coverage.
- No new held-out-station/satellite accuracy, month-long prospective gain, packet
  correctness, or calibrated uncertainty claim is made by this deployment.
- Any change to frozen source files requires a new version or documented
  amendment; never silently replace their recorded hashes.

Status and evidence are in `reports/adaptive-method-v3-20260908/`: `protocol.json`,
`progress.json`, `current.json`, and the per-stage results, fold checkpoints,
selected bundle, completion manifest and integrity audit.

Graphify was used to locate history/probability/scheduler relationships and keep
the new deployment separate from frozen experiments. Live facts were verified
from source files, reports and service state, not inferred from the graph.

Methodological references: [Google's heuristic-as-feature guidance](https://developers.google.com/machine-learning/guides/rules-of-ml),
[probability evaluation and calibration](https://scikit-learn.org/stable/modules/calibration.html),
and [SciPy's constrained integer optimizer](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html).
