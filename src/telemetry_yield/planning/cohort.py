"""Outcome-blind, deterministic design of a larger SatNOGS target cohort."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from .dataset import NON_PACKET_MODES, DatasetIntegrityError
from .models import as_utc


def _band(frequency_hz: float | None) -> str:
    if frequency_hz is None:
        return "unknown"
    if 30_000_000 <= frequency_hz < 300_000_000:
        return "VHF"
    if 300_000_000 <= frequency_hz < 1_000_000_000:
        return "UHF"
    if 1_000_000_000 <= frequency_hz < 2_000_000_000:
        return "L-band"
    if 2_000_000_000 <= frequency_hz < 4_000_000_000:
        return "S-band"
    return "other"


def _mode_family(mode: str) -> str:
    upper = mode.upper()
    if "BPSK" in upper or "QPSK" in upper or "PSK" in upper:
        return "PSK"
    if "AFSK" in upper:
        return "AFSK"
    if "GMSK" in upper or "GFSK" in upper or "FSK" in upper:
        return "FSK"
    return "other-packet"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def design_station_expansion(
    target_pool_path: Path,
    campaign_config_path: Path,
    transmitter_inventory_path: Path,
    baseline_hardware_path: Path,
    station_catalog_path: Path,
    *,
    output_path: Path,
    frozen_at: datetime,
    minimum_observations: int = 10_000,
    required_compatible_stations_per_target: int = 1,
) -> Mapping[str, object]:
    """Select a deterministic station expansion covering every target redundantly.

    The greedy rank is deliberately outcome-blind: newly covered registered
    targets descending, lifetime observation volume descending, then station ID
    ascending.  Network success-rate fields are neither read nor serialized.
    """

    from .covariates import read_covariate_archive

    frozen_at = as_utc(frozen_at, name="frozen_at")
    if minimum_observations <= 0:
        raise ValueError("minimum_observations must be positive")
    if not 1 <= required_compatible_stations_per_target <= 5:
        raise ValueError(
            "required compatible stations per target must be in [1, 5]"
        )
    target_pool = json.loads(target_pool_path.read_text(encoding="utf-8"))
    campaign = json.loads(campaign_config_path.read_text(encoding="utf-8"))
    inventory = json.loads(transmitter_inventory_path.read_text(encoding="utf-8"))
    station_catalog = json.loads(station_catalog_path.read_text(encoding="utf-8"))
    if (
        not isinstance(target_pool, Mapping)
        or target_pool.get("schema_version")
        != "observation-planning-publication-config-v1"
        or not isinstance(campaign, Mapping)
        or campaign.get("schema_version")
        != "observation-planning-prospective-config-v1"
        or not isinstance(inventory, list)
        or not isinstance(station_catalog, list)
    ):
        raise DatasetIntegrityError("station-expansion inputs use an unsupported schema")

    raw_target_ids = campaign.get("target_norad_ids")
    raw_station_ids = campaign.get("station_ids")
    if not isinstance(raw_target_ids, list) or not isinstance(raw_station_ids, list):
        raise DatasetIntegrityError("campaign target/station IDs must be arrays")
    target_ids = tuple(_positive_int(value) for value in raw_target_ids)
    baseline_station_ids = tuple(_positive_int(value) for value in raw_station_ids)
    if (
        any(value is None for value in target_ids)
        or len(set(target_ids)) != len(target_ids)
        or any(value is None for value in baseline_station_ids)
        or len(set(baseline_station_ids)) != len(baseline_station_ids)
    ):
        raise DatasetIntegrityError("campaign target/station IDs are invalid or duplicated")
    normalized_target_ids = tuple(int(value) for value in target_ids if value is not None)
    normalized_station_ids = tuple(
        int(value) for value in baseline_station_ids if value is not None
    )

    targets = target_pool.get("targets")
    if not isinstance(targets, list):
        raise DatasetIntegrityError("target pool has no targets")
    target_by_id = {
        _positive_int(item.get("norad_id")): item
        for item in targets
        if isinstance(item, Mapping)
    }
    inventory_by_uuid = {
        str(item["uuid"]): item
        for item in inventory
        if isinstance(item, Mapping) and isinstance(item.get("uuid"), str)
    }
    frequencies: dict[int, float] = {}
    for norad_id in normalized_target_ids:
        target = target_by_id.get(norad_id)
        if not isinstance(target, Mapping):
            raise DatasetIntegrityError(f"target {norad_id} is absent from the pool")
        transmitter_uuid = target.get("selection_transmitter_uuid")
        transmitter = inventory_by_uuid.get(str(transmitter_uuid))
        if (
            not isinstance(transmitter, Mapping)
            or _positive_int(transmitter.get("norad_cat_id")) != norad_id
        ):
            raise DatasetIntegrityError(f"transmitter for target {norad_id} is absent")
        raw_frequency = transmitter.get("downlink_low") or transmitter.get(
            "downlink_high"
        )
        try:
            frequency = float(raw_frequency)
        except (TypeError, ValueError) as exc:
            raise DatasetIntegrityError(
                f"transmitter frequency for target {norad_id} is invalid"
            ) from exc
        if not math.isfinite(frequency) or frequency <= 0:
            raise DatasetIntegrityError(
                f"transmitter frequency for target {norad_id} is invalid"
            )
        frequencies[norad_id] = frequency

    _, _, baseline_hardware = read_covariate_archive(baseline_hardware_path)
    hardware_by_station = {item.station_id: item for item in baseline_hardware}
    if set(hardware_by_station) != set(normalized_station_ids):
        raise DatasetIntegrityError(
            "baseline hardware must contain exactly one campaign station record"
        )

    def covered_by_ranges(
        frequency_hz: float, ranges: Sequence[tuple[float, float]]
    ) -> bool:
        return any(low <= frequency_hz <= high for low, high in ranges)

    coverage_counts = {
        norad_id: sum(
            covered_by_ranges(frequency, item.frequency_ranges_hz)
            for item in baseline_hardware
        )
        for norad_id, frequency in frequencies.items()
    }
    initially_covered = {
        norad_id
        for norad_id, count in coverage_counts.items()
        if count >= required_compatible_stations_per_target
    }
    candidates: list[tuple[int, str, int, tuple[tuple[float, float], ...]]] = []
    for raw in station_catalog:
        if not isinstance(raw, Mapping):
            continue
        station_id = _positive_int(raw.get("id"))
        observations = _positive_int(raw.get("observations"))
        name = str(raw.get("name") or "").strip()
        if (
            station_id is None
            or station_id in normalized_station_ids
            or observations is None
            or observations < minimum_observations
            or not name
            or raw.get("is_connected") is not True
            or raw.get("is_available") is not True
            or raw.get("testing") is not False
        ):
            continue
        raw_antennas = raw.get("antenna")
        if not isinstance(raw_antennas, list):
            continue
        ranges: list[tuple[float, float]] = []
        for antenna in raw_antennas:
            if not isinstance(antenna, Mapping):
                continue
            low = antenna.get("frequency")
            high = antenna.get("frequency_max")
            high = low if high is None else high
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
                continue
            ranges.append((float(low), float(high)))
        if ranges:
            candidates.append((station_id, name, observations, tuple(sorted(ranges))))

    undercovered = set(frequencies) - initially_covered
    selected: list[dict[str, object]] = []
    selected_ids: set[int] = set()
    while undercovered:
        ranked: list[
            tuple[
                int,
                int,
                int,
                str,
                tuple[tuple[float, float], ...],
                set[int],
            ]
        ] = []
        for station_id, name, observations, ranges in candidates:
            if station_id in selected_ids:
                continue
            coverage_contribution = {
                norad_id
                for norad_id in undercovered
                if covered_by_ranges(frequencies[norad_id], ranges)
            }
            if coverage_contribution:
                ranked.append(
                    (
                        -len(coverage_contribution),
                        -observations,
                        station_id,
                        name,
                        ranges,
                        coverage_contribution,
                    )
                )
        if not ranked:
            raise DatasetIntegrityError(
                "eligible station catalog cannot cover all registered target frequencies"
            )
        _, negative_observations, station_id, name, ranges, contribution = min(
            ranked, key=lambda item: item[:3]
        )
        selected_ids.add(station_id)
        for norad_id in contribution:
            coverage_counts[norad_id] += 1
        newly_covered = {
            norad_id
            for norad_id in contribution
            if coverage_counts[norad_id]
            >= required_compatible_stations_per_target
        }
        undercovered -= newly_covered
        selected.append(
            {
                "station_id": station_id,
                "station_name": name,
                "selection_observation_count": -negative_observations,
                "frequency_ranges_hz": [list(item) for item in ranges],
                "coverage_contribution_norad_ids": sorted(contribution),
                "newly_covered_norad_ids": sorted(newly_covered),
            }
        )

    snapshot_paths = (
        target_pool_path,
        campaign_config_path,
        transmitter_inventory_path,
        baseline_hardware_path,
        station_catalog_path,
    )
    payload: dict[str, object] = {
        "schema_version": "observation-planning-station-expansion-v1",
        "frozen_at": frozen_at.isoformat().replace("+00:00", "Z"),
        "selection_rule": (
            "Starting from the frozen campaign stations, repeatedly select the "
            "connected, available, non-testing station above the volume threshold "
            "that reduces the most outstanding registered-target coverage deficits; "
            "break ties by observation count descending and station ID ascending. "
            "Success rates and reception labels are not read."
        ),
        "selection_parameters": {
            "minimum_observations": minimum_observations,
            "required_compatible_stations_per_target": (
                required_compatible_stations_per_target
            ),
            "rank": [
                "coverage_deficit_reduction_target_count_desc",
                "observation_count_desc",
                "station_id_asc",
            ],
            "required_status": {
                "is_connected": True,
                "is_available": True,
                "testing": False,
            },
            "ignored_outcome_fields": ["success_rate"],
        },
        "selection_snapshots": [
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in snapshot_paths
        ],
        "registered_target_count": len(normalized_target_ids),
        "baseline_station_ids": list(normalized_station_ids),
        "initially_covered_target_count": len(initially_covered),
        "initially_covered_norad_ids": sorted(initially_covered),
        "initial_target_coverage_counts": [
            {
                "norad_id": norad_id,
                "compatible_station_count": sum(
                    covered_by_ranges(
                        frequencies[norad_id], item.frequency_ranges_hz
                    )
                    for item in baseline_hardware
                ),
            }
            for norad_id in sorted(frequencies)
        ],
        "selected_stations": selected,
        "final_station_ids": [
            *normalized_station_ids,
            *(int(item["station_id"]) for item in selected),
        ],
        "final_covered_target_count": sum(
            count >= required_compatible_stations_per_target
            for count in coverage_counts.values()
        ),
        "final_minimum_compatible_station_count": min(coverage_counts.values()),
        "final_target_coverage_counts": [
            {
                "norad_id": norad_id,
                "compatible_station_count": coverage_counts[norad_id],
            }
            for norad_id in sorted(frequencies)
        ],
        "target_frequencies": [
            {"norad_id": norad_id, "frequency_hz": frequencies[norad_id]}
            for norad_id in sorted(frequencies)
        ],
        "screening_used_success_outcomes": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def design_expanded_cohort(
    transmitter_inventory_path: Path,
    transmitter_stats_path: Path,
    *,
    output_path: Path,
    study_id: str,
    frozen_at: datetime,
    start: datetime,
    end: datetime,
    user_agent: str,
    maximum_targets: int = 50,
    minimum_total_observations: int = 500,
    external_validation_fraction: float = 0.2,
    random_seed: int = 7538,
    sampling_intervals: Sequence[tuple[datetime, datetime]] = (),
) -> Mapping[str, object]:
    """Freeze a diverse cohort using metadata and volume, never success rate."""

    frozen_at, start, end = map(as_utc, (frozen_at, start, end))
    if not 10 <= maximum_targets <= 200:
        raise ValueError("maximum_targets must be in [10, 200]")
    if minimum_total_observations <= 0:
        raise ValueError("minimum_total_observations must be positive")
    if not 0.1 <= external_validation_fraction < 0.5:
        raise ValueError("external validation fraction must be in [0.1, 0.5)")
    if not start < end <= frozen_at:
        raise ValueError("cohort time boundaries are invalid")
    normalized_sampling_intervals = tuple(
        (as_utc(interval_start), as_utc(interval_end))
        for interval_start, interval_end in sampling_intervals
    )
    if any(
        interval_end <= interval_start
        or interval_start < start
        or interval_end > end
        for interval_start, interval_end in normalized_sampling_intervals
    ):
        raise ValueError("sampling intervals must be positive and inside the cohort boundary")
    if tuple(sorted(normalized_sampling_intervals)) != normalized_sampling_intervals:
        raise ValueError("sampling intervals must be chronological")
    if any(
        current_start < previous_end
        for (_, previous_end), (current_start, _) in zip(
            normalized_sampling_intervals,
            normalized_sampling_intervals[1:],
        )
    ):
        raise ValueError("sampling intervals must not overlap")
    inventory = json.loads(transmitter_inventory_path.read_text(encoding="utf-8"))
    stats_payload = json.loads(transmitter_stats_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, list) or not isinstance(stats_payload, list):
        raise DatasetIntegrityError("transmitter inventory and stats must be arrays")
    stats: dict[str, Mapping[str, object]] = {}
    for item in stats_payload:
        if isinstance(item, Mapping) and isinstance(item.get("uuid"), str):
            values = item.get("stats")
            if isinstance(values, Mapping):
                stats[str(item["uuid"])] = values

    best_by_satellite: dict[int, dict[str, object]] = {}
    exclusion_counts: dict[str, int] = {}

    def exclude(reason: str) -> None:
        exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

    for raw in inventory:
        if not isinstance(raw, Mapping):
            exclude("malformed_inventory_row")
            continue
        uuid = raw.get("uuid")
        norad_id = _positive_int(raw.get("norad_cat_id"))
        mode = str(raw.get("mode") or "").strip()
        if not isinstance(uuid, str) or not uuid.strip() or norad_id is None:
            exclude("missing_identity")
            continue
        # SatNOGS uses 98xxx pseudo catalog numbers for objects awaiting an
        # authoritative catalog assignment.  They cannot be refreshed through
        # the operational CelesTrak TLE path, so they are outside this study.
        if norad_id >= 98_000:
            exclude("temporary_satnogs_catalog_number")
            continue
        if raw.get("status") != "active" or raw.get("alive") is not True:
            exclude("not_active")
            continue
        if raw.get("unconfirmed") is True:
            exclude("unconfirmed")
            continue
        if not mode or mode.upper() in NON_PACKET_MODES:
            exclude("non_packet_or_missing_mode")
            continue
        frequency_raw = raw.get("downlink_low") or raw.get("downlink_high")
        try:
            frequency = float(frequency_raw) if frequency_raw is not None else None
        except (TypeError, ValueError):
            frequency = None
        values = stats.get(uuid)
        if values is None:
            exclude("missing_network_stats")
            continue
        total = _positive_int(values.get("total_count"))
        if total is None or total < minimum_total_observations:
            exclude("below_volume_threshold")
            continue
        candidate = {
            "norad_id": norad_id,
            "selection_transmitter_uuid": uuid.strip(),
            "selection_mode": mode,
            "selection_total_count": total,
            # Counts are retained for audit but never enter selection/ranking.
            "selection_good_count": int(values.get("good_count") or 0),
            "selection_bad_count": int(values.get("bad_count") or 0),
            "selection_band": _band(frequency),
            "selection_mode_family": _mode_family(mode),
        }
        previous = best_by_satellite.get(norad_id)
        rank = (-total, uuid)
        if previous is None or rank < (
            -int(previous["selection_total_count"]),
            str(previous["selection_transmitter_uuid"]),
        ):
            best_by_satellite[norad_id] = candidate

    strata: dict[tuple[str, str], list[dict[str, object]]] = {}
    for candidate in best_by_satellite.values():
        key = (
            str(candidate["selection_band"]),
            str(candidate["selection_mode_family"]),
        )
        strata.setdefault(key, []).append(candidate)
    for candidates in strata.values():
        candidates.sort(
            key=lambda item: (
                -int(item["selection_total_count"]),
                int(item["norad_id"]),
            )
        )
    selected: list[dict[str, object]] = []
    active = sorted(strata)
    while active and len(selected) < maximum_targets:
        remaining: list[tuple[str, str]] = []
        for key in active:
            if strata[key] and len(selected) < maximum_targets:
                selected.append(strata[key].pop(0))
            if strata[key]:
                remaining.append(key)
        active = remaining
    if len(selected) < 10:
        raise DatasetIntegrityError("eligible expanded cohort has fewer than ten satellites")
    selected.sort(key=lambda item: int(item["norad_id"]))
    holdout_count = max(2, round(len(selected) * external_validation_fraction))
    holdout_rank = sorted(
        (int(item["norad_id"]) for item in selected),
        key=lambda value: hashlib.sha256(f"{random_seed}:{value}".encode()).hexdigest(),
    )
    held_out = sorted(holdout_rank[:holdout_count])
    targets = [
        {
            key: item[key]
            for key in (
                "norad_id",
                "selection_transmitter_uuid",
                "selection_mode",
                "selection_total_count",
                "selection_good_count",
                "selection_bad_count",
            )
        }
        for item in selected
    ]
    payload: dict[str, object] = {
        "schema_version": "observation-planning-publication-config-v1",
        "study_id": study_id,
        "frozen_at": frozen_at.isoformat().replace("+00:00", "Z"),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "selection_rule": (
            "Active, alive, confirmed packet transmitters with at least the frozen total-volume "
            "threshold; one highest-volume transmitter per satellite; round-robin sampling over "
            "frequency-band x modulation-family strata. Good/bad counts were not used for "
            "eligibility, ranking, stratification or holdout assignment."
        ),
        "selection_parameters": {
            "maximum_targets": maximum_targets,
            "minimum_total_observations": minimum_total_observations,
            "external_validation_fraction": external_validation_fraction,
            "strata": "frequency_band_x_mode_family",
            "random_seed": random_seed,
        },
        "selection_snapshots": [
            {
                "path": str(transmitter_inventory_path),
                "sha256": hashlib.sha256(
                    transmitter_inventory_path.read_bytes()
                ).hexdigest(),
            },
            {
                "path": str(transmitter_stats_path),
                "sha256": hashlib.sha256(transmitter_stats_path.read_bytes()).hexdigest(),
            },
        ],
        "selection_exclusion_counts": dict(sorted(exclusion_counts.items())),
        "selected_strata": {
            f"{band}/{mode}": sum(
                item["selection_band"] == band
                and item["selection_mode_family"] == mode
                for item in selected
            )
            for band, mode in sorted(
                {
                    (str(item["selection_band"]), str(item["selection_mode_family"]))
                    for item in selected
                }
            )
        },
        "targets": targets,
        "external_validation_norad_ids": held_out,
        "external_station_holdout_fraction": external_validation_fraction,
        "minimum_external_test_rows": 30,
        "geometry_step_seconds": 30,
        "minimum_chunk_hours": 6,
        "max_response_bytes": 20_000_000,
        "random_seed": random_seed,
        "bootstrap_replicates": 2_000,
        "temporal_test_fraction": 0.2,
        "regularization_grid": [0.01, 0.1, 1.0, 10.0],
        "user_agent": user_agent,
    }
    if normalized_sampling_intervals:
        payload["sampling_rule"] = (
            "Outcome-blind common UTC intervals applied identically to every selected "
            "satellite; all transmitters and all observation labels inside each interval."
        )
        payload["sampling_intervals"] = [
            {
                "start": interval_start.isoformat().replace("+00:00", "Z"),
                "end": interval_end.isoformat().replace("+00:00", "Z"),
            }
            for interval_start, interval_end in normalized_sampling_intervals
        ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload
