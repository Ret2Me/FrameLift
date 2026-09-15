"""Deterministic Monte Carlo yield forecast for a selected monthly plan."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping

from .models import ObservationPlan


@dataclass(frozen=True, slots=True)
class MonteCarloYield:
    trials: int
    seed: int
    mean_unique_samples: float
    percentile_05: float
    percentile_50: float
    percentile_95: float
    probability_of_any_success: float
    mean_by_satellite: Mapping[int, float]


@dataclass(frozen=True, slots=True)
class CorrelatedRiskScenario:
    """Shared operational risks for a monthly-plan sensitivity analysis."""

    station_day_availability: float = 0.98
    satellite_day_transmit_availability: float = 0.97
    severe_environment_probability: float = 0.05
    severe_environment_success_multiplier: float = 0.60

    def __post_init__(self) -> None:
        for name in (
            "station_day_availability",
            "satellite_day_transmit_availability",
            "severe_environment_probability",
            "severe_environment_success_multiplier",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class CorrelatedMonteCarloYield:
    trials: int
    seed: int
    scenario: CorrelatedRiskScenario
    mean_unique_samples: float
    percentile_05: float
    percentile_50: float
    percentile_95: float
    probability_of_any_success: float
    probability_below_half_expected: float
    mean_by_satellite: Mapping[int, float]


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        return 0.0
    position = probability * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def simulate_plan_yield(
    plan: ObservationPlan, *, trials: int = 10_000, seed: int = 0
) -> MonteCarloYield:
    if isinstance(trials, bool) or trials <= 0:
        raise ValueError("trials must be a positive integer")
    rng = random.Random(seed)
    totals: list[float] = []
    by_satellite: dict[int, float] = {}
    any_success = 0
    for _ in range(trials):
        total = 0.0
        trial_by_satellite: dict[int, float] = {}
        for item in plan.assignments:
            if rng.random() < item.probability.p_success:
                total += item.nominal_unique_samples
                trial_by_satellite[item.norad_id] = (
                    trial_by_satellite.get(item.norad_id, 0.0)
                    + item.nominal_unique_samples
                )
        if total > 0:
            any_success += 1
        totals.append(total)
        for norad_id, value in trial_by_satellite.items():
            by_satellite[norad_id] = by_satellite.get(norad_id, 0.0) + value
    totals.sort()
    return MonteCarloYield(
        trials=trials,
        seed=seed,
        mean_unique_samples=sum(totals) / trials,
        percentile_05=_percentile(totals, 0.05),
        percentile_50=_percentile(totals, 0.50),
        percentile_95=_percentile(totals, 0.95),
        probability_of_any_success=any_success / trials,
        mean_by_satellite={key: value / trials for key, value in by_satellite.items()},
    )


def simulate_plan_yield_correlated(
    plan: ObservationPlan,
    *,
    scenario: CorrelatedRiskScenario | None = None,
    trials: int = 10_000,
    seed: int = 0,
) -> CorrelatedMonteCarloYield:
    """Stress a plan with shared station-day, satellite-day and environment risk.

    This is a sensitivity model, not a learned weather forecast.  Shared draws
    prevent the misleadingly narrow intervals produced when every opportunity
    is assumed independent.
    """

    if isinstance(trials, bool) or trials <= 0:
        raise ValueError("trials must be a positive integer")
    scenario = scenario or CorrelatedRiskScenario()
    rng = random.Random(seed)
    totals: list[float] = []
    by_satellite: dict[int, float] = {}
    any_success = 0
    below_half = 0
    nominal_expected = sum(item.expected_unique_samples for item in plan.assignments)
    for _ in range(trials):
        station_day: dict[tuple[str, object], bool] = {}
        satellite_day: dict[tuple[int, object], bool] = {}
        environment_day: dict[object, bool] = {}
        total = 0.0
        trial_by_satellite: dict[int, float] = {}
        for item in plan.assignments:
            day = item.start.date()
            station_key = (item.station_id, day)
            satellite_key = (item.norad_id, day)
            if station_key not in station_day:
                station_day[station_key] = (
                    rng.random() < scenario.station_day_availability
                )
            if satellite_key not in satellite_day:
                satellite_day[satellite_key] = (
                    rng.random() < scenario.satellite_day_transmit_availability
                )
            if day not in environment_day:
                environment_day[day] = (
                    rng.random() < scenario.severe_environment_probability
                )
            if not station_day[station_key] or not satellite_day[satellite_key]:
                continue
            probability = item.probability.p_success
            if environment_day[day]:
                probability *= scenario.severe_environment_success_multiplier
            if rng.random() < probability:
                total += item.nominal_unique_samples
                trial_by_satellite[item.norad_id] = (
                    trial_by_satellite.get(item.norad_id, 0.0)
                    + item.nominal_unique_samples
                )
        totals.append(total)
        any_success += total > 0
        below_half += total < 0.5 * nominal_expected
        for norad_id, value in trial_by_satellite.items():
            by_satellite[norad_id] = by_satellite.get(norad_id, 0.0) + value
    totals.sort()
    return CorrelatedMonteCarloYield(
        trials=trials,
        seed=seed,
        scenario=scenario,
        mean_unique_samples=sum(totals) / trials,
        percentile_05=_percentile(totals, 0.05),
        percentile_50=_percentile(totals, 0.50),
        percentile_95=_percentile(totals, 0.95),
        probability_of_any_success=any_success / trials,
        probability_below_half_expected=below_half / trials,
        mean_by_satellite={key: value / trials for key, value in by_satellite.items()},
    )
