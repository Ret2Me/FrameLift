"""End-to-end orchestration for initial month plans and rolling TLE refreshes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Mapping, Sequence

from .models import (
    Blocker,
    GroundStation,
    ObservationPlan,
    Opportunity,
    SatelliteTarget,
    TleSnapshot,
    as_utc,
)
from .opportunities import OpportunityBuilder
from .probability import ReceptionEvidence
from .replanner import ReplanPolicy, RollingPlanner, TlePlanImpact
from .scheduler import MilpScheduler, SchedulePolicy
from .store import PlanningStore
from .tle import TleProvider, refresh_tles


@dataclass(frozen=True, slots=True)
class PlanningRun:
    plan: ObservationPlan
    opportunities: tuple[Opportunity, ...]
    tles: Mapping[int, TleSnapshot]
    replanned: bool
    impact: TlePlanImpact | None = None
    blocker_fingerprint: str = ""
    planning_input_fingerprint: str = ""


def _blocker_fingerprint(blockers: Sequence[Blocker]) -> str:
    rows = sorted(
        (
            item.blocker_id,
            item.station_id,
            item.resource_id or "",
            item.start.isoformat(),
            item.end.isoformat(),
            item.kind,
            item.reason,
        )
        for item in blockers
    )
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _planning_input_fingerprint(
    targets: Sequence[SatelliteTarget],
    stations: Sequence[GroundStation],
    schedule_policy: SchedulePolicy | None,
) -> str:
    payload = {
        "targets": [
            asdict(item)
            for item in sorted(targets, key=lambda value: (value.norad_id, value.name))
        ],
        "stations": [
            asdict(item) for item in sorted(stations, key=lambda value: value.station_id)
        ],
        "schedule_policy": asdict(schedule_policy or SchedulePolicy()),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _tle_snapshot_evidence(
    tles: Mapping[int, TleSnapshot],
) -> Mapping[str, Mapping[str, object]]:
    return {
        str(norad_id): {
            "name": snapshot.name,
            "line1": snapshot.line1,
            "line2": snapshot.line2,
            "epoch": snapshot.epoch.isoformat().replace("+00:00", "Z"),
            "fetched_at": snapshot.fetched_at.isoformat().replace("+00:00", "Z"),
            "source": snapshot.source,
            "fingerprint": snapshot.fingerprint,
        }
        for norad_id, snapshot in sorted(tles.items())
    }


class DynamicObservationPlanner:
    """Refresh TLEs, rebuild affected geometry and atomically version plans."""

    def __init__(
        self,
        tle_provider: TleProvider,
        opportunity_builder: OpportunityBuilder,
        *,
        scheduler: MilpScheduler | None = None,
        store: PlanningStore | None = None,
    ) -> None:
        self.tle_provider = tle_provider
        self.opportunity_builder = opportunity_builder
        self.scheduler = scheduler or MilpScheduler()
        self.rolling_planner = RollingPlanner(self.scheduler)
        self.store = store

    def _fetch_and_store_tles(
        self, targets: Sequence[SatelliteTarget], now: datetime
    ) -> dict[int, TleSnapshot]:
        tles = refresh_tles(
            self.tle_provider,
            [target.norad_id for target in targets],
            now=now,
        )
        if self.store is not None:
            for snapshot in tles.values():
                self.store.save_tle(snapshot)
        return tles

    def plan(
        self,
        targets: Sequence[SatelliteTarget],
        stations: Sequence[GroundStation],
        horizon_start: datetime,
        horizon_end: datetime,
        *,
        blockers: Sequence[Blocker] = (),
        evidence: Sequence[ReceptionEvidence] = (),
        schedule_policy: SchedulePolicy | None = None,
        plan_id: str | None = None,
        revision: int = 1,
        trigger: str = "initial",
        now: datetime | None = None,
    ) -> PlanningRun:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        horizon_start = as_utc(horizon_start, name="horizon_start")
        horizon_end = as_utc(horizon_end, name="horizon_end")
        effective_start = max(horizon_start, now)
        if effective_start >= horizon_end:
            raise ValueError("planning horizon has already elapsed")
        tles = self._fetch_and_store_tles(targets, now)
        opportunities = self.opportunity_builder.build(
            targets,
            stations,
            tles,
            effective_start,
            horizon_end,
            blockers=blockers,
            evidence=evidence,
        )
        plan = self.scheduler.schedule(
            opportunities,
            horizon_start=effective_start,
            horizon_end=horizon_end,
            tle_fingerprints={key: value.fingerprint for key, value in tles.items()},
            policy=schedule_policy,
            plan_id=plan_id,
            revision=revision,
            trigger=trigger,
            created_at=now,
        )
        blocker_fingerprint = _blocker_fingerprint(blockers)
        planning_input_fingerprint = _planning_input_fingerprint(
            targets, stations, schedule_policy
        )
        plan = replace(
            plan,
            diagnostics={
                **plan.diagnostics,
                "blocker_fingerprint": blocker_fingerprint,
                "planning_input_fingerprint": planning_input_fingerprint,
                "tle_snapshot_evidence": _tle_snapshot_evidence(tles),
            },
        )
        if self.store is not None:
            self.store.save_plan(plan)
        return PlanningRun(
            plan=plan,
            opportunities=opportunities,
            tles=tles,
            replanned=True,
            blocker_fingerprint=blocker_fingerprint,
            planning_input_fingerprint=planning_input_fingerprint,
        )

    def refresh(
        self,
        previous: PlanningRun,
        targets: Sequence[SatelliteTarget],
        stations: Sequence[GroundStation],
        *,
        blockers: Sequence[Blocker] = (),
        evidence: Sequence[ReceptionEvidence] = (),
        schedule_policy: SchedulePolicy | None = None,
        replan_policy: ReplanPolicy | None = None,
        now: datetime | None = None,
        force_rebuild_reason: str | None = None,
    ) -> PlanningRun:
        now = (now or datetime.now(UTC)).astimezone(UTC)
        current_tles = self._fetch_and_store_tles(targets, now)
        changed_ids = {
            norad_id
            for norad_id, snapshot in current_tles.items()
            if previous.plan.tle_fingerprints.get(norad_id) != snapshot.fingerprint
        }
        blocker_fingerprint = _blocker_fingerprint(blockers)
        blockers_changed = blocker_fingerprint != previous.blocker_fingerprint
        planning_input_fingerprint = _planning_input_fingerprint(
            targets, stations, schedule_policy
        )
        planning_inputs_changed = (
            planning_input_fingerprint != previous.planning_input_fingerprint
        )
        if (
            not changed_ids
            and not blockers_changed
            and not planning_inputs_changed
            and force_rebuild_reason is None
        ):
            return PlanningRun(
                plan=previous.plan,
                opportunities=previous.opportunities,
                tles=current_tles,
                replanned=False,
                blocker_fingerprint=blocker_fingerprint,
                planning_input_fingerprint=planning_input_fingerprint,
            )

        rebuild_all = (
            blockers_changed
            or planning_inputs_changed
            or force_rebuild_reason is not None
        )
        effective_start = max(previous.plan.horizon_start, now)
        if effective_start >= previous.plan.horizon_end:
            raise ValueError("planning horizon has already elapsed")
        unchanged = (
            ()
            if rebuild_all
            else tuple(
                item
                for item in previous.opportunities
                if item.norad_id not in changed_ids and item.start >= effective_start
            )
        )
        changed_targets = (
            tuple(targets)
            if rebuild_all
            else tuple(target for target in targets if target.norad_id in changed_ids)
        )
        rebuilt = self.opportunity_builder.build(
            changed_targets,
            stations,
            current_tles,
            effective_start,
            previous.plan.horizon_end,
            blockers=blockers,
            evidence=evidence,
        )
        opportunities = tuple(
            sorted(
                (*unchanged, *rebuilt),
                key=lambda item: (item.start, item.end, item.opportunity_id),
            )
        )
        if rebuild_all:
            replan_policy = replan_policy or ReplanPolicy()
            freeze_until = now + replan_policy.freeze_horizon
            available_ids = {item.opportunity_id for item in opportunities}
            frozen_ids = tuple(
                item.opportunity_id
                for item in previous.plan.assignments
                if item.opportunity_id in available_ids
                and item.end > now
                and item.start < freeze_until
            )
            reason = force_rebuild_reason or "blockers_changed"
            if planning_inputs_changed:
                reason = (
                    f"{reason}+planning_inputs_changed"
                    if force_rebuild_reason or blockers_changed
                    else "planning_inputs_changed"
                )
            if changed_ids:
                reason += "+tle_changed"
            plan = self.scheduler.schedule(
                opportunities,
                horizon_start=effective_start,
                horizon_end=previous.plan.horizon_end,
                tle_fingerprints={
                    key: value.fingerprint for key, value in current_tles.items()
                },
                policy=schedule_policy,
                fixed_opportunity_ids=frozen_ids,
                plan_id=previous.plan.plan_id,
                revision=previous.plan.revision + 1,
                trigger=f"input_change:{reason}",
                created_at=now,
            )
            result_plan, result_impact, was_replanned = plan, None, True
        else:
            result = self.rolling_planner.refresh(
                previous.plan,
                tuple(
                    item
                    for item in previous.opportunities
                    if item.start >= effective_start
                ),
                opportunities,
                current_tles,
                now=now,
                replan_policy=replan_policy,
                schedule_policy=schedule_policy,
            )
            result_plan, result_impact, was_replanned = (
                result.plan,
                result.impact,
                result.replanned,
            )
        if was_replanned:
            result_plan = replace(
                result_plan,
                diagnostics={
                    **result_plan.diagnostics,
                    "blocker_fingerprint": blocker_fingerprint,
                    "planning_input_fingerprint": planning_input_fingerprint,
                    "tle_snapshot_evidence": _tle_snapshot_evidence(current_tles),
                },
            )
        if was_replanned and self.store is not None:
            self.store.save_plan(result_plan)
        return PlanningRun(
            plan=result_plan,
            opportunities=opportunities,
            tles=current_tles,
            replanned=was_replanned,
            impact=result_impact,
            blocker_fingerprint=blocker_fingerprint,
            planning_input_fingerprint=planning_input_fingerprint,
        )
