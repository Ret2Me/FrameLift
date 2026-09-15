"""Independent synthetic verification of the interval-scheduling optimizer."""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Sequence

from .models import ObservationPlan, Opportunity, ProbabilityEstimate
from .scheduler import MilpScheduler


@dataclass(frozen=True, slots=True)
class SchedulerBenchmarkSummary:
    instances: int
    seed: int
    candidates_per_instance: int
    milp_exact_match_count: int
    multi_asset_instances: int
    multi_asset_candidates_per_instance: int
    multi_asset_milp_exact_match_count: int
    chronological_mean_relative_regret: float
    maximum_elevation_mean_relative_regret: float
    longest_duration_mean_relative_regret: float
    probability_greedy_mean_relative_regret: float
    milp_mean_objective: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _estimate(probability: float) -> ProbabilityEstimate:
    spread = (probability * (1 - probability)) ** 0.5
    return ProbabilityEstimate(
        p_transmit=1.0,
        p_decode_given_transmit=probability,
        p_success=probability,
        standard_deviation=spread,
        lower_90=max(0.0, probability - 1.645 * spread),
        upper_90=min(1.0, probability + 1.645 * spread),
        model_version="synthetic-scheduler-verification-v1",
    )


def _generate_instance(
    rng: random.Random, index: int, candidates: int
) -> tuple[Opportunity, ...]:
    base = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index)
    result: list[Opportunity] = []
    for candidate in range(candidates):
        start_minute = rng.randrange(0, 22 * 60)
        duration_minutes = rng.randrange(3, 121)
        probability = rng.uniform(0.05, 0.98)
        nominal = float(rng.randrange(1, 201))
        start = base + timedelta(minutes=start_minute)
        end = start + timedelta(minutes=duration_minutes)
        result.append(
            Opportunity(
                opportunity_id=f"synthetic-{index}-{candidate}",
                norad_id=10_000 + candidate,
                satellite_name=f"SAT-{candidate}",
                station_id="synthetic-station",
                resource_id="rx",
                start=start,
                end=end,
                tle_fingerprint=f"tle-{candidate}",
                probability=_estimate(probability),
                nominal_unique_samples=nominal,
                expected_unique_samples=nominal * probability,
                priority=1.0,
                max_elevation_deg=rng.uniform(5, 90),
                min_range_km=rng.uniform(300, 2500),
                exclusive_transmission=False,
            )
        )
    return tuple(result)


def _compatible(candidate: Opportunity, selected: Sequence[Opportunity]) -> bool:
    return all(candidate.end <= item.start or item.end <= candidate.start for item in selected)


def _greedy(
    opportunities: Sequence[Opportunity], key: Callable[[Opportunity], object]
) -> tuple[Opportunity, ...]:
    selected: list[Opportunity] = []
    for item in sorted(opportunities, key=key):
        if _compatible(item, selected):
            selected.append(item)
    return tuple(selected)


def _independent_weighted_interval_optimum(opportunities: Sequence[Opportunity]) -> float:
    """Classic DP, independent from the MILP implementation and its cliques."""

    ordered = sorted(opportunities, key=lambda item: (item.end, item.start, item.opportunity_id))
    previous: list[int] = []
    for index, item in enumerate(ordered):
        compatible = -1
        for other in range(index - 1, -1, -1):
            if ordered[other].end <= item.start:
                compatible = other
                break
        previous.append(compatible)
    optimum = [0.0] * (len(ordered) + 1)
    for index, item in enumerate(ordered, start=1):
        include = item.expected_unique_samples + optimum[previous[index - 1] + 1]
        optimum[index] = max(optimum[index - 1], include)
    return optimum[-1]


def _generate_multi_asset_instance(
    rng: random.Random, index: int, candidates: int
) -> tuple[Opportunity, ...]:
    """Generate crossed station and satellite conflicts for an independent check."""

    base = datetime(2027, 1, 1, tzinfo=UTC) + timedelta(days=index)
    result: list[Opportunity] = []
    for candidate in range(candidates):
        station = rng.randrange(3)
        satellite = rng.randrange(5)
        start = base + timedelta(minutes=rng.randrange(0, 5 * 60))
        end = start + timedelta(minutes=rng.randrange(5, 76))
        probability = rng.uniform(0.05, 0.98)
        nominal = float(rng.randrange(1, 201))
        result.append(
            Opportunity(
                opportunity_id=f"multi-{index}-{candidate}",
                norad_id=20_000 + satellite,
                satellite_name=f"MULTI-SAT-{satellite}",
                station_id=f"multi-station-{station}",
                resource_id="rx",
                start=start,
                end=end,
                tle_fingerprint=f"multi-tle-{satellite}",
                probability=_estimate(probability),
                nominal_unique_samples=nominal,
                expected_unique_samples=nominal * probability,
                priority=1.0,
                max_elevation_deg=rng.uniform(5, 90),
                min_range_km=rng.uniform(300, 2500),
                exclusive_transmission=True,
            )
        )
    return tuple(result)


