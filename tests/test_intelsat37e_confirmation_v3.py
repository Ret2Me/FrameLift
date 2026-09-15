from __future__ import annotations

import importlib.util
from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "work/intelsat37e-gr4-positive-v3/run_confirmation_v3.py"
SPEC = importlib.util.spec_from_file_location("intelsat_confirmation_v3", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_frozen_run_matrix_and_same_executable_command() -> None:
    assert [run["id"] for run in MODULE.RUNS] == [
        "baseline_upstream_costas",
        "selected_identity_1",
        "selected_identity_2",
        "selected_null_zero",
        "selected_null_fine_block_permutation",
        "selected_null_time_reversal",
    ]
    baseline = MODULE.RUNS[0]
    selected = MODULE.RUNS[1]
    assert baseline["bandwidths"] == ["0.02", "0.01", "0.005"]
    assert selected["bandwidths"] == ["0.2", "0.1", "0.05"]
    baseline_command = MODULE.docker_run_command(
        baseline, MODULE.ORIGIN_INPUTS["identity"], Path("baseline.pdus")
    )
    selected_command = MODULE.docker_run_command(
        selected, MODULE.ORIGIN_INPUTS["identity"], Path("identity.pdus")
    )
    entrypoint_index = baseline_command.index("--entrypoint") + 1
    assert baseline_command[entrypoint_index] == selected_command[entrypoint_index]
    assert baseline_command[entrypoint_index].endswith("packet_receiver_file_audit_v3")
    assert "--network" in baseline_command and "none" in baseline_command
    assert "--read-only" in baseline_command


def test_parser_accepts_known_strict_identity_and_empty_null() -> None:
    identity = MODULE.parse_pdus(
        ROOT / "work/intelsat37e-gr4-positive-v1/confirmation-v2-identity-1.pdus"
    )
    null = MODULE.parse_pdus(
        ROOT / "work/intelsat37e-gr4-positive-v1/confirmation-v2-null-zero.pdus"
    )
    assert identity["record_count"] == 26
    assert identity["valid_count"] == 26
    assert identity["unique_valid_count"] == 26
    assert identity["framing_errors"] == []
    assert null["record_count"] == 0
    assert null["sha256"] == MODULE.EMPTY_SHA256


def test_parser_fails_closed_on_truncated_record(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.pdus"
    malformed.write_bytes(struct.pack("!I", 32) + b"short")
    audit = MODULE.parse_pdus(malformed)
    assert audit["record_count"] == 0
    assert audit["framing_errors"] == ["record length exceeds remaining bytes"]


def test_v3_cpp_uses_finite_vector_source_and_runtime_costas_parameters() -> None:
    app = MODULE.APP_SOURCE.read_text()
    receiver = MODULE.PACKET_RECEIVER.read_text()
    assert "VectorSource<c64>" in app
    assert "FileSource<c64>" not in app
    assert "source.repeat = false" in app
    assert "syncword_costas_bandwidth" in app
    assert "header_costas_bandwidth" in app
    assert "payload_costas_bandwidth" in app
    assert '"syncword_costas_loop_bandwidth", syncword_costas_loop_bandwidth' in receiver
    assert '"header_costas_loop_bandwidth", header_costas_loop_bandwidth' in receiver
    assert '"payload_costas_loop_bandwidth", payload_costas_loop_bandwidth' in receiver


def test_plan_and_inputs_match_preregistered_hashes() -> None:
    assert MODULE.sha256(MODULE.PLAN) == MODULE.PLAN_SHA256
    for identifier, path in MODULE.ORIGIN_INPUTS.items():
        assert MODULE.sha256(path) == MODULE.EXPECTED_INPUT_SHA256[identifier]
