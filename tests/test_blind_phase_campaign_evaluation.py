from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/evaluate_campaign.py"


def _module():
    name = "blind_phase_campaign_evaluation_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EVALUATE = _module()
OBSERVATIONS = tuple(range(1, 31))
NULLS = ("all-zero", "pair-reversal", "phase-quarter-1", "phase-quarter-2")
MISSION_OBSERVATIONS = OBSERVATIONS[:17]
NORMALIZER_IDENTITY = {
    "path": "/frozen/normalize_result.py",
    "size_bytes": 123,
    "sha256": "3" * 64,
}


def _payload(value: bytes, admission: str = "trusted_native") -> dict[str, object]:
    return {
        "protocol_id": "ax25",
        "payload_hex": value.hex(),
        "payload_sha256": hashlib.sha256(value).hexdigest(),
        "admission": admission,
    }


def _source(observation_id: int, input_kind: str, null_id: str | None):
    label = f"{observation_id}:{input_kind}:{null_id}".encode()
    return {
        "size_bytes": 400,
        "sha256": hashlib.sha256(label).hexdigest(),
    }


def _set_projection(record: dict[str, object]) -> None:
    record.pop("normalized_result_payload_sha256", None)
    projection = {
        "status": record["status"],
        "receiver": record["receiver_projection"],
        "trusted_native": sorted(
            (item["protocol_id"], item["payload_hex"])
            for item in record["trusted_native"]
        ),
        "untrusted_diagnostic": sorted(
            (item["protocol_id"], item["payload_hex"])
            for item in record["untrusted_diagnostic"]
        ),
    }
    record["determinism_projection_sha256"] = EVALUATE._sha256_document(projection)
    record["normalized_result_payload_sha256"] = EVALUATE._sha256_document(record)


def _campaign():
    units = []
    records = []
    inputs = [("signal", None), *(("null", null_id) for null_id in NULLS)]
    for observation_id in OBSERVATIONS:
        for repeat_id in EVALUATE.REPEATS:
            for input_kind, null_id in inputs:
                arms_and_rates = [
                    *((arm, rate) for arm in ("component_a", "candidate") for rate in EVALUATE.RATES)
                ]
                if observation_id in MISSION_OBSERVATIONS:
                    arms_and_rates.append(("mission_satyaml", None))
                for arm, baudrate in arms_and_rates:
                    unit = EVALUATE.build_unit(
                        {
                            "schema_version": EVALUATE.UNIT_SCHEMA_VERSION,
                            "observation_id": observation_id,
                            "satellite_id": f"SAT-{observation_id % 15}",
                            "arm": arm,
                            "repeat_id": repeat_id,
                            "input_kind": input_kind,
                            "null_id": null_id,
                            "baudrate": baudrate,
                            "source": _source(observation_id, input_kind, null_id),
                        }
                    )
                    trusted = []
                    diagnostic = []
                    if input_kind == "signal" and baudrate in (1_200, None):
                        if arm in ("component_a", "candidate"):
                            trusted.append(_payload(f"base-{observation_id}".encode()))
                        if arm == "candidate":
                            trusted.append(_payload(f"novel-{observation_id}".encode()))
                        if arm == "mission_satyaml":
                            trusted.append(_payload(f"mission-{observation_id}".encode()))
                    if input_kind == "null" and arm == "candidate" and baudrate == 1_200:
                        diagnostic.append(
                            _payload(
                                f"repair-{observation_id}-{null_id}".encode(),
                                "repaired_untrusted",
                            )
                        )
                    units.append(unit)
                    record = {
                            "schema_version": EVALUATE.RESULT_SCHEMA_VERSION,
                            "status": "completed",
                            "unit": unit,
                            "process_tree_cleanup_passed": True,
                            "trusted_native": trusted,
                            "untrusted_diagnostic": diagnostic,
                            "receiver_projection": {
                                "config_sha256": "a" * 64,
                                "selected_windows": [],
                                "resource_counters": {"attempted": 1},
                            },
                            "provenance": {
                                "execution_plan_sha256": "1" * 64,
                                "acquisition_manifest_sha256": "2" * 64,
                                "normalizer": NORMALIZER_IDENTITY,
                            },
                        }
                    _set_projection(record)
                    records.append(record)
    return units, records


def _evaluate(units, records):
    return EVALUATE.evaluate_campaign(
        expected_units=units,
        records=records,
        observation_ids=OBSERVATIONS,
        null_ids=NULLS,
        mission_observation_ids=MISSION_OBSERVATIONS,
        execution_plan_sha256="1" * 64,
        acquisition_manifest_sha256="2" * 64,
        normalizer_identity=NORMALIZER_IDENTITY,
    )


def test_exact_all_30_primary_union_gain_and_untrusted_null_diagnostic() -> None:
    units, records = _campaign()
    report = _evaluate(units, records)
    assert report["status"] == "complete"
    assert report["coverage"]["observation_count"] == 30
    assert report["primary_component_candidate_union_increment"][
        "per_observation_differences"
    ] == [1] * 30
    assert report["primary_component_candidate_union_increment"][
        "two_sided_95_percentile_interval"
    ] == [1.0, 1.0]
    assert report["three_way_operational_system_secondary"]["total_difference"] == 47
    nulls = report["null_controls"]
    assert nulls["trusted_native_false_accept_count"] == 0
    assert nulls["repaired_or_corrected_untrusted_emission_count_diagnostic"] == 120
    assert nulls["untrusted_diagnostic_counted_as_acceptance"] is False
    assert report["gates"]["deployment_analysis_gate_pass"] is True
    assert report["gates"]["primary_incremental_publication_gate_pass"] is True
    assert report["gates"]["candidate_standalone_no_regression_is_release_blocker"] is False
    assert report["claim_guard"]["publication_ready"] is False


def test_trusted_null_fails_safety_gate_but_untrusted_does_not() -> None:
    units, records = _campaign()
    for record in records:
        unit = record["unit"]
        if (
            unit["observation_id"] == 1
            and unit["arm"] == "candidate"
            and unit["baudrate"] == 1_200
            and unit["input_kind"] == "null"
            and unit["null_id"] == NULLS[0]
        ):
            record["trusted_native"] = [_payload(b"bad-null")]
            _set_projection(record)
    report = _evaluate(units, records)
    assert report["null_controls"]["trusted_native_false_accept_count"] == 1
    assert report["gates"]["trusted_native_null_false_accepts_zero"] is False
    assert report["gates"]["deployment_analysis_gate_pass"] is False


def test_repeat_drift_is_reported_and_blocks_analysis_gate() -> None:
    units, records = _campaign()
    for record in records:
        unit = record["unit"]
        if (
            unit["observation_id"] == 1
            and unit["arm"] == "candidate"
            and unit["repeat_id"] == "repeat-b"
            and unit["input_kind"] == "signal"
            and unit["baudrate"] == 1_200
        ):
            record["trusted_native"].append(_payload(b"nondeterministic"))
            _set_projection(record)
    report = _evaluate(units, records)
    assert report["coverage"]["repeat_mismatches"]
    assert report["gates"]["two_repeats_identical"] is False
    assert report["gates"]["deployment_analysis_gate_pass"] is False


def test_terminal_timeout_is_preserved_without_becoming_zero_yield() -> None:
    units, records = _campaign()
    target = records[0]
    target["status"] = "timeout"
    target["trusted_native"] = []
    target["untrusted_diagnostic"] = []
    _set_projection(target)
    report = _evaluate(units, records)
    assert report["status"] == "incomplete"
    assert report["coverage"]["terminal_failures"][0]["status"] == "timeout"
    assert report["gates"]["all_required_units_completed"] is False
    assert report["gates"]["deployment_analysis_gate_pass"] is False


def test_schedule_omission_and_same_byte_drift_fail_closed() -> None:
    units, records = _campaign()
    with pytest.raises(ValueError, match="schedule is not exact"):
        _evaluate(units[:-1], records[:-1])

    drift_index = next(
        index
        for index, unit in enumerate(units)
        if unit["observation_id"] == 1
        and unit["arm"] == "candidate"
        and unit["input_kind"] == "signal"
    )
    drifted = dict(units[drift_index])
    drifted_source = dict(drifted["source"])
    drifted_source["sha256"] = "f" * 64
    drifted["source"] = drifted_source
    drifted = EVALUATE.build_unit(drifted)
    units[drift_index] = drifted
    records[drift_index]["unit"] = drifted
    _set_projection(records[drift_index])
    with pytest.raises(ValueError, match="exact same source bytes"):
        _evaluate(units, records)
