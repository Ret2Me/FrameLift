"""Materiality-aware rolling replan after TLE changes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Mapping, Sequence

from .models import ObservationPlan, Opportunity, TleSnapshot, as_utc
from .scheduler import MilpScheduler, SchedulePolicy


@dataclass(frozen=True, slots=True)
class ReplanPolicy:
    material_start_shift_seconds: float = 15.0
    material_duration_shift_seconds: float = 15.0
    match_tolerance_seconds: float = 20 * 60.0
    freeze_horizon: timedelta = timedelta(minutes=30)
    replan_for_added_windows: bool = True

    def __post_init__(self) -> None:
        if min(
            self.material_start_shift_seconds,
            self.material_duration_shift_seconds,
            self.match_tolerance_seconds,
        ) < 0:
            raise ValueError("replan thresholds must be non-negative")
        if self.freeze_horizon < timedelta(0):
            raise ValueError("freeze_horizon must be non-negative")


@dataclass(frozen=True, slots=True)
class TlePlanImpact:
    changed_norad_ids: tuple[int, ...]
    added_windows: tuple[str, ...]
    removed_windows: tuple[str, ...]
    shifted_windows: tuple[tuple[str, str, float, float], ...]
    selected_windows_affected: tuple[str, ...]
    material: bool
    reasons: tuple[str, ...]


def _group_key(item: Opportunity) -> tuple[int, str, str]:
    return item.norad_id, item.station_id, item.resource_id


def assess_tle_plan_impact(
    previous_opportunities: Sequence[Opportunity],
    current_opportunities: Sequence[Opportunity],
    previous_plan: ObservationPlan,
    *,
    policy: ReplanPolicy | None = None,
    current_tle_fingerprints: Mapping[int, str] | None = None,
) -> TlePlanImpact:
    policy = policy or ReplanPolicy()
    if current_tle_fingerprints is not None:
        changed_norad_ids = tuple(
            sorted(
                norad_id
                for norad_id, old_fingerprint in previous_plan.tle_fingerprints.items()
                if current_tle_fingerprints.get(norad_id) != old_fingerprint
            )
        )
    else:
        changed_norad_ids = tuple(
            sorted(
                norad_id
                for norad_id, old_fingerprint in previous_plan.tle_fingerprints.items()
                if any(
                    item.norad_id == norad_id
                    and item.tle_fingerprint != old_fingerprint
                    for item in current_opportunities
                )
            )
        )
    changed = set(changed_norad_ids)
    old = [item for item in previous_opportunities if item.norad_id in changed]
    new = [item for item in current_opportunities if item.norad_id in changed]
    by_key: dict[tuple[int, str, str], list[Opportunity]] = {}
    for item in new:
        by_key.setdefault(_group_key(item), []).append(item)
    for items in by_key.values():
        items.sort(key=lambda item: item.start)

    matched_new: set[str] = set()
    removed: list[str] = []
    shifted: list[tuple[str, str, float, float]] = []
    selected_ids = {item.opportunity_id for item in previous_plan.assignments}
    selected_affected: set[str] = set()
    for old_item in sorted(old, key=lambda item: item.start):
        candidates = [
            item
            for item in by_key.get(_group_key(old_item), ())
            if item.opportunity_id not in matched_new
        ]
        if not candidates:
            removed.append(old_item.opportunity_id)
            if old_item.opportunity_id in selected_ids:
                selected_affected.add(old_item.opportunity_id)
            continue
        nearest = min(
            candidates, key=lambda item: abs((item.start - old_item.start).total_seconds())
        )
        start_shift = (nearest.start - old_item.start).total_seconds()
        duration_shift = nearest.duration_seconds - old_item.duration_seconds
        if abs(start_shift) > policy.match_tolerance_seconds:
            removed.append(old_item.opportunity_id)
            if old_item.opportunity_id in selected_ids:
                selected_affected.add(old_item.opportunity_id)
            continue
        matched_new.add(nearest.opportunity_id)
        if start_shift or duration_shift:
            shifted.append(
                (
                    old_item.opportunity_id,
                    nearest.opportunity_id,
                    start_shift,
                    duration_shift,
                )
            )
            if old_item.opportunity_id in selected_ids and (
                abs(start_shift) >= policy.material_start_shift_seconds
                or abs(duration_shift) >= policy.material_duration_shift_seconds
            ):
                selected_affected.add(old_item.opportunity_id)
    added = [item.opportunity_id for item in new if item.opportunity_id not in matched_new]

    reasons: list[str] = []
    if removed:
        reasons.append("visibility_windows_removed")
    if added and policy.replan_for_added_windows:
        reasons.append("visibility_windows_added")
    if any(
        abs(start_shift) >= policy.material_start_shift_seconds
        or abs(duration_shift) >= policy.material_duration_shift_seconds
        for _, _, start_shift, duration_shift in shifted
    ):
        reasons.append("visibility_windows_shifted")
    if selected_affected:
        reasons.append("selected_assignments_affected")
    return TlePlanImpact(
        changed_norad_ids=changed_norad_ids,
        added_windows=tuple(sorted(added)),
        removed_windows=tuple(sorted(removed)),
        shifted_windows=tuple(shifted),
        selected_windows_affected=tuple(sorted(selected_affected)),
        material=bool(reasons),
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class ReplanResult:
    plan: ObservationPlan
    impact: TlePlanImpact
    replanned: bool


class RollingPlanner:
    def __init__(self, scheduler: MilpScheduler | None = None) -> None:
        self.scheduler = scheduler or MilpScheduler()

    def refresh(
        self,
        previous_plan: ObservationPlan,
        previous_opportunities: Sequence[Opportunity],
        current_opportunities: Sequence[Opportunity],
        current_tles: Mapping[int, TleSnapshot],
        *,
        now: datetime,
        replan_policy: ReplanPolicy | None = None,
        schedule_policy: SchedulePolicy | None = None,
    ) -> ReplanResult:
        replan_policy = replan_policy or ReplanPolicy()
        impact = assess_tle_plan_impact(
            previous_opportunities,
            current_opportunities,
            previous_plan,
            policy=replan_policy,
            current_tle_fingerprints={
                norad_id: snapshot.fingerprint
                for norad_id, snapshot in current_tles.items()
            },
        )
        if not impact.material:
            return ReplanResult(plan=previous_plan, impact=impact, replanned=False)
        now = as_utc(now, name="now")
        effective_start = max(previous_plan.horizon_start, now)
        freeze_until = now + replan_policy.freeze_horizon
        frozen = tuple(
            item
            for item in previous_plan.assignments
            if item.start >= effective_start and item.start < freeze_until
        )
        merged = {item.opportunity_id: item for item in current_opportunities}
        merged.update({item.opportunity_id: item for item in frozen})
        plan = self.scheduler.schedule(
            tuple(merged.values()),
            horizon_start=effective_start,
            horizon_end=previous_plan.horizon_end,
            tle_fingerprints={
                norad_id: snapshot.fingerprint
                for norad_id, snapshot in current_tles.items()
            },
            policy=schedule_policy,
            fixed_opportunity_ids=tuple(item.opportunity_id for item in frozen),
            plan_id=previous_plan.plan_id,
            revision=previous_plan.revision + 1,
            trigger="tle_change:" + ",".join(impact.reasons),
            created_at=now,
        )
        if frozen:
            plan = replace(
                plan,
                diagnostics={
                    **plan.diagnostics,
                    "frozen_assignments_using_prior_geometry": [
                        item.opportunity_id for item in frozen
                    ],
                },
            )
        return ReplanResult(plan=plan, impact=impact, replanned=True)
