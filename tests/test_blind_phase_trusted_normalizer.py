from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from telemetry_yield.blind_phase_fsk_file import (
    BlindPhaseFskRunConfig,
    _implementation_manifest,
    _result_document,
)
from telemetry_yield.clipping_robust_fsk import (
    BlindPhaseFskConfig,
    BlindPhaseFskResult,
    ClippingRobustAx25Frame,
)
from telemetry_yield.clock_recovery import TimingHypothesis
from telemetry_yield.crc import ax25_fcs


ROOT = Path(__file__).resolve().parents[1]
NORMALIZER_PATH = ROOT / "work/blind-phase-confirmatory-v2/normalize_result.py"
EVALUATOR_PATH = ROOT / "work/blind-phase-confirmatory-v2/evaluate_campaign.py"
REFERENCE = bytes.fromhex(
    "94a662b2a0826094a662b29eb2e103f00018ad8001020304"
)


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


NORMALIZER = _module("blind_phase_trusted_normalizer_test", NORMALIZER_PATH)
EVALUATOR = _module("blind_phase_trusted_normalizer_evaluator", EVALUATOR_PATH)


def _candidate_fixture(tmp_path: Path):
    receivers = [
        BlindPhaseFskConfig(baudrate=rate, candidate_window_limit=4)
        for rate in (1200, 4800, 9600, 19200)
    ]
    candidate_config = {
        "schema_version": "blind-phase-fsk-confirmatory-candidate-config-v1",
        "sample_rate_hz": 57_600,
        "rate_configs": [
            {"baudrate": int(receiver.baudrate), "receiver_config": asdict(receiver)}
            for receiver in receivers
        ],
    }
    config_path = tmp_path / "candidate.json"
    config_path.write_text(json.dumps(candidate_config), encoding="utf-8")
    config_identity = NORMALIZER.regular_file_identity(config_path)
    receiver = receivers[0]
    source_size = 400
    source_sha = hashlib.sha256(b"source").hexdigest()
    run_config = BlindPhaseFskRunConfig(
        receiver=receiver,
        expected_input_size_bytes=source_size,
        expected_input_sha256=source_sha,
    )
    frame = ClippingRobustAx25Frame(
        payload=REFERENCE,
        fcs=ax25_fcs(REFERENCE),
        decoder_start_seconds=0.0,
        estimated_frame_start_seconds=0.125,
        constant_radius_factor=3.0,
        phase_difference_lag=1,
        g3ruh_descramble=False,
        timing=TimingHypothesis(
            rate_error_ppm=0.0,
            phase_samples=0.0,
            step_samples=2.0,
            threshold=0.0,
            score=1.0,
            symbol_count=200,
        ),
        frame_start_symbol=0,
        frame_stop_symbol=200,
        flipped_symbol_indices=(),
        flipped_symbol_reliabilities=(),
        search_stage="native_all_paths",
        attempted_candidates=1,
    )
    decoded = BlindPhaseFskResult(
        input_path="/input/capture.ci16",
        input_sha256=source_sha,
        input_complex_samples=100,
        input_duration_seconds=100 / 57_600,
        selected_windows=(),
        decoded_windows=1,
        timing_hypotheses_examined=1,
        protocol_candidates_attempted=1,
        native_protocol_candidates_attempted=1,
        repair_protocol_candidates_attempted=0,
        frames=(frame,),
    )
    implementation = _implementation_manifest()
    raw = _result_document(
        source=Path("/input/capture.ci16"),
        source_size=source_size,
        source_sha256=source_sha,
        config=run_config,
        decoded=decoded,
        selector_counters={
            "analysis_windows_scanned": 2,
            "analysis_window_bytes": 8,
            "maximum_analysis_array_bytes": 64,
            "selector_sqlite_cache_bytes": 4096,
            "selector_peak_scratch_bytes": 0,
            "selector_estimated_scratch_bytes": 1,
            "degenerate_input": False,
            "degenerate_reason": None,
        },
        implementation=implementation,
    )
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    raw_identity = NORMALIZER.regular_file_identity(raw_path)
    unit = EVALUATOR.build_unit(
        {
            "schema_version": EVALUATOR.UNIT_SCHEMA_VERSION,
            "observation_id": 1,
            "satellite_id": "SAT-1",
            "arm": "candidate",
            "repeat_id": "repeat-a",
            "input_kind": "signal",
            "null_id": None,
            "baudrate": 1200,
            "source": {"size_bytes": source_size, "sha256": source_sha},
        }
    )
    attempt = "a" * 64
    receipt = {
        "schema_version": "blind-phase-campaign-process-receipt-v1",
        "status": "completed",
        "attempt_fingerprint": attempt,
        "unit": unit,
        "process_tree_cleanup_passed": True,
        "cgroup": {"empty_after_run": True},
        "result": raw_identity,
    }
    receipt["receipt_payload_sha256"] = NORMALIZER.sha256_document(receipt)
    source_manifest = []
    for name, identity in implementation["source_files"].items():
        source_manifest.append(
            {"path": f"/repo/src/telemetry_yield/{name}", **identity}
        )
    runtime_lock = {
        "candidate_config": config_identity,
        "candidate_config_sha256": config_identity["sha256"],
        "source_manifest": source_manifest,
    }
    return {
        "unit": unit,
        "receipt": receipt,
        "raw": raw,
        "raw_identity": raw_identity,
        "config": candidate_config,
        "config_identity": config_identity,
        "runtime_lock": runtime_lock,
        "attempt": attempt,
    }


