"""Auditable adapter from selected opportunities to SatNOGS Network scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping, Sequence

from .models import ObservationPlan

SATNOGS_SCHEDULE_API_PATH = "observations/"


class SatnogsExportError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SatnogsScheduleExport:
    """A dry-run scheduling batch plus local-only provenance.

    ``api_payload`` matches Network's list-only NewObservation serializer.  TLE
    provenance stays in ``audit`` because Network resolves and stores its own
    current TLE at scheduling time.
    """

    schema_version: str
    api_path: str
    api_payload: tuple[Mapping[str, object], ...]
    audit: tuple[Mapping[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "mode": "dry_run",
            "api_path": self.api_path,
            "api_payload": [dict(item) for item in self.api_payload],
            "audit": [dict(item) for item in self.audit],
        }


def _satnogs_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


def build_satnogs_schedule_export(plan: ObservationPlan) -> SatnogsScheduleExport:
    payload: list[dict[str, object]] = []
    audit: list[dict[str, object]] = []
    station_intervals: dict[int, list[tuple[datetime, datetime, str]]] = {}
    for item in sorted(
        plan.assignments,
        key=lambda opportunity: (opportunity.start, opportunity.opportunity_id),
    ):
        if item.satnogs_station_id is None:
            raise SatnogsExportError(
                f"{item.opportunity_id} has no SatNOGS ground-station ID"
            )
        if item.transmitter_uuid is None or len(item.transmitter_uuid) != 22:
            raise SatnogsExportError(
                f"{item.opportunity_id} requires a 22-character SatNOGS transmitter UUID"
            )
        intervals = station_intervals.setdefault(item.satnogs_station_id, [])
        conflict = next(
            (
                other_id
                for other_start, other_end, other_id in intervals
                if item.start < other_end and other_start < item.end
            ),
            None,
        )
        if conflict is not None:
            raise SatnogsExportError(
                "SatNOGS Network permits no overlapping jobs on one station: "
                f"{conflict} conflicts with {item.opportunity_id}"
            )
        intervals.append((item.start, item.end, item.opportunity_id))
        request: dict[str, object] = {
            "start": _satnogs_time(item.start),
            "end": _satnogs_time(item.end),
            "ground_station": item.satnogs_station_id,
            "transmitter_uuid": item.transmitter_uuid,
        }
        if item.frequency_hz is not None:
            request["center_frequency"] = round(item.frequency_hz)
        payload.append(request)
        audit.append(
            {
                "opportunity_id": item.opportunity_id,
                "norad_id": item.norad_id,
                "tle_fingerprint": item.tle_fingerprint,
                "p_success": item.probability.p_success,
                "expected_unique_samples": item.expected_unique_samples,
                "plan_id": plan.plan_id,
                "plan_revision": plan.revision,
                "plan_canonical_fingerprint": plan.canonical_fingerprint(),
            }
        )
    return SatnogsScheduleExport(
        schema_version="satnogs-network-schedule-export-v1",
        api_path=SATNOGS_SCHEDULE_API_PATH,
        api_payload=tuple(payload),
        audit=tuple(audit),
    )


@dataclass(frozen=True, slots=True)
class SatnogsReconciliation:
    matched_opportunity_ids: tuple[str, ...]
    missing_opportunity_ids: tuple[str, ...]
    unexpected_job_ids: tuple[int, ...]


def reconcile_satnogs_jobs(
    export: SatnogsScheduleExport,
    jobs: Sequence[Mapping[str, object]],
) -> SatnogsReconciliation:
    """Match a dry-run/export manifest against read-only ``/api/jobs/`` data."""

    expected = list(zip(export.api_payload, export.audit, strict=True))
    matched: list[str] = []
    used_jobs: set[int] = set()

    def same_station(job: Mapping[str, object], expected_station: object) -> bool:
        try:
            return int(job.get("ground_station", -1)) == int(expected_station)
        except (TypeError, ValueError):
            return False

    for request, audit in expected:
        found_index = next(
            (
                index
                for index, job in enumerate(jobs)
                if index not in used_jobs
                and str(job.get("start", "")).replace("T", " ").removesuffix("Z")[:19]
                == request["start"]
                and str(job.get("end", "")).replace("T", " ").removesuffix("Z")[:19]
                == request["end"]
                and same_station(job, request["ground_station"])
                and (job.get("transmitter") or job.get("transmitter_uuid"))
                == request["transmitter_uuid"]
            ),
            None,
        )
        if found_index is not None:
            used_jobs.add(found_index)
            matched.append(str(audit["opportunity_id"]))
    all_ids = [str(item["opportunity_id"]) for item in export.audit]
    missing = [item for item in all_ids if item not in matched]
    unexpected = [
        int(job["id"])
        for index, job in enumerate(jobs)
        if index not in used_jobs and isinstance(job.get("id"), int)
    ]
    return SatnogsReconciliation(
        matched_opportunity_ids=tuple(matched),
        missing_opportunity_ids=tuple(missing),
        unexpected_job_ids=tuple(unexpected),
    )
