"""One idempotent daily run of the prospective read-only shadow planner."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from telemetry_yield.satnogs import SatNOGSClient

from .covariates import (
    ArchivedEnvironmentProvider,
    HardwareRecord,
    read_covariate_archive,
)
from .dataset import (
    DatasetIntegrityError,
    NormalizedObservation,
    parse_api_datetime,
    read_normalized_jsonl,
    satnogs_mode_is_packet_capable,
    satnogs_outcome_labels,
)
from .engine import DynamicObservationPlanner
from .environment import EnvironmentFeatureEnricher
from .models import (
    AntennaSpec,
    Blocker,
    GeoBox,
    GroundStation,
    SatelliteTarget,
    TransmissionRule,
    as_utc,
)
from .opportunities import OpportunityBuilder
from .orbit import Sgp4PassPredictor
from .probability import ReceptionEvidence
from .prospective import (
    ProspectiveCampaignConfig,
    active_committed_assignment_versions,
    append_event,
    commit_plan,
    read_ledger,
    record_outcomes,
)
from .provenance import build_planning_source_manifest
from .satnogs_dataset import PublicationDatasetConfig
from .scheduler import SchedulePolicy
from .station_inventory import SatnogsStationInventoryProvider
from .store import PlanningStore, plan_document
from .tle import CelesTrakTleProvider


class CampaignRunnerError(ValueError):
    pass


PLANNING_COMMIT_LEAD_TIME = timedelta(hours=1)


def _verify_runtime_source_manifest(
    path: Path, *, current: datetime
) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignRunnerError("source manifest is unreadable") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version")
        != "observation-planning-source-manifest-v1"
    ):
        raise CampaignRunnerError("unsupported source manifest")
    try:
        created_at = parse_api_datetime(payload.get("created_at"), name="created_at")
    except (TypeError, ValueError) as exc:
        raise CampaignRunnerError("source manifest has an invalid timestamp") from exc
    age_seconds = (current - created_at).total_seconds()
    if age_seconds < 0 or age_seconds > 600:
        raise CampaignRunnerError(
            "source manifest must be captured within 10 minutes before planning"
        )
    root_value = payload.get("project_root")
    records = payload.get("files")
    if not isinstance(root_value, str) or not isinstance(records, list) or not records:
        raise CampaignRunnerError("source manifest is missing its file inventory")
    project_root = Path(root_value).resolve()
    runtime_project_root = Path(__file__).resolve().parents[3]
    if project_root != runtime_project_root:
        raise CampaignRunnerError(
            "source manifest project root differs from the running planner"
        )
    try:
        canonical = build_planning_source_manifest(project_root)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CampaignRunnerError(
            "runtime source inventory cannot be reconstructed"
        ) from exc
    if (
        payload.get("file_count") != canonical.get("file_count")
        or payload.get("selection_snapshot_count")
        != canonical.get("selection_snapshot_count")
        or records != canonical.get("files")
    ):
        raise CampaignRunnerError(
            "source manifest does not contain the canonical runtime inventory"
        )
    for record in records:
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            raise CampaignRunnerError("source manifest contains a malformed file record")
        source_path = (project_root / str(record["path"])).resolve()
        try:
            source_path.relative_to(project_root)
            body = source_path.read_bytes()
        except (OSError, ValueError) as exc:
            raise CampaignRunnerError(
                "source manifest references an unavailable source file"
            ) from exc
        if (
            len(body) != record.get("byte_length")
            or hashlib.sha256(body).hexdigest() != record.get("sha256")
        ):
            raise CampaignRunnerError(
                "source manifest does not match the runtime source tree"
            )
    identity = str(canonical["source_identity_sha256"])
    if identity != payload.get("source_identity_sha256"):
        raise CampaignRunnerError("source manifest identity mismatch")
    return identity


def _apply_hardware(
    stations: Sequence[GroundStation],
    hardware: Sequence[HardwareRecord],
    *,
    as_of: datetime,
) -> tuple[GroundStation, ...]:
    result: list[GroundStation] = []
    for station in stations:
        candidates = sorted(
            (
                item
                for item in hardware
                if item.station_id == station.satnogs_station_id
                and item.effective_from <= as_of
                and item.known_at <= as_of
            ),
            key=lambda item: (item.effective_from, item.known_at),
            reverse=True,
        )
        gain = next(
            (
                item.receive_antenna_gain_dbi
                for item in candidates
                if item.receive_antenna_gain_dbi is not None
            ),
            None,
        )
        noise = next(
            (
                item.system_noise_temperature_k
                for item in candidates
                if item.system_noise_temperature_k is not None
            ),
            None,
        )
        if gain is None and noise is None:
            result.append(station)
            continue
        resources = []
        for resource in station.resources:
            antenna = resource.antenna
            if antenna is None:
                latest = candidates[0]
                antenna = AntennaSpec(
                    antenna_id=f"declared-{station.satnogs_station_id}",
                    frequency_ranges_hz=latest.frequency_ranges_hz,
                    gain_dbi=gain,
                    system_noise_temperature_k=noise,
                )
            else:
                antenna = replace(
                    antenna,
                    gain_dbi=gain if gain is not None else antenna.gain_dbi,
                    system_noise_temperature_k=(
                        noise
                        if noise is not None
                        else antenna.system_noise_temperature_k
                    ),
                )
            resources.append(replace(resource, antenna=antenna))
        result.append(replace(station, resources=tuple(resources)))
    return tuple(result)


def _targets(
    campaign: ProspectiveCampaignConfig,
    publication: PublicationDatasetConfig,
    transmitter_inventory_path: Path,
    target_overrides_path: Path | None = None,
) -> tuple[SatelliteTarget, ...]:
    inventory = json.loads(transmitter_inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, list):
        raise CampaignRunnerError("transmitter inventory must be an array")
    by_uuid = {
        str(item["uuid"]): item
        for item in inventory
        if isinstance(item, Mapping) and isinstance(item.get("uuid"), str)
    }
    selected = {target.norad_id: target for target in publication.targets}
    overrides: dict[int, Mapping[str, object]] = {}
    if target_overrides_path is not None:
        override_payload = json.loads(
            target_overrides_path.read_text(encoding="utf-8")
        )
        if (
            not isinstance(override_payload, Mapping)
            or override_payload.get("schema_version")
            != "observation-planning-target-overrides-v1"
        ):
            raise CampaignRunnerError("unsupported target override schema")
        raw_overrides = (
            override_payload.get("targets")
            if isinstance(override_payload, Mapping)
            else None
        )
        if not isinstance(raw_overrides, list):
            raise CampaignRunnerError("target override file must contain targets")
        for item in raw_overrides:
            if not isinstance(item, Mapping):
                raise CampaignRunnerError("target override entry must be an object")
            norad_id = int(item.get("norad_id", 0))
            if norad_id in overrides or norad_id not in campaign.target_norad_ids:
                raise CampaignRunnerError(
                    "target override IDs must be unique campaign targets"
                )
            overrides[norad_id] = item
    result: list[SatelliteTarget] = []
    for norad_id in campaign.target_norad_ids:
        target = selected.get(norad_id)
        if target is None:
            raise CampaignRunnerError(
                f"prospective target {norad_id} is absent from publication config"
            )
        raw = by_uuid.get(target.selection_transmitter_uuid)
        if raw is None or int(raw.get("norad_cat_id", 0)) != norad_id:
            raise CampaignRunnerError(
                f"frozen transmitter metadata is missing for NORAD {norad_id}"
            )
        frequency_raw = raw.get("downlink_low") or raw.get("downlink_high")
        frequency = float(frequency_raw) if frequency_raw is not None else None
        baud_raw = raw.get("baud")
        baud = float(baud_raw) if baud_raw is not None else None
        override = overrides.get(norad_id, {})
        rule_payload = override.get("transmission_rule", {})
        if not isinstance(rule_payload, Mapping):
            raise CampaignRunnerError("transmission_rule must be an object")
        rule_lists: dict[str, list[object]] = {}
        for field_name in (
            "weekdays_utc",
            "month_days_utc",
            "daily_windows_utc",
            "transmit_regions",
        ):
            field_value = rule_payload.get(field_name, [])
            if not isinstance(field_value, list):
                raise CampaignRunnerError(
                    f"transmission_rule.{field_name} must be an array"
                )
            rule_lists[field_name] = field_value
        windows = []
        for window in rule_lists["daily_windows_utc"]:
            if not isinstance(window, list) or len(window) != 2:
                raise CampaignRunnerError("daily transmit window must contain start/end")
            try:
                windows.append(
                    (
                        datetime.strptime(str(window[0]), "%H:%M:%S").time(),
                        datetime.strptime(str(window[1]), "%H:%M:%S").time(),
                    )
                )
            except ValueError as exc:
                raise CampaignRunnerError(
                    "daily transmit windows require HH:MM:SS UTC"
                ) from exc
        regions = []
        for region in rule_lists["transmit_regions"]:
            if not isinstance(region, Mapping):
                raise CampaignRunnerError("transmit region must be an object")
            regions.append(
                GeoBox(
                    south_deg=float(region["south_deg"]),
                    north_deg=float(region["north_deg"]),
                    west_deg=float(region["west_deg"]),
                    east_deg=float(region["east_deg"]),
                    label=str(region.get("label") or "transmit-region"),
                )
            )
        exclusive = override.get("exclusive_transmission", True)
        if not isinstance(exclusive, bool):
            raise CampaignRunnerError("exclusive_transmission must be boolean")
        result.append(
            SatelliteTarget(
                norad_id=norad_id,
                name=str(raw.get("sat_id") or f"NORAD {norad_id}"),
                transmitter_uuid=target.selection_transmitter_uuid,
                frequency_hz=frequency,
                modulation=target.selection_mode,
                baud=baud,
                transmit_power_dbw=(
                    float(override["transmit_power_dbw"])
                    if override.get("transmit_power_dbw") is not None
                    else None
                ),
                transmit_antenna_gain_dbi=(
                    float(override["transmit_antenna_gain_dbi"])
                    if override.get("transmit_antenna_gain_dbi") is not None
                    else None
                ),
                samples_per_second=float(override.get("samples_per_second", 1.0)),
                priority=float(override.get("priority", 1.0)),
                transmission_rule=TransmissionRule(
                    weekdays_utc=tuple(
                        int(value) for value in rule_lists["weekdays_utc"]
                    ),
                    month_days_utc=tuple(
                        int(value) for value in rule_lists["month_days_utc"]
                    ),
                    daily_windows_utc=tuple(windows),
                    transmit_regions=tuple(regions),
                    probability_when_allowed=float(
                        rule_payload.get("probability_when_allowed", 1.0)
                    ),
                ),
                exclusive_transmission=exclusive,
                transmitter_status=(
                    str(raw["status"])
                    if raw.get("status") is not None
                    else None
                ),
            )
        )
    return tuple(result)


def _evidence(rows: Sequence[NormalizedObservation]) -> tuple[ReceptionEvidence, ...]:
    result: list[ReceptionEvidence] = []
    for row in rows:
        if row.signal_present is None and row.decode_success_given_signal is None:
            continue
        signal = (
            bool(row.signal_present) if row.signal_present is not None else None
        )
        result.append(
            ReceptionEvidence(
                norad_id=row.norad_id,
                station_id=str(row.station_id),
                resource_id=None,
                listened=True,
                transmitter_state="confirmed" if signal else "unknown",
                decoded=(
                    bool(row.decode_success_given_signal)
                    if row.decode_success_given_signal is not None
                    else None
                ),
                source="satnogs",
                signal_present=signal,
                transmitter_uuid=row.transmitter_uuid,
            )
        )
    return tuple(result)


def _blockers(
    path: Path | None, stations: Sequence[GroundStation]
) -> tuple[Blocker, ...]:
    if path is None:
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        if payload.get("schema_version") != "observation-planning-blockers-v1":
            raise CampaignRunnerError("unsupported blocker schema")
        raw_items = payload.get("blockers")
    else:
        raw_items = payload
    if not isinstance(raw_items, list):
        raise CampaignRunnerError("blocker file must be an array or contain blockers")
    station_by_network_id = {
        station.satnogs_station_id: station.station_id for station in stations
    }
    result: list[Blocker] = []
    blocker_ids: set[str] = set()
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise CampaignRunnerError("blocker entry must be an object")
        raw_station = item.get("station_id")
        if isinstance(raw_station, int) and not isinstance(raw_station, bool):
            station_id = station_by_network_id.get(raw_station)
            if station_id is None:
                raise CampaignRunnerError(
                    f"blocker references station {raw_station} outside the campaign"
                )
        else:
            station_id = str(raw_station or "")
            if station_id not in {station.station_id for station in stations}:
                raise CampaignRunnerError("blocker station_id is outside the campaign")
        raw_resource = item.get("resource_id")
        resource_id = str(raw_resource) if raw_resource is not None else None
        station = next(item for item in stations if item.station_id == station_id)
        if resource_id is not None and resource_id not in {
            resource.resource_id for resource in station.resources
        }:
            raise CampaignRunnerError(
                f"blocker resource {resource_id!r} is absent from station {station_id}"
            )
        blocker_id = str(item.get("blocker_id") or "")
        if blocker_id in blocker_ids:
            raise CampaignRunnerError(f"duplicate blocker_id {blocker_id!r}")
        blocker_ids.add(blocker_id)
        result.append(
            Blocker(
                blocker_id=blocker_id,
                station_id=station_id,
                resource_id=resource_id,
                start=parse_api_datetime(item.get("start"), name="blocker.start"),
                end=parse_api_datetime(item.get("end"), name="blocker.end"),
                reason=str(item.get("reason") or "unavailable"),
                kind=str(item.get("kind") or "maintenance"),
            )
        )
    return tuple(result)


def run_shadow_once(
    *,
    campaign_config_path: Path,
    publication_config_path: Path,
    transmitter_inventory_path: Path,
    covariate_archive_path: Path,
    history_dataset_path: Path,
    ledger_path: Path,
    plan_path: Path,
    state_database_path: Path,
    cache_dir: Path,
    user_agent: str,
    blocker_path: Path | None = None,
    target_overrides_path: Path | None = None,
    probability_model_path: Path | None = None,
    source_manifest_path: Path | None = None,
    additional_input_artifacts: Sequence[Path] = (),
    now: datetime | None = None,
    commit_now: datetime | None = None,
) -> Mapping[str, object]:
    """Refresh all live inputs, solve the month and commit without submission."""

    current = as_utc(now or datetime.now(UTC), name="now")
    campaign = ProspectiveCampaignConfig.load(campaign_config_path)
    if (
        campaign.runtime_user_agent is not None
        and user_agent != campaign.runtime_user_agent
    ):
        append_event(
            ledger_path,
            campaign_id=campaign.campaign_id,
            event_type="plan_failed",
            occurred_at=current,
            payload={
                "error_type": "CampaignRunnerError",
                "error": (
                    "runtime User-Agent differs from the registered campaign identity"
                ),
                "retryable": False,
            },
        )
        raise CampaignRunnerError(
            "runtime User-Agent differs from the registered campaign identity"
        )
    if campaign.start <= current < campaign.end and source_manifest_path is None:
        append_event(
            ledger_path,
            campaign_id=campaign.campaign_id,
            event_type="plan_failed",
            occurred_at=current,
            payload={
                "error_type": "CampaignRunnerError",
                "error": "an in-campaign planning run requires a runtime source manifest",
                "retryable": True,
            },
        )
        raise CampaignRunnerError(
            "an in-campaign planning run requires a runtime source manifest"
        )
    publication = PublicationDatasetConfig.load(publication_config_path)
    targets = _targets(
        campaign,
        publication,
        transmitter_inventory_path,
        target_overrides_path,
    )
    satnogs = SatNOGSClient(
        user_agent=user_agent,
        cache_dir=cache_dir / "satnogs-stations",
        max_concurrency=1,
    )
    try:
        source_identity = (
            _verify_runtime_source_manifest(source_manifest_path, current=current)
            if source_manifest_path is not None
            else None
        )
        station_snapshot = SatnogsStationInventoryProvider(
            satnogs, now=lambda: current
        ).fetch(campaign.station_ids, skip_unavailable=True)
        weather, kp, hardware = read_covariate_archive(covariate_archive_path)
        forecast_hardware_station_ids = {item.station_id for item in hardware}
        stations = tuple(
            station
            for station in station_snapshot.stations
            if station.satnogs_station_id in forecast_hardware_station_ids
        )
        if not stations:
            raise CampaignRunnerError(
                "no currently available station has a bound forecast hardware record"
            )
        stations = _apply_hardware(stations, hardware, as_of=current)
        environment = ArchivedEnvironmentProvider(
            weather, kp, as_of=current, retrospective=False
        )
        estimator = None
        if probability_model_path is not None:
            from .learned_probability import FrozenLogitProbabilityEstimator

            estimator = FrozenLogitProbabilityEstimator.load(
                probability_model_path,
                history_dataset_path=history_dataset_path,
            )
            if estimator.training_data_end > campaign.start:
                raise CampaignRunnerError(
                    "frozen probability model includes data after campaign start"
                )
        builder = OpportunityBuilder(
            Sgp4PassPredictor(step_seconds=60),
            estimator=estimator,
            feature_enricher=EnvironmentFeatureEnricher(environment),
            minimum_opportunity_duration_seconds=(
                campaign.minimum_opportunity_duration_seconds
            ),
            maximum_opportunity_duration_seconds=(
                campaign.maximum_opportunity_duration_seconds
            ),
        )
        store = PlanningStore(state_database_path)
        campaign_plan_id = f"prospective-{campaign.campaign_id}"
        revision = store.next_plan_revision(campaign_plan_id)
        planner = DynamicObservationPlanner(
            CelesTrakTleProvider(user_agent=user_agent),
            builder,
            store=store,
        )
        # A month-scale solve is not instantaneous.  Do not let an assignment
        # begin while its recommendation is still being computed: reserve a
        # conservative lead and then verify the actual commit boundary below.
        planning_horizon_start = max(
            campaign.start, current + PLANNING_COMMIT_LEAD_TIME
        )
        result = planner.plan(
            targets,
            stations,
            planning_horizon_start,
            campaign.end,
            blockers=_blockers(blocker_path, stations),
            evidence=_evidence(read_normalized_jsonl(history_dataset_path)),
            schedule_policy=SchedulePolicy(
                risk_aversion=0.25,
                minimum_observations_by_satellite=(
                    {
                        norad_id: campaign.minimum_observations_per_target
                        for norad_id in campaign.target_norad_ids
                    }
                    if campaign.minimum_observations_per_target > 0
                    else {}
                ),
                time_limit_seconds=120,
                require_proven_optimum=True,
            ),
            plan_id=campaign_plan_id,
            revision=revision,
            trigger="daily_full_rebuild",
            now=current,
        )
        document = plan_document(result.plan)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        committed_at = as_utc(
            commit_now
            or (datetime.now(UTC) if now is None else current),
            name="commit_now",
        )
        event = commit_plan(
            campaign_config_path,
            ledger_path,
            plan_path,
            input_artifacts=(
                publication_config_path,
                transmitter_inventory_path,
                covariate_archive_path,
                history_dataset_path,
                *((blocker_path,) if blocker_path is not None else ()),
                *((target_overrides_path,) if target_overrides_path is not None else ()),
                *((probability_model_path,) if probability_model_path is not None else ()),
                *((source_manifest_path,) if source_manifest_path is not None else ()),
                *additional_input_artifacts,
            ),
            now=lambda: committed_at,
        )
    except Exception as exc:
        failed_at = as_utc(
            commit_now
            or (datetime.now(UTC) if now is None else current),
            name="failure_now",
        )
        append_event(
            ledger_path,
            campaign_id=campaign.campaign_id,
            event_type="plan_failed",
            occurred_at=failed_at,
            payload={
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "retryable": True,
            },
        )
        raise
    return {
        "plan_path": str(plan_path),
        "plan_fingerprint": result.plan.canonical_fingerprint(),
        "plan_id": result.plan.plan_id,
        "revision": result.plan.revision,
        "opportunity_count": len(result.opportunities),
        "assignment_count": len(result.plan.assignments),
        "expected_unique_samples": sum(
            item.expected_unique_samples for item in result.plan.assignments
        ),
        "ledger_event_sha256": event.event_sha256,
        "planning_started_at": current.isoformat().replace("+00:00", "Z"),
        "committed_at": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "commit_latency_seconds": (
            event.occurred_at - result.plan.created_at
        ).total_seconds(),
        "probability_model": (
            estimator.model_version
            if estimator is not None
            else "hierarchical-beta-signal-v2"
        ),
        "source_identity_sha256": source_identity,
        "dry_run_only": True,
    }


def reconcile_shadow_network(
    *,
    campaign_config_path: Path,
    ledger_path: Path,
    raw_snapshot_path: Path,
    outcomes_path: Path,
    cache_dir: Path,
    user_agent: str,
    lookback_hours: int = 48,
    minimum_request_interval_seconds: float = 61.0,
    shared_rate_limit_path: Path | None = None,
    now: datetime | None = None,
) -> Mapping[str, object]:
    """Match ended shadow recommendations to naturally scheduled Network jobs."""

    if lookback_hours <= 0 or minimum_request_interval_seconds < 0:
        raise ValueError("lookback and request interval are invalid")
    current = as_utc(now or datetime.now(UTC), name="now")
    campaign = ProspectiveCampaignConfig.load(campaign_config_path)
    events = read_ledger(ledger_path)
    if not events or events[0].campaign_id != campaign.campaign_id:
        raise CampaignRunnerError("campaign ledger is not initialized for this config")
    already = {
        str(event.payload.get("opportunity_id"))
        for event in events
        if event.event_type == "outcome_recorded"
    }
    already_observation_ids = {
        int(event.payload["observation_id"])
        for event in events
        if event.event_type == "outcome_recorded"
        and isinstance(event.payload.get("observation_id"), int)
        and not isinstance(event.payload.get("observation_id"), bool)
    }
    assignments: dict[str, tuple[datetime, Mapping[str, object]]] = {}
    lower = max(campaign.start, current - timedelta(hours=lookback_hours))
    for event, item in active_committed_assignment_versions(events):
        opportunity_id = str(item.get("opportunity_id", ""))
        try:
            start = datetime.fromisoformat(str(item.get("start")))
            end = datetime.fromisoformat(str(item.get("end")))
        except ValueError:
            continue
        start, end = as_utc(start), as_utc(end)
        if (
            opportunity_id not in already
            and event.occurred_at <= start
            and lower <= end < current
        ):
            existing = assignments.get(opportunity_id)
            if existing is None or event.occurred_at > existing[0]:
                assignments[opportunity_id] = (event.occurred_at, item)
    by_pair: dict[
        tuple[int, int, str],
        list[tuple[str, datetime, Mapping[str, object]]],
    ] = {}
    for opportunity_id, (committed_at, item) in assignments.items():
        raw_station_id = item.get("satnogs_station_id")
        if (
            isinstance(raw_station_id, bool)
            or not isinstance(raw_station_id, int)
            or raw_station_id <= 0
        ):
            raise CampaignRunnerError(
                "committed assignment is missing its numeric SatNOGS station ID"
            )
        station_id = raw_station_id
        norad_id = int(item["norad_id"])
        transmitter_uuid = item.get("transmitter_uuid")
        if not isinstance(transmitter_uuid, str) or not transmitter_uuid.strip():
            raise CampaignRunnerError(
                "committed assignment is missing its transmitter UUID"
            )
        by_pair.setdefault((station_id, norad_id, transmitter_uuid), []).append(
            (opportunity_id, committed_at, item)
        )
    client = SatNOGSClient(
        user_agent=user_agent,
        cache_dir=cache_dir,
        max_concurrency=1,
        max_response_bytes=20_000_000,
        timeout_seconds=90,
        shared_rate_limit_path=shared_rate_limit_path,
        shared_minimum_interval_seconds=(
            minimum_request_interval_seconds
            if shared_rate_limit_path is not None
            else 0.0
        ),
    )
    observations: list[Mapping[str, object]] = []
    query_windows: list[dict[str, object]] = []
    last_request = 0.0

    def throttle() -> None:
        nonlocal last_request
        wait = minimum_request_interval_seconds - (time.monotonic() - last_request)
        if wait > 0:
            time.sleep(wait)
        last_request = time.monotonic()

    def next_link(headers: Mapping[str, str]) -> str | None:
        link = next(
            (value for key, value in headers.items() if key.casefold() == "link"),
            None,
        )
        if not link:
            return None
        for part in link.split(","):
            if 'rel="next"' in part or "rel=next" in part:
                if "<" not in part or ">" not in part:
                    raise CampaignRunnerError("malformed SatNOGS pagination link")
                return part[part.index("<") + 1 : part.index(">")]
        return None

    for station_id, norad_id, transmitter_uuid in sorted(by_pair):
        pair_key = (station_id, norad_id, transmitter_uuid)
        pair_start = min(
            as_utc(datetime.fromisoformat(str(item["start"])))
            for _opportunity_id, _committed_at, item in by_pair[pair_key]
        )
        query_windows.append(
            {
                "station_id": station_id,
                "norad_id": norad_id,
                "transmitter_uuid": transmitter_uuid,
                "start": pair_start.isoformat().replace("+00:00", "Z"),
                "end": current.isoformat().replace("+00:00", "Z"),
            }
        )
        throttle()
        response = client.get(
            "observations/",
            params={
                "ground_station": station_id,
                "norad_cat_id": norad_id,
                "transmitter_uuid": transmitter_uuid,
                "start": pair_start.isoformat().replace("+00:00", "Z"),
                "end": current.isoformat().replace("+00:00", "Z"),
            },
        )
        for page in range(20):
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CampaignRunnerError("SatNOGS reconciliation returned invalid JSON") from exc
            if not isinstance(payload, list) or any(
                not isinstance(item, Mapping) for item in payload
            ):
                raise CampaignRunnerError("SatNOGS reconciliation response must be an array")
            observations.extend(payload)
            following = next_link(response.headers)
            if following is None:
                break
            if page == 19:
                raise CampaignRunnerError("SatNOGS reconciliation exceeded 20 pages")
            throttle()
            response = client.get(following)
    raw_payload = {
        "schema_version": "prospective-satnogs-reconciliation-snapshot-v1",
        "retrieved_at": current.isoformat().replace("+00:00", "Z"),
        "source": "https://network.satnogs.org/api/observations/",
        "query_start": lower.isoformat().replace("+00:00", "Z"),
        "query_end": current.isoformat().replace("+00:00", "Z"),
        "lookback_hours": lookback_hours,
        "station_ids": sorted({key[0] for key in by_pair}),
        "norad_ids": sorted({key[1] for key in by_pair}),
        "query_windows": query_windows,
        "observations": observations,
    }
    raw_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    raw_snapshot_path.write_text(
        json.dumps(raw_payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raw_snapshot_sha256 = hashlib.sha256(raw_snapshot_path.read_bytes()).hexdigest()
    used_observations: set[int] = set(already_observation_ids)
    outcomes: list[dict[str, object]] = []
    skipped_unknown_labels = 0
    skipped_contradictory_labels = 0
    skipped_malformed_labels = 0
    ordered_assignments = sorted(
        assignments.items(),
        key=lambda entry: (
            -entry[1][0].timestamp(),
            datetime.fromisoformat(str(entry[1][1]["start"])),
            entry[0],
        ),
    )
    for opportunity_id, (_committed_at, assignment) in ordered_assignments:
        predicted_start = datetime.fromisoformat(str(assignment["start"]))
        predicted_end = datetime.fromisoformat(str(assignment["end"]))
        norad_id = int(assignment["norad_id"])
        raw_station_id = assignment.get("satnogs_station_id")
        if (
            isinstance(raw_station_id, bool)
            or not isinstance(raw_station_id, int)
            or raw_station_id <= 0
        ):
            raise CampaignRunnerError(
                "committed assignment is missing its numeric SatNOGS station ID"
            )
        station_id = raw_station_id
        expected_transmitter = assignment.get("transmitter_uuid")
        if not isinstance(expected_transmitter, str) or not expected_transmitter.strip():
            raise CampaignRunnerError(
                "committed assignment is missing its transmitter UUID"
            )
        packet_capable = satnogs_mode_is_packet_capable(
            assignment.get("modulation")
        )
        matches: list[tuple[float, Mapping[str, object]]] = []
        for observation in observations:
            observation_id = observation.get("id")
            if (
                isinstance(observation_id, bool)
                or not isinstance(observation_id, int)
                or observation_id in used_observations
            ):
                continue
            try:
                actual_start = datetime.fromisoformat(
                    str(observation.get("start")).replace("Z", "+00:00")
                )
                actual_end = datetime.fromisoformat(
                    str(observation.get("end")).replace("Z", "+00:00")
                )
                actual_norad = int(observation.get("norad_cat_id", 0))
                actual_station = int(observation.get("ground_station", 0))
                actual_transmitter = observation.get("transmitter_uuid") or observation.get(
                    "transmitter"
                )
            except (TypeError, ValueError):
                continue
            if (
                actual_norad == norad_id
                and actual_station == station_id
                and actual_transmitter == expected_transmitter
                and actual_start < predicted_end
                and predicted_start < actual_end
            ):
                matches.append(
                    (abs((actual_start - predicted_start).total_seconds()), observation)
                )
        if not matches:
            continue
        observation = min(matches, key=lambda pair: pair[0])[1]
        observation_id = int(observation["id"])
        try:
            signal, decoded = satnogs_outcome_labels(
                observation.get("waterfall_status"),
                observation.get("demoddata"),
                packet_capable=packet_capable,
            )
        except DatasetIntegrityError as exc:
            if str(exc) == "contradictory_signal_and_demoddata":
                skipped_contradictory_labels += 1
            else:
                skipped_malformed_labels += 1
            continue
        if signal is None and decoded is None:
            skipped_unknown_labels += 1
            continue
        used_observations.add(observation_id)
        outcomes.append(
            {
                "opportunity_id": opportunity_id,
                "observation_id": observation_id,
                "observed_at": str(observation.get("end")),
                "signal_present": signal,
                "decode_success_given_signal": decoded,
                "source": "SatNOGS Network natural shadow-match",
                "raw_source_artifact_path": str(raw_snapshot_path),
                "raw_source_artifact_sha256": raw_snapshot_sha256,
            }
        )
    outcomes_path.parent.mkdir(parents=True, exist_ok=True)
    outcomes_path.write_text(
        json.dumps(outcomes, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    outcomes_sha256 = hashlib.sha256(outcomes_path.read_bytes()).hexdigest()
    recorded = record_outcomes(
        campaign_config_path,
        ledger_path,
        outcomes_path,
        now=lambda: current,
    )
    append_event(
        ledger_path,
        campaign_id=campaign.campaign_id,
        event_type="campaign_note",
        occurred_at=current,
        payload={
            "kind": "shadow_reconciliation",
            "pending_assignment_count": len(assignments),
            "query_pair_count": len(by_pair),
            "network_observation_count": len(observations),
            "matched_outcome_count": len(recorded),
            "previously_recorded_observation_count": len(already_observation_ids),
            "skipped_unknown_label_count": skipped_unknown_labels,
            "skipped_contradictory_label_count": skipped_contradictory_labels,
            "skipped_malformed_label_count": skipped_malformed_labels,
            "lookback_hours": lookback_hours,
            "raw_snapshot_path": str(raw_snapshot_path),
            "raw_snapshot_sha256": raw_snapshot_sha256,
            "outcomes_path": str(outcomes_path),
            "outcomes_sha256": outcomes_sha256,
            "selection_boundary": (
                "natural Network jobs overlapping a committed recommendation; "
                "not randomized execution"
            ),
        },
    )
    return {
        "pending_assignments": len(assignments),
        "query_pairs": len(by_pair),
        "network_observations": len(observations),
        "matched_outcomes": len(recorded),
        "skipped_unknown_labels": skipped_unknown_labels,
        "skipped_contradictory_labels": skipped_contradictory_labels,
        "skipped_malformed_labels": skipped_malformed_labels,
        "raw_snapshot_path": str(raw_snapshot_path),
        "raw_snapshot_sha256": raw_snapshot_sha256,
        "outcomes_path": str(outcomes_path),
        "outcomes_sha256": outcomes_sha256,
    }
