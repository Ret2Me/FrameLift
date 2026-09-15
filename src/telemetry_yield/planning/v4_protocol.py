"""Frozen, outcome-blind methodological contract for publication v4."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping


V4_METHOD_CONTRACT_SCHEMA = "observation-planning-method-contract-v1"
V4_METHOD_CONTRACT_RELATIVE_PATH = Path(
    "configs/observation-planning-method-contract-v4.json"
)
V4_STUDY_ID = "observation-planning-publication-v4"
V4_PREDECLARED_TEMPORAL_CUTOFF = "2026-08-01T00:00:00Z"
V4_TARGET_COUNT = 50
V4_MIN_SIGNAL_SATELLITES = 40
V4_MIN_CONDITIONAL_DECODE_SATELLITES = 25
V4_EXTERNAL_SATELLITE_COUNT = 10
V4_EXTERNAL_STATION_HOLDOUT_FRACTION = 0.20
V4_MIN_EXTERNAL_GROUPS = 5
V4_MIN_EXTERNAL_TEST_ROWS = 30

MIN_CAPTURED_LOCATION_FRACTION = 0.85
MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION = 0.75
MIN_CAPTURED_RECEIVER_DRIVER_FRACTION = 0.75
MIN_CAPTURED_RECEIVER_GAIN_FRACTION = 0.60
MIN_VERIFIED_WEATHER_FRACTION = 0.95
MIN_VERIFIED_SPACE_WEATHER_FRACTION = 0.95


def expected_v4_method_contract() -> dict[str, object]:
    """Return the exact pre-model-fit contract enforced by v4 tooling."""

    return {
        "schema_version": V4_METHOD_CONTRACT_SCHEMA,
        "study_id": V4_STUDY_ID,
        "frozen_at": "2026-09-04T05:45:00Z",
        "decision_boundary": {
            "before_v4_model_fit": True,
            "before_v4_performance_evaluation": True,
            "before_v4_scheduler_replay": True,
            "outcome_blind": True,
            "inspected_inputs": [
                "collection progress and request metadata",
                "input-only capture-location coverage",
                "input-only receiver-field coverage",
                "row counts by NORAD ID",
                "transmitter UUID counts",
            ],
            "forbidden_inputs": [
                "AUROC",
                "Brier score",
                "decode-success labels",
                "model predictions",
                "scheduler yield",
            ],
        },
        "coverage_thresholds": {
            "capture_time_location_fraction": MIN_CAPTURED_LOCATION_FRACTION,
            "capture_time_receiver_configuration_fraction": (
                MIN_CAPTURED_RECEIVER_CONFIGURATION_FRACTION
            ),
            "capture_time_receiver_driver_fraction": (
                MIN_CAPTURED_RECEIVER_DRIVER_FRACTION
            ),
            "capture_time_receiver_rf_gain_fraction": (
                MIN_CAPTURED_RECEIVER_GAIN_FRACTION
            ),
            "complete_weather_fraction_of_capture_location_eligible_rows": (
                MIN_VERIFIED_WEATHER_FRACTION
            ),
            "weather_snapshot_fraction_of_capture_location_eligible_rows": (
                MIN_VERIFIED_WEATHER_FRACTION
            ),
            "weather_value_fraction_of_capture_location_eligible_rows": (
                MIN_VERIFIED_WEATHER_FRACTION
            ),
            "space_weather_fraction_of_normalized_rows": (
                MIN_VERIFIED_SPACE_WEATHER_FRACTION
            ),
        },
        "cohort_requirements": {
            "target_count": V4_TARGET_COUNT,
            "minimum_signal_satellites": V4_MIN_SIGNAL_SATELLITES,
            "minimum_conditional_decode_satellites": (
                V4_MIN_CONDITIONAL_DECODE_SATELLITES
            ),
            "external_satellite_count": V4_EXTERNAL_SATELLITE_COUNT,
            "external_station_holdout_fraction": (
                V4_EXTERNAL_STATION_HOLDOUT_FRACTION
            ),
            "minimum_external_evaluated_groups_per_task": V4_MIN_EXTERNAL_GROUPS,
            "minimum_external_test_rows_per_task": V4_MIN_EXTERNAL_TEST_ROWS,
            "both_outcome_classes_required_in_every_validation_split": True,
            "composed_reception_endpoint": (
                "packet-mode reviewed observation with a demodulation artifact"
            ),
            "composed_reception_probability": (
                "P(signal) * P(decode artifact | signal)"
            ),
            "composed_reception_scheduler_objective": (
                "maximize expected end-to-end packet receptions"
            ),
            "composed_reception_binary_decision_threshold": 0.5,
            "composed_reception_validation_required": [
                "temporal",
                "external_satellite",
                "external_station",
            ],
        },
        "weather_provenance": {
            "eligible_location_source": "satnogs-client-metadata-capture",
            "current_station_api_fallback": "ineligible",
            "denominator": "capture-time-location-eligible normalized rows",
            "required_complete_fields": [
                "air_temperature_c",
                "precipitation_corrected",
                "relative_humidity_percent",
                "surface_pressure_kpa",
                "wind_speed_m_s",
            ],
        },
        "receiver_provenance": {
            "source": "satnogs-client-metadata-capture",
            "retained_fields": [
                "captured_receiver_antenna_port",
                "captured_receiver_bandwidth_hz",
                "captured_receiver_driver",
                "captured_receiver_gain_mode",
                "captured_receiver_ppm_error",
                "captured_receiver_radio_name",
                "captured_receiver_radio_version",
                "captured_receiver_rf_gain_db",
                "captured_receiver_sample_rate_hz",
            ],
            "discarded_fields": [
                "device identifiers",
                "device strings except allowlisted driver",
                "filesystem paths",
                "unrestricted client metadata",
            ],
            "receiver_rf_gain_semantics": "SoapySDR receiver gain setting in dB",
            "receiver_antenna_port_semantics": (
                "receiver port name, not physical antenna type"
            ),
            "physical_antenna_gain_inference": "forbidden",
            "same_observation_prediction_use": "forbidden",
        },
        "temporal_rules": {
            "predeclared_cutoff": V4_PREDECLARED_TEMPORAL_CUTOFF,
            "history_eligibility": (
                "source observation end strictly before target observation start"
            ),
            "training_eligibility": "observation end strictly before cutoff",
            "test_eligibility": "observation start at or after cutoff",
            "cutoff_crossing_observations": "excluded from training and test",
            "forward_validation": (
                "every training observation ends before validation starts"
            ),
            "applies_to": [
                "covariate ablation",
                "external satellite holdout",
                "external station holdout",
                "leave-one-satellite-out",
                "leave-one-station-out",
                "temporal holdout",
            ],
        },
        "claim_boundaries": {
            "capture_receiver_metadata_is_physical_antenna_evidence": False,
            "current_station_profile_may_backfill_history": False,
            "missing_optional_covariates_may_drop_rows": False,
            "missing_optional_covariates_are_explicit": True,
        },
    }


def validate_v4_method_contract(
    project_root: Path, *, required: bool | None = None
) -> Path | None:
    """Validate the exact contract and return its path.

    Fixture repositories without publication-v4 inputs are allowed to omit the
    contract. A real v4 project fails closed on absence or any semantic drift.
    """

    root = project_root.resolve()
    publication_config = root / "configs/observation-planning-publication-v4.json"
    if required is None:
        required = publication_config.is_file()
    path = root / V4_METHOD_CONTRACT_RELATIVE_PATH
    if not path.is_file():
        if required:
            raise ValueError(f"v4 method contract is absent: {path}")
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload != expected_v4_method_contract():
        raise ValueError(
            "v4 method contract differs from the frozen outcome-blind contract"
        )
    return path


def validate_v4_publication_config_contract(config_path: Path) -> None:
    """Bind the v4 cohort configuration to the frozen method contract."""

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("study_id") != V4_STUDY_ID:
        raise ValueError("publication config is not the frozen v4 study")
    targets = payload.get("targets")
    external = payload.get("external_validation_norad_ids")
    sampling = payload.get("sampling_intervals")
    target_ids = (
        {
            int(item.get("norad_id", 0))
            for item in targets
            if isinstance(item, Mapping)
        }
        if isinstance(targets, list)
        else set()
    )
    external_ids = (
        {int(value) for value in external}
        if isinstance(external, list)
        else set()
    )
    final_interval = sampling[-1] if isinstance(sampling, list) and sampling else None
    valid = (
        isinstance(targets, list)
        and len(targets) == V4_TARGET_COUNT
        and len(target_ids) == V4_TARGET_COUNT
        and all(value > 0 for value in target_ids)
        and isinstance(external, list)
        and len(external) == V4_EXTERNAL_SATELLITE_COUNT
        and len(external_ids) == V4_EXTERNAL_SATELLITE_COUNT
        and external_ids <= target_ids
        and payload.get("external_station_holdout_fraction")
        == V4_EXTERNAL_STATION_HOLDOUT_FRACTION
        and payload.get("minimum_external_test_rows") == V4_MIN_EXTERNAL_TEST_ROWS
        and isinstance(final_interval, Mapping)
        and final_interval.get("start") == V4_PREDECLARED_TEMPORAL_CUTOFF
    )
    if not valid:
        raise ValueError("publication config differs from the frozen v4 cohort contract")
