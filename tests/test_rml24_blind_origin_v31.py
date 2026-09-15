from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work" / "rml24" / "run_blind_origin_v31.py"
SPEC = importlib.util.spec_from_file_location("run_blind_origin_v31", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
v31 = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = v31
SPEC.loader.exec_module(v31)


def test_v31_is_honest_replay_and_not_a_new_holdout() -> None:
    prereg = json.loads(
        (ROOT / "reports" / "rml24-blind-origin-v3.1-prereg.json").read_text()
    )
    assert prereg["not_a_new_holdout"] is True
    assert prereg["claim_boundary"]["independent_pass"] is False
    assert prereg["prior_result_status"]["historical_verdict"] == (
        "FAIL_CLOSED_PROVENANCE"
    )


def test_v31_config_constants_exactly_match_historical_frozen_config() -> None:
    current = json.loads(
        (ROOT / "reports" / "rml24-blind-origin-v3.1-config.json").read_text()
    )
    historical = json.loads(
        (ROOT / "reports" / "rml24-blind-origin-v3-frozen-config.json").read_text()
    )
    expected = {
        key: int(value["deterministic_shift_bits"])
        for key, value in historical["profiles"].items()
    }
    assert current["profiles"] == expected


def test_every_known_executed_local_dependency_is_explicitly_locked() -> None:
    expected = {
        "work/rml24/run_blind_origin_v31.py",
        "work/rml24/run_blind_origin_v3.py",
        "work/rml24/diagnose_acquisition_v2.py",
        "src/telemetry_yield/__init__.py",
        "src/telemetry_yield/canonical.py",
        "src/telemetry_yield/crc.py",
        "src/telemetry_yield/license_gate.py",
        "src/telemetry_yield/metrics.py",
        "src/telemetry_yield/models.py",
        "src/telemetry_yield/rml24_benchmark.py",
        "src/telemetry_yield/rml24_physical_plugin.py",
    }
    assert set(v31.LOCAL_CODE_FILES) == expected
    inventory = v31.local_code_inventory()
    assert {str(entry["path"]) for entry in inventory} == expected
    assert all(len(str(entry["sha256"])) == 64 for entry in inventory)


def test_evaluator_verifies_lock_before_importing_old_evaluator() -> None:
    tree = ast.parse(SCRIPT.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate"
    )
    verify_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "verify_lock"
    )
    import_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
    )
    assert verify_line < import_line


def test_noncanonical_input_paths_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="non-canonical"):
        v31.require_canonical_inputs(
            tmp_path / "prereg.json",
            ROOT / "reports/rml24-blind-origin-v3.1-config.json",
            ROOT / "work/nature-dataset/shards/manifest.json",
        )


def test_immutable_writer_emits_exact_sidecar_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    sidecar = tmp_path / "result.json.sha256"
    digest = v31.write_immutable_json_with_sidecar(
        output,
        sidecar,
        {"metric_core": {"errors": 7, "truth_bits": 10}},
    )
    assert v31.sha256_file(output) == digest
    assert sidecar.read_text().split()[0] == digest
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o444
    with pytest.raises(FileExistsError):
        v31.write_immutable_json_with_sidecar(
            output,
            sidecar,
            {"metric_core": {"errors": 0}},
        )


def test_metric_core_hash_is_canonical_and_independent_of_key_order() -> None:
    left = {"b": [2, 1], "a": {"errors": 4}}
    right = {"a": {"errors": 4}, "b": [2, 1]}
    assert v31.canonical_sha256(left) == v31.canonical_sha256(right)
