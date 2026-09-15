"""Outcome-blind integrity and metadata coverage checks for live acquisition."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Mapping

from .dataset import (
    _captured_receiver_configuration,
    _captured_station_location,
    _parse_client_metadata,
)
from .v4_protocol import (
    MIN_CAPTURED_LOCATION_FRACTION,
    MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION,
    MIN_CAPTURED_RECEIVER_DRIVER_FRACTION,
    MIN_CAPTURED_RECEIVER_GAIN_FRACTION,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit_partial_input_metadata(
    snapshot_manifest_path: Path,
    raw_directory: Path,
) -> dict[str, object]:
    """Audit only acquisition structure and capture metadata, never outcomes.

    This is an early-warning check over unique raw observations. Final
    publication readiness independently recomputes the same coverage after
    normalization, so this result cannot substitute for a completed cohort.
    """

    snapshot = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot manifest is not an object")
    responses = snapshot.get("responses")
    if not isinstance(responses, list):
        raise ValueError("snapshot responses are absent")

    raw_root = raw_directory.resolve()
    status_counts: Counter[str] = Counter()
    unique_metadata: dict[int, str] = {}
    unique_coverage: dict[int, tuple[bool, bool, bool, bool]] = {}
    raw_row_count = 0
    duplicate_observation_count = 0
    integrity_errors: list[str] = []

    for response_index, response in enumerate(responses):
        if not isinstance(response, Mapping):
            integrity_errors.append(f"response[{response_index}] is not an object")
            continue
        relative_path = response.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            integrity_errors.append(f"response[{response_index}] has no path")
            continue
        path = (raw_root / relative_path).resolve()
        try:
            path.relative_to(raw_root)
        except ValueError:
            integrity_errors.append(f"response[{response_index}] leaves raw directory")
            continue
        if not path.is_file():
            integrity_errors.append(f"missing raw response: {relative_path}")
            continue
        body = path.read_bytes()
        if len(body) != int(response.get("byte_length", -1)):
            integrity_errors.append(f"byte length mismatch: {relative_path}")
            continue
        if hashlib.sha256(body).hexdigest() != response.get("sha256"):
            integrity_errors.append(f"SHA-256 mismatch: {relative_path}")
            continue
        rows = json.loads(body)
        if not isinstance(rows, list):
            integrity_errors.append(f"raw response is not a list: {relative_path}")
            continue
        if len(rows) != int(response.get("row_count", -1)):
            integrity_errors.append(f"row count mismatch: {relative_path}")
            continue
        raw_row_count += len(rows)

        for row_index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                integrity_errors.append(
                    f"non-object row: {relative_path}:{row_index}"
                )
                continue
            observation_id = row.get("id")
            if (
                isinstance(observation_id, bool)
                or not isinstance(observation_id, int)
                or observation_id <= 0
            ):
                integrity_errors.append(
                    f"invalid observation ID: {relative_path}:{row_index}"
                )
                continue

            raw_metadata = row.get("client_metadata")
            metadata_fingerprint = hashlib.sha256(
                json.dumps(
                    raw_metadata,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            previous = unique_metadata.get(observation_id)
            if previous is not None:
                duplicate_observation_count += 1
                if previous != metadata_fingerprint:
                    integrity_errors.append(
                        f"conflicting duplicate metadata: {observation_id}"
                    )
                continue

            metadata, parse_status = _parse_client_metadata(raw_metadata)
            location, location_status = _captured_station_location(
                metadata, parse_status
            )
            receiver = _captured_receiver_configuration(metadata)
            unique_metadata[observation_id] = metadata_fingerprint
            unique_coverage[observation_id] = (
                location is not None,
                bool(receiver),
                receiver.get("captured_receiver_driver") is not None,
                receiver.get("captured_receiver_rf_gain_db") is not None,
            )
            status_counts[location_status] += 1

    expected_raw_rows = int(snapshot.get("total_rows_before_deduplication", -1))
    if raw_row_count != expected_raw_rows:
        integrity_errors.append(
            f"manifest raw rows={expected_raw_rows}; audited={raw_row_count}"
        )

    unique_rows = len(unique_coverage)
    captured_locations = sum(item[0] for item in unique_coverage.values())
    receiver_configurations = sum(item[1] for item in unique_coverage.values())
    receiver_drivers = sum(item[2] for item in unique_coverage.values())
    receiver_gains = sum(item[3] for item in unique_coverage.values())

    def fraction(count: int) -> float:
        return count / unique_rows if unique_rows else 0.0

    coverage_checks = {
        "capture_time_location": (
            fraction(captured_locations) >= MIN_CAPTURED_LOCATION_FRACTION
        ),
        "capture_time_receiver_configuration": (
            fraction(receiver_configurations)
            >= MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION
        ),
        "capture_time_receiver_driver": (
            fraction(receiver_drivers) >= MIN_CAPTURED_RECEIVER_DRIVER_FRACTION
        ),
        "capture_time_receiver_rf_gain": (
            fraction(receiver_gains) >= MIN_CAPTURED_RECEIVER_GAIN_FRACTION
        ),
    }
    integrity_pass = not integrity_errors and unique_rows > 0
    coverage_pass = bool(coverage_checks) and all(coverage_checks.values())
    return {
        "schema_version": "observation-planning-input-metadata-audit-v1",
        "snapshot_manifest_path": str(snapshot_manifest_path),
        "snapshot_manifest_sha256": _sha256_file(snapshot_manifest_path),
        "snapshot_complete": snapshot.get("complete") is True,
        "scope": "provisional unique raw observations; not final normalized cohort",
        "outcome_blind": True,
        "inspected_fields": ["client_metadata", "id"],
        "outcome_fields_accessed": [],
        "response_count": len(responses),
        "raw_row_count": raw_row_count,
        "unique_observation_count": unique_rows,
        "duplicate_observation_count": duplicate_observation_count,
        "client_metadata_parse_status_counts": dict(sorted(status_counts.items())),
        "coverage": {
            "capture_time_location": {
                "count": captured_locations,
                "fraction": fraction(captured_locations),
                "minimum_fraction": MIN_CAPTURED_LOCATION_FRACTION,
            },
            "capture_time_receiver_configuration": {
                "count": receiver_configurations,
                "fraction": fraction(receiver_configurations),
                "minimum_fraction": MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION,
            },
            "capture_time_receiver_driver": {
                "count": receiver_drivers,
                "fraction": fraction(receiver_drivers),
                "minimum_fraction": MIN_CAPTURED_RECEIVER_DRIVER_FRACTION,
            },
            "capture_time_receiver_rf_gain": {
                "count": receiver_gains,
                "fraction": fraction(receiver_gains),
                "minimum_fraction": MIN_CAPTURED_RECEIVER_GAIN_FRACTION,
            },
        },
        "coverage_checks": coverage_checks,
        "integrity_errors": integrity_errors,
        "integrity_pass": integrity_pass,
        "coverage_pass": coverage_pass,
        "pass": integrity_pass and coverage_pass,
    }