def _independent_multi_asset_optimum(
    opportunities: Sequence[Opportunity],
) -> float:
    """Exhaustive oracle written without scheduler cliques or solver matrices."""

    if len(opportunities) > 20:
        raise ValueError("independent multi-asset enumeration is bounded at 20 candidates")
    best = 0.0
    for mask in range(1 << len(opportunities)):
        selected = [
            item
            for index, item in enumerate(opportunities)
            if mask & (1 << index)
        ]
        feasible = True
        for index, left in enumerate(selected):
            for right in selected[index + 1 :]:
                overlaps = left.start < right.end and right.start < left.end
                if not overlaps:
                    continue
                same_receiver = (
                    left.station_id == right.station_id
                    and left.resource_id == right.resource_id
                )
                same_exclusive_satellite = (
                    left.exclusive_transmission
                    and right.exclusive_transmission
                    and left.norad_id == right.norad_id
                )
                if same_receiver or same_exclusive_satellite:
                    feasible = False
                    break
            if not feasible:
                break
        if feasible:
            best = max(best, sum(item.expected_unique_samples for item in selected))
    return best


def run_scheduler_benchmark(
    *,
    instances: int = 100,
    candidates_per_instance: int = 40,
    multi_asset_instances: int = 50,
    multi_asset_candidates_per_instance: int = 10,
    seed: int = 7538,
) -> SchedulerBenchmarkSummary:
    if min(
        instances,
        candidates_per_instance,
        multi_asset_instances,
        multi_asset_candidates_per_instance,
    ) <= 0:
        raise ValueError("benchmark sizes must be positive")
    if multi_asset_candidates_per_instance > 20:
        raise ValueError("multi-asset benchmark is bounded at 20 candidates")
    rng = random.Random(seed)
    scheduler = MilpScheduler()
    regrets: dict[str, list[float]] = {
        "chronological": [],
        "maximum_elevation": [],
        "longest_duration": [],
        "probability_greedy": [],
    }
    exact = 0
    objectives: list[float] = []
    for index in range(instances):
        opportunities = _generate_instance(rng, index, candidates_per_instance)
        horizon_start = min(item.start for item in opportunities)
        horizon_end = max(item.end for item in opportunities)
        plan = scheduler.schedule(
            opportunities,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            tle_fingerprints={},
            trigger="synthetic-independent-verification",
        )
        independent = _independent_weighted_interval_optimum(opportunities)
        if abs(plan.objective_value - independent) <= 1e-7 * max(1.0, independent):
            exact += 1
        objectives.append(plan.objective_value)
        selections = {
            "chronological": _greedy(
                opportunities, lambda item: (item.start, item.end, item.opportunity_id)
            ),
            "maximum_elevation": _greedy(
                opportunities,
                lambda item: (-item.max_elevation_deg, item.start, item.opportunity_id),
            ),
            "longest_duration": _greedy(
                opportunities,
                lambda item: (-item.duration_seconds, item.start, item.opportunity_id),
            ),
            "probability_greedy": _greedy(
                opportunities,
                lambda item: (-item.probability.p_success, item.start, item.opportunity_id),
            ),
        }
        for name, selected in selections.items():
            value = sum(item.expected_unique_samples for item in selected)
            regrets[name].append(
                (plan.objective_value - value) / plan.objective_value
                if plan.objective_value > 0
                else 0.0
            )
    multi_asset_exact = 0
    for index in range(multi_asset_instances):
        opportunities = _generate_multi_asset_instance(
            rng, index, multi_asset_candidates_per_instance
        )
        plan = scheduler.schedule(
            opportunities,
            horizon_start=min(item.start for item in opportunities),
            horizon_end=max(item.end for item in opportunities),
            tle_fingerprints={},
            trigger="synthetic-multi-asset-independent-verification",
        )
        independent = _independent_multi_asset_optimum(opportunities)
        if abs(plan.objective_value - independent) <= 1e-7 * max(1.0, independent):
            multi_asset_exact += 1
    mean = lambda values: sum(values) / len(values)
    return SchedulerBenchmarkSummary(
        instances=instances,
        seed=seed,
        candidates_per_instance=candidates_per_instance,
        milp_exact_match_count=exact,
        multi_asset_instances=multi_asset_instances,
        multi_asset_candidates_per_instance=multi_asset_candidates_per_instance,
        multi_asset_milp_exact_match_count=multi_asset_exact,
        chronological_mean_relative_regret=mean(regrets["chronological"]),
        maximum_elevation_mean_relative_regret=mean(regrets["maximum_elevation"]),
        longest_duration_mean_relative_regret=mean(regrets["longest_duration"]),
        probability_greedy_mean_relative_regret=mean(regrets["probability_greedy"]),
        milp_mean_objective=mean(objectives),
    )


def build_scheduler_robustness_plan(
    *,
    days: int = 30,
    candidates_per_day: int = 40,
    seed: int = 7538,
) -> ObservationPlan:
    """Build a deterministic month-like conflict plan for risk sensitivity."""

    if days <= 0 or candidates_per_day <= 0:
        raise ValueError("robustness benchmark sizes must be positive")
    rng = random.Random(seed)
    opportunities = tuple(
        item
        for day in range(days)
        for item in _generate_instance(rng, day, candidates_per_day)
    )
    horizon_start = min(item.start for item in opportunities)
    return MilpScheduler().schedule(
        opportunities,
        horizon_start=horizon_start,
        horizon_end=max(item.end for item in opportunities),
        tle_fingerprints={},
        plan_id=f"synthetic-robustness-{seed}",
        trigger="synthetic-correlated-risk-sensitivity",
        created_at=horizon_start,
    )
