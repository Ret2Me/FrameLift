"""Real-outcome scheduling replay for the frozen publication holdout."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal, Mapping, Sequence

from .dataset import DatasetIntegrityError, NormalizedObservation
from .evaluation import (
    ComposedPredictionRecord,
    PredictionRecord,
    reception_success_outcome,
)
from .models import Opportunity, ProbabilityEstimate
from .scheduler import MilpScheduler


@dataclass(frozen=True, slots=True)
class ReplayAssignment:
    scheduler: str
    observation_id: int
    norad_id: int
    station_id: int
    start: str
    end: str
    outcome: int
    predicted_probability: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReplayMetrics:
    scheduler: str
    selected_observations: int
    realized_successful_samples: int
    predicted_expected_samples: float
    satellites_covered: int
    successful_samples_by_satellite: Mapping[int, int]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReplayResult:
    assignments: tuple[ReplayAssignment, ...]
    metrics: Mapping[str, ReplayMetrics]
    candidate_count: int
    natural_receiver_conflict_pairs: int
    informative_for_scheduler_comparison: bool


def _estimate(value: float, version: str) -> ProbabilityEstimate:
    value = min(1.0, max(0.0, float(value)))
    standard_deviation = (value * (1 - value)) ** 0.5
    return ProbabilityEstimate(
        p_transmit=1.0,
        p_decode_given_transmit=value,
        p_success=value,
        standard_deviation=standard_deviation,
        lower_90=max(0.0, value - 1.645 * standard_deviation),
        upper_90=min(1.0, value + 1.645 * standard_deviation),
        model_version=version,
    )


def _opportunity(
    row: NormalizedObservation,
    probability: float,
    *,
    version: str,
    outcome: int,
) -> Opportunity:
    return Opportunity(
        opportunity_id=f"satnogs-observation-{row.observation_id}",
        norad_id=row.norad_id,
        satellite_name=row.tle_name,
        station_id=str(row.station_id),
        resource_id="network-receiver",
        start=row.start,
        end=row.end,
        tle_fingerprint=row.tle_fingerprint,
        probability=_estimate(probability, version),
        nominal_unique_samples=1.0,
        expected_unique_samples=probability,
        priority=1.0,
        max_elevation_deg=row.max_elevation_deg,
        min_range_km=row.min_range_km,
        frequency_hz=row.frequency_hz,
        modulation=row.transmitter_mode,
        transmitter_uuid=row.transmitter_uuid,
        satnogs_station_id=row.station_id,
        exclusive_transmission=False,
        metadata={"outcome": outcome, "receiver_capacity": 1},
    )


def _compatible(candidate: Opportunity, selected: Sequence[Opportunity]) -> bool:
    return all(
        candidate.station_id != current.station_id
        or candidate.end <= current.start
        or current.end <= candidate.start
        for current in selected
    )


def _conflict_pair_count(opportunities: Sequence[Opportunity]) -> int:
    count = 0
    by_station: defaultdict[str, list[Opportunity]] = defaultdict(list)
    for item in opportunities:
        by_station[item.station_id].append(item)
    for items in by_station.values():
        ordered = sorted(items, key=lambda item: (item.start, item.end))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                if right.start >= left.end:
                    break
                if left.start < right.end and right.start < left.end:
                    count += 1
    return count


def _greedy(
    opportunities: Sequence[Opportunity], *, key
) -> tuple[Opportunity, ...]:
    selected: list[Opportunity] = []
    for item in sorted(opportunities, key=key):
        if _compatible(item, selected):
            selected.append(item)
    return tuple(sorted(selected, key=lambda item: (item.start, item.opportunity_id)))


def scheduling_replay(
    rows: Sequence[NormalizedObservation],
    temporal_predictions: Sequence[PredictionRecord | ComposedPredictionRecord],
    *,
    probability_model: str = "full_logit",
    outcome_task: Literal["signal_present", "reception_success"] = "signal_present",
) -> ReplayResult:
    """Replay identical station conflicts using held-out endpoint outcomes."""

    predicted = {
        record.observation_id: record
        for record in temporal_predictions
        if record.model == probability_model and record.task == outcome_task
    }
    by_id = {row.observation_id: row for row in rows}
    missing = set(predicted) - set(by_id)
    if missing:
        raise DatasetIntegrityError(f"predictions reference missing observations: {sorted(missing)[:5]}")
    replay_rows = [by_id[identifier] for identifier in sorted(predicted)]
    if not replay_rows:
        raise DatasetIntegrityError("scheduling replay has no held-out observations")
    endpoint_outcomes = {
        row.observation_id: (
            int(row.signal_present)
            if outcome_task == "signal_present" and row.signal_present in {0, 1}
            else (
                reception_success_outcome(row)
                if outcome_task == "reception_success"
                else None
            )
        )
        for row in replay_rows
    }
    if any(outcome not in {0, 1} for outcome in endpoint_outcomes.values()):
        raise DatasetIntegrityError(
            f"scheduling replay requires known {outcome_task} outcomes"
        )
    opportunities = tuple(
        _opportunity(
            row,
            predicted[row.observation_id].probability,
            version=f"{outcome_task}:{probability_model}",
            outcome=int(endpoint_outcomes[row.observation_id]),
        )
        for row in replay_rows
    )
    horizon_start = min(row.start for row in replay_rows)
    horizon_end = max(row.end for row in replay_rows)
    fingerprints = {row.norad_id: row.tle_fingerprint for row in replay_rows}
    scheduler = MilpScheduler()
    probability_plan = scheduler.schedule(
        opportunities,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        tle_fingerprints=fingerprints,
        trigger="publication-replay-probability",
    )
    successful_opportunities = tuple(
        _opportunity(
            row,
            1.0,
            version=f"held-out-{outcome_task}-oracle",
            outcome=1,
        )
        for row in replay_rows
        if endpoint_outcomes[row.observation_id] == 1
    )
    oracle_plan = scheduler.schedule(
        successful_opportunities,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        tle_fingerprints=fingerprints,
        trigger="publication-replay-oracle-upper-bound",
    )
    selections: dict[str, tuple[Opportunity, ...]] = {
        "chronological": _greedy(
            opportunities, key=lambda item: (item.start, item.end, item.opportunity_id)
        ),
        "maximum_elevation": _greedy(
            opportunities,
            key=lambda item: (-item.max_elevation_deg, item.start, item.opportunity_id),
        ),
        "longest_duration": _greedy(
            opportunities,
            key=lambda item: (-item.duration_seconds, item.start, item.opportunity_id),
        ),
        "probability_milp": probability_plan.assignments,
        "oracle_milp_upper_bound": oracle_plan.assignments,
    }
    assignments: list[ReplayAssignment] = []
    metrics: dict[str, ReplayMetrics] = {}
    for name, selected in selections.items():
        successful_by_satellite: defaultdict[int, int] = defaultdict(int)
        expected = 0.0
        for item in selected:
            observation_id = int(item.opportunity_id.rsplit("-", 1)[1])
            row = by_id[observation_id]
            probability = predicted[observation_id].probability
            outcome = int(endpoint_outcomes[observation_id])
            successful_by_satellite[row.norad_id] += outcome
            expected += probability
            assignments.append(
                ReplayAssignment(
                    scheduler=name,
                    observation_id=observation_id,
                    norad_id=row.norad_id,
                    station_id=row.station_id,
                    start=row.start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    end=row.end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    outcome=outcome,
                    predicted_probability=probability,
                )
            )
        metrics[name] = ReplayMetrics(
            scheduler=name,
            selected_observations=len(selected),
            realized_successful_samples=sum(successful_by_satellite.values()),
            predicted_expected_samples=expected,
            satellites_covered=sum(
                value > 0 for value in successful_by_satellite.values()
            ),
            successful_samples_by_satellite=dict(
                sorted(successful_by_satellite.items())
            ),
        )
    assignments.sort(key=lambda item: (item.scheduler, item.start, item.observation_id))
    conflict_pairs = _conflict_pair_count(opportunities)
    return ReplayResult(
        tuple(assignments),
        metrics,
        candidate_count=len(opportunities),
        natural_receiver_conflict_pairs=conflict_pairs,
        informative_for_scheduler_comparison=conflict_pairs > 0,
    )


def paired_day_bootstrap_replay(
    assignments: Sequence[ReplayAssignment],
    *,
    candidate: str = "probability_milp",
    baseline: str,
    replicates: int = 2000,
    seed: int = 7538,
) -> Mapping[str, object]:
    import numpy as np

    daily: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))
    for item in assignments:
        if item.scheduler in {candidate, baseline}:
            day = item.start[:10]
            daily[day][item.scheduler] += item.outcome
    days = sorted(daily)
    if not days:
        raise DatasetIntegrityError("replay bootstrap has no days")
    differences = np.asarray(
        [daily[day][candidate] - daily[day][baseline] for day in days], dtype=float
    )
    rng = np.random.default_rng(seed)
    samples = tuple(
        float(np.mean(rng.choice(differences, size=len(differences), replace=True)))
        for _ in range(replicates)
    )
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {
        "candidate": candidate,
        "baseline": baseline,
        "day_count": len(days),
        "estimate_mean_daily_success_difference": float(differences.mean()),
        "lower_95": float(lower),
        "upper_95": float(upper),
        "replicates": samples,
    }
