"""Globally optimal binary scheduling for overlapping observation windows."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Mapping, Sequence

from .models import ObservationPlan, Opportunity, as_utc


class SchedulingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SchedulePolicy:
    risk_aversion: float = 0.0
    minimum_observations_by_satellite: Mapping[int, int] = field(default_factory=dict)
    maximum_observations_by_satellite: Mapping[int, int] = field(default_factory=dict)
    time_limit_seconds: float = 120.0
    require_proven_optimum: bool = True

    def __post_init__(self) -> None:
        if (
            isinstance(self.risk_aversion, bool)
            or not isinstance(self.risk_aversion, (int, float))
            or not math.isfinite(float(self.risk_aversion))
            or not 0 <= self.risk_aversion <= 1
        ):
            raise ValueError("risk_aversion must be in [0, 1]")
        if (
            isinstance(self.time_limit_seconds, bool)
            or not isinstance(self.time_limit_seconds, (int, float))
            or not math.isfinite(float(self.time_limit_seconds))
            or self.time_limit_seconds <= 0
        ):
            raise ValueError("time_limit_seconds must be finite and positive")
        if not isinstance(self.require_proven_optimum, bool):
            raise ValueError("require_proven_optimum must be boolean")
        for name, limits in (
            ("minimum", self.minimum_observations_by_satellite),
            ("maximum", self.maximum_observations_by_satellite),
        ):
            for norad_id, value in limits.items():
                if (
                    isinstance(norad_id, bool)
                    or not isinstance(norad_id, int)
                    or norad_id <= 0
                    or isinstance(value, bool)
                    or not isinstance(value, int)
                    or value < 0
                ):
                    raise ValueError(
                        f"{name} observation limits require positive integer NORAD IDs "
                        "and non-negative integer counts"
                    )
        for norad_id in (
            set(self.minimum_observations_by_satellite)
            & set(self.maximum_observations_by_satellite)
        ):
            if (
                self.minimum_observations_by_satellite[norad_id]
                > self.maximum_observations_by_satellite[norad_id]
            ):
                raise ValueError(
                    f"minimum observations exceed maximum for NORAD {norad_id}"
                )


def _asset_cliques(
    opportunities: Sequence[Opportunity],
) -> tuple[tuple[tuple[int, ...], int], ...]:
    by_asset: dict[str, list[tuple[int, Opportunity]]] = defaultdict(list)
    capacities: dict[str, int] = {}
    for index, opportunity in enumerate(opportunities):
        for asset in opportunity.conflict_assets:
            by_asset[asset].append((index, opportunity))
            if asset.startswith("receiver:"):
                raw = opportunity.metadata.get("receiver_capacity", 1)
                if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
                    raise ValueError(
                        f"receiver capacity for {asset} must be a positive integer"
                    )
                previous = capacities.setdefault(asset, raw)
                if previous != raw:
                    raise ValueError(
                        f"inconsistent receiver capacity for {asset}: {previous} != {raw}"
                    )
            else:
                capacities[asset] = 1

    constraints: list[tuple[tuple[int, ...], int]] = []
    seen: set[tuple[str, tuple[int, ...]]] = set()
    for asset, indexed in by_asset.items():
        events: list[tuple[datetime, int, int]] = []
        for index, opportunity in indexed:
            events.append((opportunity.start, 1, index))
            events.append((opportunity.end, 0, index))  # end before start: [start, end)
        active: set[int] = set()
        for _, kind, index in sorted(events):
            if kind == 0:
                active.discard(index)
            else:
                active.add(index)
                if len(active) > capacities[asset]:
                    clique = tuple(sorted(active))
                    key = (asset, clique)
                    if key not in seen:
                        seen.add(key)
                        constraints.append((clique, capacities[asset]))
    return tuple(constraints)


def _validate_assignment(assignments: Sequence[Opportunity]) -> None:
    for clique, capacity in _asset_cliques(assignments):
        if len(clique) > capacity:
            raise SchedulingError("solver returned overlapping assignments")


class MilpScheduler:
    """Solve an additive expected-yield objective with exact resource conflicts.

    SciPy/HiGHS is used for month-scale schedules.  A bounded exact search keeps
    unit tests and very small deployments dependency-light; it refuses larger
    instances instead of silently claiming a heuristic schedule is optimal.
    """

    def schedule(
        self,
        opportunities: Sequence[Opportunity],
        *,
        horizon_start: datetime,
        horizon_end: datetime,
        tle_fingerprints: Mapping[int, str],
        policy: SchedulePolicy | None = None,
        fixed_opportunity_ids: Sequence[str] = (),
        plan_id: str | None = None,
        revision: int = 1,
        trigger: str = "initial",
        created_at: datetime | None = None,
    ) -> ObservationPlan:
        policy = policy or SchedulePolicy()
        horizon_start = as_utc(horizon_start, name="horizon_start")
        horizon_end = as_utc(horizon_end, name="horizon_end")
        if horizon_end <= horizon_start:
            raise ValueError("planning horizon must be positive")
        candidates = tuple(
            sorted(
                (
                    item
                    for item in opportunities
                    if item.start >= horizon_start and item.end <= horizon_end
                ),
                key=lambda item: (item.start, item.end, item.opportunity_id),
            )
        )
        if len({item.opportunity_id for item in candidates}) != len(candidates):
            raise ValueError("opportunity IDs must be unique")
        fixed = set(fixed_opportunity_ids)
        missing_fixed = fixed - {item.opportunity_id for item in candidates}
        if missing_fixed:
            raise ValueError(f"fixed opportunities missing from candidates: {sorted(missing_fixed)}")
        scores = tuple(item.objective_value(policy.risk_aversion) for item in candidates)
        try:
            selected, diagnostics = self._solve_scipy(candidates, scores, policy, fixed)
            solver = "scipy-highs-milp"
        except ImportError:
            selected, diagnostics = self._solve_small_exact(candidates, scores, policy, fixed)
            solver = "exact-branch-and-bound"
        assignments = tuple(candidates[index] for index in selected)
        _validate_assignment(assignments)
        objective = sum(scores[index] for index in selected)
        if plan_id is None:
            identity = hashlib.sha256(
                f"{horizon_start.isoformat()}|{horizon_end.isoformat()}".encode()
            ).hexdigest()[:20]
            plan_id = f"monthly-{identity}"
        diagnostics = {
            **diagnostics,
            "candidate_count": len(candidates),
            "selected_count": len(assignments),
            "risk_aversion": policy.risk_aversion,
            "expected_unique_samples": sum(
                item.expected_unique_samples for item in assignments
            ),
        }
        return ObservationPlan(
            plan_id=plan_id,
            revision=revision,
            created_at=(created_at or datetime.now(UTC)),
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            assignments=assignments,
            tle_fingerprints=dict(tle_fingerprints),
            objective_value=objective,
            solver=solver,
            trigger=trigger,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _solve_scipy(
        candidates: Sequence[Opportunity],
        scores: Sequence[float],
        policy: SchedulePolicy,
        fixed: set[str],
    ) -> tuple[tuple[int, ...], dict[str, object]]:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import coo_matrix

        count = len(candidates)
        if count == 0:
            return (), {"status": "optimal", "mip_gap": 0.0}
        rows: list[int] = []
        columns: list[int] = []
        values: list[float] = []
        lower: list[float] = []
        upper: list[float] = []

        def add_constraint(indices: Sequence[int], low: float, high: float) -> None:
            row = len(lower)
            for index in indices:
                rows.append(row)
                columns.append(index)
                values.append(1.0)
            lower.append(low)
            upper.append(high)

        for indices, capacity in _asset_cliques(candidates):
            add_constraint(indices, -np.inf, float(capacity))
        by_satellite: dict[int, list[int]] = defaultdict(list)
        for index, item in enumerate(candidates):
            by_satellite[item.norad_id].append(index)
        for norad_id, minimum in policy.minimum_observations_by_satellite.items():
            add_constraint(by_satellite.get(norad_id, ()), float(minimum), np.inf)
        for norad_id, maximum in policy.maximum_observations_by_satellite.items():
            add_constraint(by_satellite.get(norad_id, ()), -np.inf, float(maximum))

        matrix = coo_matrix((values, (rows, columns)), shape=(len(lower), count)).tocsr()
        constraints = (
            LinearConstraint(matrix, np.array(lower), np.array(upper))
            if lower
            else None
        )
        bound_lower = np.zeros(count)
        for index, item in enumerate(candidates):
            if item.opportunity_id in fixed:
                bound_lower[index] = 1.0
        result = milp(
            c=-np.asarray(scores, dtype=float),
            integrality=np.ones(count, dtype=int),
            bounds=Bounds(bound_lower, np.ones(count)),
            constraints=constraints,
            options={"time_limit": policy.time_limit_seconds},
        )
        if result.x is None or result.status in {2, 3, 4}:
            raise SchedulingError(f"MILP did not produce a feasible plan: {result.message}")
        if policy.require_proven_optimum and result.status != 0:
            raise SchedulingError(
                f"MILP stopped without proving optimality: {result.message}"
            )
        selected = tuple(index for index, value in enumerate(result.x) if value >= 0.5)
        return selected, {
            "status": "optimal" if result.status == 0 else "feasible_limit",
            "mip_gap": float(getattr(result, "mip_gap", math.nan)),
            "solver_message": str(result.message),
        }

    @staticmethod
    def _solve_small_exact(
        candidates: Sequence[Opportunity],
        scores: Sequence[float],
        policy: SchedulePolicy,
        fixed: set[str],
    ) -> tuple[tuple[int, ...], dict[str, object]]:
        if len(candidates) > 30:
            raise SchedulingError(
                "month-scale global optimisation requires scipy (planning extra)"
            )
        cliques = _asset_cliques(candidates)
        conflicts: list[set[int]] = [set() for _ in candidates]
        # The fallback handles unit-capacity assets. Higher capacities need MILP.
        for indices, capacity in cliques:
            if capacity != 1:
                raise SchedulingError("resource capacity >1 requires scipy")
            for left in indices:
                conflicts[left].update(right for right in indices if right != left)
        order = sorted(range(len(candidates)), key=lambda index: (-scores[index], index))
        remaining = [0.0] * (len(order) + 1)
        for position in range(len(order) - 1, -1, -1):
            remaining[position] = remaining[position + 1] + max(0.0, scores[order[position]])
        best_score = -math.inf
        best: tuple[int, ...] = ()

        def search(position: int, selected: set[int], score: float) -> None:
            nonlocal best, best_score
            if score + remaining[position] < best_score - 1e-12:
                return
            if position == len(order):
                selected_ids = {candidates[index].opportunity_id for index in selected}
                if not fixed <= selected_ids:
                    return
                counts: dict[int, int] = defaultdict(int)
                for index in selected:
                    counts[candidates[index].norad_id] += 1
                if any(
                    counts[norad] < minimum
                    for norad, minimum in policy.minimum_observations_by_satellite.items()
                ):
                    return
                if any(
                    counts[norad] > maximum
                    for norad, maximum in policy.maximum_observations_by_satellite.items()
                ):
                    return
                candidate = tuple(sorted(selected))
                if score > best_score + 1e-12 or (
                    abs(score - best_score) <= 1e-12 and candidate < best
                ):
                    best_score, best = score, candidate
                return
            index = order[position]
            item = candidates[index]
            mandatory = item.opportunity_id in fixed
            if not any(other in conflicts[index] for other in selected):
                selected.add(index)
                search(position + 1, selected, score + scores[index])
                selected.remove(index)
            if not mandatory:
                search(position + 1, selected, score)

        search(0, set(), 0.0)
        if best_score == -math.inf:
            raise SchedulingError("constraints are infeasible")
        return best, {"status": "optimal", "mip_gap": 0.0}