def _normalize(fixture):
    return NORMALIZER.normalize_result(
        unit=fixture["unit"],
        receipt=fixture["receipt"],
        raw_result=fixture["raw"],
        raw_identity=fixture["raw_identity"],
        execution_plan_sha256="1" * 64,
        acquisition_manifest_sha256="2" * 64,
        candidate_config=fixture["config"],
        candidate_config_identity=fixture["config_identity"],
        candidate_runtime_lock=fixture["runtime_lock"],
        component_lock={},
        mission_route=None,
        mission_runtime_lock={},
        normalizer_identity={"path": str(NORMALIZER_PATH), "size_bytes": 1, "sha256": "3" * 64},
        expected_process_attempt_fingerprint=fixture["attempt"],
    )


def test_candidate_native_is_revalidated_from_detection_fcs_and_ax25(tmp_path: Path) -> None:
    fixture = _candidate_fixture(tmp_path)
    normalized = _normalize(fixture)
    assert normalized["status"] == "completed"
    assert normalized["trusted_native"] == [
        {
            "protocol_id": "ax25",
            "payload_hex": REFERENCE.hex(),
            "payload_sha256": hashlib.sha256(REFERENCE).hexdigest(),
            "admission": "trusted_native",
        }
    ]
    unhashed = dict(normalized)
    payload_hash = unhashed.pop("normalized_result_payload_sha256")
    assert payload_hash == NORMALIZER.sha256_document(unhashed)


def test_candidate_relabelled_repair_rejected_even_with_recomputed_selfhash(tmp_path: Path) -> None:
    fixture = _candidate_fixture(tmp_path)
    detection = fixture["raw"]["candidates"]["detections"][0]
    detection["classification"] = "crc_valid_list_repaired_candidate_not_authenticated"
    detection["native_trusted"] = False
    detection["provenance"]["flipped_symbol_indices"] = [1]
    detection["provenance"]["flipped_symbol_reliabilities"] = [0.1]
    detection["provenance"]["search_stage"] = "global_shared_repair"
    fixture["raw"].pop("result_payload_sha256")
    fixture["raw"]["result_payload_sha256"] = NORMALIZER.sha256_document(fixture["raw"])
    with pytest.raises(ValueError, match="consensus is not derivable"):
        _normalize(fixture)


def test_receipt_attempt_must_match_exact_launcher_contract(tmp_path: Path) -> None:
    fixture = _candidate_fixture(tmp_path)
    fixture["attempt"] = "b" * 64
    with pytest.raises(ValueError, match="argv/limits attempt"):
        _normalize(fixture)
