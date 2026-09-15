"""Tamper-evident protocol for a real, at-least-30-day planner campaign."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .api_contact import ApiContactError, validate_publication_user_agent
from .dataset import DatasetIntegrityError, parse_api_datetime
from .store import plan_from_document
from .models import as_utc


PROSPECTIVE_CONFIG_SCHEMA = "observation-planning-prospective-config-v1"
PROSPECTIVE_EVENT_SCHEMA = "observation-planning-prospective-event-v1"
ALLOWED_EVENTS = {
    "campaign_initialized",
    "plan_committed",
    "plan_failed",
    "outcome_recorded",
    "campaign_note",
}


class ProspectiveError(ValueError):
    pass


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProspectiveCampaignConfig:
    schema_version: str
    campaign_id: str
    registered_at: datetime
    start: datetime
    end: datetime
    station_ids: tuple[int, ...]
    target_norad_ids: tuple[int, ...]
    mode: str = "shadow"
    planning_interval_hours: int = 24
    minimum_elapsed_days: int = 30
    minimum_plan_commit_days: int = 25
    minimum_reconciled_outcomes: int = 30
    minimum_observations_per_target: int = 0
    minimum_scored_targets: int = 0
    minimum_scored_stations: int = 0
    minimum_signal_positive_outcomes: int = 0
    minimum_signal_negative_outcomes: int = 0
    minimum_conditional_decode_positive_outcomes: int = 0
    minimum_conditional_decode_negative_outcomes: int = 0
    minimum_opportunity_duration_seconds: int = 60
    maximum_opportunity_duration_seconds: int | None = None
    runtime_user_agent: str | None = None

    @classmethod
    def load(cls, path: Path) -> "ProspectiveCampaignConfig":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ProspectiveError("prospective config must be a JSON object")
        config = cls(
            schema_version=str(payload.get("schema_version", "")),
            campaign_id=str(payload.get("campaign_id", "")),
            registered_at=parse_api_datetime(
                payload.get("registered_at"), name="registered_at"
            ),
            start=parse_api_datetime(payload.get("start"), name="start"),
            end=parse_api_datetime(payload.get("end"), name="end"),
            station_ids=tuple(int(value) for value in payload.get("station_ids", [])),  # type: ignore[arg-type]
            target_norad_ids=tuple(
                int(value) for value in payload.get("target_norad_ids", [])  # type: ignore[arg-type]
            ),
            mode=str(payload.get("mode", "shadow")),
            planning_interval_hours=int(payload.get("planning_interval_hours", 24)),
            minimum_elapsed_days=int(payload.get("minimum_elapsed_days", 30)),
            minimum_plan_commit_days=int(payload.get("minimum_plan_commit_days", 25)),
            minimum_reconciled_outcomes=int(
                payload.get("minimum_reconciled_outcomes", 30)
            ),
            minimum_observations_per_target=int(
                payload.get("minimum_observations_per_target", 0)
            ),
            minimum_scored_targets=int(
                payload.get("minimum_scored_targets", 0)
            ),
            minimum_scored_stations=int(
                payload.get("minimum_scored_stations", 0)
            ),
            minimum_signal_positive_outcomes=int(
                payload.get("minimum_signal_positive_outcomes", 0)
            ),
            minimum_signal_negative_outcomes=int(
                payload.get("minimum_signal_negative_outcomes", 0)
            ),
            minimum_conditional_decode_positive_outcomes=int(
                payload.get("minimum_conditional_decode_positive_outcomes", 0)
            ),
            minimum_conditional_decode_negative_outcomes=int(
                payload.get("minimum_conditional_decode_negative_outcomes", 0)
            ),
            minimum_opportunity_duration_seconds=int(
                payload.get("minimum_opportunity_duration_seconds", 60)
            ),
            maximum_opportunity_duration_seconds=(
                int(payload["maximum_opportunity_duration_seconds"])
                if payload.get("maximum_opportunity_duration_seconds") is not None
                else None
            ),
            runtime_user_agent=(
                str(payload["runtime_user_agent"])
                if payload.get("runtime_user_agent") is not None
                else None
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.schema_version != PROSPECTIVE_CONFIG_SCHEMA:
            raise ProspectiveError("unsupported prospective config schema")
        if not self.campaign_id.strip():
            raise ProspectiveError("campaign_id is required")
        registered = as_utc(self.registered_at, name="registered_at")
        start, end = as_utc(self.start, name="start"), as_utc(self.end, name="end")
        if registered > start:
            raise ProspectiveError("campaign must be registered no later than its start")
        if end - start < timedelta(days=30):
            raise ProspectiveError("prospective campaign must span at least 30 days")
        for name, values in (
            ("station_ids", self.station_ids),
            ("target_norad_ids", self.target_norad_ids),
        ):
            if not values or any(value <= 0 for value in values):
                raise ProspectiveError(f"{name} must contain positive IDs")
            if len(values) != len(set(values)):
                raise ProspectiveError(f"{name} must be unique")
        if self.mode not in {"shadow", "submission-gated"}:
            raise ProspectiveError("mode must be shadow or submission-gated")
        if not 1 <= self.planning_interval_hours <= 24:
            raise ProspectiveError("planning interval must be in [1, 24] hours")
        if self.minimum_elapsed_days < 30:
            raise ProspectiveError("minimum elapsed days cannot weaken the 30-day gate")
        campaign_days = math.ceil((end - start).total_seconds() / 86_400)
        if not 1 <= self.minimum_plan_commit_days <= campaign_days:
            raise ProspectiveError("minimum plan-commit days is outside the campaign")
        if self.minimum_reconciled_outcomes <= 0:
            raise ProspectiveError("minimum reconciled outcomes must be positive")
        if not 0 <= self.minimum_observations_per_target <= 100:
            raise ProspectiveError(
                "minimum observations per target must be in [0, 100]"
            )
        if not 0 <= self.minimum_scored_targets <= len(self.target_norad_ids):
            raise ProspectiveError(
                "minimum scored targets exceeds the registered target count"
            )
        if not 0 <= self.minimum_scored_stations <= len(self.station_ids):
            raise ProspectiveError(
                "minimum scored stations exceeds the registered station count"
            )
        class_minima = (
            self.minimum_signal_positive_outcomes,
            self.minimum_signal_negative_outcomes,
            self.minimum_conditional_decode_positive_outcomes,
            self.minimum_conditional_decode_negative_outcomes,
        )
        if any(value < 0 for value in class_minima):
            raise ProspectiveError(
                "prospective outcome class minimums cannot be negative"
            )
        if (
            self.minimum_signal_positive_outcomes
            + self.minimum_signal_negative_outcomes
            > self.minimum_reconciled_outcomes
        ):
            raise ProspectiveError(
                "signal class minimums exceed minimum reconciled outcomes"
            )
        if (
            self.minimum_conditional_decode_positive_outcomes
            + self.minimum_conditional_decode_negative_outcomes
            > self.minimum_reconciled_outcomes
        ):
            raise ProspectiveError(
                "conditional decode class minimums exceed minimum reconciled outcomes"
            )
        if not 60 <= self.minimum_opportunity_duration_seconds <= 86_400:
            raise ProspectiveError(
                "minimum opportunity duration must be in [60, 86400] seconds"
            )
        if self.maximum_opportunity_duration_seconds is not None and not (
            60 <= self.maximum_opportunity_duration_seconds <= 86_400
        ):
            raise ProspectiveError(
                "maximum opportunity duration must be in [60, 86400] seconds"
            )
        if (
            self.maximum_opportunity_duration_seconds is not None
            and self.minimum_opportunity_duration_seconds
            > self.maximum_opportunity_duration_seconds
        ):
            raise ProspectiveError(
                "minimum opportunity duration cannot exceed the maximum"
            )
        if self.runtime_user_agent is not None:
            try:
                validate_publication_user_agent(self.runtime_user_agent)
            except ApiContactError as exc:
                raise ProspectiveError(
                    "runtime_user_agent is invalid or contains a placeholder"
                ) from exc


@dataclass(frozen=True, slots=True)
class ProspectiveEvent:
    sequence: int
    campaign_id: str
    event_type: str
    occurred_at: datetime
    payload: Mapping[str, object]
    previous_event_sha256: str | None
    event_sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": PROSPECTIVE_EVENT_SCHEMA,
            "sequence": self.sequence,
            "campaign_id": self.campaign_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat().replace("+00:00", "Z"),
            "payload": dict(self.payload),
            "previous_event_sha256": self.previous_event_sha256,
            "event_sha256": self.event_sha256,
        }


def _event_without_hash(
    *,
    sequence: int,
    campaign_id: str,
    event_type: str,
    occurred_at: datetime,
    payload: Mapping[str, object],
    previous_event_sha256: str | None,
) -> dict[str, object]:
    return {
        "schema_version": PROSPECTIVE_EVENT_SCHEMA,
        "sequence": sequence,
        "campaign_id": campaign_id,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "payload": dict(payload),
        "previous_event_sha256": previous_event_sha256,
    }


def read_ledger(path: Path) -> tuple[ProspectiveEvent, ...]:
    if not path.exists():
        return ()
    events: list[ProspectiveEvent] = []
    previous_hash: str | None = None
    previous_time: datetime | None = None
    campaign_id: str | None = None
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProspectiveError(f"invalid ledger JSON at line {line_number}") from exc
            if not isinstance(item, Mapping):
                raise ProspectiveError(f"ledger line {line_number} is not an object")
            if item.get("schema_version") != PROSPECTIVE_EVENT_SCHEMA:
                raise ProspectiveError("unsupported prospective event schema")
            sequence = int(item.get("sequence", 0))
            if sequence != len(events) + 1:
                raise ProspectiveError("ledger sequence is not contiguous")
            current_campaign = str(item.get("campaign_id", ""))
            if not current_campaign or (
                campaign_id is not None and current_campaign != campaign_id
            ):
                raise ProspectiveError("ledger campaign_id changed")
            campaign_id = current_campaign
            event_type = str(item.get("event_type", ""))
            if event_type not in ALLOWED_EVENTS:
                raise ProspectiveError("ledger contains an unknown event type")
            occurred_at = parse_api_datetime(
                item.get("occurred_at"), name="occurred_at"
            )
            if previous_time is not None and occurred_at < previous_time:
                raise ProspectiveError("ledger event time moved backwards")
            payload = item.get("payload")
            if not isinstance(payload, Mapping):
                raise ProspectiveError("ledger event payload must be an object")
            if item.get("previous_event_sha256") != previous_hash:
                raise ProspectiveError("ledger hash chain is broken")
            unsigned = _event_without_hash(
                sequence=sequence,
                campaign_id=current_campaign,
                event_type=event_type,
                occurred_at=occurred_at,
                payload=payload,
                previous_event_sha256=previous_hash,
            )
            actual_hash = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
            if item.get("event_sha256") != actual_hash:
                raise ProspectiveError("ledger event hash mismatch")
            events.append(
                ProspectiveEvent(
                    sequence,
                    current_campaign,
                    event_type,
                    occurred_at,
                    dict(payload),
                    previous_hash,
                    actual_hash,
                )
            )
            previous_hash = actual_hash
            previous_time = occurred_at
    if events and events[0].event_type != "campaign_initialized":
        raise ProspectiveError("ledger must begin with campaign_initialized")
    return tuple(events)


def append_event(
    ledger_path: Path,
    *,
    campaign_id: str,
    event_type: str,
    payload: Mapping[str, object],
    occurred_at: datetime,
) -> ProspectiveEvent:
    if event_type not in ALLOWED_EVENTS:
        raise ProspectiveError("unsupported prospective event type")
    events = read_ledger(ledger_path)
    if events and events[0].campaign_id != campaign_id:
        raise ProspectiveError("event campaign_id differs from the ledger")
    occurred_at = as_utc(occurred_at, name="occurred_at")
    if events and occurred_at < events[-1].occurred_at:
        raise ProspectiveError("new event time predates the ledger tail")
    previous_hash = events[-1].event_sha256 if events else None
    unsigned = _event_without_hash(
        sequence=len(events) + 1,
        campaign_id=campaign_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=payload,
        previous_event_sha256=previous_hash,
    )
    event_hash = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()
    event = ProspectiveEvent(
        sequence=len(events) + 1,
        campaign_id=campaign_id,
        event_type=event_type,
        occurred_at=occurred_at,
        payload=dict(payload),
        previous_event_sha256=previous_hash,
        event_sha256=event_hash,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical_bytes(event.as_dict()) + b"\n"
    descriptor = os.open(
        ledger_path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND,
        0o644,
    )
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return event


def initialize_campaign(
    config_path: Path,
    ledger_path: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProspectiveEvent:
    config = ProspectiveCampaignConfig.load(config_path)
    if read_ledger(ledger_path):
        raise ProspectiveError("prospective ledger is already initialized")
    current = as_utc(now(), name="now")
    if current < config.registered_at:
        raise ProspectiveError("cannot initialize before the declared registration time")
    if current > config.start:
        raise ProspectiveError("cannot prospectively initialize after campaign start")
    return append_event(
        ledger_path,
        campaign_id=config.campaign_id,
        event_type="campaign_initialized",
        occurred_at=current,
        payload={
            "config_path": str(config_path),
            "config_sha256": sha256_file(config_path),
            "mode": config.mode,
            "start": config.start.isoformat().replace("+00:00", "Z"),
            "end": config.end.isoformat().replace("+00:00", "Z"),
            "station_ids": list(config.station_ids),
            "target_norad_ids": list(config.target_norad_ids),
            "minimum_observations_per_target": (
                config.minimum_observations_per_target
            ),
            "minimum_scored_targets": config.minimum_scored_targets,
            "minimum_scored_stations": config.minimum_scored_stations,
            "minimum_signal_positive_outcomes": (
                config.minimum_signal_positive_outcomes
            ),
            "minimum_signal_negative_outcomes": (
                config.minimum_signal_negative_outcomes
            ),
            "minimum_conditional_decode_positive_outcomes": (
                config.minimum_conditional_decode_positive_outcomes
            ),
            "minimum_conditional_decode_negative_outcomes": (
                config.minimum_conditional_decode_negative_outcomes
            ),
            "minimum_opportunity_duration_seconds": (
                config.minimum_opportunity_duration_seconds
            ),
            "maximum_opportunity_duration_seconds": (
                config.maximum_opportunity_duration_seconds
            ),
        },
    )


def _assert_config_binding(
    config_path: Path, ledger_path: Path
) -> tuple[ProspectiveCampaignConfig, tuple[ProspectiveEvent, ...]]:
    config = ProspectiveCampaignConfig.load(config_path)
    events = read_ledger(ledger_path)
    if not events:
        raise ProspectiveError("prospective ledger has not been initialized")
    initialized = events[0].payload
    if initialized.get("config_sha256") != sha256_file(config_path):
        raise ProspectiveError("prospective config changed after registration")
    if events[0].campaign_id != config.campaign_id:
        raise ProspectiveError("config and ledger campaign IDs differ")
    return config, events


def commit_plan(
    config_path: Path,
    ledger_path: Path,
    plan_path: Path,
    *,
    input_artifacts: Sequence[Path] = (),
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProspectiveEvent:
    config, events = _assert_config_binding(config_path, ledger_path)
    document = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ProspectiveError("plan document must be an object")
    plan = plan_from_document(document)
    current = as_utc(now(), name="now")
    if not events[0].occurred_at <= current < config.end:
        raise ProspectiveError("plan commitment is outside the registered campaign")
    if plan.created_at > current:
        raise ProspectiveError("plan creation time is in the future")
    if plan.horizon_end <= current or plan.horizon_start >= config.end:
        raise ProspectiveError("plan horizon does not overlap the remaining campaign")
    if any(
        item.satnogs_station_id not in config.station_ids
        or item.norad_id not in config.target_norad_ids
        for item in plan.assignments
    ):
        raise ProspectiveError("plan contains a station or target outside the protocol")
    if any(
        (item.end - item.start).total_seconds()
        < config.minimum_opportunity_duration_seconds
        or (
            config.maximum_opportunity_duration_seconds is not None
            and (item.end - item.start).total_seconds()
            > config.maximum_opportunity_duration_seconds
        )
        for item in plan.assignments
    ):
        raise ProspectiveError("plan contains an assignment outside duration bounds")
    if any(item.start <= current for item in plan.assignments):
        raise ProspectiveError(
            "every assignment must start strictly after the plan commitment"
        )
    plan_hash = sha256_file(plan_path)
    if any(
        event.event_type == "plan_committed"
        and event.payload.get("plan_sha256") == plan_hash
        for event in events
    ):
        raise ProspectiveError("this exact plan was already committed")
    previous_revisions = [
        int(event.payload.get("revision", 0))
        for event in events
        if event.event_type == "plan_committed"
        and event.payload.get("plan_id") == plan.plan_id
    ]
    if previous_revisions and plan.revision <= max(previous_revisions):
        raise ProspectiveError("plan revision must increase monotonically")
    artifacts = [
        {"path": str(path), "sha256": sha256_file(path)} for path in input_artifacts
    ]
    return append_event(
        ledger_path,
        campaign_id=config.campaign_id,
        event_type="plan_committed",
        occurred_at=current,
        payload={
            "plan_path": str(plan_path),
            "plan_sha256": plan_hash,
            "plan_id": plan.plan_id,
            "revision": plan.revision,
            "canonical_fingerprint": plan.canonical_fingerprint(),
            "created_at": plan.created_at.isoformat().replace("+00:00", "Z"),
            "commit_latency_seconds": (current - plan.created_at).total_seconds(),
            "horizon_start": plan.horizon_start.isoformat().replace("+00:00", "Z"),
            "horizon_end": plan.horizon_end.isoformat().replace("+00:00", "Z"),
            "trigger": plan.trigger,
            "assignment_count": len(plan.assignments),
            "tle_fingerprints": {
                str(key): value for key, value in sorted(plan.tle_fingerprints.items())
            },
            "input_artifacts": artifacts,
            "assignments": [
                {
                    "opportunity_id": item.opportunity_id,
                    "norad_id": item.norad_id,
                    "station_id": item.station_id,
                    "satnogs_station_id": item.satnogs_station_id,
                    "transmitter_uuid": item.transmitter_uuid,
                    "frequency_hz": item.frequency_hz,
                    "modulation": item.modulation,
                    "start": item.start.isoformat().replace("+00:00", "Z"),
                    "end": item.end.isoformat().replace("+00:00", "Z"),
                    "p_signal_present": item.probability.p_signal_present,
                    "p_decode_given_signal": item.probability.p_decode_given_signal,
                    "p_success": item.probability.p_success,
                    "expected_unique_samples": item.expected_unique_samples,
                    "model_version": item.probability.model_version,
                }
                for item in plan.assignments
            ],
        },
    )


def active_committed_assignment_versions(
    events: Sequence[ProspectiveEvent],
) -> tuple[tuple[ProspectiveEvent, Mapping[str, object]], ...]:
    """Return assignments not replaced by a later full plan revision.

    Replacement is horizon-based rather than opportunity-ID-based because a
    refreshed TLE or a changed feasibility filter can legitimately change the
    identifier of the same future contact.  A later plan can replace an older
    assignment only when it belongs to the same plan lineage, was committed
    before that assignment began, and covers its complete interval.
    """

    plans = tuple(event for event in events if event.event_type == "plan_committed")
    active: list[tuple[ProspectiveEvent, Mapping[str, object]]] = []
    for index, event in enumerate(plans):
        plan_id = event.payload.get("plan_id")
        if not isinstance(plan_id, str) or not plan_id:
            plan_id = None
        for assignment in event.payload.get("assignments", []):  # type: ignore[union-attr]
            if not isinstance(assignment, Mapping):
                continue
            try:
                start = parse_api_datetime(
                    assignment.get("start"), name="assignment.start"
                )
                end = parse_api_datetime(
                    assignment.get("end"), name="assignment.end"
                )
            except (TypeError, ValueError):
                continue
            superseded = False
            if plan_id is not None:
                for later in plans[index + 1 :]:
                    if later.payload.get("plan_id") != plan_id:
                        continue
                    try:
                        horizon_start = parse_api_datetime(
                            later.payload.get("horizon_start"),
                            name="plan.horizon_start",
                        )
                        horizon_end = parse_api_datetime(
                            later.payload.get("horizon_end"),
                            name="plan.horizon_end",
                        )
                    except (TypeError, ValueError):
                        continue
                    if (
                        later.occurred_at < start
                        and horizon_start <= start
                        and end <= horizon_end
                    ):
                        superseded = True
                        break
            if not superseded:
                active.append((event, assignment))
    return tuple(active)


def record_outcomes(
    config_path: Path,
    ledger_path: Path,
    outcomes_path: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[ProspectiveEvent, ...]:
    config, events = _assert_config_binding(config_path, ledger_path)
    payload = json.loads(outcomes_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ProspectiveError("outcomes must be a JSON array")
    committed: dict[str, list[tuple[ProspectiveEvent, Mapping[str, object]]]] = {}
    for event, item in active_committed_assignment_versions(events):
        if item.get("opportunity_id"):
            committed.setdefault(str(item["opportunity_id"]), []).append(
                (event, item)
            )
    already = {
        str(event.payload.get("opportunity_id"))
        for event in events
        if event.event_type == "outcome_recorded"
    }
    recorded_observation_ids = {
        int(event.payload["observation_id"])
        for event in events
        if event.event_type == "outcome_recorded"
        and isinstance(event.payload.get("observation_id"), int)
        and not isinstance(event.payload.get("observation_id"), bool)
    }
    batch_observation_ids: set[int] = set()
    prepared: list[dict[str, object]] = []
    current = as_utc(now(), name="now")
    source_artifact_sha256 = sha256_file(outcomes_path)
    for raw in payload:
        if not isinstance(raw, Mapping):
            raise ProspectiveError("outcome entry must be an object")
        opportunity_id = str(raw.get("opportunity_id", ""))
        if opportunity_id not in committed:
            raise ProspectiveError("outcome does not reference a committed opportunity")
        if opportunity_id in already:
            raise ProspectiveError("outcome for this opportunity is already recorded")
        signal = raw.get("signal_present")
        decoded = raw.get("decode_success_given_signal")
        if (
            isinstance(signal, bool)
            or isinstance(decoded, bool)
            or signal not in {0, 1, None}
            or decoded not in {0, 1, None}
        ):
            raise ProspectiveError("outcome labels must be 0, 1 or null")
        # A non-empty decoded artifact establishes a positive decode even when
        # the independent waterfall review is still unknown.  A decode failure
        # requires a confirmed signal, and no decode result may contradict an
        # explicit no-signal review.
        if decoded == 0 and signal != 1:
            raise ProspectiveError("decode failure requires a confirmed signal")
        if decoded == 1 and signal == 0:
            raise ProspectiveError("positive decode contradicts a no-signal label")
        observed_at = parse_api_datetime(raw.get("observed_at"), name="observed_at")
        if observed_at > current:
            raise ProspectiveError("outcome observation is in the future")
        observation_id = raw.get("observation_id")
        if observation_id is not None:
            if (
                isinstance(observation_id, bool)
                or not isinstance(observation_id, int)
                or observation_id <= 0
            ):
                raise ProspectiveError("observation_id must be a positive integer")
            if (
                observation_id in recorded_observation_ids
                or observation_id in batch_observation_ids
            ):
                raise ProspectiveError(
                    "a Network observation ID cannot score multiple opportunities"
                )
        source_label = str(raw.get("source", "local-or-satnogs-reconciliation"))
        raw_source_path = raw.get("raw_source_artifact_path")
        raw_source_sha256 = raw.get("raw_source_artifact_sha256")
        has_raw_path = isinstance(raw_source_path, str) and bool(raw_source_path)
        has_raw_hash = isinstance(raw_source_sha256, str) and bool(raw_source_sha256)
        if has_raw_path != has_raw_hash:
            raise ProspectiveError("raw source artifact path and hash must be paired")
        if "SatNOGS Network" in source_label and not (has_raw_path and has_raw_hash):
            raise ProspectiveError("Network outcomes require a raw source artifact")
        if has_raw_path and (
            len(str(raw_source_sha256)) != 64
            or sha256_file(Path(str(raw_source_path))) != raw_source_sha256
        ):
            raise ProspectiveError("raw source artifact SHA-256 mismatch")
        eligible_predictions: list[
            tuple[ProspectiveEvent, Mapping[str, object]]
        ] = []
        for plan_event, assignment in committed[opportunity_id]:
            assignment_start = parse_api_datetime(
                assignment.get("start"), name="assignment.start"
            )
            if plan_event.occurred_at < assignment_start <= observed_at:
                eligible_predictions.append((plan_event, assignment))
        if not eligible_predictions:
            raise ProspectiveError(
                "outcome has no prediction committed before its observation window"
            )
        prediction_event, prediction_assignment = max(
            eligible_predictions, key=lambda item: item[0].occurred_at
        )
        prediction = {
            name: prediction_assignment[name]
            for name in (
                "p_signal_present",
                "p_decode_given_signal",
                "p_success",
                "model_version",
            )
            if name in prediction_assignment
        }
        prepared.append(
            {
                "opportunity_id": opportunity_id,
                "observation_id": observation_id,
                "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
                "signal_present": signal,
                "decode_success_given_signal": decoded,
                "source": source_label,
                "source_artifact_path": str(outcomes_path),
                "source_artifact_sha256": source_artifact_sha256,
                "raw_source_artifact_path": raw_source_path if has_raw_path else None,
                "raw_source_artifact_sha256": (
                    raw_source_sha256 if has_raw_hash else None
                ),
                "prediction_plan_event_sha256": prediction_event.event_sha256,
                "prediction_plan_committed_at": prediction_event.occurred_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "prediction": prediction,
            }
        )
        already.add(opportunity_id)
        if observation_id is not None:
            batch_observation_ids.add(observation_id)
    result = [
        append_event(
            ledger_path,
            campaign_id=config.campaign_id,
            event_type="outcome_recorded",
            occurred_at=current,
            payload=item,
        )
        for item in prepared
    ]
    return tuple(result)


def prospective_report(
    config_path: Path,
    ledger_path: Path,
    *,
    now: datetime | None = None,
) -> Mapping[str, object]:
    config, events = _assert_config_binding(config_path, ledger_path)
    current = as_utc(now or datetime.now(UTC), name="now")
    effective_end = min(current, config.end)
    elapsed_seconds = max(0.0, (effective_end - config.start).total_seconds())
    elapsed_days = elapsed_seconds / 86_400
    commits = [event for event in events if event.event_type == "plan_committed"]
    in_campaign_commits = [
        event for event in commits if config.start <= event.occurred_at < config.end
    ]
    outcomes = [event for event in events if event.event_type == "outcome_recorded"]
    commit_days = {
        event.occurred_at.date().isoformat()
        for event in in_campaign_commits
    }
    target_complete_plan_commits = 0
    assigned_target_ids: set[int] = set()
    opportunity_duration_violation_count = 0
    prediction_commit_timing_violation_count = 0
    for event in in_campaign_commits:
        counts: dict[int, int] = {}
        for item in event.payload.get("assignments", []):  # type: ignore[union-attr]
            if not isinstance(item, Mapping):
                continue
            norad_id = item.get("norad_id")
            if isinstance(norad_id, int) and not isinstance(norad_id, bool):
                counts[norad_id] = counts.get(norad_id, 0) + 1
                assigned_target_ids.add(norad_id)
            try:
                assignment_start = parse_api_datetime(
                    item.get("start"), name="assignment.start"
                )
                if assignment_start <= event.occurred_at:
                    prediction_commit_timing_violation_count += 1
            except (TypeError, ValueError):
                prediction_commit_timing_violation_count += 1
            try:
                assignment_start = parse_api_datetime(
                    item.get("start"), name="assignment.start"
                )
                assignment_end = parse_api_datetime(
                    item.get("end"), name="assignment.end"
                )
                duration_seconds = (
                    assignment_end - assignment_start
                ).total_seconds()
                if (
                    duration_seconds
                    < config.minimum_opportunity_duration_seconds
                    or (
                        config.maximum_opportunity_duration_seconds is not None
                        and duration_seconds
                        > config.maximum_opportunity_duration_seconds
                    )
                ):
                    opportunity_duration_violation_count += 1
            except (TypeError, ValueError):
                opportunity_duration_violation_count += 1
        if all(
            counts.get(norad_id, 0) >= config.minimum_observations_per_target
            for norad_id in config.target_norad_ids
        ):
            target_complete_plan_commits += 1
    assignment_versions: dict[
        str, list[tuple[datetime, Mapping[str, object]]]
    ] = {}
    for event, item in active_committed_assignment_versions(events):
        assignment_versions.setdefault(
            str(item.get("opportunity_id")), []
        ).append((event.occurred_at, item))
    scored_signal: list[tuple[int, float]] = []
    scored_decode: list[tuple[int, float]] = []
    scored_end_to_end: list[tuple[int, float]] = []
    scored_target_ids: set[int] = set()
    scored_station_ids: set[int] = set()
    for event in outcomes:
        opportunity_id = str(event.payload.get("opportunity_id"))
        observed_raw = event.payload.get("observed_at")
        observed_at = (
            parse_api_datetime(observed_raw, name="observed_at")
            if observed_raw is not None
            else event.occurred_at
        )
        eligible = []
        for committed_at, item in assignment_versions.get(opportunity_id, []):
            try:
                assignment_start = parse_api_datetime(
                    item.get("start"), name="assignment.start"
                )
            except (TypeError, ValueError):
                continue
            if committed_at < assignment_start <= observed_at:
                eligible.append(item)
        eligible_assignment = eligible[-1] if eligible else None
        prediction = event.payload.get("prediction")
        if not isinstance(prediction, Mapping):
            prediction = eligible_assignment
        signal = event.payload.get("signal_present")
        decode = event.payload.get("decode_success_given_signal")
        if prediction is None or eligible_assignment is None:
            continue
        if signal in {0, 1} and "p_signal_present" in prediction:
            scored_signal.append(
                (int(signal), float(prediction["p_signal_present"]))
            )
            raw_target = eligible_assignment.get("norad_id")
            if isinstance(raw_target, int) and not isinstance(raw_target, bool):
                scored_target_ids.add(raw_target)
            raw_station = eligible_assignment.get("satnogs_station_id")
            if isinstance(raw_station, int) and not isinstance(raw_station, bool):
                scored_station_ids.add(raw_station)
        if decode in {0, 1} and "p_decode_given_signal" in prediction:
            scored_decode.append(
                (int(decode), float(prediction["p_decode_given_signal"]))
            )
        end_to_end = 0 if signal == 0 else int(decode) if decode in {0, 1} else None
        if end_to_end is not None and "p_success" in prediction:
            scored_end_to_end.append(
                (end_to_end, float(prediction["p_success"]))
            )

    def brier_score(values: Sequence[tuple[int, float]]) -> float | None:
        return (
            sum((outcome - probability) ** 2 for outcome, probability in values)
            / len(values)
            if values
            else None
        )

    minimum_signal_outcomes = config.minimum_reconciled_outcomes
    minimum_decode_outcomes = max(
        1, math.ceil(config.minimum_reconciled_outcomes / 3)
    )
    signal_positive_outcomes = sum(outcome for outcome, _ in scored_signal)
    signal_negative_outcomes = len(scored_signal) - signal_positive_outcomes
    conditional_decode_positive_outcomes = sum(
        outcome for outcome, _ in scored_decode
    )
    conditional_decode_negative_outcomes = (
        len(scored_decode) - conditional_decode_positive_outcomes
    )
    tle_change_commit_count = 0
    previous_tles: Mapping[str, object] | None = None
    for event in commits:
        current_tles = event.payload.get("tle_fingerprints")
        if isinstance(current_tles, Mapping):
            if previous_tles is not None and dict(current_tles) != dict(previous_tles):
                tle_change_commit_count += 1
            previous_tles = current_tles
    gates = {
        "registered_before_start": (
            config.registered_at <= events[0].occurred_at <= config.start
        ),
        "configured_duration_at_least_30_days": (
            config.end - config.start >= timedelta(days=30)
        ),
        "elapsed_at_least_30_days": elapsed_days >= config.minimum_elapsed_days,
        "campaign_end_reached": current >= config.end,
        "plan_commit_days_met": len(commit_days) >= config.minimum_plan_commit_days,
        "registered_target_coverage_met": (
            config.minimum_observations_per_target == 0
            or (
                bool(in_campaign_commits)
                and target_complete_plan_commits == len(in_campaign_commits)
            )
        ),
        "opportunity_duration_bound_met": (
            opportunity_duration_violation_count == 0
        ),
        "predictions_committed_before_pass": (
            prediction_commit_timing_violation_count == 0
        ),
        "reconciled_outcomes_met": len(outcomes)
        >= config.minimum_reconciled_outcomes,
        "scored_signal_outcomes_met": len(scored_signal)
        >= minimum_signal_outcomes,
        "conditional_decode_outcomes_met": len(scored_decode)
        >= minimum_decode_outcomes,
        "scored_target_diversity_met": len(scored_target_ids)
        >= config.minimum_scored_targets,
        "scored_station_diversity_met": len(scored_station_ids)
        >= config.minimum_scored_stations,
        "signal_positive_outcomes_met": signal_positive_outcomes
        >= config.minimum_signal_positive_outcomes,
        "signal_negative_outcomes_met": signal_negative_outcomes
        >= config.minimum_signal_negative_outcomes,
        "conditional_decode_positive_outcomes_met": (
            conditional_decode_positive_outcomes
            >= config.minimum_conditional_decode_positive_outcomes
        ),
        "conditional_decode_negative_outcomes_met": (
            conditional_decode_negative_outcomes
            >= config.minimum_conditional_decode_negative_outcomes
        ),
        "hash_chain_valid": True,
    }
    return {
        "schema_version": "observation-planning-prospective-report-v1",
        "campaign_id": config.campaign_id,
        "generated_at": current.isoformat().replace("+00:00", "Z"),
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "ledger_path": str(ledger_path),
        "ledger_sha256": sha256_file(ledger_path),
        "mode": config.mode,
        "start": config.start.isoformat().replace("+00:00", "Z"),
        "end": config.end.isoformat().replace("+00:00", "Z"),
        "elapsed_days": elapsed_days,
        "event_count": len(events),
        "plan_commit_count": len(commits),
        "plan_commit_days": len(commit_days),
        "minimum_observations_per_target": config.minimum_observations_per_target,
        "minimum_scored_targets": config.minimum_scored_targets,
        "minimum_scored_stations": config.minimum_scored_stations,
        "minimum_signal_positive_outcomes": (
            config.minimum_signal_positive_outcomes
        ),
        "minimum_signal_negative_outcomes": (
            config.minimum_signal_negative_outcomes
        ),
        "minimum_conditional_decode_positive_outcomes": (
            config.minimum_conditional_decode_positive_outcomes
        ),
        "minimum_conditional_decode_negative_outcomes": (
            config.minimum_conditional_decode_negative_outcomes
        ),
        "minimum_opportunity_duration_seconds": (
            config.minimum_opportunity_duration_seconds
        ),
        "maximum_opportunity_duration_seconds": (
            config.maximum_opportunity_duration_seconds
        ),
        "opportunity_duration_violation_count": (
            opportunity_duration_violation_count
        ),
        "prediction_commit_timing_violation_count": (
            prediction_commit_timing_violation_count
        ),
        "assigned_target_ids": sorted(assigned_target_ids),
        "target_complete_plan_commit_count": target_complete_plan_commits,
        "reconciled_outcomes": len(outcomes),
        "minimum_scored_signal_outcomes": minimum_signal_outcomes,
        "minimum_conditional_decode_outcomes": minimum_decode_outcomes,
        "scored_signal_outcomes": len(scored_signal),
        "signal_brier": brier_score(scored_signal),
        "observed_signals": signal_positive_outcomes,
        "observed_signal_absences": signal_negative_outcomes,
        "scored_target_ids": sorted(scored_target_ids),
        "scored_station_ids": sorted(scored_station_ids),
        "conditional_decode_rows": len(scored_decode),
        "conditional_decode_brier": brier_score(scored_decode),
        "successful_decodes": conditional_decode_positive_outcomes,
        "failed_decodes_given_signal": conditional_decode_negative_outcomes,
        "end_to_end_scored_outcomes": len(scored_end_to_end),
        "end_to_end_brier": brier_score(scored_end_to_end),
        "tle_change_commit_count": tle_change_commit_count,
        "gates": gates,
        "publication_complete": all(gates.values()),
        "claim_boundary": (
            "A shadow campaign evaluates frozen recommendations only where an outcome can be "
            "reconciled; it does not identify the causal gain of executing the planner. A "
            "submission-gated campaign additionally requires immutable SatNOGS receipts."
        ),
    }


def write_prospective_report(
    config_path: Path,
    ledger_path: Path,
    output_path: Path,
    *,
    now: datetime | None = None,
) -> Mapping[str, object]:
    report = prospective_report(config_path, ledger_path, now=now)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return report
