"""End-to-end artifact builders for the frozen observation-planning study."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Mapping, Sequence

from telemetry_yield.satnogs import SATNOGS_NETWORK_API, SatNOGSClient

from .dataset import (
    DatasetIntegrityError,
    NormalizedObservation,
    build_normalized_dataset,
    read_normalized_jsonl,
    write_normalized_jsonl,
)
from .evaluation import (
    ComposedPredictionRecord,
    PredictionRecord,
    deterministic_group_holdout,
    evaluate_covariate_ablation,
    evaluate_composed_reception_group_holdout,
    evaluate_composed_reception_temporal_holdout,
    evaluate_leave_one_group_out,
    evaluate_predeclared_group_holdout,
    evaluate_temporal_holdout,
    paired_cluster_bootstrap_brier,
    probability_metrics,
    reception_success_outcome,
)
from .satnogs_dataset import (
    PublicationDatasetConfig,
    fetch_publication_snapshot,
    load_snapshot_observations,
    publication_config_identity,
)
from .replay import paired_day_bootstrap_replay, scheduling_replay
from .tle_audit import (
    DEFAULT_TLE_MATCH_TOLERANCE,
    audit_successive_tle_drift,
    summarize_tle_drift,
)
from .scheduler_benchmark import build_scheduler_robustness_plan, run_scheduler_benchmark
from .monte_carlo import (
    CorrelatedRiskScenario,
    simulate_plan_yield,
    simulate_plan_yield_correlated,
)
from .reporting import write_evaluation_figures
from .store import plan_document
from .v4_protocol import (
    V4_MIN_CONDITIONAL_DECODE_SATELLITES,
    V4_MIN_EXTERNAL_GROUPS,
    V4_MIN_EXTERNAL_TEST_ROWS,
    V4_MIN_SIGNAL_SATELLITES,
    V4_PREDECLARED_TEMPORAL_CUTOFF,
    V4_STUDY_ID,
    V4_TARGET_COUNT,
    validate_v4_publication_config_contract,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _publication_count_requirements(
    rows: Sequence[NormalizedObservation],
    *,
    config: PublicationDatasetConfig | None = None,
) -> dict[str, bool]:
    signal_rows = [row for row in rows if row.signal_present is not None]
    decode_rows = [
        row for row in rows if row.decode_success_given_signal is not None
    ]
    requirements = {
        "known_signal_rows_at_least_1000": len(signal_rows) >= 1000,
        "signal_satellites_at_least_5": len({row.norad_id for row in signal_rows})
        >= 5,
        "signal_stations_at_least_5": len({row.station_id for row in signal_rows})
        >= 5,
        "conditional_decode_rows_at_least_300": len(decode_rows) >= 300,
        "conditional_decode_has_both_classes": {
            row.decode_success_given_signal for row in decode_rows
        }
        == {0, 1},
        "conditional_decode_satellites_at_least_5": len(
            {row.norad_id for row in decode_rows}
        )
        >= 5,
        "conditional_decode_stations_at_least_5": len(
            {row.station_id for row in decode_rows}
        )
        >= 5,
    }
    if config is None or config.study_id != V4_STUDY_ID:
        return requirements

    cutoff = config.sampling_intervals[-1].start if config.sampling_intervals else None
    target_ids = {target.norad_id for target in config.targets}
    external_satellites = set(config.external_validation_norad_ids)
    observed_target_ids = {row.norad_id for row in rows}
    station_holdout = set(
        deterministic_group_holdout(
            (row.station_id for row in rows),
            fraction=config.external_station_holdout_fraction,
            seed=config.random_seed,
        )
    )
    requirements.update(
        {
            "v4_target_count_is_50": len(target_ids) == V4_TARGET_COUNT,
            "v4_all_target_ids_represented": observed_target_ids == target_ids,
            "v4_signal_satellites_at_least_40": len(
                {row.norad_id for row in signal_rows}
            )
            >= V4_MIN_SIGNAL_SATELLITES,
            "v4_conditional_decode_satellites_at_least_25": len(
                {row.norad_id for row in decode_rows}
            )
            >= V4_MIN_CONDITIONAL_DECODE_SATELLITES,
            "v4_predeclared_temporal_cutoff_matches_contract": (
                cutoff is not None
                and cutoff.isoformat().replace("+00:00", "Z")
                == V4_PREDECLARED_TEMPORAL_CUTOFF
            ),
        }
    )

    def has_both_classes(selected: Sequence[NormalizedObservation], task: str) -> bool:
        return {getattr(row, task) for row in selected} == {0, 1}

    for task in ("signal_present", "decode_success_given_signal"):
        eligible = [row for row in rows if getattr(row, task) in {0, 1}]
        training = [
            row for row in eligible if cutoff is not None and row.end < cutoff
        ]
        test = [
            row for row in eligible if cutoff is not None and row.start >= cutoff
        ]
        external_satellite_training = [
            row for row in training if row.norad_id not in external_satellites
        ]
        external_satellite_test = [
            row for row in test if row.norad_id in external_satellites
        ]
        external_station_training = [
            row for row in training if row.station_id not in station_holdout
        ]
        external_station_test = [
            row for row in test if row.station_id in station_holdout
        ]
        requirements.update(
            {
                f"v4_{task}_temporal_training_is_estimable": (
                    len(training) >= 10 and has_both_classes(training, task)
                ),
                f"v4_{task}_temporal_test_is_informative": (
                    len(test) >= V4_MIN_EXTERNAL_TEST_ROWS
                    and has_both_classes(test, task)
                ),
                f"v4_{task}_external_satellite_training_is_estimable": (
                    len(external_satellite_training) >= 10
                    and has_both_classes(external_satellite_training, task)
                ),
                f"v4_{task}_external_satellite_test_is_informative": (
                    len(external_satellite_test) >= V4_MIN_EXTERNAL_TEST_ROWS
                    and len(
                        {row.norad_id for row in external_satellite_test}
                    )
                    >= V4_MIN_EXTERNAL_GROUPS
                    and has_both_classes(external_satellite_test, task)
                ),
                f"v4_{task}_external_station_training_is_estimable": (
                    len(external_station_training) >= 10
                    and has_both_classes(external_station_training, task)
                ),
                f"v4_{task}_external_station_test_is_informative": (
                    len(external_station_test) >= V4_MIN_EXTERNAL_TEST_ROWS
                    and len({row.station_id for row in external_station_test})
                    >= V4_MIN_EXTERNAL_GROUPS
                    and has_both_classes(external_station_test, task)
                ),
            }
        )

    reception_rows = [
        row for row in rows if reception_success_outcome(row) in {0, 1}
    ]
    reception_test = [
        row
        for row in reception_rows
        if cutoff is not None and row.start >= cutoff
    ]
    reception_external_satellite_test = [
        row for row in reception_test if row.norad_id in external_satellites
    ]
    reception_external_station_test = [
        row for row in reception_test if row.station_id in station_holdout
    ]
    requirements.update(
        {
            "v4_reception_success_temporal_test_is_informative": (
                len(reception_test) >= V4_MIN_EXTERNAL_TEST_ROWS
                and {
                    reception_success_outcome(row) for row in reception_test
                }
                == {0, 1}
            ),
            "v4_reception_success_external_satellite_test_is_informative": (
                len(reception_external_satellite_test)
                >= V4_MIN_EXTERNAL_TEST_ROWS
                and len(
                    {
                        row.norad_id
                        for row in reception_external_satellite_test
                    }
                )
                >= V4_MIN_EXTERNAL_GROUPS
                and {
                    reception_success_outcome(row)
                    for row in reception_external_satellite_test
                }
                == {0, 1}
            ),
            "v4_reception_success_external_station_test_is_informative": (
                len(reception_external_station_test)
                >= V4_MIN_EXTERNAL_TEST_ROWS
                and len(
                    {row.station_id for row in reception_external_station_test}
                )
                >= V4_MIN_EXTERNAL_GROUPS
                and {
                    reception_success_outcome(row)
                    for row in reception_external_station_test
                }
                == {0, 1}
            ),
        }
    )
    return requirements


def acquire_frozen_cohort(
    config_path: Path,
    *,
    raw_dir: Path,
    manifest_path: Path,
    cache_dir: Path,
    shared_rate_limit_path: Path | None = None,
    api_token: str | None = None,
) -> Mapping[str, object]:
    config = PublicationDatasetConfig.load(config_path)
    if config.study_id == V4_STUDY_ID:
        validate_v4_publication_config_contract(config_path)
    # The raw page plus its sidecar are already the resumable, hash-bound
    # publication cache.  Keeping SatNOGSClient's second base64/JSON copy for
    # every cursor page can exhaust a research host during a multi-day cohort
    # fetch.  Retain the argument for CLI compatibility with earlier runs, but
    # deliberately avoid the redundant HTTP cache for this acquisition path.
    _ = cache_dir
    minimum_request_interval_seconds = 15.25 if api_token is not None else 61.0
    client = SatNOGSClient(
        user_agent=config.user_agent,
        api_token=api_token,
        cache_dir=None,
        max_concurrency=1,
        max_retries=3,
        timeout_seconds=90,
        max_response_bytes=config.max_response_bytes,
        shared_rate_limit_path=shared_rate_limit_path,
        shared_minimum_interval_seconds=(
            minimum_request_interval_seconds
            if shared_rate_limit_path is not None
            else 0.0
        ),
    )
    manifest = dict(fetch_publication_snapshot(
        client,
        config,
        raw_dir=raw_dir,
        manifest_path=manifest_path,
        minimum_request_interval_seconds=minimum_request_interval_seconds,
    ))
    manifest["config_path"] = str(config_path)
    manifest["config_file_sha256"] = _sha256_file(config_path)
    _write_json(manifest_path, manifest)
    return manifest


def build_publication_dataset(
    config_path: Path,
    snapshot_manifest_path: Path,
    *,
    dataset_path: Path,
    dataset_manifest_path: Path,
    raw_dir: Path | None = None,
    covariate_archive_path: Path | None = None,
) -> Mapping[str, object]:
    config = PublicationDatasetConfig.load(config_path)
    if config.study_id == V4_STUDY_ID:
        validate_v4_publication_config_contract(config_path)
    snapshot_manifest = json.loads(snapshot_manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(snapshot_manifest, Mapping)
        or snapshot_manifest.get("study_id") != config.study_id
        or snapshot_manifest.get("source") != SATNOGS_NETWORK_API
        or snapshot_manifest.get("config_identity_sha256")
        != publication_config_identity(config)
    ):
        raise DatasetIntegrityError(
            "snapshot manifest does not belong to the supplied publication config"
        )
    observations = load_snapshot_observations(snapshot_manifest_path, raw_dir=raw_dir)
    retrieved_at_text = snapshot_manifest.get("created_at")
    if not isinstance(retrieved_at_text, str):
        raise DatasetIntegrityError("snapshot manifest is missing created_at")
    from .dataset import parse_api_datetime

    page_times = sorted(
        parse_api_datetime(entry["snapshot_recorded_at"], name="snapshot_recorded_at")
        for entry in snapshot_manifest.get("responses", [])
        if isinstance(entry, Mapping)
        and isinstance(entry.get("snapshot_recorded_at"), str)
    )

    result = build_normalized_dataset(
        observations,
        retrieved_at=parse_api_datetime(retrieved_at_text, name="created_at"),
        geometry_step_seconds=config.geometry_step_seconds,
    )
    rows = result.rows
    covariate_source_summary: dict[str, object] | None = None
    if covariate_archive_path is not None:
        from .covariates import enrich_rows, read_covariate_archive

        weather, kp, hardware = read_covariate_archive(covariate_archive_path)
        rows = enrich_rows(rows, weather=weather, kp=kp, hardware=hardware)
        def source_counts(items) -> dict[str, int]:
            counts: dict[str, int] = {}
            for item in items:
                counts[item.source] = counts.get(item.source, 0) + 1
            return dict(sorted(counts.items()))

        covariate_source_summary = {
            "weather_records": len(weather),
            "weather_sources": source_counts(weather),
            "space_weather_records": len(kp),
            "space_weather_sources": source_counts(kp),
            "hardware_records": len(hardware),
            "hardware_sources": source_counts(hardware),
        }
    write_normalized_jsonl(dataset_path, rows)
    signal_rows = [row for row in rows if row.signal_present is not None]
    decode_rows = [row for row in rows if row.decode_success_given_signal is not None]
    signal_satellites = len({row.norad_id for row in signal_rows})
    signal_stations = len({row.station_id for row in signal_rows})
    decode_satellites = len({row.norad_id for row in decode_rows})
    decode_stations = len({row.station_id for row in decode_rows})
    selected_transmitter_by_satellite = {
        target.norad_id: target.selection_transmitter_uuid
        for target in config.targets
    }
    selected_transmitter_rows = sum(
        row.transmitter_uuid == selected_transmitter_by_satellite.get(row.norad_id)
        for row in rows
    )
    per_target_counts: list[dict[str, object]] = []
    for target in config.targets:
        target_rows = [row for row in rows if row.norad_id == target.norad_id]
        target_signal_rows = [
            row for row in target_rows if row.signal_present is not None
        ]
        target_decode_rows = [
            row
            for row in target_rows
            if row.decode_success_given_signal is not None
        ]
        per_target_counts.append(
            {
                "norad_id": target.norad_id,
                "normalized_rows": len(target_rows),
                "known_signal_rows": len(target_signal_rows),
                "known_signal_positive_rows": sum(
                    row.signal_present == 1 for row in target_signal_rows
                ),
                "conditional_decode_rows": len(target_decode_rows),
                "conditional_decode_positive_rows": sum(
                    row.decode_success_given_signal == 1
                    for row in target_decode_rows
                ),
                "observed_transmitter_count": len(
                    {row.transmitter_uuid for row in target_rows}
                ),
                "rows_matching_selection_transmitter": sum(
                    row.transmitter_uuid == target.selection_transmitter_uuid
                    for row in target_rows
                ),
            }
        )
    def packet_capable(row) -> bool:
        return (
            row.transmitter_mode is not None
            and row.transmitter_mode.upper() not in {
                "CW",
                "FM",
                "AM",
                "USB",
                "LSB",
            }
        )

    weather_value_fields = (
        "weather_air_temperature_c",
        "weather_relative_humidity_percent",
        "weather_surface_pressure_kpa",
        "weather_wind_speed_m_s",
        "weather_precipitation_corrected",
    )
    requirements = _publication_count_requirements(rows, config=config)
    manifest: dict[str, object] = {
        "schema_version": "observation-planning-normalized-dataset-manifest-v1",
        "study_id": config.study_id,
        "config_path": str(config_path),
        "config_sha256": _sha256_file(config_path),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_snapshot_manifest": str(snapshot_manifest_path),
        "source_snapshot_manifest_sha256": _sha256_file(snapshot_manifest_path),
        "source_snapshot_page_time_start": (
            page_times[0].isoformat().replace("+00:00", "Z") if page_times else None
        ),
        "source_snapshot_page_time_end": (
            page_times[-1].isoformat().replace("+00:00", "Z") if page_times else None
        ),
        "source_snapshot_acquisition_span_seconds": (
            (page_times[-1] - page_times[0]).total_seconds()
            if len(page_times) >= 2
            else 0.0
            if page_times
            else None
        ),
        "dataset_path": str(dataset_path),
        "dataset_sha256": _sha256_file(dataset_path),
        "dataset_bytes": dataset_path.stat().st_size,
        "input_rows": result.input_count,
        "normalized_rows": len(rows),
        "normalized_satellites": len({row.norad_id for row in rows}),
        "per_target_counts": per_target_counts,
        "sampling_intervals": (
            [
                {
                    "start": interval.start.isoformat().replace("+00:00", "Z"),
                    "end": interval.end.isoformat().replace("+00:00", "Z"),
                }
                for interval in config.sampling_intervals
            ]
            if config.sampling_intervals
            else [
                {
                    "start": config.start.isoformat().replace("+00:00", "Z"),
                    "end": config.end.isoformat().replace("+00:00", "Z"),
                }
            ]
        ),
        "sampled_days_per_target": (
            sum(
                (interval.end - interval.start).total_seconds()
                for interval in config.sampling_intervals
            )
            if config.sampling_intervals
            else (config.end - config.start).total_seconds()
        )
        / 86_400,
        "observed_transmitter_count": len(
            {row.transmitter_uuid for row in rows}
        ),
        "normalized_rows_matching_selection_transmitter": selected_transmitter_rows,
        "normalized_rows_from_other_transmitters": len(rows)
        - selected_transmitter_rows,
        "duplicate_rows": result.duplicate_count,
        "exclusion_counts": result.exclusion_counts,
        "known_signal_rows": len(signal_rows),
        "known_signal_positive_rows": sum(row.signal_present == 1 for row in signal_rows),
        "unknown_signal_rows_excluded_from_task": sum(
            row.signal_present is None for row in rows
        ),
        "signal_satellites": signal_satellites,
        "signal_stations": signal_stations,
        "conditional_decode_rows": len(decode_rows),
        "conditional_decode_positive_rows": sum(
            row.decode_success_given_signal == 1 for row in decode_rows
        ),
        "conditional_decode_satellites": decode_satellites,
        "conditional_decode_stations": decode_stations,
        "conditional_decode_task_exclusions": {
            "mode_missing_or_non_packet": sum(
                not packet_capable(row) for row in rows
            ),
            "packet_without_confirmed_signal_or_positive_artifact": sum(
                packet_capable(row)
                and row.decode_success_given_signal is None
                for row in rows
            ),
            "positive_artifact_without_waterfall_label": sum(
                row.decode_success_given_signal == 1
                and row.signal_present is None
                for row in rows
            ),
        },
        "publication_requirements": requirements,
        "publication_count_gate": "pass" if all(requirements.values()) else "fail",
        "label_contract": {
            "signal_present": "with-signal=1; without-signal=0; unknown excluded",
            "decode_success_given_signal": (
                "packet mode: non-empty demoddata=1 (artifact proves signal); "
                "explicit empty demoddata=0 only when waterfall is with-signal"
            ),
            "status_good_used_as_label": False,
        },
        "feature_time_boundary": (
            "probability features are observation-cached or available no later than start; "
            "capture-time client coordinates are used when present, while current API "
            "station coordinates are flagged and excluded from historical weather joins"
        ),
        "geometry_source": (
            "observation-cached schedule elevation/azimuth for model features; "
            "embedded-TLE SGP4 with capture-time client coordinates when present, otherwise "
            "flagged current API station coordinates, for range/Doppler diagnostics"
        ),
        "geometry_step_seconds": config.geometry_step_seconds,
        "feature_availability": {
            "frequency_hz_present": sum(row.frequency_hz is not None for row in rows),
            "transmitter_mode_present": sum(row.transmitter_mode is not None for row in rows),
            "transmitter_baud_present": sum(row.transmitter_baud is not None for row in rows),
            "observation_cached_schedule_geometry_present": len(rows),
            "historical_station_location_captured_present": sum(
                row.station_location_source == "satnogs-client-metadata"
                and row.station_location_recorded_at is not None
                and row.station_location_recorded_at <= row.start
                for row in rows
            ),
            "current_api_station_location_fallback_present": sum(
                row.station_location_source == "satnogs-api-current-station"
                for row in rows
            ),
            "client_metadata_parse_status_counts": {
                status: sum(row.client_metadata_parse_status == status for row in rows)
                for status in sorted(
                    {
                        str(row.client_metadata_parse_status)
                        for row in rows
                        if row.client_metadata_parse_status is not None
                    }
                )
            },
            "historical_receiver_configuration_present": sum(
                row.captured_receiver_configuration_source
                == "satnogs-client-metadata"
                and row.captured_receiver_configuration_recorded_at is not None
                and row.captured_receiver_configuration_recorded_at <= row.start
                for row in rows
            ),
            "historical_receiver_configuration_value_counts": {
                name: sum(getattr(row, name) is not None for row in rows)
                for name in (
                    "captured_receiver_radio_name",
                    "captured_receiver_radio_version",
                    "captured_receiver_driver",
                    "captured_receiver_gain_mode",
                    "captured_receiver_rf_gain_db",
                    "captured_receiver_antenna_port",
                    "captured_receiver_sample_rate_hz",
                    "captured_receiver_ppm_error",
                    "captured_receiver_bandwidth_hz",
                )
            },
            "historical_antenna_configuration_present": sum(
                row.antenna_configuration_id is not None for row in rows
            ),
            "historical_terrestrial_weather_snapshot_present": sum(
                row.weather_valid_at is not None for row in rows
            ),
            "historical_terrestrial_weather_any_value_present": sum(
                any(getattr(row, name) is not None for name in weather_value_fields)
                for row in rows
            ),
            "historical_terrestrial_weather_complete_vector_present": sum(
                all(getattr(row, name) is not None for name in weather_value_fields)
                for row in rows
            ),
            "historical_terrestrial_weather_value_counts": {
                name: sum(getattr(row, name) is not None for row in rows)
                for name in weather_value_fields
            },
            "historical_space_weather_snapshot_present": sum(
                row.space_weather_valid_at is not None for row in rows
            ),
        },
        "feature_availability_boundary": (
            "Observation-cached schedule geometry remains the probability input. Current API station "
            "coordinates and their coordinate-derived range/Doppler remain excluded. Historical "
            "terrestrial weather is joined only for capture-time client coordinates; Kp is joined by "
            "its valid interval when a hashed covariate archive is supplied. Captured receiver settings "
            "are retained for coverage/audit and are not used as same-observation model inputs. "
            "Antenna configuration is joined only when its known_at timestamp is no later than the "
            "observation; otherwise it remains missing."
        ),
        "data_minimization": {
            "normalized_operator_identity_present": False,
            "normalized_payload_or_media_url_present": False,
            "normalized_client_metadata_present": False,
            "normalized_allowlisted_client_metadata_derivatives_present": True,
            "discarded_client_metadata_fields_include_paths_and_device_identifiers": True,
            "raw_public_pages_may_contain_these_fields": True,
        },
    }
    if covariate_archive_path is not None:
        manifest["covariate_archive_path"] = str(covariate_archive_path)
        manifest["covariate_archive_sha256"] = _sha256_file(
            covariate_archive_path
        )
        manifest["covariate_source_summary"] = covariate_source_summary
    _write_json(dataset_manifest_path, manifest)
    return manifest


def _records_metrics(
    records: Sequence[PredictionRecord | ComposedPredictionRecord],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for model in sorted({record.model for record in records}):
        selected = [record for record in records if record.model == model]
        pooled = probability_metrics(
            [record.outcome for record in selected],
            [record.probability for record in selected],
        ).as_dict()
        fold_aurocs: list[float] = []
        for fold in sorted({record.fold for record in selected}):
            fold_rows = [record for record in selected if record.fold == fold]
            metric = probability_metrics(
                [record.outcome for record in fold_rows],
                [record.probability for record in fold_rows],
            )
            if metric.auroc is not None:
                fold_aurocs.append(metric.auroc)
        result[model] = {
            "pooled": pooled,
            "fold_auroc_mean": (
                sum(fold_aurocs) / len(fold_aurocs) if fold_aurocs else None
            ),
            "fold_auroc_eligible_count": len(fold_aurocs),
            "fold_count": len({record.fold for record in selected}),
        }
    return result


def _write_predictions(
    path: Path,
    records: Sequence[PredictionRecord | ComposedPredictionRecord],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")


def _write_scheduling_replay_report(
    *,
    rows: Sequence[NormalizedObservation],
    predictions: Sequence[PredictionRecord | ComposedPredictionRecord],
    config: PublicationDatasetConfig,
    output_dir: Path,
    artifact_prefix: str,
    outcome_task: Literal["signal_present", "reception_success"],
) -> Mapping[str, object]:
    replay = scheduling_replay(
        rows,
        predictions,
        outcome_task=outcome_task,
    )
    replay_path = output_dir / f"{artifact_prefix}-assignments.jsonl"
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    with replay_path.open("w", encoding="utf-8", newline="\n") as handle:
        for assignment in replay.assignments:
            handle.write(
                json.dumps(
                    assignment.as_dict(), sort_keys=True, separators=(",", ":")
                )
            )
            handle.write("\n")
    replay_bootstrap: dict[str, object] = {}
    for baseline in ("chronological", "maximum_elevation", "longest_duration"):
        bootstrap_result = paired_day_bootstrap_replay(
            replay.assignments,
            baseline=baseline,
            replicates=config.bootstrap_replicates,
            seed=config.random_seed,
        )
        bootstrap_summary = {
            key: value for key, value in bootstrap_result.items() if key != "replicates"
        }
        bootstrap_path = output_dir / f"{artifact_prefix}-{baseline}-bootstrap.json"
        _write_json(
            bootstrap_path,
            {
                "schema_version": "scheduling-day-bootstrap-v1",
                **bootstrap_result,
            },
        )
        bootstrap_summary["replicate_count"] = len(
            bootstrap_result["replicates"]  # type: ignore[arg-type]
        )
        bootstrap_summary["artifact_path"] = str(bootstrap_path)
        bootstrap_summary["artifact_sha256"] = hashlib.sha256(
            bootstrap_path.read_bytes()
        ).hexdigest()
        replay_bootstrap[baseline] = bootstrap_summary
    return {
        "sample_unit": "one SatNOGS observation",
        "outcome_task": outcome_task,
        "probability_model": "full_logit",
        "candidate_count": replay.candidate_count,
        "natural_receiver_conflict_pairs": replay.natural_receiver_conflict_pairs,
        "informative_for_scheduler_comparison": replay.informative_for_scheduler_comparison,
        "interpretation_guard": (
            "Network observations are already scheduled; if no natural same-station "
            "conflicts remain, the replay is a degeneracy check and cannot estimate "
            "scheduler gain"
        ),
        "metrics": {name: metric.as_dict() for name, metric in replay.metrics.items()},
        "paired_day_bootstrap_vs_probability_milp": replay_bootstrap,
        "assignments_path": str(replay_path),
        "assignments_sha256": hashlib.sha256(replay_path.read_bytes()).hexdigest(),
    }


def _comparison_bootstraps(
    predictions: Sequence[PredictionRecord],
    *,
    cluster: str,
    config: PublicationDatasetConfig,
    output_dir: Path,
    task: str,
    split: str,
) -> dict[str, object]:
    """Write the frozen paired Brier comparisons, including every draw."""

    if not predictions:
        return {
            "status": "insufficient-data",
            "reason": "evaluation split emitted no aligned predictions",
        }
    summary: dict[str, object] = {}
    for model in ("group_rate", "geometry_logit", "full_logit"):
        result = paired_cluster_bootstrap_brier(
            predictions,
            model=model,
            cluster=cluster,  # type: ignore[arg-type]
            replicates=config.bootstrap_replicates,
            seed=config.random_seed,
        )
        values = {
            key: value for key, value in asdict(result).items() if key != "replicates"
        }
        values["passes_predeclared_useful_effect"] = (
            result.cluster_count >= 5
            and result.relative_brier_reduction >= 0.05
            and result.upper_95 < 0
        )
        values["replicate_count"] = len(result.replicates)
        summary[model] = values
        artifact_path = output_dir / f"{task}-{model}-{split}-bootstrap.json"
        _write_json(
            artifact_path,
            {
                "schema_version": "paired-cluster-bootstrap-v1",
                "cluster": cluster,
                **asdict(result),
            },
        )
        values["artifact_path"] = str(artifact_path)
        values["artifact_sha256"] = hashlib.sha256(
            artifact_path.read_bytes()
        ).hexdigest()
    return summary


def _ablation_bootstraps(
    predictions: Sequence[PredictionRecord],
    *,
    cluster: str,
    config: PublicationDatasetConfig,
    output_dir: Path,
    task: str,
    split: str,
) -> dict[str, object]:
    if not predictions:
        return {
            "status": "insufficient-data",
            "reason": "evaluation split emitted no aligned predictions",
        }
    summary: dict[str, object] = {}
    for model, baseline in (
        ("full_logit", "geometry_logit"),
        ("full_logit", "group_rate"),
    ):
        result = paired_cluster_bootstrap_brier(
            predictions,
            model=model,
            baseline=baseline,
            cluster=cluster,  # type: ignore[arg-type]
            replicates=config.bootstrap_replicates,
            seed=config.random_seed,
        )
        values = {
            key: value for key, value in asdict(result).items() if key != "replicates"
        }
        values["replicate_count"] = len(result.replicates)
        values["passes_predeclared_useful_effect"] = (
            result.cluster_count >= 5
            and result.relative_brier_reduction >= 0.05
            and result.upper_95 < 0
        )
        key = f"{model}_vs_{baseline}"
        artifact_path = output_dir / f"{task}-{key}-{split}-bootstrap.json"
        _write_json(
            artifact_path,
            {
                "schema_version": "paired-cluster-bootstrap-v1",
                "cluster": cluster,
                **asdict(result),
            },
        )
        values["artifact_path"] = str(artifact_path)
        values["artifact_sha256"] = hashlib.sha256(
            artifact_path.read_bytes()
        ).hexdigest()
        summary[key] = values
    return summary


def _covariate_bootstraps(
    predictions: Sequence[PredictionRecord],
    *,
    candidate_models: Sequence[str],
    cluster: str,
    config: PublicationDatasetConfig,
    output_dir: Path,
    task: str,
) -> dict[str, object]:
    """Compare optional covariate families to the operational model."""

    summary: dict[str, object] = {}
    for model in candidate_models:
        if model == "operational_logit":
            continue
        result = paired_cluster_bootstrap_brier(
            predictions,
            model=model,
            baseline="operational_logit",
            cluster=cluster,  # type: ignore[arg-type]
            replicates=config.bootstrap_replicates,
            seed=config.random_seed,
        )
        values = {
            key: value for key, value in asdict(result).items() if key != "replicates"
        }
        values["replicate_count"] = len(result.replicates)
        values["passes_outcome_blind_deployment_candidate_gate"] = (
            result.cluster_count >= 5
            and result.relative_brier_reduction >= 0.05
            and result.upper_95 < 0
        )
        artifact_path = output_dir / (
            f"{task}-{model}-vs-operational-{cluster}-covariate-bootstrap.json"
        )
        _write_json(
            artifact_path,
            {
                "schema_version": "paired-cluster-bootstrap-v1",
                "cluster": cluster,
                **asdict(result),
            },
        )
        values["artifact_path"] = str(artifact_path)
        values["artifact_sha256"] = hashlib.sha256(
            artifact_path.read_bytes()
        ).hexdigest()
        summary[model] = values
    return summary


def evaluate_publication_dataset(
    config_path: Path,
    dataset_path: Path,
    *,
    output_dir: Path,
    enforce_publication_count_gate: bool = True,
) -> Mapping[str, object]:
    config = PublicationDatasetConfig.load(config_path)
    if config.study_id == V4_STUDY_ID:
        validate_v4_publication_config_contract(config_path)
    rows = read_normalized_jsonl(dataset_path)
    requirements = _publication_count_requirements(rows, config=config)
    if enforce_publication_count_gate and not all(requirements.values()):
        failures = sorted(name for name, passed in requirements.items() if not passed)
        raise DatasetIntegrityError(
            "predeclared publication count gate failed; performance evaluation is "
            f"prohibited: {failures}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    temporal_cutoff = (
        config.sampling_intervals[-1].start
        if config.sampling_intervals
        else None
    )
    report: dict[str, object] = {
        "schema_version": "observation-planning-evaluation-v1",
        "study_id": config.study_id,
        "config_path": str(config_path),
        "config_sha256": _sha256_file(config_path),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "dataset_path": str(dataset_path),
        "dataset_sha256": _sha256_file(dataset_path),
        "random_seed": config.random_seed,
        "bootstrap_replicates": config.bootstrap_replicates,
        "publication_count_gate_enforced": enforce_publication_count_gate,
        "publication_count_requirements": requirements,
        "predeclared_temporal_cutoff": (
            temporal_cutoff.isoformat().replace("+00:00", "Z")
            if temporal_cutoff is not None
            else None
        ),
        "temporal_cutoff_rule": (
            "start of the final outcome-blind sampling interval"
            if temporal_cutoff is not None
            else "row-count fraction fallback for legacy configurations"
        ),
        "analysis_hierarchy": {
            "confirmatory": {
                "task": "signal_present",
                "split": "temporal",
                "model": "full_logit",
                "baseline": "global_rate",
                "metric": "brier",
            },
            "secondary_scope": (
                "conditional decode, composed end-to-end reception and its "
                "scheduler replay, transfer, station-cluster sensitivity, "
                "alternative models and ablations; raw intervals without a "
                "family-wise multiplicity claim"
            ),
        },
        "model_contract": {
            "global_rate": "Laplace-smoothed training-fold event rate",
            "group_rate": "empirical-Bayes satellite/station/pair shrinkage using training outcomes strictly earlier than the prediction",
            "geometry_logit": "L2 logistic regression on observation-cached schedule elevation/azimuth, duration and embedded-TLE age",
            "full_logit": "schedule geometry, transmitter metadata, categorical identities, fold-safe trailing histories and any time-aligned weather/space-weather/hardware covariates; retrieval-time station coordinates and coordinate-derived range/Doppler are excluded",
            "missing_numeric_policy": "training-fold median plus missingness indicator",
            "categorical_unknown_policy": "all-zero unseen category vector",
        },
        "tasks": {},
    }
    for task in ("signal_present", "decode_success_given_signal"):
        eligible = [row for row in rows if getattr(row, task) is not None]
        if len(eligible) < 20 or len({getattr(row, task) for row in eligible}) < 2:
            report["tasks"][task] = {  # type: ignore[index]
                "status": "insufficient-data",
                "eligible_rows": len(eligible),
            }
            continue
        temporal = evaluate_temporal_holdout(
            rows,
            task=task,  # type: ignore[arg-type]
            test_fraction=config.temporal_test_fraction,
            regularization_grid=config.regularization_grid,
            cutoff=temporal_cutoff,
        )
        satellite_predictions = evaluate_leave_one_group_out(
            rows,
            task=task,  # type: ignore[arg-type]
            group="norad_id",
            regularization_grid=config.regularization_grid,
            temporal_test_fraction=config.temporal_test_fraction,
            cutoff=temporal_cutoff,
        )
        station_predictions = evaluate_leave_one_group_out(
            rows,
            task=task,  # type: ignore[arg-type]
            group="station_id",
            regularization_grid=config.regularization_grid,
            temporal_test_fraction=config.temporal_test_fraction,
            cutoff=temporal_cutoff,
        )
        all_predictions = (
            temporal.predictions + satellite_predictions + station_predictions
        )
        predictions_path = output_dir / f"{task}-predictions.jsonl"
        _write_predictions(predictions_path, all_predictions)
        bootstrap = _comparison_bootstraps(
            temporal.predictions,
            cluster="norad_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="temporal",
        )
        temporal_station_bootstrap = _comparison_bootstraps(
            temporal.predictions,
            cluster="station_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="temporal-station-cluster-sensitivity",
        )
        satellite_bootstrap = _comparison_bootstraps(
            satellite_predictions,
            cluster="norad_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="loso-satellite",
        )
        station_bootstrap = _comparison_bootstraps(
            station_predictions,
            cluster="station_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="loso-station",
        )
        temporal_ablations = _ablation_bootstraps(
            temporal.predictions,
            cluster="norad_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="temporal",
        )
        temporal_station_ablations = _ablation_bootstraps(
            temporal.predictions,
            cluster="station_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="temporal-station-cluster-sensitivity",
        )
        satellite_ablations = _ablation_bootstraps(
            satellite_predictions,
            cluster="norad_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="loso-satellite",
        )
        station_ablations = _ablation_bootstraps(
            station_predictions,
            cluster="station_id",
            config=config,
            output_dir=output_dir,
            task=task,
            split="loso-station",
        )
        task_report = {
            "status": "evaluated",
            "eligible_rows": len(eligible),
            "temporal": {
                "train_count": temporal.train_count,
                "test_count": temporal.test_count,
                "train_start": temporal.train_start,
                "train_end": temporal.train_end,
                "test_start": temporal.test_start,
                "test_end": temporal.test_end,
                "selected_regularization": temporal.selected_regularization,
                "metrics": {
                    model: metric.as_dict() for model, metric in temporal.metrics.items()
                },
                "paired_bootstrap_vs_global_rate": bootstrap,
                "paired_ablation_bootstraps": temporal_ablations,
                "paired_bootstrap_station_cluster_sensitivity": temporal_station_bootstrap,
                "paired_ablation_station_cluster_sensitivity": temporal_station_ablations,
                "primary_full_logit_useful_effect": bootstrap["full_logit"][
                    "passes_predeclared_useful_effect"
                ],
                "station_cluster_sensitivity_full_logit_useful_effect": temporal_station_bootstrap[
                    "full_logit"
                ]["passes_predeclared_useful_effect"],
            },
            "loso_satellite": {
                "prediction_rows": len(satellite_predictions),
                "folds": len({record.fold for record in satellite_predictions}),
                "metrics": _records_metrics(satellite_predictions),
                "paired_bootstrap_vs_global_rate": satellite_bootstrap,
                "paired_ablation_bootstraps": satellite_ablations,
            },
            "loso_station": {
                "prediction_rows": len(station_predictions),
                "folds": len({record.fold for record in station_predictions}),
                "metrics": _records_metrics(station_predictions),
                "paired_bootstrap_vs_global_rate": station_bootstrap,
                "paired_ablation_bootstraps": station_ablations,
            },
            "predictions_path": str(predictions_path),
            "predictions_sha256": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
        }
        ablation = evaluate_covariate_ablation(
            rows,
            task=task,  # type: ignore[arg-type]
            test_fraction=config.temporal_test_fraction,
            regularization_grid=config.regularization_grid,
            cutoff=temporal_cutoff,
        )
        ablation_path = output_dir / f"{task}-covariate-ablation-predictions.jsonl"
        _write_predictions(ablation_path, ablation.predictions)
        covariate_satellite_bootstrap = _covariate_bootstraps(
            ablation.predictions,
            candidate_models=ablation.available_families,
            cluster="norad_id",
            config=config,
            output_dir=output_dir,
            task=task,
        )
        covariate_station_bootstrap = _covariate_bootstraps(
            ablation.predictions,
            candidate_models=ablation.available_families,
            cluster="station_id",
            config=config,
            output_dir=output_dir,
            task=task,
        )
        deployment_candidates = {
            model: bool(
                isinstance(covariate_satellite_bootstrap.get(model), Mapping)
                and isinstance(covariate_station_bootstrap.get(model), Mapping)
                and covariate_satellite_bootstrap[model].get(
                    "passes_outcome_blind_deployment_candidate_gate"
                )
                is True
                and covariate_station_bootstrap[model].get(
                    "passes_outcome_blind_deployment_candidate_gate"
                )
                is True
            )
            for model in ablation.available_families
            if model != "operational_logit"
        }
        task_report["covariate_ablation"] = {
            "cutoff": ablation.cutoff,
            "train_count": ablation.train_count,
            "test_count": ablation.test_count,
            "available_families": list(ablation.available_families),
            "unavailable_reasons": dict(ablation.unavailable_reasons),
            "selected_regularization": dict(ablation.selected_regularization),
            "metrics": {
                name: metric.as_dict() for name, metric in ablation.metrics.items()
            },
            "paired_bootstrap_vs_operational": {
                "satellite_cluster": covariate_satellite_bootstrap,
                "station_cluster_sensitivity": covariate_station_bootstrap,
            },
            "deployment_candidate_gate": {
                "rule": (
                    "at least five clusters, at least 5% relative Brier reduction, "
                    "and upper 95% paired cluster-bootstrap bound below zero on "
                    "both satellite and station clustering"
                ),
                "by_model": deployment_candidates,
                "meaning": (
                    "candidate status permits a separately frozen successor "
                    "campaign; it does not alter the active prospective protocol"
                ),
            },
            "predictions_path": str(ablation_path),
            "predictions_sha256": hashlib.sha256(ablation_path.read_bytes()).hexdigest(),
            "interpretation_guard": (
                "weather and hardware increments are estimable only when each covariate family "
                "has non-missing observations in both the training and untouched temporal folds"
            ),
        }

        external: dict[str, object] = {}
        external_specs: list[tuple[str, str, tuple[int, ...]]] = []
        if config.external_validation_norad_ids:
            external_specs.append(
                (
                    "satellite",
                    "norad_id",
                    config.external_validation_norad_ids,
                )
            )
        if config.external_station_holdout_fraction:
            station_holdout = deterministic_group_holdout(
                (row.station_id for row in rows),
                fraction=config.external_station_holdout_fraction,
                seed=config.random_seed,
            )
            external_specs.append(("station", "station_id", station_holdout))
        for label, group, held_out in external_specs:
            minimum_evaluated_groups = min(5, len(held_out))
            try:
                external_predictions = evaluate_predeclared_group_holdout(
                    rows,
                    task=task,  # type: ignore[arg-type]
                    group=group,  # type: ignore[arg-type]
                    held_out_values=held_out,
                    regularization_grid=config.regularization_grid,
                    temporal_test_fraction=config.temporal_test_fraction,
                    minimum_test_rows=config.minimum_external_test_rows,
                    minimum_test_groups=minimum_evaluated_groups,
                    cutoff=temporal_cutoff,
                )
            except DatasetIntegrityError as exc:
                external[label] = {
                    "status": "insufficient-data",
                    "held_out_group_ids": list(held_out),
                    "minimum_evaluated_groups": minimum_evaluated_groups,
                    "reason": str(exc),
                }
                continue
            external_path = output_dir / f"{task}-external-{label}-predictions.jsonl"
            _write_predictions(external_path, external_predictions)
            cluster = "norad_id" if label == "satellite" else "station_id"
            evaluated_group_ids = sorted(
                {
                    int(getattr(record, cluster))
                    for record in external_predictions
                }
            )
            test_observation_rows = len(
                {record.observation_id for record in external_predictions}
            )
            external[label] = {
                "status": "evaluated",
                "selection": "predeclared identifiers" if label == "satellite" else "seeded SHA-256 identifier split",
                "held_out_group_ids": list(held_out),
                "minimum_evaluated_groups": minimum_evaluated_groups,
                "evaluated_group_ids": evaluated_group_ids,
                "evaluated_group_count": len(evaluated_group_ids),
                "held_out_group_coverage_fraction": (
                    len(evaluated_group_ids) / len(held_out) if held_out else 0.0
                ),
                "prediction_rows": len(external_predictions),
                "test_observation_rows": test_observation_rows,
                "test_start": min(
                    record.start for record in external_predictions
                ),
                "metrics": _records_metrics(external_predictions),
                "paired_bootstrap_vs_global_rate": _comparison_bootstraps(
                    external_predictions,
                    cluster=cluster,
                    config=config,
                    output_dir=output_dir,
                    task=task,
                    split=f"external-{label}",
                ),
                "predictions_path": str(external_path),
                "predictions_sha256": hashlib.sha256(
                    external_path.read_bytes()
                ).hexdigest(),
                "group_disjoint": True,
                "future_only": True,
            }
        if external:
            task_report["external_validation"] = external
        if task == "signal_present":
            task_report["scheduling_replay"] = _write_scheduling_replay_report(
                rows=rows,
                predictions=temporal.predictions,
                config=config,
                output_dir=output_dir,
                artifact_prefix="scheduling-replay",
                outcome_task="signal_present",
            )
        report["tasks"][task] = task_report  # type: ignore[index]

    try:
        composed_temporal = evaluate_composed_reception_temporal_holdout(
            rows,
            test_fraction=config.temporal_test_fraction,
            regularization_grid=config.regularization_grid,
            cutoff=temporal_cutoff,
        )
    except DatasetIntegrityError as exc:
        report["composed_reception"] = {
            "status": "insufficient-data",
            "reason": str(exc),
            "definition": "P(signal) * P(decode artifact | signal)",
        }
    else:
        composed_path = output_dir / "reception_success-predictions.jsonl"
        _write_predictions(composed_path, composed_temporal.predictions)
        composed_report: dict[str, object] = {
            "status": "evaluated",
            "definition": "P(signal) * P(decode artifact | signal)",
            "endpoint": (
                "packet-mode observation with a reviewed signal label; success "
                "requires a non-empty demodulation artifact"
            ),
            "temporal": {
                "signal_train_count": composed_temporal.signal_train_count,
                "decode_train_count": composed_temporal.decode_train_count,
                "test_count": composed_temporal.test_count,
                "train_start": composed_temporal.train_start,
                "train_end": composed_temporal.train_end,
                "test_start": composed_temporal.test_start,
                "test_end": composed_temporal.test_end,
                "selected_regularization": dict(
                    composed_temporal.selected_regularization
                ),
                "metrics": {
                    name: value.as_dict()
                    for name, value in composed_temporal.metrics.items()
                },
                "decision_metrics_at_0_5": {
                    name: dict(value)
                    for name, value in composed_temporal.decision_metrics.items()
                },
                "paired_bootstrap_vs_global_rate": _comparison_bootstraps(
                    composed_temporal.predictions,  # type: ignore[arg-type]
                    cluster="norad_id",
                    config=config,
                    output_dir=output_dir,
                    task="reception_success",
                    split="temporal-composed",
                ),
                "paired_bootstrap_station_cluster_sensitivity": (
                    _comparison_bootstraps(
                        composed_temporal.predictions,  # type: ignore[arg-type]
                        cluster="station_id",
                        config=config,
                        output_dir=output_dir,
                        task="reception_success",
                        split="temporal-composed-station-cluster-sensitivity",
                    )
                ),
            },
            "predictions_path": str(composed_path),
            "predictions_sha256": hashlib.sha256(
                composed_path.read_bytes()
            ).hexdigest(),
            "probability_factor_audit": (
                "every prediction stores both factors and their product"
            ),
        }
        composed_report["scheduling_replay"] = _write_scheduling_replay_report(
            rows=rows,
            predictions=composed_temporal.predictions,
            config=config,
            output_dir=output_dir,
            artifact_prefix="reception-success-scheduling-replay",
            outcome_task="reception_success",
        )
        composed_external: dict[str, object] = {}
        composed_specs: list[tuple[str, str, tuple[int, ...]]] = []
        if config.external_validation_norad_ids:
            composed_specs.append(
                (
                    "satellite",
                    "norad_id",
                    config.external_validation_norad_ids,
                )
            )
        if config.external_station_holdout_fraction:
            composed_station_holdout = deterministic_group_holdout(
                (row.station_id for row in rows),
                fraction=config.external_station_holdout_fraction,
                seed=config.random_seed,
            )
            composed_specs.append(
                ("station", "station_id", composed_station_holdout)
            )
        for label, group, held_out in composed_specs:
            minimum_evaluated_groups = min(5, len(held_out))
            try:
                composed_split = evaluate_composed_reception_group_holdout(
                    rows,
                    group=group,  # type: ignore[arg-type]
                    held_out_values=held_out,
                    regularization_grid=config.regularization_grid,
                    test_fraction=config.temporal_test_fraction,
                    minimum_test_rows=config.minimum_external_test_rows,
                    minimum_test_groups=minimum_evaluated_groups,
                    cutoff=temporal_cutoff,
                )
            except DatasetIntegrityError as exc:
                composed_external[label] = {
                    "status": "insufficient-data",
                    "held_out_group_ids": list(held_out),
                    "minimum_evaluated_groups": minimum_evaluated_groups,
                    "reason": str(exc),
                }
                continue
            external_path = (
                output_dir
                / f"reception_success-external-{label}-predictions.jsonl"
            )
            _write_predictions(external_path, composed_split.predictions)
            cluster = "norad_id" if label == "satellite" else "station_id"
            evaluated_group_ids = sorted(
                {
                    int(getattr(record, cluster))
                    for record in composed_split.predictions
                }
            )
            composed_external[label] = {
                "status": "evaluated",
                "selection": (
                    "predeclared identifiers"
                    if label == "satellite"
                    else "seeded SHA-256 identifier split"
                ),
                "held_out_group_ids": list(held_out),
                "minimum_evaluated_groups": minimum_evaluated_groups,
                "evaluated_group_ids": evaluated_group_ids,
                "evaluated_group_count": len(evaluated_group_ids),
                "held_out_group_coverage_fraction": (
                    len(evaluated_group_ids) / len(held_out) if held_out else 0.0
                ),
                "signal_train_count": composed_split.signal_train_count,
                "decode_train_count": composed_split.decode_train_count,
                "test_observation_rows": composed_split.test_count,
                "prediction_rows": len(composed_split.predictions),
                "train_start": composed_split.train_start,
                "train_end": composed_split.train_end,
                "test_start": composed_split.test_start,
                "test_end": composed_split.test_end,
                "metrics": {
                    name: value.as_dict()
                    for name, value in composed_split.metrics.items()
                },
                "decision_metrics_at_0_5": {
                    name: dict(value)
                    for name, value in composed_split.decision_metrics.items()
                },
                "paired_bootstrap_vs_global_rate": _comparison_bootstraps(
                    composed_split.predictions,  # type: ignore[arg-type]
                    cluster=cluster,
                    config=config,
                    output_dir=output_dir,
                    task="reception_success",
                    split=f"external-{label}-composed",
                ),
                "predictions_path": str(external_path),
                "predictions_sha256": hashlib.sha256(
                    external_path.read_bytes()
                ).hexdigest(),
                "group_disjoint": True,
                "future_only": True,
            }
        if composed_external:
            composed_report["external_validation"] = composed_external
        report["composed_reception"] = composed_report

    drift_records = audit_successive_tle_drift(rows)
    drift_path = output_dir / "tle-drift-records.jsonl"
    with drift_path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in drift_records:
            handle.write(json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":")))
            handle.write("\n")
    report["tle_refresh_audit"] = {
        **summarize_tle_drift(drift_records),
        "pass_match_tolerance_seconds": DEFAULT_TLE_MATCH_TOLERANCE.total_seconds(),
        "records_path": str(drift_path),
        "records_sha256": hashlib.sha256(drift_path.read_bytes()).hexdigest(),
    }
    scheduler_benchmark = run_scheduler_benchmark(
        instances=100,
        candidates_per_instance=40,
        seed=config.random_seed,
    )
    report["synthetic_scheduler_verification"] = {
        **scheduler_benchmark.as_dict(),
        "claim_boundary": (
            "independent exactness and conflict-stress engineering verification; not empirical SatNOGS yield evidence"
        ),
    }
    robustness_started = time.perf_counter()
    robustness_plan = build_scheduler_robustness_plan(seed=config.random_seed)
    robustness_planning_wall_seconds = time.perf_counter() - robustness_started
    robustness_plan_path = output_dir / "synthetic-robustness-plan.json"
    _write_json(robustness_plan_path, plan_document(robustness_plan))
    independent_risk = simulate_plan_yield(
        robustness_plan,
        trials=config.bootstrap_replicates,
        seed=config.random_seed,
    )
    risk_scenarios = {
        "mild_correlated": CorrelatedRiskScenario(
            station_day_availability=0.98,
            satellite_day_transmit_availability=0.95,
            severe_environment_probability=0.10,
            severe_environment_success_multiplier=0.70,
        ),
        "stress_correlated": CorrelatedRiskScenario(
            station_day_availability=0.90,
            satellite_day_transmit_availability=0.85,
            severe_environment_probability=0.25,
            severe_environment_success_multiplier=0.50,
        ),
    }
    report["correlated_risk_sensitivity"] = {
        "schema_version": "synthetic-correlated-risk-sensitivity-v1",
        "plan_path": str(robustness_plan_path),
        "plan_sha256": hashlib.sha256(robustness_plan_path.read_bytes()).hexdigest(),
        "plan_canonical_fingerprint": robustness_plan.canonical_fingerprint(),
        "selected_opportunities": len(robustness_plan.assignments),
        "candidate_opportunities": robustness_plan.diagnostics.get("candidate_count"),
        "planning_wall_seconds": robustness_planning_wall_seconds,
        "solver": robustness_plan.solver,
        "solver_status": robustness_plan.diagnostics.get("status"),
        "solver_mip_gap": robustness_plan.diagnostics.get("mip_gap"),
        "nominal_expected_samples": sum(
            item.expected_unique_samples for item in robustness_plan.assignments
        ),
        "independent": asdict(independent_risk),
        "scenarios": {
            name: asdict(
                simulate_plan_yield_correlated(
                    robustness_plan,
                    scenario=scenario,
                    trials=config.bootstrap_replicates,
                    seed=config.random_seed,
                )
            )
            for name, scenario in risk_scenarios.items()
        },
        "claim_boundary": (
            "seeded engineering sensitivity using synthetic opportunities and declared shared risks; "
            "not a calibrated weather or outage forecast"
        ),
    }
    evaluation_path = output_dir / "evaluation.json"
    _write_json(evaluation_path, report)
    figures = write_evaluation_figures(evaluation_path, output_dir / "figures")
    report["figures"] = [
        {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in figures
    ]
    _write_json(evaluation_path, report)
    return report
