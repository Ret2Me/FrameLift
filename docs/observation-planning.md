# TLE-aware monthly observation planning

This module plans receiver time; it does not decode or accept telemetry. The
existing classical, fail-closed CRC/protocol path remains the only authority for
payload acceptance.

## Implemented flow

1. `CelesTrakTleProvider` fetches a bounded current TLE for every NORAD ID and
   stores its source, epoch, retrieval time and SHA-256 fingerprint.
2. `Sgp4PassPredictor` derives AOS/LOS, elevation, azimuth, range, range rate,
   Doppler and subsatellite position for every target/station pair.
3. `OpportunityBuilder` intersects those samples with known transmit calendars
   (weekday, day of month, UTC window and ground region), receiver/antenna
   compatibility and maintenance/high-priority blockers. A blocker cuts its
   exact interval out of an otherwise useful pass; represented unblocked
   fragments remain candidates instead of discarding the entire pass.
4. `HierarchicalBetaEstimator` estimates `P(detectable signal)`,
   `P(decode artifact | detectable signal)` and their product. Optional link-budget, ground
   weather, space-weather and history inputs narrow the interval. Missing inputs
   are listed and widen uncertainty; they do not prevent planning.
5. `MilpScheduler` maximizes risk-adjusted expected unique samples over all
   stations and satellites at once. Receiver-capacity, satellite-contention and
   optional per-satellite min/max constraints form one binary MILP. By default a
   plan is rejected if HiGHS stops without proving optimality.
6. `DynamicObservationPlanner.refresh()` fetches TLEs again, recomputes only
   changed satellites, measures AOS/duration/add/remove impact and writes a new
   immutable plan revision only for a material change. A configurable near-term
   freeze horizon protects jobs that are already about to run. Canonical hashes
   of targets, station hardware, schedule policy and blockers also trigger and
   annotate a full rebuild when those inputs change.
7. `simulate_plan_yield()` runs the transparent independent baseline;
   `simulate_plan_yield_correlated()` adds shared station-day outages,
   satellite-day transmission outages and severe-environment degradation. Both
   are seeded and report expected and 5/50/95-percentile sample yield.

`PlanningStore` is the local SQLite implementation. It persists immutable TLE
snapshots, plan revisions and timestamped qualified reception evidence; the
last category can be queried with a strict `before` boundary for leakage-safe
planning. Production PostgreSQL tables are in
`db/migrations/002_observation_planning.sql` and
`db/migrations/003_planning_reception_evidence.sql`; migration
`004_planning_plan_document.sql` adds full opportunity metadata,
`exclusive_transmission` and the signed v2 plan document. Its document column
is deliberately nullable for a staged legacy backfill; new writers must fill it.

New serialized plans use `observation-plan-v2`. Their canonical fingerprint
covers selected geometry, both probability factors, uncertainty, model/evidence
provenance, optional-feature snapshot, sample value, TLE identity and scheduling
assets, plus plan/revision/time, objective, solver, trigger and diagnostics.
Legacy v1 plans remain readable through their original narrower
fingerprint, but every newly written revision uses v2.

## Evidence rules

Local outcomes and SatNOGS history share one explicit record. An absent SatNOGS
decode is a decoder failure only when the station listened and a signal or
expected transmission is independently established. Otherwise it stays
unknown. This avoids training the probability model on network coverage or
upload gaps as false negatives.

A human-vetted `without-signal` waterfall is, however, qualified negative
evidence for overall reception probability and is stored separately as
`signal_present=False`; it is not mislabeled as a decoder failure or proof that
the spacecraft did not transmit.

A non-empty packet artifact with an unknown waterfall is positive decode
evidence. An explicit `without-signal` review paired with any non-empty
demodulated-data artifact is internally contradictory and is excluded from
both the publication dataset and the operational history posterior before
mode-specific decode eligibility is considered.

For backward compatibility, serialized plans retain the historical field names
`p_transmit` and `p_decode_given_transmit`. Their operational meanings are
`p_signal_present` and `p_decode_given_signal`, exposed as explicit properties
and recorded in each opportunity's `probability_semantics` metadata. The
planner does not claim to infer the spacecraft's physical transmitter state
from a `without-signal` waterfall.

The baseline estimator is an auditable statistical cold-start model, not a
trained AI claim. A later calibrated supervised model can implement the same
`ProbabilityEstimator` interface. Reinforcement learning is not required for
the static monthly CSP/MILP and should be evaluated only after the simulator and
historical backtest are trustworthy.

## Environment and optional inputs

`EnvironmentFeatureEnricher` accepts any terrestrial/space-weather provider and
adds atmospheric attenuation, Kp and scintillation plus modulation-specific SNR
requirements. The provider is injected so NOAA SWPC, a numerical-weather
service, a local sensor or a frozen test snapshot can be used without changing
the scheduler. Antenna ranges are hard compatibility constraints; gain and
noise temperature are optional link-budget features.

`RecordedEnvironmentProvider` consumes versioned, bounded time windows and
returns missing values outside their validity interval instead of silently
extrapolating a forecast. `CompositeEnvironmentProvider` can prioritize local
station attenuation while filling global Kp/scintillation from a separate
source. Source strings are retained in the opportunity feature snapshot.

## SatNOGS Network boundary

`SatnogsHistorySource` reads historical observations using the existing bounded,
GET-only client, follows cursor pagination, deduplicates IDs and fails rather
than silently truncating at an explicit page limit.
`build_satnogs_schedule_export()` produces the exact list body
accepted by `POST /api/observations/`: `start`, `end`, `ground_station`,
`transmitter_uuid` and optional `center_frequency`. It is intentionally a dry
run; the existing client remains read-only and no job is scheduled by merely
building a plan. TLE fingerprints and probability provenance stay in the local
audit sidecar because SatNOGS resolves its own current TLE while scheduling.
The sidecar carries the canonical fingerprint of the complete source plan on
every row, so a reviewed export remains cryptographically attributable to one
immutable plan revision.

This contract was checked against SatNOGS Network source revision
`2ef19b8a1937e9143452e799dadeb64c7f26d1ae` (2026-09-01): the create view
requires a non-empty list, the serializer uses exactly those request fields,
validates same-station overlaps and returns full observation objects containing
their IDs. Recheck the upstream serializer before deploying against a later
Network revision.

`SatnogsStationInventoryProvider` reads exact station-detail endpoints and maps
the current published antenna frequency ranges into one unit-capacity receiver
per Network station. It deliberately leaves antenna gain and system noise
unknown. The snapshot is marked `current_configuration_only`: it is appropriate
for building a future plan, but cannot be used as historical antenna truth in
the publication cohort.

After an authorized submitter posts the reviewed list, `reconcile_satnogs_jobs()`
compares it with read-only `/api/jobs/` results and reports missing/unexpected
jobs. This also catches drift between the local plan and Network state.

The first CLI command below validates the plan's canonical fingerprint and
rejects cross-resource overlaps that the Network would treat as one-station
conflicts, then emits the reviewable dry-run list plus local probability/TLE
audit sidecar. It never performs the POST:

```console
telemetry-yield planning-export-satnogs --plan plan.json --output satnogs-dry-run.json
```

An authorized station owner may then submit exactly that artifact. Submission
requires the API token in `SATNOGS_API_TOKEN`, a reachable User-Agent contact,
and the `confirm-sha256` printed by the export command after human review:

```console
telemetry-yield planning-submit-satnogs \
  --export satnogs-dry-run.json \
  --confirm-sha256 REVIEWED_EXPORT_SHA256 \
  --user-agent my-project/contact@example.org \
  --receipt satnogs-submission-receipt.json
```

The write client makes one POST and never retries after a timeout, transport
failure, HTTP 5xx, oversized response or malformed successful response: each
case is treated as ambiguous and requires read-only job reconciliation first.
It accepts only the exact official SatNOGS API origin, revalidates the complete
dry-run document immediately before transmission, rejects jobs whose start is
not in the future, and requires one unique positive observation ID per submitted
job in the response. The token is never written to the export or receipt. This repository's
publication run does not perform an external submission.

## Minimal API example

```python
from datetime import UTC, datetime

from telemetry_yield.planning.engine import DynamicObservationPlanner
from telemetry_yield.planning.opportunities import OpportunityBuilder
from telemetry_yield.planning.orbit import Sgp4PassPredictor
from telemetry_yield.planning.tle import CelesTrakTleProvider

planner = DynamicObservationPlanner(
    CelesTrakTleProvider(user_agent="my-project/contact@example.org"),
    OpportunityBuilder(Sgp4PassPredictor()),
)
run = planner.plan(targets, stations, month_start, month_end, blockers=blockers)

# Run periodically (for example after a TLE refresh or blocker change).
updated = planner.refresh(run, targets, stations, blockers=blockers, now=datetime.now(UTC))
```

For a long-running deployment, persist the prior run's opportunity snapshot as
well as every plan revision, and invoke `refresh()` after the chosen TLE polling
interval. A missing/partial TLE refresh fails closed instead of producing a
mixed-epoch schedule.

Pass `force_rebuild_reason="weather_refresh"` (or another auditable reason) when
weather/history inputs changed without a TLE or blocker change. Correlated risk
parameters are an explicit sensitivity scenario until calibrated from local
station outage and environmental history; they must not be presented as a
weather forecast.
