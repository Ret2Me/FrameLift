"""Fail-closed technical publication gate for the planning study."""

from __future__ import annotations

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from datetime import datetime, timedelta
from typing import Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from .api_contact import ApiContactError, load_api_contact, validate_api_contact
from .covariates import (
    CovariateError,
    HardwareRecord,
    KpRecord,
    WeatherRecord,
    read_covariate_archive,
    validate_covariate_evidence_manifest,
)
from .dataset import (
    parse_api_datetime,
    satnogs_mode_is_packet_capable,
    satnogs_outcome_labels,
)
from .prospective import (
    ProspectiveCampaignConfig,
    prospective_report,
    read_ledger,
)
from .test_attestation import (
    PlanningTestAttestationError,
    validate_test_attestation,
)
from .tle import TleError, parse_tle_text
from .v4_protocol import (
    MIN_CAPTURED_LOCATION_FRACTION,
    MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION,
    MIN_CAPTURED_RECEIVER_DRIVER_FRACTION,
    MIN_CAPTURED_RECEIVER_GAIN_FRACTION,
    MIN_VERIFIED_SPACE_WEATHER_FRACTION,
    MIN_VERIFIED_WEATHER_FRACTION,
    V4_EXTERNAL_SATELLITE_COUNT,
    V4_EXTERNAL_STATION_HOLDOUT_FRACTION,
    V4_MIN_CONDITIONAL_DECODE_SATELLITES,
    V4_MIN_EXTERNAL_GROUPS,
    V4_MIN_EXTERNAL_TEST_ROWS,
    V4_MIN_SIGNAL_SATELLITES,
    V4_PREDECLARED_TEMPORAL_CUTOFF,
    V4_STUDY_ID,
    V4_TARGET_COUNT,
    validate_v4_method_contract,
    validate_v4_publication_config_contract,
)


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str
    human_gate: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _load(path: Path) -> Mapping[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _covariate_contract_integrity(payload: Mapping[str, object]) -> bool:
    contracts = payload.get("contracts")
    if not isinstance(contracts, Mapping):
        return False
    expected = payload.get("contract_identity_sha256")
    actual = hashlib.sha256(
        json.dumps(
            contracts,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    weather = contracts.get("weather")
    space_weather = contracts.get("space_weather")
    hardware = contracts.get("hardware")
    return (
        expected == actual
        and isinstance(weather, Mapping)
        and weather.get("units")
        == {
            "air_temperature_c": "degC",
            "relative_humidity_percent": "percent",
            "surface_pressure_kpa": "kPa",
            "wind_speed_m_s": "m/s",
            "precipitation_corrected": "mm/hour",
        }
        and isinstance(space_weather, Mapping)
        and space_weather.get("units")
        == {"space_weather_kp": "dimensionless_0_to_9"}
        and isinstance(hardware, Mapping)
        and hardware.get("units")
        == {
            "frequency_ranges_hz": "Hz",
            "receive_antenna_gain_dbi": "dBi",
            "system_noise_temperature_k": "K",
        }
    )


def _same_optional_number(actual: object, expected: float | None) -> bool:
    if expected is None:
        return actual is None
    return (
        not isinstance(actual, bool)
        and isinstance(actual, (int, float))
        and math.isfinite(float(actual))
        and math.isclose(float(actual), expected, rel_tol=1e-12, abs_tol=1e-12)
    )


def _contract_accounts_for_feature_group(
    contract: Mapping[str, object], feature_names: set[str]
) -> bool:
    """Require an explicit, outcome-independent deployment disposition.

    A feature may be active after passing the predeclared model gate, or it may
    remain evaluation-only after failing that gate. Requiring one particular
    side would make readiness depend on the experimental result and can make
    the gate impossible to satisfy. Evaluation-only classifications therefore
    require a reason, and the two feature sets must be disjoint.
    """

    raw_active = contract.get("active_probability_features")
    raw_evaluation_only = contract.get("evaluation_only_features")
    if not isinstance(raw_active, list) or not isinstance(raw_evaluation_only, list):
        return False
    if any(not isinstance(name, str) or not name for name in raw_active):
        return False
    if any(not isinstance(name, str) or not name for name in raw_evaluation_only):
        return False
    active = set(raw_active)
    evaluation_only = set(raw_evaluation_only)
    if active & evaluation_only:
        return False
    if active & feature_names:
        return True
    reason = contract.get("evaluation_only_reason")
    return bool(evaluation_only & feature_names) and (
        isinstance(reason, str) and bool(reason.strip())
    )


_DYNAMIC_FEATURE_CONTRACT_FIELDS = {
    "active_feasibility_constraints",
    "receiver_antenna",
}
_ALLOWED_FEASIBILITY_CONSTRAINTS = {
    "receiver_antenna_frequency_range",
    "receiver_supported_modulation",
}


def _contract_matches_frozen_probability_model(
    contract: Mapping[str, object],
    expected: Mapping[str, object],
    probability: Mapping[str, object],
) -> bool:
    """Bind a serialized opportunity contract to one frozen model artifact.

    Receiver feasibility is opportunity-specific. Every other field is an
    immutable statement derived from the deployment model and must match it
    byte-for-byte at the JSON value level.
    """

    expected_keys = set(expected)
    if set(contract) != expected_keys | {"receiver_antenna"}:
        return False
    static_keys = expected_keys - {"active_feasibility_constraints"}
    if any(contract.get(name) != expected.get(name) for name in static_keys):
        return False
    if contract.get("model_version") != probability.get("model_version"):
        return False

    constraints = contract.get("active_feasibility_constraints")
    if (
        not isinstance(constraints, list)
        or any(not isinstance(name, str) for name in constraints)
        or constraints != sorted(set(constraints))
        or not set(constraints) <= _ALLOWED_FEASIBILITY_CONSTRAINTS
    ):
        return False

    antenna = contract.get("receiver_antenna")
    if not isinstance(antenna, Mapping) or set(antenna) != {
        "antenna_id",
        "frequency_ranges_hz",
        "gain_supplied",
        "system_noise_temperature_supplied",
    }:
        return False
    if antenna.get("antenna_id") is not None and not isinstance(
        antenna.get("antenna_id"), str
    ):
        return False
    ranges = antenna.get("frequency_ranges_hz")
    if not isinstance(ranges, list):
        return False
    for bounds in ranges:
        if (
            not isinstance(bounds, list)
            or len(bounds) != 2
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in bounds
            )
            or float(bounds[0]) > float(bounds[1])
        ):
            return False
    return isinstance(antenna.get("gain_supplied"), bool) and isinstance(
        antenna.get("system_noise_temperature_supplied"), bool
    )


def _audit_assignment_covariates(
    assignments: object,
    *,
    weather: Sequence[WeatherRecord],
    kp: Sequence[KpRecord],
    hardware: Sequence[HardwareRecord],
    as_of: object,
) -> tuple[bool, Mapping[str, int]]:
    """Reconstruct every selected assignment's archived covariate join.

    This intentionally does not call the production environment provider.  It
    repeats the documented temporal joins from immutable archive records so a
    publication check can catch provider, serialization, or wiring defects.
    """

    counts = {
        "assignment_count": 0,
        "weather_match_count": 0,
        "weather_value_count": 0,
        "kp_match_count": 0,
        "hardware_match_count": 0,
        "antenna_gain_value_count": 0,
        "antenna_noise_value_count": 0,
    }
    if not isinstance(assignments, list) or not assignments:
        return False, counts
    try:
        audited_at = parse_api_datetime(as_of, name="covariate_audit.as_of")
    except (TypeError, ValueError):
        return False, counts

    weather_fields = (
        "air_temperature_c",
        "relative_humidity_percent",
        "surface_pressure_kpa",
        "wind_speed_m_s",
        "precipitation_corrected",
    )
    valid = True
    for assignment in assignments:
        counts["assignment_count"] += 1
        try:
            if not isinstance(assignment, Mapping):
                raise ValueError("assignment is not an object")
            metadata = assignment.get("metadata")
            if not isinstance(metadata, Mapping):
                raise ValueError("assignment metadata is absent")
            features = metadata.get("feature_snapshot")
            contract = metadata.get("feature_use_contract")
            if not isinstance(features, Mapping) or not isinstance(contract, Mapping):
                raise ValueError("assignment covariate evidence is absent")

            culmination = parse_api_datetime(
                metadata.get("culmination_at"), name="assignment.culmination_at"
            )
            start = parse_api_datetime(assignment.get("start"), name="assignment.start")
            end = parse_api_datetime(assignment.get("end"), name="assignment.end")
            if not start <= culmination <= end:
                raise ValueError("culmination lies outside the selected assignment")
            hour = culmination.replace(minute=0, second=0, microsecond=0)

            station_id = assignment.get("satnogs_station_id")
            if (
                isinstance(station_id, bool)
                or not isinstance(station_id, int)
                or station_id <= 0
                or features.get("satnogs_station_id") != station_id
            ):
                raise ValueError("assignment station identity is inconsistent")
            frequency_hz = assignment.get("frequency_hz")
            if (
                isinstance(frequency_hz, bool)
                or not isinstance(frequency_hz, (int, float))
                or not math.isfinite(float(frequency_hz))
                or float(frequency_hz) <= 0
                or not _same_optional_number(
                    features.get("frequency_hz"), float(frequency_hz)
                )
            ):
                raise ValueError("assignment frequency is invalid or inconsistent")

            weather_matches = [
                item
                for item in weather
                if item.station_id == station_id
                and item.valid_at == hour
                and item.retrieved_at <= audited_at
            ]
            if len(weather_matches) > 1:
                raise ValueError("weather join is ambiguous")
            weather_item = weather_matches[0] if weather_matches else None
            if weather_item is not None:
                counts["weather_match_count"] += 1
                if any(getattr(weather_item, field) is not None for field in weather_fields):
                    counts["weather_value_count"] += 1
            for field in weather_fields:
                expected = getattr(weather_item, field) if weather_item else None
                if not _same_optional_number(features.get(field), expected):
                    raise ValueError(f"weather feature mismatch: {field}")

            kp_matches = [
                item
                for item in kp
                if item.valid_at <= culmination < item.valid_at + timedelta(hours=3)
                and item.retrieved_at <= audited_at
            ]
            if len(kp_matches) > 1:
                raise ValueError("Kp join is ambiguous")
            kp_item = kp_matches[0] if kp_matches else None
            if kp_item is not None:
                counts["kp_match_count"] += 1
            if not _same_optional_number(
                features.get("space_weather_kp"), kp_item.kp if kp_item else None
            ):
                raise ValueError("Kp feature mismatch")

            expected_sources = [
                item.source for item in (weather_item, kp_item) if item is not None
            ]
            expected_environment_source = (
                " + ".join(expected_sources)
                if expected_sources
                else "missing:frozen-covariate-archive"
            )
            if features.get("environment_source") != expected_environment_source:
                raise ValueError("environment source lineage mismatch")

            candidates = sorted(
                (
                    item
                    for item in hardware
                    if item.station_id == station_id
                    and item.effective_from <= audited_at
                    and item.known_at <= audited_at
                ),
                key=lambda item: (item.effective_from, item.known_at),
                reverse=True,
            )
            api_candidates = [
                item for item in candidates if "SatNOGS Network" in item.source
            ]
            if not api_candidates:
                raise ValueError("current SatNOGS antenna evidence is absent")
            current_api_hardware = api_candidates[0]
            receiver = contract.get("receiver_antenna")
            if not isinstance(receiver, Mapping):
                raise ValueError("receiver antenna contract is absent")
            raw_ranges = receiver.get("frequency_ranges_hz")
            if not isinstance(raw_ranges, list) or not raw_ranges:
                raise ValueError("receiver antenna ranges are absent")
            receiver_ranges: list[tuple[float, float]] = []
            for raw_range in raw_ranges:
                if not isinstance(raw_range, list) or len(raw_range) != 2:
                    raise ValueError("receiver antenna range is malformed")
                low, high = raw_range
                if (
                    isinstance(low, bool)
                    or isinstance(high, bool)
                    or not isinstance(low, (int, float))
                    or not isinstance(high, (int, float))
                    or not math.isfinite(float(low))
                    or not math.isfinite(float(high))
                    or float(low) <= 0
                    or float(high) < float(low)
                ):
                    raise ValueError("receiver antenna range is invalid")
                receiver_ranges.append((float(low), float(high)))
            if not all(
                item in current_api_hardware.frequency_ranges_hz
                for item in receiver_ranges
            ):
                raise ValueError("planned antenna range is absent from SatNOGS evidence")
            if not any(
                low <= float(frequency_hz) <= high
                for low, high in receiver_ranges
            ):
                raise ValueError("planned frequency is outside the receiver range")
            antenna_id = receiver.get("antenna_id")
            documented_antenna_ids = set(
                str(current_api_hardware.antenna_type or "").split(" + ")
            )
            if (
                not isinstance(antenna_id, str)
                or not antenna_id
                or antenna_id not in documented_antenna_ids
                or features.get("antenna_type") != antenna_id
                or features.get("antenna_frequency_supported") is not True
            ):
                raise ValueError("planned antenna identity/support is inconsistent")

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
            if (
                not _same_optional_number(
                    features.get("receive_antenna_gain_dbi"), gain
                )
                or not _same_optional_number(
                    features.get("system_noise_temperature_k"), noise
                )
                or receiver.get("gain_supplied") is not (gain is not None)
                or receiver.get("system_noise_temperature_supplied")
                is not (noise is not None)
            ):
                raise ValueError("antenna gain/noise evidence is inconsistent")
            if gain is not None:
                counts["antenna_gain_value_count"] += 1
            if noise is not None:
                counts["antenna_noise_value_count"] += 1
            counts["hardware_match_count"] += 1
        except (AttributeError, KeyError, TypeError, ValueError):
            valid = False
    return valid, counts


def _audit_assignment_blockers(
    assignments: object,
    blocker_payload: object,
    *,
    campaign_station_ids: Sequence[int],
) -> tuple[bool, int]:
    """Independently verify blocker syntax and absence of blocked assignments."""

    if (
        not isinstance(assignments, list)
        or not isinstance(blocker_payload, Mapping)
        or blocker_payload.get("schema_version")
        != "observation-planning-blockers-v1"
    ):
        return False, 0
    raw_blockers = blocker_payload.get("blockers")
    if not isinstance(raw_blockers, list):
        return False, 0
    allowed_stations = set(campaign_station_ids)
    blocker_ids: set[str] = set()
    audited = 0
    try:
        normalized_assignments: list[tuple[int, str, object, object]] = []
        for assignment in assignments:
            if not isinstance(assignment, Mapping):
                raise ValueError("assignment is not an object")
            station_id = assignment.get("satnogs_station_id")
            resource_id = assignment.get("resource_id")
            if (
                isinstance(station_id, bool)
                or not isinstance(station_id, int)
                or station_id not in allowed_stations
                or not isinstance(resource_id, str)
                or not resource_id
            ):
                raise ValueError("assignment resource identity is invalid")
            normalized_assignments.append(
                (
                    station_id,
                    resource_id,
                    parse_api_datetime(
                        assignment.get("start"), name="assignment.start"
                    ),
                    parse_api_datetime(
                        assignment.get("end"), name="assignment.end"
                    ),
                )
            )
        for raw in raw_blockers:
            if not isinstance(raw, Mapping):
                raise ValueError("blocker is not an object")
            blocker_id = raw.get("blocker_id")
            if (
                not isinstance(blocker_id, str)
                or not blocker_id.strip()
                or blocker_id in blocker_ids
            ):
                raise ValueError("blocker identity is invalid or duplicated")
            blocker_ids.add(blocker_id)
            raw_station_id = raw.get("station_id")
            if isinstance(raw_station_id, int) and not isinstance(
                raw_station_id, bool
            ):
                station_id = raw_station_id
            elif isinstance(raw_station_id, str):
                candidate = raw_station_id.strip()
                if candidate.startswith("satnogs-"):
                    candidate = candidate.removeprefix("satnogs-")
                station_id = int(candidate)
            else:
                raise ValueError("blocker station identity is invalid")
            if station_id not in allowed_stations:
                raise ValueError("blocker station is outside the campaign")
            raw_resource_id = raw.get("resource_id")
            resource_id = (
                None if raw_resource_id is None else str(raw_resource_id).strip()
            )
            if resource_id == "":
                raise ValueError("blocker resource identity is empty")
            start = parse_api_datetime(raw.get("start"), name="blocker.start")
            end = parse_api_datetime(raw.get("end"), name="blocker.end")
            if end <= start:
                raise ValueError("blocker interval is not positive")
            if any(
                assignment_station_id == station_id
                and (
                    resource_id is None
                    or assignment_resource_id == resource_id
                )
                and assignment_start < end
                and start < assignment_end
                for (
                    assignment_station_id,
                    assignment_resource_id,
                    assignment_start,
                    assignment_end,
                ) in normalized_assignments
            ):
                raise ValueError("selected assignment overlaps a blocker")
            audited += 1
    except (TypeError, ValueError):
        return False, audited
    return True, audited


def _audit_assignment_target_overrides(
    assignments: object,
    override_payload: object,
    *,
    campaign_target_ids: Sequence[int],
) -> tuple[bool, int]:
    """Validate frozen target rules and independently check serialized effects."""

    if (
        not isinstance(assignments, list)
        or not isinstance(override_payload, Mapping)
        or override_payload.get("schema_version")
        != "observation-planning-target-overrides-v1"
    ):
        return False, 0
    raw_targets = override_payload.get("targets")
    if not isinstance(raw_targets, list):
        return False, 0
    allowed_targets = set(campaign_target_ids)
    overrides: dict[int, Mapping[str, object]] = {}
    parsed_rules: dict[
        int, tuple[set[int], set[int], tuple[tuple[object, object], ...]]
    ] = {}
    try:
        for raw in raw_targets:
            if not isinstance(raw, Mapping):
                raise ValueError("target override is not an object")
            norad_id = raw.get("norad_id")
            if (
                isinstance(norad_id, bool)
                or not isinstance(norad_id, int)
                or norad_id not in allowed_targets
                or norad_id in overrides
            ):
                raise ValueError("target override identity is invalid or duplicated")
            for name in ("transmit_power_dbw", "transmit_antenna_gain_dbi"):
                value = raw.get(name)
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise ValueError(f"invalid target override {name}")
            samples_per_second = raw.get("samples_per_second", 1.0)
            priority = raw.get("priority", 1.0)
            if (
                isinstance(samples_per_second, bool)
                or not isinstance(samples_per_second, (int, float))
                or not math.isfinite(float(samples_per_second))
                or float(samples_per_second) < 0
                or isinstance(priority, bool)
                or not isinstance(priority, (int, float))
                or not math.isfinite(float(priority))
                or float(priority) <= 0
                or not isinstance(raw.get("exclusive_transmission", True), bool)
            ):
                raise ValueError("invalid target rate, priority or exclusivity")
            rule = raw.get("transmission_rule", {})
            if not isinstance(rule, Mapping):
                raise ValueError("transmission_rule is not an object")
            raw_weekdays = rule.get("weekdays_utc", [])
            raw_month_days = rule.get("month_days_utc", [])
            raw_windows = rule.get("daily_windows_utc", [])
            raw_regions = rule.get("transmit_regions", [])
            if not all(
                isinstance(value, list)
                for value in (
                    raw_weekdays,
                    raw_month_days,
                    raw_windows,
                    raw_regions,
                )
            ):
                raise ValueError("transmission rule selectors are not arrays")
            weekdays = set(raw_weekdays)
            month_days = set(raw_month_days)
            if (
                len(weekdays) != len(raw_weekdays)
                or any(
                    isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6
                    for day in raw_weekdays
                )
                or len(month_days) != len(raw_month_days)
                or any(
                    isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31
                    for day in raw_month_days
                )
            ):
                raise ValueError("invalid or duplicated calendar selector")
            windows: list[tuple[object, object]] = []
            for window in raw_windows:
                if not isinstance(window, list) or len(window) != 2:
                    raise ValueError("daily window is malformed")
                windows.append(
                    (
                        datetime.strptime(str(window[0]), "%H:%M:%S").time(),
                        datetime.strptime(str(window[1]), "%H:%M:%S").time(),
                    )
                )
            for region in raw_regions:
                if not isinstance(region, Mapping):
                    raise ValueError("transmit region is not an object")
                south = float(region["south_deg"])
                north = float(region["north_deg"])
                west = float(region["west_deg"])
                east = float(region["east_deg"])
                label = region.get("label", "transmit-region")
                if (
                    not all(math.isfinite(value) for value in (south, north, west, east))
                    or not -90 <= south <= north <= 90
                    or not -180 <= west <= 180
                    or not -180 <= east <= 180
                    or not isinstance(label, str)
                    or not label.strip()
                ):
                    raise ValueError("transmit region is invalid")
            probability_when_allowed = rule.get("probability_when_allowed", 1.0)
            if (
                isinstance(probability_when_allowed, bool)
                or not isinstance(probability_when_allowed, (int, float))
                or not math.isfinite(float(probability_when_allowed))
                or not 0 <= float(probability_when_allowed) <= 1
            ):
                raise ValueError("invalid allowed-window probability")
            overrides[norad_id] = raw
            parsed_rules[norad_id] = (
                weekdays,
                month_days,
                tuple(windows),
            )

        for assignment in assignments:
            if not isinstance(assignment, Mapping):
                raise ValueError("assignment is not an object")
            norad_id = assignment.get("norad_id")
            if norad_id not in overrides:
                continue
            raw = overrides[norad_id]  # type: ignore[index]
            start = parse_api_datetime(assignment.get("start"), name="assignment.start")
            end = parse_api_datetime(assignment.get("end"), name="assignment.end")
            if end <= start:
                raise ValueError("assignment interval is not positive")
            weekdays, month_days, windows = parsed_rules[norad_id]  # type: ignore[index]
            for instant in (start, end - timedelta(microseconds=1)):
                wall = instant.time().replace(tzinfo=None)
                if weekdays and instant.weekday() not in weekdays:
                    raise ValueError("assignment violates weekday selector")
                if month_days and instant.day not in month_days:
                    raise ValueError("assignment violates month-day selector")
                if windows and not any(
                    (window_start <= wall < window_end)
                    if window_start <= window_end
                    else (wall >= window_start or wall < window_end)
                    for window_start, window_end in windows
                ):
                    raise ValueError("assignment violates daily window selector")
            expected_priority = float(raw.get("priority", 1.0))
            expected_exclusive = raw.get("exclusive_transmission", True)
            if (
                not _same_optional_number(assignment.get("priority"), expected_priority)
                or assignment.get("exclusive_transmission") is not expected_exclusive
            ):
                raise ValueError("assignment priority or exclusivity mismatch")
            duration = (end - start).total_seconds()
            expected_nominal = duration * float(raw.get("samples_per_second", 1.0))
            if not _same_optional_number(
                assignment.get("nominal_unique_samples"), expected_nominal
            ):
                raise ValueError("assignment sample yield mismatch")
            metadata = assignment.get("metadata")
            features = (
                metadata.get("feature_snapshot")
                if isinstance(metadata, Mapping)
                else None
            )
            if not isinstance(features, Mapping):
                raise ValueError("assignment feature snapshot is absent")
            for name in ("transmit_power_dbw", "transmit_antenna_gain_dbi"):
                expected = raw.get(name)
                if name in raw and not _same_optional_number(
                    features.get(name), float(expected) if expected is not None else None
                ):
                    raise ValueError(f"assignment target override mismatch: {name}")
    except (KeyError, TypeError, ValueError):
        return False, len(overrides)
    return True, len(overrides)


def _audit_plan_tle_evidence(
    plan_payload: object,
    *,
    expected_norad_ids: Sequence[int],
    committed_at: object,
) -> bool:
    """Reparse every raw TLE bound into a plan and reproduce its fingerprint."""

    if not isinstance(plan_payload, Mapping):
        return False
    diagnostics = plan_payload.get("diagnostics")
    fingerprints = plan_payload.get("tle_fingerprints")
    if not isinstance(diagnostics, Mapping) or not isinstance(fingerprints, Mapping):
        return False
    evidence = diagnostics.get("tle_snapshot_evidence")
    expected_keys = {str(item) for item in expected_norad_ids}
    if (
        not isinstance(evidence, Mapping)
        or set(evidence) != expected_keys
        or set(fingerprints) != expected_keys
    ):
        return False
    try:
        plan_created_at = parse_api_datetime(
            plan_payload.get("created_at"), name="plan.created_at"
        )
        event_time = parse_api_datetime(committed_at, name="plan.committed_at")
        if plan_created_at > event_time:
            return False
        for raw_norad_id in sorted(expected_keys, key=int):
            raw_snapshot = evidence[raw_norad_id]
            if not isinstance(raw_snapshot, Mapping):
                return False
            norad_id = int(raw_norad_id)
            fetched_at = parse_api_datetime(
                raw_snapshot.get("fetched_at"), name="tle.fetched_at"
            )
            epoch = parse_api_datetime(raw_snapshot.get("epoch"), name="tle.epoch")
            source_url = str(raw_snapshot.get("source", ""))
            if (
                fetched_at != plan_created_at
                or not source_url.startswith(
                    "https://celestrak.org/NORAD/elements/gp.php?"
                )
                or f"CATNR={norad_id}" not in source_url
            ):
                return False
            snapshot = parse_tle_text(
                "\n".join(
                    (
                        str(raw_snapshot.get("name", "")),
                        str(raw_snapshot.get("line1", "")),
                        str(raw_snapshot.get("line2", "")),
                    )
                ),
                norad_id=norad_id,
                fetched_at=fetched_at,
                source=source_url,
            )
            if (
                snapshot.epoch != epoch
                or raw_snapshot.get("fingerprint") != snapshot.fingerprint
                or fingerprints.get(raw_norad_id) != snapshot.fingerprint
            ):
                return False
    except (TleError, TypeError, ValueError):
        return False
    return True


def _resolved_artifact(project_root: Path, raw_path: object) -> Path:
    path = Path(str(raw_path))
    resolved = (project_root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("prospective input artifact leaves the project root") from exc
    return resolved


def _v4_extension_checks(
    *,
    publication_config_path: Path,
    prospective_config_path: Path,
    prospective_ledger_path: Path,
    dataset: Mapping[str, object],
    evaluation: Mapping[str, object],
    source: Mapping[str, object],
    covariate_evidence_path: Path | None = None,
    probability_model_path: Path | None = None,
    history_dataset_path: Path | None = None,
    frozen_probability_contract: Mapping[str, object] | None = None,
) -> tuple[list[ReadinessCheck], Mapping[str, object]]:
    """Gate the expanded, external-domain and real prospective v4 claims."""

    publication = _load(publication_config_path)
    strict_v4_contract = publication.get("study_id") == V4_STUDY_ID
    project_root = Path(str(source.get("project_root", ""))).resolve()
    try:
        method_contract_path = validate_v4_method_contract(
            project_root, required=True
        )
        if method_contract_path is None:
            raise ValueError("v4 method contract is absent")
        if strict_v4_contract:
            validate_v4_publication_config_contract(publication_config_path)
        method_contract_valid = True
        method_contract_detail = (
            f"path={method_contract_path.relative_to(project_root)}; "
            "thresholds_and_provenance_match_frozen_contract=true"
        )
    except (OSError, TypeError, ValueError) as exc:
        method_contract_valid = False
        method_contract_detail = f"{type(exc).__name__}: {exc}"
    targets = publication.get("targets", [])
    external_satellites = publication.get("external_validation_norad_ids", [])
    station_fraction = publication.get("external_station_holdout_fraction", 0.0)
    target_ids = {
        int(item.get("norad_id", 0))
        for item in targets
        if isinstance(item, Mapping)
    } if isinstance(targets, list) else set()
    external_ids = {
        int(value) for value in external_satellites
    } if isinstance(external_satellites, list) else set()
    normalized_satellites = int(dataset.get("normalized_satellites", 0))
    signal_satellites = int(dataset.get("signal_satellites", 0))
    decode_satellites = int(dataset.get("conditional_decode_satellites", 0))
    per_target_counts = dataset.get("per_target_counts", [])
    represented_target_ids = {
        int(item.get("norad_id", 0))
        for item in per_target_counts
        if isinstance(item, Mapping) and int(item.get("normalized_rows", 0)) > 0
    } if isinstance(per_target_counts, list) else set()
    cohort_valid = (
        isinstance(targets, list)
        and len(targets) == V4_TARGET_COUNT
        and len(target_ids) == len(targets)
        and len(external_ids) == V4_EXTERNAL_SATELLITE_COUNT
        and external_ids <= target_ids
        and float(station_fraction) == V4_EXTERNAL_STATION_HOLDOUT_FRACTION
        and _sha256_file(publication_config_path) == dataset.get("config_sha256")
        and dataset.get("study_id") == publication.get("study_id")
        and normalized_satellites == len(target_ids)
        and represented_target_ids == target_ids
        and signal_satellites >= V4_MIN_SIGNAL_SATELLITES
        and decode_satellites >= V4_MIN_CONDITIONAL_DECODE_SATELLITES
    )

    sampling_valid = False
    sampling_detail = "sampling_intervals missing or invalid"
    temporal_cutoff: datetime | None = None
    temporal_cutoff_text = ""
    try:
        raw_sampling = publication.get("sampling_intervals")
        if not isinstance(raw_sampling, list):
            raise ValueError("sampling_intervals is not a list")
        sampling = [
            (
                parse_api_datetime(item["start"], name="sampling_interval.start"),
                parse_api_datetime(item["end"], name="sampling_interval.end"),
            )
            for item in raw_sampling
            if isinstance(item, Mapping)
        ]
        if len(sampling) != len(raw_sampling):
            raise ValueError("sampling interval is not an object")
        cohort_start = parse_api_datetime(publication["start"], name="cohort.start")
        cohort_end = parse_api_datetime(publication["end"], name="cohort.end")
        durations = [(end - start).total_seconds() / 86_400 for start, end in sampling]
        sampled_days = sum(durations)
        temporal_cutoff = sampling[-1][0] if sampling else None
        temporal_cutoff_text = (
            temporal_cutoff.isoformat().replace("+00:00", "Z")
            if temporal_cutoff is not None
            else ""
        )
        evaluation_tasks_for_cutoff = evaluation.get("tasks", {})
        evaluation_tail_valid = (
            isinstance(evaluation_tasks_for_cutoff, Mapping)
            and set(evaluation_tasks_for_cutoff)
            == {"signal_present", "decode_success_given_signal"}
            and evaluation.get("predeclared_temporal_cutoff")
            == temporal_cutoff_text
            and all(
                isinstance(task, Mapping)
                and task.get("status") == "evaluated"
                and isinstance(task.get("temporal"), Mapping)
                and parse_api_datetime(
                    task["temporal"].get("test_start"),
                    name="temporal.test_start",
                )
                >= temporal_cutoff
                and parse_api_datetime(
                    task["temporal"].get("train_end"),
                    name="temporal.train_end",
                )
                < temporal_cutoff
                for task in evaluation_tasks_for_cutoff.values()
            )
        )
        sampling_valid = (
            len(sampling) >= 5
            and all(start < end for start, end in sampling)
            and all(
                cohort_start <= start < end <= cohort_end
                for start, end in sampling
            )
            and all(
                current_start >= previous_end
                for (_, previous_end), (current_start, _) in zip(
                    sampling, sampling[1:]
                )
            )
            and sampled_days >= 80
            and durations[-1] >= 28
            and sampling[-1][1] == cohort_end
            and (
                not strict_v4_contract
                or temporal_cutoff_text == V4_PREDECLARED_TEMPORAL_CUTOFF
            )
            and "outcome-blind" in str(publication.get("sampling_rule", "")).casefold()
            and evaluation_tail_valid
        )
        sampling_detail = (
            f"intervals={len(sampling)}; sampled_days={sampled_days}; "
            f"final_tail_days={durations[-1] if durations else 0}; "
            f"final_tail_ends_at_cohort_end={bool(sampling and sampling[-1][1] == cohort_end)}; "
            f"evaluation_cutoff={temporal_cutoff_text}; "
            f"evaluation_tail_bound={evaluation_tail_valid}"
        )
    except (KeyError, TypeError, ValueError):
        sampling_valid = False

    normalized_rows = int(dataset.get("normalized_rows", 0))
    availability = dataset.get("feature_availability", {})
    raw_source_summary = dataset.get("covariate_source_summary", {})
    source_summary = (
        raw_source_summary if isinstance(raw_source_summary, Mapping) else {}
    )
    weather_rows = (
        int(availability.get("historical_terrestrial_weather_snapshot_present", 0))
        if isinstance(availability, Mapping)
        else 0
    )
    weather_value_rows = (
        int(
            availability.get(
                "historical_terrestrial_weather_any_value_present", 0
            )
        )
        if isinstance(availability, Mapping)
        else 0
    )
    weather_complete_rows = (
        int(
            availability.get(
                "historical_terrestrial_weather_complete_vector_present", 0
            )
        )
        if isinstance(availability, Mapping)
        else 0
    )
    kp_rows = (
        int(availability.get("historical_space_weather_snapshot_present", 0))
        if isinstance(availability, Mapping)
        else 0
    )
    captured_location_rows = (
        int(availability.get("historical_station_location_captured_present", 0))
        if isinstance(availability, Mapping)
        else 0
    )
    current_location_fallback_rows = (
        int(availability.get("current_api_station_location_fallback_present", 0))
        if isinstance(availability, Mapping)
        else 0
    )
    metadata_status_counts = (
        availability.get("client_metadata_parse_status_counts", {})
        if isinstance(availability, Mapping)
        else {}
    )
    receiver_configuration_rows = (
        int(availability.get("historical_receiver_configuration_present", 0))
        if isinstance(availability, Mapping)
        else 0
    )
    receiver_value_counts = (
        availability.get("historical_receiver_configuration_value_counts", {})
        if isinstance(availability, Mapping)
        else {}
    )
    receiver_driver_rows = (
        int(receiver_value_counts.get("captured_receiver_driver", 0))
        if isinstance(receiver_value_counts, Mapping)
        else 0
    )
    receiver_gain_rows = (
        int(receiver_value_counts.get("captured_receiver_rf_gain_db", 0))
        if isinstance(receiver_value_counts, Mapping)
        else 0
    )
    location_receiver_provenance_valid = (
        normalized_rows > 0
        and captured_location_rows + current_location_fallback_rows == normalized_rows
        and isinstance(metadata_status_counts, Mapping)
        and all(
            isinstance(name, str)
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count >= 0
            for name, count in metadata_status_counts.items()
        )
        and sum(int(count) for count in metadata_status_counts.values())
        == normalized_rows
        and captured_location_rows / normalized_rows
        >= MIN_CAPTURED_LOCATION_FRACTION
        and receiver_configuration_rows / normalized_rows
        >= MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION
        and receiver_driver_rows / normalized_rows
        >= MIN_CAPTURED_RECEIVER_DRIVER_FRACTION
        and receiver_gain_rows / normalized_rows
        >= MIN_CAPTURED_RECEIVER_GAIN_FRACTION
    )
    weather_sources = (
        source_summary.get("weather_sources", {})
    )
    kp_sources = (
        source_summary.get("space_weather_sources", {})
    )
    covariate_path_raw = dataset.get("covariate_archive_path")
    model_binding_requested = any(
        value is not None
        for value in (
            probability_model_path,
            history_dataset_path,
            frozen_probability_contract,
        )
    )
    model_binding_configuration_valid = False
    expected_probability_model_path: Path | None = None
    expected_history_dataset_path: Path | None = None
    if model_binding_requested:
        try:
            if (
                probability_model_path is None
                or history_dataset_path is None
                or frozen_probability_contract is None
            ):
                raise ValueError("incomplete frozen model binding")
            expected_probability_model_path = _resolved_artifact(
                project_root, probability_model_path
            )
            expected_history_dataset_path = _resolved_artifact(
                project_root, history_dataset_path
            )
            model_binding_configuration_valid = (
                expected_probability_model_path.is_file()
                and expected_history_dataset_path.is_file()
                and _sha256_file(expected_probability_model_path)
                == frozen_probability_contract.get("model_artifact_file_sha256")
                and _sha256_file(expected_history_dataset_path)
                == frozen_probability_contract.get("training_dataset_sha256")
            )
        except (OSError, TypeError, ValueError):
            model_binding_configuration_valid = False
    covariate_hash_valid = False
    archive_sources_valid = False
    archive_units_valid = False
    historical_raw_evidence_valid = False
    historical_raw_evidence_response_count = 0
    if covariate_path_raw is not None:
        try:
            covariate_path = _resolved_artifact(project_root, covariate_path_raw)
            covariate_hash_valid = (
                covariate_path.is_file()
                and _sha256_file(covariate_path)
                == dataset.get("covariate_archive_sha256")
            )
            if covariate_hash_valid:
                raw_archive = _load(covariate_path)
                archive_units_valid = _covariate_contract_integrity(raw_archive)
                archive_weather, archive_kp, _archive_hardware = read_covariate_archive(
                    covariate_path
                )
                archive_sources_valid = (
                    len(archive_weather)
                    == int(source_summary.get("weather_records", -1))
                    and len(archive_kp)
                    == int(source_summary.get("space_weather_records", -1))
                    and all(
                        "Open-Meteo Historical Forecast" in item.source
                        for item in archive_weather
                    )
                    and all("GFZ" in item.source for item in archive_kp)
                    and bool(archive_weather)
                    and bool(archive_kp)
                )
                if covariate_evidence_path is not None:
                    evidence_summary = validate_covariate_evidence_manifest(
                        covariate_evidence_path, covariate_path
                    )
                    historical_raw_evidence_response_count = int(
                        evidence_summary.get("response_count", 0)
                    )
                    historical_raw_evidence_valid = (
                        historical_raw_evidence_response_count > 0
                    )
        except (CovariateError, OSError, TypeError, ValueError):
            covariate_hash_valid = False
            archive_sources_valid = False
            archive_units_valid = False
            historical_raw_evidence_valid = False
    historical_environment_valid = (
        normalized_rows > 0
        and location_receiver_provenance_valid
        and captured_location_rows > 0
        and weather_rows <= captured_location_rows
        and weather_value_rows <= weather_rows
        and weather_complete_rows <= weather_rows
        and weather_rows / captured_location_rows
        >= MIN_VERIFIED_WEATHER_FRACTION
        and weather_value_rows / captured_location_rows
        >= MIN_VERIFIED_WEATHER_FRACTION
        and weather_complete_rows / captured_location_rows
        >= MIN_VERIFIED_WEATHER_FRACTION
        and kp_rows / normalized_rows >= MIN_VERIFIED_SPACE_WEATHER_FRACTION
        and isinstance(weather_sources, Mapping)
        and any(
            "Open-Meteo Historical Forecast" in str(name)
            for name in weather_sources
        )
        and isinstance(kp_sources, Mapping)
        and any("GFZ" in str(name) for name in kp_sources)
        and covariate_hash_valid
        and archive_sources_valid
        and archive_units_valid
        and historical_raw_evidence_valid
    )

    evaluation_tasks = evaluation.get("tasks", {})
    minimum_external_rows = int(publication.get("minimum_external_test_rows", 1))

    def external_valid(label: str) -> tuple[bool, str]:
        details: list[str] = []
        valid = True
        for task_name in ("signal_present", "decode_success_given_signal"):
            task = (
                evaluation_tasks.get(task_name, {})
                if isinstance(evaluation_tasks, Mapping)
                else {}
            )
            external = (
                task.get("external_validation", {})
                if isinstance(task, Mapping)
                else {}
            )
            split = external.get(label, {}) if isinstance(external, Mapping) else {}
            prediction_rows = (
                int(split.get("prediction_rows", 0))
                if isinstance(split, Mapping)
                else 0
            )
            try:
                split_test_start = parse_api_datetime(
                    split.get("test_start"), name="external.test_start"
                )
                temporal_boundary_valid = (
                    temporal_cutoff is not None
                    and split_test_start >= temporal_cutoff
                )
            except (TypeError, ValueError):
                temporal_boundary_valid = False
            test_rows = (
                int(split.get("test_observation_rows", 0))
                if isinstance(split, Mapping)
                else 0
            )
            raw_held_out = (
                split.get("held_out_group_ids", [])
                if isinstance(split, Mapping)
                else []
            )
            raw_evaluated = (
                split.get("evaluated_group_ids", [])
                if isinstance(split, Mapping)
                else []
            )
            held_out = (
                {int(value) for value in raw_held_out}
                if isinstance(raw_held_out, list)
                else set()
            )
            evaluated = (
                {int(value) for value in raw_evaluated}
                if isinstance(raw_evaluated, list)
                else set()
            )
            minimum_groups = min(5, len(held_out))
            metrics = split.get("metrics", {}) if isinstance(split, Mapping) else {}
            full_metrics = (
                metrics.get("full_logit", {})
                if isinstance(metrics, Mapping)
                else {}
            )
            pooled_metrics = (
                full_metrics.get("pooled", {})
                if isinstance(full_metrics, Mapping)
                else {}
            )
            try:
                metric_count = int(pooled_metrics.get("count", -1))
                metric_positives = int(pooled_metrics.get("positives", -1))
                metric_brier = float(pooled_metrics.get("brier"))
                metric_auroc = float(pooled_metrics.get("auroc"))
                informative_metrics = (
                    metric_count == test_rows
                    and 0 < metric_positives < metric_count
                    and math.isfinite(metric_brier)
                    and 0 <= metric_brier <= 1
                    and math.isfinite(metric_auroc)
                    and 0 <= metric_auroc <= 1
                )
            except (TypeError, ValueError):
                informative_metrics = False
            group_coverage_valid = (
                minimum_groups == V4_MIN_EXTERNAL_GROUPS
                and evaluated <= held_out
                and len(evaluated) >= minimum_groups
                and test_rows >= len(evaluated)
                and split.get("minimum_evaluated_groups") == minimum_groups
                and split.get("evaluated_group_count") == len(evaluated)
                and math.isclose(
                    float(split.get("held_out_group_coverage_fraction", -1)),
                    len(evaluated) / len(held_out),
                    rel_tol=0,
                    abs_tol=1e-12,
                )
            )
            passed = (
                isinstance(split, Mapping)
                and split.get("status") == "evaluated"
                and split.get("group_disjoint") is True
                and split.get("future_only") is True
                and test_rows >= minimum_external_rows
                and (
                    not strict_v4_contract
                    or minimum_external_rows == V4_MIN_EXTERNAL_TEST_ROWS
                )
                and prediction_rows == 4 * test_rows
                and group_coverage_valid
                and temporal_boundary_valid
                and informative_metrics
            )
            valid = valid and passed
            details.append(
                f"{task_name}=test_rows:{test_rows},predictions:{prediction_rows},"
                f"groups:{len(evaluated)}/"
                f"{len(held_out)}(min:{minimum_groups}):"
                f"tail_bound:{temporal_boundary_valid}:"
                f"both_classes_and_metrics:{informative_metrics}:"
                f"{'pass' if passed else 'fail'}"
            )
        return valid, "; ".join(details)

    external_satellite_valid, external_satellite_detail = external_valid("satellite")
    external_station_valid, external_station_detail = external_valid("station")

    composed = evaluation.get("composed_reception", {})
    minimum_composed_rows = (
        V4_MIN_EXTERNAL_TEST_ROWS
        if strict_v4_contract
        else int(publication.get("minimum_external_test_rows", 1))
    )

    def composed_split_valid(
        split: object, *, expected_rows: int, require_status: bool
    ) -> bool:
        if not isinstance(split, Mapping):
            return False
        if require_status and split.get("status") != "evaluated":
            return False
        metrics = split.get("metrics", {})
        decisions = split.get("decision_metrics_at_0_5", {})
        full = metrics.get("full_logit", {}) if isinstance(metrics, Mapping) else {}
        full_decision = (
            decisions.get("full_logit", {})
            if isinstance(decisions, Mapping)
            else {}
        )
        try:
            count = int(full.get("count", -1))
            positives = int(full.get("positives", -1))
            brier = float(full.get("brier"))
            auroc = float(full.get("auroc"))
            decision_count = int(full_decision.get("count", -1))
            accuracy = float(full_decision.get("accuracy"))
            confusion_count = sum(
                int(full_decision.get(name, -1))
                for name in (
                    "true_positive",
                    "true_negative",
                    "false_positive",
                    "false_negative",
                )
            )
        except (TypeError, ValueError):
            return False
        return (
            count == expected_rows
            and decision_count == expected_rows
            and confusion_count == expected_rows
            and expected_rows >= minimum_composed_rows
            and 0 < positives < count
            and math.isfinite(brier)
            and 0 <= brier <= 1
            and math.isfinite(auroc)
            and 0 <= auroc <= 1
            and math.isfinite(accuracy)
            and 0 <= accuracy <= 1
        )

    composed_temporal = (
        composed.get("temporal", {}) if isinstance(composed, Mapping) else {}
    )
    composed_external = (
        composed.get("external_validation", {})
        if isinstance(composed, Mapping)
        else {}
    )
    try:
        composed_temporal_rows = int(composed_temporal.get("test_count", -1))
    except (AttributeError, TypeError, ValueError):
        composed_temporal_rows = -1
    composed_parts = {
        "temporal": composed_split_valid(
            composed_temporal,
            expected_rows=composed_temporal_rows,
            require_status=False,
        ),
    }
    for label in ("satellite", "station"):
        split = (
            composed_external.get(label, {})
            if isinstance(composed_external, Mapping)
            else {}
        )
        try:
            rows_count = int(split.get("test_observation_rows", -1))
        except (AttributeError, TypeError, ValueError):
            rows_count = -1
        composed_parts[label] = composed_split_valid(
            split,
            expected_rows=rows_count,
            require_status=True,
        )
    composed_replay = (
        composed.get("scheduling_replay", {})
        if isinstance(composed, Mapping)
        else {}
    )
    replay_metrics = (
        composed_replay.get("metrics", {})
        if isinstance(composed_replay, Mapping)
        else {}
    )
    composed_parts["scheduling_replay"] = bool(
        isinstance(composed_replay, Mapping)
        and composed_replay.get("outcome_task") == "reception_success"
        and composed_replay.get("candidate_count") == composed_temporal_rows
        and isinstance(replay_metrics, Mapping)
        and set(replay_metrics)
        == {
            "chronological",
            "maximum_elevation",
            "longest_duration",
            "probability_milp",
            "oracle_milp_upper_bound",
        }
    )
    composed_reception_valid = (
        isinstance(composed, Mapping)
        and composed.get("status") == "evaluated"
        and composed.get("definition")
        == "P(signal) * P(decode artifact | signal)"
        and all(composed_parts.values())
    )
    composed_reception_detail = "; ".join(
        f"{name}={'pass' if passed else 'fail'}"
        for name, passed in composed_parts.items()
    )

    weather_effect_evaluated = True
    weather_effect_detail: list[str] = []
    for task_name in ("signal_present", "decode_success_given_signal"):
        task = (
            evaluation_tasks.get(task_name, {})
            if isinstance(evaluation_tasks, Mapping)
            else {}
        )
        ablation = (
            task.get("covariate_ablation", {})
            if isinstance(task, Mapping)
            else {}
        )
        families = (
            ablation.get("available_families", [])
            if isinstance(ablation, Mapping)
            else []
        )
        metrics = (
            ablation.get("metrics", {})
            if isinstance(ablation, Mapping)
            else {}
        )
        operational = (
            metrics.get("operational_logit", {})
            if isinstance(metrics, Mapping)
            else {}
        )
        with_weather = (
            metrics.get("operational_weather_logit", {})
            if isinstance(metrics, Mapping)
            else {}
        )
        try:
            operational_brier = float(operational["brier"])
            weather_brier = float(with_weather["brier"])
            predictions_path = _resolved_artifact(
                project_root, ablation.get("predictions_path")
            )
            prediction_hash_valid = (
                predictions_path.is_file()
                and _sha256_file(predictions_path)
                == ablation.get("predictions_sha256")
            )
            paired = ablation.get("paired_bootstrap_vs_operational", {})
            bootstrap_hashes_valid = True
            bootstrap_cluster_counts: list[int] = []
            for cluster_name in (
                "satellite_cluster",
                "station_cluster_sensitivity",
            ):
                cluster_report = (
                    paired.get(cluster_name, {})
                    if isinstance(paired, Mapping)
                    else {}
                )
                comparison = (
                    cluster_report.get("operational_weather_logit", {})
                    if isinstance(cluster_report, Mapping)
                    else {}
                )
                artifact_path = _resolved_artifact(
                    project_root, comparison.get("artifact_path")
                )
                artifact_valid = (
                    artifact_path.is_file()
                    and _sha256_file(artifact_path)
                    == comparison.get("artifact_sha256")
                    and int(comparison.get("replicate_count", 0)) >= 1000
                )
                bootstrap_hashes_valid = bootstrap_hashes_valid and artifact_valid
                bootstrap_cluster_counts.append(
                    int(comparison.get("cluster_count", 0))
                )
            deployment_gate = ablation.get("deployment_candidate_gate", {})
            deployment_decision_present = (
                isinstance(deployment_gate, Mapping)
                and isinstance(deployment_gate.get("by_model"), Mapping)
                and isinstance(
                    deployment_gate["by_model"].get(
                        "operational_weather_logit"
                    ),
                    bool,
                )
            )
            task_valid = (
                isinstance(families, list)
                and {"operational_logit", "operational_weather_logit"}
                <= set(families)
                and math.isfinite(operational_brier)
                and math.isfinite(weather_brier)
                and 0 <= operational_brier <= 1
                and 0 <= weather_brier <= 1
                and int(ablation.get("train_count", 0)) > 0
                and int(ablation.get("test_count", 0)) >= minimum_external_rows
                and prediction_hash_valid
                and bootstrap_hashes_valid
                and len(bootstrap_cluster_counts) == 2
                and all(count >= 5 for count in bootstrap_cluster_counts)
                and deployment_decision_present
            )
            delta = weather_brier - operational_brier
        except (KeyError, OSError, TypeError, ValueError):
            task_valid = False
            delta = float("nan")
            prediction_hash_valid = False
            bootstrap_hashes_valid = False
        weather_effect_evaluated = weather_effect_evaluated and task_valid
        weather_effect_detail.append(
            f"{task_name}=delta_brier:{delta:.8g},predictions_bound:"
            f"{prediction_hash_valid},bootstraps_bound:{bootstrap_hashes_valid},"
            f"{'pass' if task_valid else 'fail'}"
        )

    campaign = ProspectiveCampaignConfig.load(prospective_config_path)
    prospective_config_payload = _load(prospective_config_path)
    runtime_contact_valid = False
    runtime_contact_path: Path | None = None
    runtime_contact_sha256 = ""
    try:
        historical_dependencies = prospective_config_payload.get(
            "historical_dependencies"
        )
        if not isinstance(historical_dependencies, Mapping):
            raise ValueError("historical dependencies are missing")
        contact_dependency = historical_dependencies.get("runtime_api_contact")
        if not isinstance(contact_dependency, Mapping):
            raise ValueError("runtime API contact dependency is missing")
        runtime_contact_path = _resolved_artifact(
            project_root, contact_dependency.get("path")
        )
        runtime_contact_sha256 = _sha256_file(runtime_contact_path)
        runtime_user_agent, _ = load_api_contact(runtime_contact_path)
        runtime_contact_valid = (
            contact_dependency.get("sha256") == runtime_contact_sha256
            and contact_dependency.get("byte_length")
            == runtime_contact_path.stat().st_size
            and campaign.runtime_user_agent == runtime_user_agent
            and prospective_config_payload.get("runtime_user_agent")
            == runtime_user_agent
        )
    except (ApiContactError, OSError, TypeError, ValueError):
        runtime_contact_valid = False
    events = read_ledger(prospective_ledger_path)
    prospective = prospective_report(prospective_config_path, prospective_ledger_path)
    plan_events = [event for event in events if event.event_type == "plan_committed"]
    valid_input_hashes = True
    valid_plan_hashes = True
    valid_covariate_days: set[str] = set()
    audited_covariate_use_days: set[str] = set()
    raw_covariate_evidence_days: set[str] = set()
    tle_evidence_days: set[str] = set()
    runtime_source_manifest_days: set[str] = set()
    runtime_source_identities: set[str] = set()
    blocker_snapshot_paths: set[str] = set()
    target_override_snapshot_paths: set[str] = set()
    in_campaign_plan_event_count = 0
    valid_runtime_source_event_count = 0
    expected_campaign_plan_id = f"prospective-{campaign.campaign_id}"
    plan_revision_lineage_valid = True
    plan_revision_lineage_event_count = 0
    previous_plan_revision = 0
    audited_covariate_assignment_count = 0
    audited_weather_match_count = 0
    audited_weather_value_count = 0
    audited_kp_match_count = 0
    audited_hardware_match_count = 0
    audited_antenna_gain_value_count = 0
    audited_antenna_noise_value_count = 0
    audited_raw_covariate_response_count = 0
    valid_tle_evidence_event_count = 0
    valid_blocker_event_count = 0
    audited_blocker_count = 0
    valid_target_override_event_count = 0
    audited_target_override_count = 0
    valid_probability_model_binding_event_count = 0
    terrestrial_names = {
        "air_temperature_c",
        "relative_humidity_percent",
        "surface_pressure_kpa",
        "wind_speed_m_s",
        "precipitation_corrected",
    }
    for event in plan_events:
        event_has_valid_covariates = False
        event_has_audited_use = False
        event_covariate_archives: list[
            tuple[
                Path,
                tuple[WeatherRecord, ...],
                tuple[KpRecord, ...],
                tuple[HardwareRecord, ...],
            ]
        ] = []
        event_covariate_evidence_paths: list[Path] = []
        event_raw_evidence_valid = False
        event_raw_evidence_response_count = 0
        event_tle_evidence_valid = False
        plan_payload: object = {}
        event_covariate_audit_counts: Mapping[str, int] = {
            "assignment_count": 0,
            "weather_match_count": 0,
            "weather_value_count": 0,
            "kp_match_count": 0,
            "hardware_match_count": 0,
            "antenna_gain_value_count": 0,
            "antenna_noise_value_count": 0,
        }
        assignments: object = []
        assigned_station_ids: set[int] = set()
        assignment_station_ids_valid = False
        event_runtime_source_identities: set[str] = set()
        event_runtime_source_manifest_count = 0
        event_blocker_artifact_count = 0
        event_blocker_valid = False
        event_blocker_count = 0
        event_blocker_path: Path | None = None
        event_target_override_artifact_count = 0
        event_target_override_valid = False
        event_target_override_count = 0
        event_target_override_path: Path | None = None
        event_probability_contracts_valid = False
        event_probability_model_artifact_count = 0
        event_history_dataset_artifact_count = 0
        in_campaign = campaign.start <= event.occurred_at < campaign.end
        if in_campaign:
            in_campaign_plan_event_count += 1
        try:
            plan_path = _resolved_artifact(project_root, event.payload.get("plan_path"))
            plan_hash_valid = (
                plan_path.is_file()
                and _sha256_file(plan_path) == event.payload.get("plan_sha256")
            )
            plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
            event_tle_evidence_valid = (
                plan_hash_valid
                and _audit_plan_tle_evidence(
                    plan_payload,
                    expected_norad_ids=campaign.target_norad_ids,
                    committed_at=event.occurred_at.isoformat(),
                )
                and isinstance(plan_payload, Mapping)
                and event.payload.get("tle_fingerprints")
                == plan_payload.get("tle_fingerprints")
            )
            if in_campaign:
                plan_revision_lineage_event_count += 1
                raw_revision = (
                    plan_payload.get("revision")
                    if isinstance(plan_payload, Mapping)
                    else None
                )
                raw_plan_id = (
                    plan_payload.get("plan_id")
                    if isinstance(plan_payload, Mapping)
                    else None
                )
                revision_valid = (
                    plan_hash_valid
                    and raw_plan_id == expected_campaign_plan_id
                    and not isinstance(raw_revision, bool)
                    and isinstance(raw_revision, int)
                    and raw_revision > previous_plan_revision
                    and event.payload.get("plan_id") == raw_plan_id
                    and event.payload.get("revision") == raw_revision
                )
                plan_revision_lineage_valid = (
                    plan_revision_lineage_valid and revision_valid
                )
                if revision_valid:
                    previous_plan_revision = raw_revision
            assignments = (
                plan_payload.get("assignments", [])
                if isinstance(plan_payload, Mapping)
                else []
            )
            assignment_station_ids_valid = (
                isinstance(assignments, list)
                and bool(assignments)
                and all(
                    isinstance(assignment, Mapping)
                    and not isinstance(
                        assignment.get("satnogs_station_id"), bool
                    )
                    and isinstance(assignment.get("satnogs_station_id"), int)
                    and int(assignment["satnogs_station_id"]) > 0
                    for assignment in assignments
                )
            )
            assigned_station_ids = (
                {
                    int(assignment["satnogs_station_id"])
                    for assignment in assignments
                    if isinstance(assignment, Mapping)
                }
                if assignment_station_ids_valid
                else set()
            )
            contracts = [
                metadata.get("feature_use_contract", {})
                for assignment in assignments
                if isinstance(assignment, Mapping)
                and isinstance(assignment.get("metadata"), Mapping)
                for metadata in (assignment["metadata"],)
            ] if isinstance(assignments, list) else []
            has_accounted_kp = any(
                isinstance(contract, Mapping)
                and _contract_accounts_for_feature_group(
                    contract, {"space_weather_kp"}
                )
                for contract in contracts
            )
            has_accounted_terrestrial_weather = any(
                isinstance(contract, Mapping)
                and _contract_accounts_for_feature_group(
                    contract, terrestrial_names
                )
                for contract in contracts
            )
            has_active_antenna_constraint = any(
                isinstance(contract, Mapping)
                and "receiver_antenna_frequency_range"
                in contract.get("active_feasibility_constraints", [])
                for contract in contracts
            )
            contracts_valid = (
                bool(contracts)
                and len(contracts) == len(assignments)
                and all(
                    isinstance(assignment, Mapping)
                    and isinstance(assignment.get("probability"), Mapping)
                    and isinstance(contract, Mapping)
                    and contract.get("schema_version")
                    == "probability-feature-use-v1"
                    and contract.get("model_version")
                    == assignment["probability"].get("model_version")
                    for assignment, contract in zip(assignments, contracts, strict=True)
                )
            )
            event_probability_contracts_valid = (
                model_binding_configuration_valid
                and isinstance(frozen_probability_contract, Mapping)
                and contracts_valid
                and all(
                    _contract_matches_frozen_probability_model(
                        contract,
                        frozen_probability_contract,
                        assignment["probability"],
                    )
                    for assignment, contract in zip(
                        assignments, contracts, strict=True
                    )
                )
            )
            event_has_audited_use = (
                plan_hash_valid
                and contracts_valid
                and has_accounted_kp
                and has_accounted_terrestrial_weather
                and has_active_antenna_constraint
            )
            if not plan_hash_valid:
                valid_plan_hashes = False
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            valid_plan_hashes = False
            if in_campaign:
                plan_revision_lineage_valid = False
        raw_artifacts = event.payload.get("input_artifacts", [])
        if not isinstance(raw_artifacts, list):
            valid_input_hashes = False
            continue
        for artifact in raw_artifacts:
            if not isinstance(artifact, Mapping):
                valid_input_hashes = False
                continue
            try:
                path = _resolved_artifact(project_root, artifact.get("path"))
                if not path.is_file() or _sha256_file(path) != artifact.get("sha256"):
                    valid_input_hashes = False
                    continue
            except (OSError, ValueError):
                valid_input_hashes = False
                continue
            if (
                expected_probability_model_path is not None
                and path == expected_probability_model_path
            ):
                event_probability_model_artifact_count += 1
            if (
                expected_history_dataset_path is not None
                and path == expected_history_dataset_path
            ):
                event_history_dataset_artifact_count += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(payload, Mapping)
                and payload.get("schema_version")
                == "observation-planning-blockers-v1"
            ):
                event_blocker_artifact_count += 1
                current_valid, current_count = _audit_assignment_blockers(
                    assignments,
                    payload,
                    campaign_station_ids=campaign.station_ids,
                )
                if event_blocker_artifact_count == 1:
                    event_blocker_valid = current_valid
                    event_blocker_count = current_count
                    event_blocker_path = path
                else:
                    event_blocker_valid = False
                continue
            if (
                isinstance(payload, Mapping)
                and payload.get("schema_version")
                == "observation-planning-target-overrides-v1"
            ):
                event_target_override_artifact_count += 1
                current_valid, current_count = _audit_assignment_target_overrides(
                    assignments,
                    payload,
                    campaign_target_ids=campaign.target_norad_ids,
                )
                if event_target_override_artifact_count == 1:
                    event_target_override_valid = current_valid
                    event_target_override_count = current_count
                    event_target_override_path = path
                else:
                    event_target_override_valid = False
                continue
            if not isinstance(payload, Mapping) or payload.get("schema_version") != (
                "observation-planning-covariates-v1"
            ):
                if (
                    isinstance(payload, Mapping)
                    and payload.get("schema_version")
                    == "observation-planning-covariate-evidence-v1"
                ):
                    event_covariate_evidence_paths.append(path)
                if (
                    isinstance(payload, Mapping)
                    and payload.get("schema_version")
                    == "observation-planning-source-manifest-v1"
                ):
                    records = payload.get("files")
                    try:
                        created_at = parse_api_datetime(
                            payload.get("created_at"), name="created_at"
                        )
                        if not isinstance(plan_payload, Mapping):
                            raise ValueError("plan payload is unavailable")
                        planning_started_at = parse_api_datetime(
                            plan_payload.get("created_at"),
                            name="plan.created_at",
                        )
                        age_seconds = (
                            planning_started_at - created_at
                        ).total_seconds()
                        identity = hashlib.sha256(
                            json.dumps(
                                records,
                                sort_keys=True,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest()
                        records_valid = isinstance(records, list) and all(
                            isinstance(record, Mapping)
                            and isinstance(record.get("path"), str)
                            and bool(record.get("path"))
                            and isinstance(record.get("byte_length"), int)
                            and int(record.get("byte_length")) >= 0
                            and isinstance(record.get("sha256"), str)
                            and len(str(record.get("sha256"))) == 64
                            for record in records
                        )
                        source_contract_valid = (
                            isinstance(records, list)
                            and len(records) >= 20
                            and records_valid
                            and len({str(record["path"]) for record in records})
                            == len(records)
                            and payload.get("file_count") == len(records)
                            and payload.get("source_identity_sha256") == identity
                            and 0 <= age_seconds <= 600
                        )
                    except (TypeError, ValueError):
                        source_contract_valid = False
                        identity = ""
                    if source_contract_valid:
                        event_runtime_source_manifest_count += 1
                        event_runtime_source_identities.add(identity)
                continue
            contract_valid = _covariate_contract_integrity(payload)
            weather = payload.get("weather", [])
            kp = payload.get("space_weather_kp", [])
            hardware = payload.get("hardware", [])
            if not all(isinstance(values, list) for values in (weather, kp, hardware)):
                continue
            weather_station_ids = {
                int(item.get("station_id", 0))
                for item in weather
                if isinstance(item, Mapping)
                and "Open-Meteo" in str(item.get("source", ""))
                and parse_api_datetime(item.get("retrieved_at"), name="retrieved_at")
                <= event.occurred_at
            }
            hardware_station_ids = {
                int(item.get("station_id", 0))
                for item in hardware
                if isinstance(item, Mapping)
                and "SatNOGS Network" in str(item.get("source", ""))
                and item.get("frequency_ranges_hz")
                and parse_api_datetime(item.get("known_at"), name="known_at")
                <= event.occurred_at
            }
            has_prospective_kp = any(
                isinstance(item, Mapping)
                and "NOAA SWPC" in str(item.get("source", ""))
                and parse_api_datetime(item.get("retrieved_at"), name="retrieved_at")
                <= event.occurred_at
                for item in kp
            )
            if (
                assignment_station_ids_valid
                and assigned_station_ids <= weather_station_ids
                and assigned_station_ids <= hardware_station_ids
                and has_prospective_kp
                and contract_valid
            ):
                event_has_valid_covariates = True
            try:
                archived = read_covariate_archive(path)
                event_covariate_archives.append((path, *archived))
            except (CovariateError, TypeError, ValueError):
                valid_input_hashes = False
        exact_covariate_use_valid = False
        if len(event_covariate_archives) == 1:
            (
                archived_path,
                archived_weather,
                archived_kp,
                archived_hardware,
            ) = (
                event_covariate_archives[0]
            )
            exact_covariate_use_valid, event_covariate_audit_counts = (
                _audit_assignment_covariates(
                    assignments,
                    weather=archived_weather,
                    kp=archived_kp,
                    hardware=archived_hardware,
                    as_of=event.occurred_at.isoformat(),
                )
            )
            if len(event_covariate_evidence_paths) == 1:
                try:
                    evidence_summary = validate_covariate_evidence_manifest(
                        event_covariate_evidence_paths[0], archived_path
                    )
                    event_raw_evidence_response_count = int(
                        evidence_summary.get("response_count", 0)
                    )
                    event_raw_evidence_valid = (
                        event_raw_evidence_response_count > 0
                    )
                except (CovariateError, OSError, TypeError, ValueError):
                    event_raw_evidence_valid = False
        event_has_audited_use = (
            event_has_audited_use
            and exact_covariate_use_valid
            and event_raw_evidence_valid
            and event_covariate_audit_counts["weather_value_count"] > 0
            and event_covariate_audit_counts["kp_match_count"] > 0
            and event_covariate_audit_counts["hardware_match_count"]
            == event_covariate_audit_counts["assignment_count"]
        )
        if (
            in_campaign
            and event_probability_contracts_valid
            and event_probability_model_artifact_count == 1
            and event_history_dataset_artifact_count == 1
        ):
            valid_probability_model_binding_event_count += 1
        if in_campaign and event_has_valid_covariates:
            valid_covariate_days.add(event.occurred_at.date().isoformat())
        if in_campaign and event_raw_evidence_valid:
            raw_covariate_evidence_days.add(event.occurred_at.date().isoformat())
            audited_raw_covariate_response_count += (
                event_raw_evidence_response_count
            )
        if in_campaign and event_tle_evidence_valid:
            valid_tle_evidence_event_count += 1
            tle_evidence_days.add(event.occurred_at.date().isoformat())
        blocker_path_text = (
            str(event_blocker_path) if event_blocker_path is not None else ""
        )
        if (
            in_campaign
            and event_blocker_artifact_count == 1
            and event_blocker_valid
            and blocker_path_text
            and blocker_path_text not in blocker_snapshot_paths
        ):
            valid_blocker_event_count += 1
            audited_blocker_count += event_blocker_count
            blocker_snapshot_paths.add(blocker_path_text)
        target_override_path_text = (
            str(event_target_override_path)
            if event_target_override_path is not None
            else ""
        )
        if (
            in_campaign
            and event_target_override_artifact_count == 1
            and event_target_override_valid
            and target_override_path_text
            and target_override_path_text not in target_override_snapshot_paths
        ):
            valid_target_override_event_count += 1
            audited_target_override_count += event_target_override_count
            target_override_snapshot_paths.add(target_override_path_text)
        if in_campaign and event_has_audited_use:
            audited_covariate_use_days.add(event.occurred_at.date().isoformat())
            audited_covariate_assignment_count += event_covariate_audit_counts[
                "assignment_count"
            ]
            audited_weather_match_count += event_covariate_audit_counts[
                "weather_match_count"
            ]
            audited_weather_value_count += event_covariate_audit_counts[
                "weather_value_count"
            ]
            audited_kp_match_count += event_covariate_audit_counts["kp_match_count"]
            audited_hardware_match_count += event_covariate_audit_counts[
                "hardware_match_count"
            ]
            audited_antenna_gain_value_count += event_covariate_audit_counts[
                "antenna_gain_value_count"
            ]
            audited_antenna_noise_value_count += event_covariate_audit_counts[
                "antenna_noise_value_count"
            ]
        if (
            in_campaign
            and event_runtime_source_manifest_count == 1
            and len(event_runtime_source_identities) == 1
        ):
            valid_runtime_source_event_count += 1
            runtime_source_manifest_days.add(event.occurred_at.date().isoformat())
            runtime_source_identities.update(event_runtime_source_identities)

    outcome_events = [
        event for event in events if event.event_type == "outcome_recorded"
    ]
    plan_events_by_hash = {event.event_sha256: event for event in plan_events}
    bound_json_cache: dict[tuple[Path, str], object] = {}
    seen_network_observation_ids: set[int] = set()
    outcome_provenance_valid = True
    valid_outcome_provenance_count = 0
    valid_scored_target_ids: set[int] = set()
    valid_scored_station_ids: set[int] = set()
    valid_signal_positive_outcomes = 0
    valid_signal_negative_outcomes = 0
    valid_conditional_decode_positive_outcomes = 0
    valid_conditional_decode_negative_outcomes = 0

    def load_bound_json(raw_path: object, raw_sha256: object) -> object:
        if not isinstance(raw_sha256, str) or len(raw_sha256) != 64:
            raise ValueError("artifact SHA-256 is malformed")
        path = _resolved_artifact(project_root, raw_path)
        key = (path, raw_sha256)
        if key not in bound_json_cache:
            if not path.is_file() or _sha256_file(path) != raw_sha256:
                raise ValueError("outcome source artifact hash mismatch")
            bound_json_cache[key] = json.loads(path.read_text(encoding="utf-8"))
        return bound_json_cache[key]

    for outcome_event in outcome_events:
        try:
            outcome = outcome_event.payload
            opportunity_id = str(outcome.get("opportunity_id", ""))
            observed_at = parse_api_datetime(
                outcome.get("observed_at"), name="outcome.observed_at"
            )
            source_rows = load_bound_json(
                outcome.get("source_artifact_path"),
                outcome.get("source_artifact_sha256"),
            )
            if not isinstance(source_rows, list):
                raise ValueError("outcome source artifact must be an array")
            matching_source_rows = [
                row
                for row in source_rows
                if isinstance(row, Mapping)
                and str(row.get("opportunity_id", "")) == opportunity_id
            ]
            if len(matching_source_rows) != 1:
                raise ValueError("outcome source row is not unique")
            source_row = matching_source_rows[0]
            source_label = str(outcome.get("source", ""))
            source_row_label = str(
                source_row.get("source", "local-or-satnogs-reconciliation")
            )
            labels_valid = (
                source_row.get("observation_id") == outcome.get("observation_id")
                and source_row.get("signal_present")
                == outcome.get("signal_present")
                and source_row.get("decode_success_given_signal")
                == outcome.get("decode_success_given_signal")
                and source_row_label == source_label
                and parse_api_datetime(
                    source_row.get("observed_at"), name="source_row.observed_at"
                )
                == observed_at
                and observed_at <= outcome_event.occurred_at
            )

            eligible_predictions: list[
                tuple[object, Mapping[str, object]]
            ] = []
            for plan_event in plan_events:
                raw_assignments = plan_event.payload.get("assignments", [])
                if not isinstance(raw_assignments, list):
                    continue
                for assignment in raw_assignments:
                    if (
                        not isinstance(assignment, Mapping)
                        or str(assignment.get("opportunity_id", ""))
                        != opportunity_id
                    ):
                        continue
                    assignment_start = parse_api_datetime(
                        assignment.get("start"), name="assignment.start"
                    )
                    if plan_event.occurred_at < assignment_start <= observed_at:
                        eligible_predictions.append((plan_event, assignment))
            if not eligible_predictions:
                raise ValueError("outcome has no eligible prospective prediction")
            expected_plan_event, expected_assignment = max(
                eligible_predictions, key=lambda item: item[0].occurred_at
            )
            expected_prediction = {
                name: expected_assignment[name]
                for name in (
                    "p_signal_present",
                    "p_decode_given_signal",
                    "p_success",
                    "model_version",
                )
            }
            prediction = outcome.get("prediction")
            probabilities_valid = all(
                not isinstance(expected_prediction[name], bool)
                and isinstance(expected_prediction[name], (int, float))
                and math.isfinite(float(expected_prediction[name]))
                and 0 <= float(expected_prediction[name]) <= 1
                for name in (
                    "p_signal_present",
                    "p_decode_given_signal",
                    "p_success",
                )
            ) and bool(str(expected_prediction["model_version"]))
            prediction_valid = (
                isinstance(prediction, Mapping)
                and dict(prediction) == expected_prediction
                and outcome.get("prediction_plan_event_sha256")
                == expected_plan_event.event_sha256
                and plan_events_by_hash.get(
                    str(outcome.get("prediction_plan_event_sha256", ""))
                )
                == expected_plan_event
                and parse_api_datetime(
                    outcome.get("prediction_plan_committed_at"),
                    name="prediction_plan_committed_at",
                )
                == expected_plan_event.occurred_at
                and probabilities_valid
            )

            network_source_valid = True
            if "SatNOGS Network" in source_label:
                observation_id = outcome.get("observation_id")
                if (
                    isinstance(observation_id, bool)
                    or not isinstance(observation_id, int)
                    or observation_id <= 0
                    or observation_id in seen_network_observation_ids
                ):
                    network_source_valid = False
                raw_snapshot = load_bound_json(
                    outcome.get("raw_source_artifact_path"),
                    outcome.get("raw_source_artifact_sha256"),
                )
                raw_observations = (
                    raw_snapshot.get("observations", [])
                    if isinstance(raw_snapshot, Mapping)
                    else []
                )
                raw_matches = [
                    row
                    for row in raw_observations
                    if isinstance(row, Mapping)
                    and row.get("id") == observation_id
                ] if isinstance(raw_observations, list) else []
                if len(raw_matches) != 1:
                    network_source_valid = False
                else:
                    raw_observation = raw_matches[0]
                    raw_signal, raw_decoded = satnogs_outcome_labels(
                        raw_observation.get("waterfall_status"),
                        raw_observation.get("demoddata"),
                        packet_capable=satnogs_mode_is_packet_capable(
                            expected_assignment.get("modulation")
                        ),
                    )
                    raw_transmitter = raw_observation.get(
                        "transmitter_uuid"
                    ) or raw_observation.get("transmitter")
                    network_source_valid = network_source_valid and (
                        raw_signal == outcome.get("signal_present")
                        and raw_decoded
                        == outcome.get("decode_success_given_signal")
                        and parse_api_datetime(
                            raw_observation.get("end"), name="raw_observation.end"
                        )
                        == observed_at
                        and int(raw_observation.get("ground_station", 0))
                        == int(expected_assignment.get("satnogs_station_id", 0))
                        and int(raw_observation.get("norad_cat_id", 0))
                        == int(expected_assignment.get("norad_id", 0))
                        and raw_transmitter
                        == expected_assignment.get("transmitter_uuid")
                    )
                if network_source_valid:
                    seen_network_observation_ids.add(int(observation_id))

            event_valid = labels_valid and prediction_valid and network_source_valid
        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            event_valid = False
        outcome_provenance_valid = outcome_provenance_valid and event_valid
        if event_valid:
            valid_outcome_provenance_count += 1
            signal_label = outcome.get("signal_present")
            decode_label = outcome.get("decode_success_given_signal")
            if signal_label in {0, 1}:
                valid_signal_positive_outcomes += int(signal_label)
                valid_signal_negative_outcomes += 1 - int(signal_label)
                raw_target_id = expected_assignment.get("norad_id")
                if isinstance(raw_target_id, int) and not isinstance(
                    raw_target_id, bool
                ):
                    valid_scored_target_ids.add(raw_target_id)
                raw_station_id = expected_assignment.get("satnogs_station_id")
                if isinstance(raw_station_id, int) and not isinstance(
                    raw_station_id, bool
                ):
                    valid_scored_station_ids.add(raw_station_id)
            if decode_label in {0, 1}:
                valid_conditional_decode_positive_outcomes += int(decode_label)
                valid_conditional_decode_negative_outcomes += 1 - int(
                    decode_label
                )

    plan_commit_days = int(prospective.get("plan_commit_days", 0))
    required_covariate_days = max(1, int(plan_commit_days * 0.8 + 0.999999))
    prospective_covariates_valid = (
        plan_commit_days >= campaign.minimum_plan_commit_days
        and len(valid_covariate_days) >= required_covariate_days
    )
    prospective_covariate_use_valid = (
        plan_commit_days >= campaign.minimum_plan_commit_days
        and len(audited_covariate_use_days) >= required_covariate_days
    )
    prospective_raw_covariate_evidence_valid = (
        plan_commit_days >= campaign.minimum_plan_commit_days
        and len(raw_covariate_evidence_days) >= required_covariate_days
        and audited_raw_covariate_response_count > 0
    )
    prospective_tle_evidence_valid = (
        len(campaign.target_norad_ids) >= 50
        and len(set(campaign.target_norad_ids)) == len(campaign.target_norad_ids)
        and plan_commit_days >= campaign.minimum_plan_commit_days
        and in_campaign_plan_event_count > 0
        and valid_tle_evidence_event_count == in_campaign_plan_event_count
        and len(tle_evidence_days) == plan_commit_days
    )
    prospective_blockers_valid = (
        plan_commit_days >= campaign.minimum_plan_commit_days
        and in_campaign_plan_event_count > 0
        and valid_blocker_event_count == in_campaign_plan_event_count
        and len(blocker_snapshot_paths) == in_campaign_plan_event_count
    )
    prospective_target_overrides_valid = (
        plan_commit_days >= campaign.minimum_plan_commit_days
        and in_campaign_plan_event_count > 0
        and valid_target_override_event_count == in_campaign_plan_event_count
        and len(target_override_snapshot_paths) == in_campaign_plan_event_count
    )
    runtime_source_frozen = (
        plan_commit_days >= campaign.minimum_plan_commit_days
        and in_campaign_plan_event_count > 0
        and valid_runtime_source_event_count == in_campaign_plan_event_count
        and len(runtime_source_manifest_days) == plan_commit_days
        and len(runtime_source_identities) == 1
        and runtime_source_identities
        == {str(source.get("source_identity_sha256", ""))}
    )
    plan_revision_lineage_complete = (
        plan_commit_days >= campaign.minimum_plan_commit_days
        and plan_revision_lineage_event_count == in_campaign_plan_event_count
        and plan_revision_lineage_valid
        and previous_plan_revision > 0
    )
    prospective_probability_model_binding_valid = (
        model_binding_configuration_valid
        and plan_commit_days >= campaign.minimum_plan_commit_days
        and in_campaign_plan_event_count > 0
        and valid_probability_model_binding_event_count
        == in_campaign_plan_event_count
    )
    outcome_provenance_complete = (
        len(outcome_events) >= campaign.minimum_reconciled_outcomes
        and valid_outcome_provenance_count == len(outcome_events)
        and outcome_provenance_valid
    )
    prospective_outcome_sample_design_valid = (
        outcome_provenance_complete
        and len(valid_scored_target_ids) >= campaign.minimum_scored_targets
        and len(valid_scored_station_ids) >= campaign.minimum_scored_stations
        and valid_signal_positive_outcomes
        >= campaign.minimum_signal_positive_outcomes
        and valid_signal_negative_outcomes
        >= campaign.minimum_signal_negative_outcomes
        and valid_conditional_decode_positive_outcomes
        >= campaign.minimum_conditional_decode_positive_outcomes
        and valid_conditional_decode_negative_outcomes
        >= campaign.minimum_conditional_decode_negative_outcomes
    )
    checks = [
        ReadinessCheck(
            "v4_outcome_blind_method_contract",
            method_contract_valid,
            method_contract_detail,
        ),
        ReadinessCheck(
            "v4_expanded_outcome_blind_cohort",
            cohort_valid,
            (
                f"targets={len(target_ids)}; external_satellites={len(external_ids)}; "
                f"external_station_fraction={station_fraction}; config_bound="
                f"{_sha256_file(publication_config_path) == dataset.get('config_sha256')}; "
                f"normalized_satellites={normalized_satellites}; "
                f"signal_satellites={signal_satellites}; "
                f"decode_satellites={decode_satellites}; "
                f"represented_targets={len(represented_target_ids)}"
            ),
        ),
        ReadinessCheck(
            "v4_outcome_blind_sampling_panel",
            sampling_valid,
            sampling_detail,
        ),
        ReadinessCheck(
            "v4_historical_capture_provenance",
            location_receiver_provenance_valid,
            (
                f"captured_locations={captured_location_rows}/{normalized_rows} "
                f"(minimum_fraction={MIN_CAPTURED_LOCATION_FRACTION}); "
                f"current_api_fallbacks={current_location_fallback_rows}; "
                f"metadata_status_total="
                f"{sum(int(count) for count in metadata_status_counts.values()) if isinstance(metadata_status_counts, Mapping) else 0}; "
                f"receiver_configurations={receiver_configuration_rows}/{normalized_rows} "
                f"(minimum_fraction={MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION}); "
                f"receiver_drivers={receiver_driver_rows}/{normalized_rows} "
                f"(minimum_fraction={MIN_CAPTURED_RECEIVER_DRIVER_FRACTION}); "
                f"receiver_rf_gain={receiver_gain_rows}/{normalized_rows} "
                f"(minimum_fraction={MIN_CAPTURED_RECEIVER_GAIN_FRACTION})"
            ),
        ),
        ReadinessCheck(
            "v4_real_historical_environment",
            historical_environment_valid,
            (
                f"weather_rows={weather_rows}/{captured_location_rows} eligible capture locations "
                f"(minimum_fraction={MIN_VERIFIED_WEATHER_FRACTION}); kp_rows={kp_rows}/"
                f"{normalized_rows} (minimum_fraction={MIN_VERIFIED_SPACE_WEATHER_FRACTION}); "
                f"weather_value_rows={weather_value_rows}/"
                f"{captured_location_rows}; weather_complete_rows={weather_complete_rows}/"
                f"{captured_location_rows}; archive_bound={covariate_hash_valid}; "
                f"archive_sources_valid={archive_sources_valid}; "
                f"archive_units_valid={archive_units_valid}; "
                f"raw_evidence_valid={historical_raw_evidence_valid}; "
                f"raw_responses={historical_raw_evidence_response_count}"
            ),
        ),
        ReadinessCheck(
            "v4_historical_weather_effect_evaluated",
            weather_effect_evaluated,
            "; ".join(weather_effect_detail),
        ),
        ReadinessCheck(
            "v4_external_satellite_validation",
            external_satellite_valid,
            external_satellite_detail,
        ),
        ReadinessCheck(
            "v4_external_station_validation",
            external_station_valid,
            external_station_detail,
        ),
        ReadinessCheck(
            "v4_composed_reception_validation",
            composed_reception_valid,
            composed_reception_detail,
        ),
        ReadinessCheck(
            "v4_prospective_30_day_campaign",
            prospective.get("publication_complete") is True,
            json.dumps(prospective.get("gates", {}), sort_keys=True),
        ),
        ReadinessCheck(
            "v4_prospective_runtime_api_contact",
            runtime_contact_valid,
            (
                f"path={runtime_contact_path}; sha256={runtime_contact_sha256}; "
                f"registered_user_agent={campaign.runtime_user_agent}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_real_covariate_capture",
            prospective_covariates_valid,
            (
                f"valid_covariate_days={len(valid_covariate_days)}; "
                f"required={required_covariate_days}; plan_commit_days={plan_commit_days}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_covariate_use_audited",
            prospective_covariate_use_valid,
            (
                f"audited_use_days={len(audited_covariate_use_days)}; "
                f"required={required_covariate_days}; plan_commit_days={plan_commit_days}; "
                f"assignments={audited_covariate_assignment_count}; "
                f"weather_matches={audited_weather_match_count}; "
                f"weather_values={audited_weather_value_count}; "
                f"kp_matches={audited_kp_match_count}; "
                f"hardware_matches={audited_hardware_match_count}; "
                f"gain_values={audited_antenna_gain_value_count}; "
                f"noise_values={audited_antenna_noise_value_count}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_raw_covariate_evidence",
            prospective_raw_covariate_evidence_valid,
            (
                f"evidence_days={len(raw_covariate_evidence_days)}; "
                f"required={required_covariate_days}; "
                f"embedded_responses={audited_raw_covariate_response_count}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_50_target_tle_evidence",
            prospective_tle_evidence_valid,
            (
                f"registered_targets={len(campaign.target_norad_ids)}; "
                f"valid_events={valid_tle_evidence_event_count}/"
                f"{in_campaign_plan_event_count}; "
                f"days={len(tle_evidence_days)}/{plan_commit_days}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_blocker_snapshots",
            prospective_blockers_valid,
            (
                f"valid_events={valid_blocker_event_count}/"
                f"{in_campaign_plan_event_count}; immutable_snapshots="
                f"{len(blocker_snapshot_paths)}; audited_blockers="
                f"{audited_blocker_count}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_target_override_snapshots",
            prospective_target_overrides_valid,
            (
                f"valid_events={valid_target_override_event_count}/"
                f"{in_campaign_plan_event_count}; immutable_snapshots="
                f"{len(target_override_snapshot_paths)}; audited_overrides="
                f"{audited_target_override_count}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_plan_hashes",
            valid_plan_hashes and bool(plan_events),
            f"plan_events={len(plan_events)}; hashes_valid={valid_plan_hashes}",
        ),
        ReadinessCheck(
            "v4_prospective_plan_revision_lineage",
            plan_revision_lineage_complete,
            (
                f"expected_plan_id={expected_campaign_plan_id}; valid="
                f"{plan_revision_lineage_valid}; events="
                f"{plan_revision_lineage_event_count}/{in_campaign_plan_event_count}; "
                f"latest_revision={previous_plan_revision}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_input_hashes",
            valid_input_hashes and bool(plan_events),
            f"plan_events={len(plan_events)}; hashes_valid={valid_input_hashes}",
        ),
        ReadinessCheck(
            "v4_prospective_outcome_provenance",
            outcome_provenance_complete,
            (
                f"valid_outcomes={valid_outcome_provenance_count}/"
                f"{len(outcome_events)}; required="
                f"{campaign.minimum_reconciled_outcomes}; unique_network_observations="
                f"{len(seen_network_observation_ids)}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_outcome_sample_design",
            prospective_outcome_sample_design_valid,
            (
                f"targets={len(valid_scored_target_ids)}/"
                f"{campaign.minimum_scored_targets}; stations="
                f"{len(valid_scored_station_ids)}/"
                f"{campaign.minimum_scored_stations}; signal_positive="
                f"{valid_signal_positive_outcomes}/"
                f"{campaign.minimum_signal_positive_outcomes}; signal_negative="
                f"{valid_signal_negative_outcomes}/"
                f"{campaign.minimum_signal_negative_outcomes}; decode_positive="
                f"{valid_conditional_decode_positive_outcomes}/"
                f"{campaign.minimum_conditional_decode_positive_outcomes}; "
                f"decode_negative={valid_conditional_decode_negative_outcomes}/"
                f"{campaign.minimum_conditional_decode_negative_outcomes}"
            ),
        ),
        ReadinessCheck(
            "v4_prospective_runtime_source_frozen",
            runtime_source_frozen,
            (
                f"valid_events={valid_runtime_source_event_count}/"
                f"{in_campaign_plan_event_count}; days="
                f"{len(runtime_source_manifest_days)}/{plan_commit_days}; "
                f"identities={sorted(runtime_source_identities)}; final_identity="
                f"{source.get('source_identity_sha256')}"
            ),
        ),
    ]
    if model_binding_requested:
        checks.append(
            ReadinessCheck(
                "v4_prospective_probability_model_binding",
                prospective_probability_model_binding_valid,
                (
                    f"binding_configuration_valid="
                    f"{model_binding_configuration_valid}; valid_events="
                    f"{valid_probability_model_binding_event_count}/"
                    f"{in_campaign_plan_event_count}; model_path="
                    f"{expected_probability_model_path}; history_path="
                    f"{expected_history_dataset_path}"
                ),
            )
        )
    return checks, {
        "publication_config": str(publication_config_path),
        "prospective_config": str(prospective_config_path),
        "prospective_ledger": str(prospective_ledger_path),
        "prospective_report": prospective,
        "valid_covariate_days": len(valid_covariate_days),
        "audited_covariate_use_days": len(audited_covariate_use_days),
        "raw_covariate_evidence_days": len(raw_covariate_evidence_days),
        "audited_raw_covariate_response_count": (
            audited_raw_covariate_response_count
        ),
        "registered_prospective_target_count": len(campaign.target_norad_ids),
        "valid_tle_evidence_event_count": valid_tle_evidence_event_count,
        "tle_evidence_days": len(tle_evidence_days),
        "valid_blocker_event_count": valid_blocker_event_count,
        "blocker_snapshot_count": len(blocker_snapshot_paths),
        "audited_blocker_count": audited_blocker_count,
        "valid_target_override_event_count": valid_target_override_event_count,
        "target_override_snapshot_count": len(target_override_snapshot_paths),
        "audited_target_override_count": audited_target_override_count,
        "audited_covariate_assignment_count": audited_covariate_assignment_count,
        "audited_weather_match_count": audited_weather_match_count,
        "audited_weather_value_count": audited_weather_value_count,
        "audited_kp_match_count": audited_kp_match_count,
        "audited_hardware_match_count": audited_hardware_match_count,
        "audited_antenna_gain_value_count": audited_antenna_gain_value_count,
        "audited_antenna_noise_value_count": audited_antenna_noise_value_count,
        "required_covariate_days": required_covariate_days,
        "runtime_source_manifest_days": len(runtime_source_manifest_days),
        "runtime_source_identities": sorted(runtime_source_identities),
        "expected_campaign_plan_id": expected_campaign_plan_id,
        "plan_revision_lineage_event_count": plan_revision_lineage_event_count,
        "latest_plan_revision": previous_plan_revision,
        "valid_outcome_provenance_count": valid_outcome_provenance_count,
        "unique_network_observation_count": len(seen_network_observation_ids),
        "valid_probability_model_binding_event_count": (
            valid_probability_model_binding_event_count
        ),
        "probability_model_binding_configuration_valid": (
            model_binding_configuration_valid
        ),
        "runtime_api_contact_valid": runtime_contact_valid,
        "runtime_api_contact_path": (
            str(runtime_contact_path) if runtime_contact_path is not None else None
        ),
        "runtime_api_contact_sha256": runtime_contact_sha256,
    }


def _snapshot_integrity(
    snapshot: Mapping[str, object],
    *,
    snapshot_manifest_path: Path,
    dataset: Mapping[str, object],
    publication_config_path: Path | None = None,
) -> tuple[bool, str]:
    responses = snapshot.get("responses")
    if not isinstance(responses, list) or not responses:
        return False, "snapshot has no response pages"
    root = Path(str(snapshot.get("raw_directory", "")))
    if not root.is_dir():
        candidate = snapshot_manifest_path.parent / root
        root = candidate if candidate.is_dir() else root
    rows = 0
    actual_request_groups: dict[
        tuple[int, str, str, int, str | None], list[tuple[int, str, str | None]]
    ] = {}
    try:
        for entry in responses:
            if not isinstance(entry, Mapping):
                return False, "malformed response entry"
            relative = str(entry["relative_path"])
            if Path(relative).name != relative:
                return False, "unsafe raw response path"
            path = root / relative
            body = path.read_bytes()
            if len(body) != int(entry["byte_length"]):
                return False, f"byte mismatch: {relative}"
            if hashlib.sha256(body).hexdigest() != entry["sha256"]:
                return False, f"hash mismatch: {relative}"
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, list) or len(payload) != int(entry["row_count"]):
                return False, f"row mismatch: {relative}"
            if publication_config_path is not None:
                metadata_path = path.with_suffix(".meta.json")
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                if (
                    not isinstance(metadata, Mapping)
                    or metadata.get("manifest_entry") != entry
                ):
                    return False, f"sidecar mismatch: {relative}"
                next_url = metadata.get("next_url")
                if next_url is not None and not isinstance(next_url, str):
                    return False, f"invalid next URL: {relative}"
                request_url = str(entry["request_url"])
                parsed_request = urlparse(request_url)
                if (
                    parsed_request.scheme != "https"
                    or parsed_request.netloc != "network.satnogs.org"
                    or parsed_request.path != "/api/observations/"
                ):
                    return False, f"unexpected request origin/path: {relative}"
                key = (
                    int(entry["norad_id"]),
                    str(entry["start"]),
                    str(entry["end"]),
                    int(entry.get("waterfall_status_filter", -1)),
                    (
                        str(entry["transmitter_uuid_filter"])
                        if entry.get("transmitter_uuid_filter") is not None
                        else None
                    ),
                )
                actual_request_groups.setdefault(key, []).append(
                    (int(entry["page_index"]), request_url, next_url)
                )
            rows += len(payload)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, str(exc)
    expected_request_count: int | None = None
    if publication_config_path is not None:
        try:
            from .satnogs_dataset import (
                PublicationDatasetConfig,
                publication_config_identity,
            )

            publication = PublicationDatasetConfig.load(publication_config_path)
            expected_groups: set[tuple[int, str, str, int, str | None]] = set()
            for target in publication.targets:
                for window in publication.acquisition_windows_for(target.norad_id):
                    cursor = window.start
                    while cursor < window.end:
                        boundary = (
                            cursor.replace(
                                year=cursor.year + 1,
                                month=1,
                                day=1,
                                hour=0,
                                minute=0,
                                second=0,
                                microsecond=0,
                            )
                            if cursor.month == 12
                            else cursor.replace(
                                month=cursor.month + 1,
                                day=1,
                                hour=0,
                                minute=0,
                                second=0,
                                microsecond=0,
                            )
                        )
                        chunk_end = min(boundary, window.end)
                        expected_groups.add(
                            (
                                target.norad_id,
                                cursor.isoformat().replace("+00:00", "Z"),
                                chunk_end.isoformat().replace("+00:00", "Z"),
                                window.waterfall_status,
                                window.transmitter_uuid,
                            )
                        )
                        cursor = chunk_end
            expected_request_count = len(expected_groups)
            if set(actual_request_groups) != expected_groups:
                missing = len(expected_groups - set(actual_request_groups))
                extra = len(set(actual_request_groups) - expected_groups)
                return False, f"acquisition request coverage mismatch: missing={missing}; extra={extra}"
            if snapshot.get("config_identity_sha256") != publication_config_identity(
                publication
            ):
                return False, "snapshot/config acquisition identity mismatch"
            if snapshot.get("config_file_sha256") != _sha256_file(
                publication_config_path
            ):
                return False, "snapshot/config file hash mismatch"
            for key, pages in actual_request_groups.items():
                ordered_pages = sorted(pages)
                indexes = [item[0] for item in ordered_pages]
                if indexes != list(range(1, len(ordered_pages) + 1)):
                    return False, f"non-contiguous page indexes for request {key}"
                first_query = parse_qs(urlparse(ordered_pages[0][1]).query)
                expected_query = {
                    "norad_cat_id": [str(key[0])],
                    "start": [key[1]],
                    "start__lt": [key[2]],
                }
                if key[4] is not None:
                    expected_query["transmitter_uuid"] = [key[4]]
                if key[3] >= 0:
                    expected_query["waterfall_status"] = [str(key[3])]
                if first_query != expected_query:
                    return False, f"first-page query mismatch for request {key}"
                for current_page, following_page in zip(
                    ordered_pages, ordered_pages[1:]
                ):
                    if current_page[2] != following_page[1]:
                        return False, f"broken cursor chain for request {key}"
                if ordered_pages[-1][2] is not None:
                    return False, f"unterminated cursor chain for request {key}"
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return False, f"acquisition coverage audit failed: {exc}"
    snapshot_hash = hashlib.sha256(snapshot_manifest_path.read_bytes()).hexdigest()
    acquisition_terminal_state_valid = (
        publication_config_path is None
        or (
            snapshot.get("active_request") is None
            and snapshot.get("pending_requests") == []
            and int(snapshot.get("remaining_request_count", -1)) == 0
        )
    )
    passed = (
        snapshot.get("complete") is True
        and acquisition_terminal_state_valid
        and rows == int(snapshot.get("total_rows_before_deduplication", -1))
        and snapshot.get("study_id") == dataset.get("study_id")
        and snapshot_hash == dataset.get("source_snapshot_manifest_sha256")
        and snapshot.get("config_file_sha256") == dataset.get("config_sha256")
    )
    return passed, (
        f"pages={len(responses)}; rows={rows}; "
        f"request_groups={len(actual_request_groups)}; "
        f"expected_request_groups={expected_request_count}; "
        f"manifest_sha256={snapshot_hash}"
    )


def _source_integrity(source: Mapping[str, object]) -> tuple[bool, str]:
    records = source.get("files")
    root = Path(str(source.get("project_root", ""))).resolve()
    if not isinstance(records, list) or not records:
        return False, "source manifest has no files"
    try:
        for record in records:
            if not isinstance(record, Mapping):
                return False, "malformed source record"
            path = (root / str(record["path"])).resolve()
            path.relative_to(root)
            body = path.read_bytes()
            if len(body) != int(record["byte_length"]):
                return False, f"byte mismatch: {record['path']}"
            if hashlib.sha256(body).hexdigest() != record["sha256"]:
                return False, f"hash mismatch: {record['path']}"
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return False, str(exc)
    identity = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    passed = (
        int(source.get("file_count", -1)) == len(records)
        and len(records) >= 20
        and identity == source.get("source_identity_sha256")
    )
    return passed, f"files={len(records)}; identity={identity}"


_HUMAN_RELEASE_GATE_NAMES = (
    "project_code_license_attested",
    "reachable_api_contact_attested",
    "satnogs_attribution_review_attested",
    "external_data_attribution_review_attested",
)

_EXTERNAL_DATA_ATTRIBUTION_REQUIREMENTS = {
    "open_meteo": {
        "source": "https://open-meteo.com/",
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "citation": "https://doi.org/10.5281/zenodo.7970649",
        "terms_url": "https://open-meteo.com/en/terms",
    },
    "gfz_kp": {
        "source": "https://kp.gfz.de/",
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "citation": "https://doi.org/10.5880/Kp.0001",
    },
    "noaa_swpc": {
        "source": "https://services.swpc.noaa.gov/",
        "license": "US-PUBLIC-DOMAIN",
        "terms_url": "https://www.weather.gov/disclaimer/",
    },
    "celestrak": {
        "source": "https://celestrak.org/",
        "usage_policy_url": "https://celestrak.org/usage-policy.php",
    },
}


def _release_attestation_evidence(
    path: Path,
    *,
    study_id: str,
    dataset_config_sha256: str,
    source_identity_sha256: str,
) -> Mapping[str, object]:
    """Validate a persistent human release decision bound to frozen inputs.

    The file is deliberately separate from technical readiness.  It lets daily
    prospective runs preserve a reviewed release decision without turning any
    automated process into the attestor.
    """

    result: dict[str, object] = {
        "path": str(path),
        "artifact_sha256": None,
        "valid": False,
        "gates": {
            name: {"passed": False, "detail": "release attestation unavailable"}
            for name in _HUMAN_RELEASE_GATE_NAMES
        },
    }
    try:
        body = path.read_bytes()
        result["artifact_sha256"] = hashlib.sha256(body).hexdigest()
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("release attestation must be a JSON object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        detail = f"invalid release attestation: {type(exc).__name__}: {exc}"
        result["gates"] = {
            name: {"passed": False, "detail": detail}
            for name in _HUMAN_RELEASE_GATE_NAMES
        }
        return result

    common_errors: list[str] = []
    if payload.get("schema_version") != "observation-planning-release-attestation-v2":
        common_errors.append("schema_version mismatch")
    if payload.get("study_id") != study_id:
        common_errors.append("study_id mismatch")
    if payload.get("dataset_config_sha256") != dataset_config_sha256:
        common_errors.append("dataset_config_sha256 mismatch")
    if payload.get("source_identity_sha256") != source_identity_sha256:
        common_errors.append("source_identity_sha256 mismatch")
    attestor = payload.get("attestor")
    if not isinstance(attestor, str) or not attestor.strip() or "REPLACE" in attestor.upper():
        common_errors.append("named attestor is required")
    try:
        parse_api_datetime(payload["attested_at"], name="attested_at")
    except (KeyError, TypeError, ValueError):
        common_errors.append("valid attested_at timestamp is required")

    license_evidence = payload.get("project_code_license")
    license_errors = list(common_errors)
    if not isinstance(license_evidence, Mapping):
        license_errors.append("project_code_license object is required")
    else:
        identifier = license_evidence.get("spdx_identifier")
        if license_evidence.get("attested") is not True:
            license_errors.append("project license was not attested")
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or identifier.strip().upper() in {"NOASSERTION", "NONE", "UNLICENSED"}
            or "REPLACE" in identifier.upper()
        ):
            license_errors.append("a concrete SPDX license identifier is required")

    contact_evidence = payload.get("reachable_api_contact")
    contact_errors = list(common_errors)
    if not isinstance(contact_evidence, Mapping):
        contact_errors.append("reachable_api_contact object is required")
    else:
        contact = contact_evidence.get("contact")
        if contact_evidence.get("attested") is not True:
            contact_errors.append("reachable API contact was not attested")
        try:
            validate_api_contact(contact)
            contact_valid = True
        except ApiContactError:
            contact_valid = False
        if not contact_valid:
            contact_errors.append("a non-placeholder HTTPS URL or email contact is required")

    attribution_evidence = payload.get("satnogs_attribution_review")
    attribution_errors = list(common_errors)
    if not isinstance(attribution_evidence, Mapping):
        attribution_errors.append("satnogs_attribution_review object is required")
    else:
        if attribution_evidence.get("attested") is not True:
            attribution_errors.append("SatNOGS attribution review was not attested")
        if attribution_evidence.get("source") != "https://network.satnogs.org/api/":
            attribution_errors.append("SatNOGS Network API source mismatch")
        if attribution_evidence.get("license") != "CC-BY-SA-4.0":
            attribution_errors.append("SatNOGS data license mismatch")
        if (
            attribution_evidence.get("license_url")
            != "https://creativecommons.org/licenses/by-sa/4.0/"
        ):
            attribution_errors.append("SatNOGS license URL mismatch")

    external_evidence = payload.get("external_data_attribution_review")
    external_errors = list(common_errors)
    if not isinstance(external_evidence, Mapping):
        external_errors.append("external_data_attribution_review object is required")
    else:
        if external_evidence.get("attested") is not True:
            external_errors.append("external data attribution was not attested")
        sources = external_evidence.get("sources")
        if not isinstance(sources, Mapping):
            external_errors.append("external data source declarations are required")
        else:
            for name, expected_fields in _EXTERNAL_DATA_ATTRIBUTION_REQUIREMENTS.items():
                declaration = sources.get(name)
                if not isinstance(declaration, Mapping):
                    external_errors.append(f"{name} declaration is required")
                    continue
                for field, expected in expected_fields.items():
                    if declaration.get(field) != expected:
                        external_errors.append(f"{name}.{field} mismatch")

    errors_by_gate = {
        "project_code_license_attested": license_errors,
        "reachable_api_contact_attested": contact_errors,
        "satnogs_attribution_review_attested": attribution_errors,
        "external_data_attribution_review_attested": external_errors,
    }
    gates = {
        name: {
            "passed": not errors,
            "detail": "hash-bound persistent human attestation"
            if not errors
            else "; ".join(errors),
        }
        for name, errors in errors_by_gate.items()
    }
    result["gates"] = gates
    result["valid"] = all(item["passed"] is True for item in gates.values())
    return result


def assess_planning_publication_readiness(
    *,
    snapshot_manifest_path: Path,
    dataset_manifest_path: Path,
    evaluation_path: Path,
    independent_audit_path: Path,
    source_manifest_path: Path,
    results_manifest_path: Path,
    junit_path: Path,
    test_attestation_path: Path | None = None,
    project_license_attested: bool = False,
    reachable_contact_attested: bool = False,
    attribution_review_attested: bool = False,
    release_attestation_path: Path | None = None,
    publication_config_path: Path | None = None,
    prospective_config_path: Path | None = None,
    prospective_ledger_path: Path | None = None,
    probability_model_path: Path | None = None,
    covariate_evidence_path: Path | None = None,
) -> Mapping[str, object]:
    snapshot = _load(snapshot_manifest_path)
    dataset = _load(dataset_manifest_path)
    evaluation = _load(evaluation_path)
    audit = _load(independent_audit_path)
    source = _load(source_manifest_path)
    results = _load(results_manifest_path)
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    snapshot_valid, snapshot_detail = _snapshot_integrity(
        snapshot,
        snapshot_manifest_path=snapshot_manifest_path,
        dataset=dataset,
        publication_config_path=publication_config_path,
    )
    source_valid, source_detail = _source_integrity(source)
    test_attestation_evidence: Mapping[str, object] | None = None
    if test_attestation_path is not None:
        try:
            test_attestation_evidence = validate_test_attestation(
                test_attestation_path,
                project_root=Path(str(source.get("project_root", ""))),
                source_manifest_path=source_manifest_path,
                junit_path=junit_path,
            )
        except (OSError, TypeError, ValueError, PlanningTestAttestationError) as exc:
            test_attestation_evidence = {
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    release_attestation: Mapping[str, object] | None = None
    human_gate_details = {
        "project_code_license_attested": (
            project_license_attested,
            "human attestation required before redistributing source",
        ),
        "reachable_api_contact_attested": (
            reachable_contact_attested,
            "replace placeholder for future API runs",
        ),
        "satnogs_attribution_review_attested": (
            attribution_review_attested,
            "human CC BY-SA attribution/share-alike review required",
        ),
        "external_data_attribution_review_attested": (
            False,
            "human Open-Meteo/GFZ/NOAA/CelesTrak attribution review required",
        ),
    }
    if release_attestation_path is not None:
        release_attestation = _release_attestation_evidence(
            release_attestation_path,
            study_id=str(snapshot.get("study_id", "")),
            dataset_config_sha256=str(dataset.get("config_sha256", "")),
            source_identity_sha256=str(source.get("source_identity_sha256", "")),
        )
        raw_gates = release_attestation.get("gates", {})
        if isinstance(raw_gates, Mapping):
            human_gate_details = {
                name: (
                    bool(raw_gates.get(name, {}).get("passed", False))
                    if isinstance(raw_gates.get(name), Mapping)
                    else False,
                    str(raw_gates.get(name, {}).get("detail", "invalid release attestation"))
                    if isinstance(raw_gates.get(name), Mapping)
                    else "invalid release attestation",
                )
                for name in _HUMAN_RELEASE_GATE_NAMES
            }
    source_records = source.get("files", [])
    dataset_config_path = dataset.get("config_path")
    source_root = Path(str(source.get("project_root", ""))).resolve()
    if isinstance(dataset_config_path, str):
        candidate_config_path = Path(dataset_config_path)
        if candidate_config_path.is_absolute():
            try:
                expected_config_path = candidate_config_path.resolve().relative_to(
                    source_root
                ).as_posix()
            except ValueError:
                expected_config_path = "__outside_project_root__"
        else:
            expected_config_path = candidate_config_path.as_posix()
    else:
        expected_config_path = "configs/observation-planning-publication-v1.json"
    source_config_record = next(
        (
            item
            for item in source_records
            if isinstance(item, Mapping)
            and item.get("path") == expected_config_path
        ),
        None,
    ) if isinstance(source_records, list) else None
    source_config_identity_valid = (
        isinstance(source_config_record, Mapping)
        and source_config_record.get("sha256") == dataset.get("config_sha256")
    )
    dataset_manifest_sha = hashlib.sha256(dataset_manifest_path.read_bytes()).hexdigest()
    evaluation_sha = hashlib.sha256(evaluation_path.read_bytes()).hexdigest()
    audit_sha = hashlib.sha256(independent_audit_path.read_bytes()).hexdigest()
    audit_inputs = audit.get("input_artifacts", {})
    audit_dataset_manifest = (
        audit_inputs.get("dataset_manifest", {})
        if isinstance(audit_inputs, Mapping)
        else {}
    )
    audit_dataset = (
        audit_inputs.get("dataset", {})
        if isinstance(audit_inputs, Mapping)
        else {}
    )
    audit_evaluation = (
        audit_inputs.get("evaluation", {})
        if isinstance(audit_inputs, Mapping)
        else {}
    )
    audit_input_identity_valid = (
        isinstance(audit_dataset, Mapping)
        and isinstance(audit_dataset_manifest, Mapping)
        and isinstance(audit_evaluation, Mapping)
        and audit_dataset.get("sha256") == dataset.get("dataset_sha256")
        and audit_dataset_manifest.get("sha256") == dataset_manifest_sha
        and audit_evaluation.get("sha256") == evaluation_sha
    )
    results_input_identity_valid = (
        results.get("dataset_manifest_sha256") == dataset_manifest_sha
        and results.get("evaluation_sha256") == evaluation_sha
        and results.get("independent_audit_sha256") == audit_sha
    )
    result_artifacts = results.get("artifacts", [])
    result_hashes_valid = bool(result_artifacts)
    if isinstance(result_artifacts, list):
        for artifact in result_artifacts:
            if not isinstance(artifact, Mapping):
                result_hashes_valid = False
                break
            relative = artifact.get("relative_path")
            if not isinstance(relative, str) or Path(relative).name != relative:
                result_hashes_valid = False
                break
            path = results_manifest_path.parent / relative
            if (
                not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.get("sha256")
                or path.stat().st_size != int(artifact.get("bytes", -1))
            ):
                result_hashes_valid = False
                break
    else:
        result_hashes_valid = False

    probability_model_check: ReadinessCheck | None = None
    probability_model_sparse_check: ReadinessCheck | None = None
    frozen_probability_contract: Mapping[str, object] | None = None
    resolved_probability_model_path: Path | None = None
    resolved_history_dataset_path: Path | None = None
    if probability_model_path is not None:
        model_valid = False
        model_detail = "validation did not complete"
        sparse_audit: Mapping[str, object] = {
            "passed": False,
            "error": "validation did not complete",
        }
        try:
            from .learned_probability import FrozenLogitProbabilityEstimator

            model_path = _resolved_artifact(source_root, probability_model_path)
            history_path = _resolved_artifact(source_root, dataset.get("dataset_path"))
            resolved_probability_model_path = model_path
            resolved_history_dataset_path = history_path
            model_payload = _load(model_path)
            estimator = FrozenLogitProbabilityEstimator.load(
                model_path,
                history_dataset_path=history_path,
            )
            temporal_boundary_valid = True
            if prospective_config_path is not None:
                prospective_config = ProspectiveCampaignConfig.load(
                    prospective_config_path
                )
                temporal_boundary_valid = (
                    estimator.training_data_end <= prospective_config.start
                )
            model_valid = (
                model_payload.get("study_id") == dataset.get("study_id")
                and model_payload.get("dataset_manifest_sha256")
                == dataset_manifest_sha
                and model_payload.get("evaluation_sha256") == evaluation_sha
                and results.get("probability_model_sha256")
                == _sha256_file(model_path)
                and results.get("probability_model_payload_sha256")
                == estimator.artifact_payload_sha256
                and temporal_boundary_valid
            )
            sparse_audit = estimator.sparse_inference_audit()
            frozen_probability_contract = estimator.feature_use_contract_template()
            model_detail = (
                f"file_sha256={_sha256_file(model_path)}; "
                f"payload_sha256={estimator.artifact_payload_sha256}; "
                f"history_bound={estimator.training_dataset_sha256 == dataset.get('dataset_sha256')}; "
                f"temporal_boundary_valid={temporal_boundary_valid}"
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            model_valid = False
        probability_model_check = ReadinessCheck(
            "frozen_probability_model_bound",
            model_valid,
            model_detail,
        )
        probability_model_sparse_check = ReadinessCheck(
            "frozen_probability_model_sparse_inference",
            sparse_audit.get("passed") is True,
            json.dumps(sparse_audit, sort_keys=True),
        )

    tasks = evaluation.get("tasks", {})
    signal = tasks.get("signal_present", {}) if isinstance(tasks, Mapping) else {}
    decode = (
        tasks.get("decode_success_given_signal", {}) if isinstance(tasks, Mapping) else {}
    )
    synthetic = evaluation.get("synthetic_scheduler_verification", {})
    risk = evaluation.get("correlated_risk_sensitivity", {})
    hierarchy = evaluation.get("analysis_hierarchy", {})
    confirmatory = (
        hierarchy.get("confirmatory", {}) if isinstance(hierarchy, Mapping) else {}
    )
    bootstrap_cluster_counts: dict[str, int] = {}
    for task_name, task_report in (
        ("signal_present", signal),
        ("decode_success_given_signal", decode),
    ):
        if not isinstance(task_report, Mapping):
            continue
        for split_name, comparison_key in (
            ("temporal_satellite", "paired_bootstrap_vs_global_rate"),
            (
                "temporal_station",
                "paired_bootstrap_station_cluster_sensitivity",
            ),
        ):
            temporal = task_report.get("temporal", {})
            comparisons = (
                temporal.get(comparison_key, {})
                if isinstance(temporal, Mapping)
                else {}
            )
            full = (
                comparisons.get("full_logit", {})
                if isinstance(comparisons, Mapping)
                else {}
            )
            bootstrap_cluster_counts[f"{task_name}.{split_name}"] = (
                int(full.get("cluster_count", 0)) if isinstance(full, Mapping) else 0
            )
        for report_key, split_name in (
            ("loso_satellite", "loso_satellite"),
            ("loso_station", "loso_station"),
        ):
            split = task_report.get(report_key, {})
            comparisons = (
                split.get("paired_bootstrap_vs_global_rate", {})
                if isinstance(split, Mapping)
                else {}
            )
            full = (
                comparisons.get("full_logit", {})
                if isinstance(comparisons, Mapping)
                else {}
            )
            bootstrap_cluster_counts[f"{task_name}.{split_name}"] = (
                int(full.get("cluster_count", 0)) if isinstance(full, Mapping) else 0
            )
    checks = [
        ReadinessCheck(
            "snapshot_complete",
            snapshot.get("complete") is True,
            f"responses={len(snapshot.get('responses', []))}",
        ),
        ReadinessCheck("snapshot_integrity", snapshot_valid, snapshot_detail),
        ReadinessCheck(
            "frozen_count_gate",
            dataset.get("publication_count_gate") == "pass",
            json.dumps(dataset.get("publication_requirements", {}), sort_keys=True),
        ),
        ReadinessCheck(
            "signal_task_evaluated",
            isinstance(signal, Mapping) and signal.get("status") == "evaluated",
            f"eligible={signal.get('eligible_rows') if isinstance(signal, Mapping) else None}",
        ),
        ReadinessCheck(
            "decode_task_evaluated",
            isinstance(decode, Mapping) and decode.get("status") == "evaluated",
            f"eligible={decode.get('eligible_rows') if isinstance(decode, Mapping) else None}",
        ),
        ReadinessCheck(
            "bootstrap_cluster_coverage",
            len(bootstrap_cluster_counts) == 8
            and all(count >= 5 for count in bootstrap_cluster_counts.values()),
            json.dumps(bootstrap_cluster_counts, sort_keys=True),
        ),
        ReadinessCheck(
            "confirmatory_analysis_frozen",
            isinstance(confirmatory, Mapping)
            and confirmatory
            == {
                "task": "signal_present",
                "split": "temporal",
                "model": "full_logit",
                "baseline": "global_rate",
                "metric": "brier",
            },
            json.dumps(confirmatory, sort_keys=True),
        ),
        ReadinessCheck(
            "independent_audit",
            audit.get("passed") is True and audit_input_identity_valid,
            (
                f"checks={audit.get('check_count')}; "
                f"failed={audit.get('failed_check_count')}; "
                f"inputs_bound={audit_input_identity_valid}"
            ),
        ),
        ReadinessCheck(
            "full_test_suite",
            tests > 0 and failures == 0 and errors == 0,
            f"tests={tests}; failures={failures}; errors={errors}",
        ),
        ReadinessCheck(
            "source_manifest",
            source_valid and source_config_identity_valid,
            f"{source_detail}; config_bound={source_config_identity_valid}",
        ),
        ReadinessCheck(
            "scheduler_exactness",
            isinstance(synthetic, Mapping)
            and synthetic.get("milp_exact_match_count") == synthetic.get("instances")
            and int(synthetic.get("instances", 0)) > 0,
            (
                f"matches={synthetic.get('milp_exact_match_count')}; "
                f"instances={synthetic.get('instances')}"
                if isinstance(synthetic, Mapping)
                else "missing"
            ),
        ),
        ReadinessCheck(
            "multi_asset_scheduler_exactness",
            isinstance(synthetic, Mapping)
            and synthetic.get("multi_asset_milp_exact_match_count")
            == synthetic.get("multi_asset_instances")
            and int(synthetic.get("multi_asset_instances", 0)) > 0,
            (
                f"matches={synthetic.get('multi_asset_milp_exact_match_count')}; "
                f"instances={synthetic.get('multi_asset_instances')}"
                if isinstance(synthetic, Mapping)
                else "missing"
            ),
        ),
        ReadinessCheck(
            "claim_guarded_results",
            results.get("schema_version")
            == "observation-planning-publication-results-manifest-v1"
            and result_hashes_valid
            and results_input_identity_valid,
            (
                f"artifacts={len(result_artifacts) if isinstance(result_artifacts, list) else 0}; "
                f"inputs_bound={results_input_identity_valid}"
            ),
        ),
        ReadinessCheck(
            "correlated_risk_sensitivity",
            isinstance(risk, Mapping)
            and risk.get("schema_version")
            == "synthetic-correlated-risk-sensitivity-v1"
            and isinstance(risk.get("scenarios"), Mapping)
            and len(risk.get("scenarios", {})) >= 2
            and risk.get("solver_status") == "optimal",
            (
                f"scenarios={len(risk.get('scenarios', {}))}"
                if isinstance(risk, Mapping) and isinstance(risk.get("scenarios"), Mapping)
                else "missing"
            ),
        ),
        ReadinessCheck(
            "project_code_license_attested",
            human_gate_details["project_code_license_attested"][0],
            human_gate_details["project_code_license_attested"][1],
            human_gate=True,
        ),
        ReadinessCheck(
            "reachable_api_contact_attested",
            human_gate_details["reachable_api_contact_attested"][0],
            human_gate_details["reachable_api_contact_attested"][1],
            human_gate=True,
        ),
        ReadinessCheck(
            "satnogs_attribution_review_attested",
            human_gate_details["satnogs_attribution_review_attested"][0],
            human_gate_details["satnogs_attribution_review_attested"][1],
            human_gate=True,
        ),
        ReadinessCheck(
            "external_data_attribution_review_attested",
            human_gate_details["external_data_attribution_review_attested"][0],
            human_gate_details["external_data_attribution_review_attested"][1],
            human_gate=True,
        ),
    ]
    if probability_model_check is not None:
        checks.append(probability_model_check)
    if probability_model_sparse_check is not None:
        checks.append(probability_model_sparse_check)
    v4_paths = (
        publication_config_path,
        prospective_config_path,
        prospective_ledger_path,
    )
    if any(path is not None for path in v4_paths) and not all(
        path is not None for path in v4_paths
    ):
        raise ValueError(
            "publication config, prospective config and ledger must be supplied together"
        )
    if publication_config_path is not None or test_attestation_path is not None:
        test_attestation_valid = (
            test_attestation_path is not None
            and isinstance(test_attestation_evidence, Mapping)
            and test_attestation_evidence.get("passed") is True
        )
        checks.append(
            ReadinessCheck(
                "frozen_source_test_attestation",
                test_attestation_valid,
                json.dumps(test_attestation_evidence or {}, sort_keys=True),
            )
        )
    v4_summary: Mapping[str, object] | None = None
    if all(path is not None for path in v4_paths):
        assert publication_config_path is not None
        assert prospective_config_path is not None
        assert prospective_ledger_path is not None
        v4_checks, v4_summary = _v4_extension_checks(
            publication_config_path=publication_config_path,
            prospective_config_path=prospective_config_path,
            prospective_ledger_path=prospective_ledger_path,
            dataset=dataset,
            evaluation=evaluation,
            source=source,
            covariate_evidence_path=covariate_evidence_path,
            probability_model_path=(
                resolved_probability_model_path or probability_model_path
            ),
            history_dataset_path=(
                resolved_history_dataset_path
                or (
                    Path(str(dataset.get("dataset_path")))
                    if dataset.get("dataset_path") is not None
                    else None
                )
            ),
            frozen_probability_contract=frozen_probability_contract,
        )
        checks.extend(v4_checks)

    technical = [check for check in checks if not check.human_gate]
    human = [check for check in checks if check.human_gate]
    useful = False
    if isinstance(signal, Mapping) and signal.get("status") == "evaluated":
        useful = bool(
            signal.get("temporal", {}).get("primary_full_logit_useful_effect", False)  # type: ignore[union-attr]
        )
    signal_replay = (
        signal.get("scheduling_replay", {}) if isinstance(signal, Mapping) else {}
    )
    composed = evaluation.get("composed_reception", {})
    composed_replay = (
        composed.get("scheduling_replay", {})
        if isinstance(composed, Mapping)
        else {}
    )
    replay = composed_replay if composed_replay else signal_replay
    result = {
        "schema_version": "observation-planning-publication-readiness-v1",
        "technical_publication_ready": all(check.passed for check in technical),
        "external_release_ready": all(check.passed for check in checks),
        "probability_claim": (
            "predeclared useful calibrated improvement"
            if useful
            else "negative or below-threshold calibrated comparison"
        ),
        "scheduler_empirical_claim_informative": bool(
            isinstance(replay, Mapping)
            and replay.get("informative_for_scheduler_comparison") is True
        ),
        "technical_failed_checks": [check.name for check in technical if not check.passed],
        "human_release_gates": [check.name for check in human if not check.passed],
        "checks": [check.as_dict() for check in checks],
    }
    if v4_summary is not None:
        result["v4_extension"] = v4_summary
    if release_attestation is not None:
        result["release_attestation"] = release_attestation
    if test_attestation_evidence is not None:
        result["test_attestation"] = test_attestation_evidence
    return result
