from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "work/rml24/run_blind_origin_v32.py"
LAUNCHER = ROOT / "work/rml24/launch_blind_origin_v32.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v32 = _load("run_blind_origin_v32_test", RUNNER)
launcher = _load("launch_blind_origin_v32_test", LAUNCHER)


def test_v32_prereg_is_honest_replay_with_source_native_and_toctou_gates() -> None:
    prereg = json.loads(
        (ROOT / "reports/rml24-blind-origin-v3.2-prereg.json").read_text()
    )
    assert prereg["not_a_new_holdout"] is True
    assert prereg["claim_boundary"]["independent_pass"] is False
    requirements = prereg["provenance_requirements"]
    assert requirements["fresh_empty_python_pycacheprefix_per_process"] is True
    assert requirements["hash_every_executable_file_mapping_from_proc_self_maps"] is True
    assert requirements["preflight_and_postflight_hash_all_code_config_support_and_data"] is True


def test_v32_config_constants_exactly_match_v31() -> None:
    current = json.loads(
        (ROOT / "reports/rml24-blind-origin-v3.2-config.json").read_text()
    )
    previous = json.loads(
        (ROOT / "reports/rml24-blind-origin-v3.1-config.json").read_text()
    )
    assert current["profiles"] == previous["profiles"]
    assert current["rng_seed"] == previous["rng_seed"]


def test_launcher_and_every_transitive_local_source_have_explicit_roles() -> None:
    entries = dict(v32.LOCAL_CODE_FILES)
    assert entries["work/rml24/launch_blind_origin_v32.py"] == "launcher_source"
    assert entries["work/rml24/run_blind_origin_v32.py"] == "child_entry_source"
    assert entries["work/rml24/run_blind_origin_v31.py"] == "imported_source"
    assert entries["work/rml24/run_blind_origin_v3.py"] == "imported_source"
    assert entries["work/rml24/diagnose_acquisition_v2.py"] == "imported_source"
    assert len(entries) == 13


def test_evaluator_verifies_lock_before_import_and_postflight_before_write() -> None:
    tree = ast.parse(RUNNER.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate"
    )

    def first_call(name: str) -> int:
        return min(
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and (
                isinstance(node.func, ast.Name)
                and node.func.id == name
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == name
            )
        )

    assert first_call("verify_lock_before_local_imports") < first_call("_import_v31")
    assert first_call("replay_postflight") < first_call(
        "write_protected_json_with_sidecar"
    )


def test_write_protected_writer_is_O_EXCL_tamper_evident_not_immutable(
    tmp_path: Path,
) -> None:
    output = tmp_path / "result.json"
    sidecar = tmp_path / "result.json.sha256"
    digest = v32.write_protected_json_with_sidecar(
        output,
        sidecar,
        {"errors": 3, "truth_bits": 10},
    )
    assert v32.sha256_file(output) == digest
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o444
    with pytest.raises(FileExistsError):
        v32.write_protected_json_with_sidecar(output, sidecar, {"errors": 0})


def _minimal_postflight_lock(path: Path) -> dict[str, object]:
    entry = {
        "path": str(path.resolve()),
        "sha256": v32.sha256_file(path),
        "bytes": path.stat().st_size,
        "execution_role": "child_entry_source",
    }
    native = v32.native_executable_inventory()
    return {
        "code_files": [entry],
        "support_files": [],
        "data_files": [],
        "native_runtime": {"child": {"files": native}},
    }


def test_mid_replay_file_mutation_fails_closed_before_output(tmp_path: Path) -> None:
    target = tmp_path / "pinned.py"
    target.write_text("VALUE = 1\n")
    lock = _minimal_postflight_lock(target)
    target.write_text("VALUE = 2\n")
    with pytest.raises(ValueError, match="postflight verification failed"):
        v32.replay_postflight(lock)


def test_missing_native_dependency_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "pinned.py"
    target.write_text("VALUE = 1\n")
    lock = _minimal_postflight_lock(target)
    lock["native_runtime"]["child"]["files"].append(
        {"path": "/missing/native.so", "sha256": "0" * 64, "bytes": 1}
    )
    with pytest.raises(ValueError, match="child_native_runtime_post"):
        v32.replay_postflight(lock)


def test_launcher_requires_B_and_child_B_v_and_empty_cache_contract() -> None:
    source = LAUNCHER.read_text()
    assert "if not sys.flags.dont_write_bytecode" in source
    assert '[sys.executable, "-B", "-v"' in source
    contract = v32.bytecode_execution_contract()
    assert contract["fresh_unique_empty_python_pycache_prefix_required"] is True
    assert contract["zero_local_pyc_required"] is True


def test_native_inventory_is_complete_regular_executable_map_set() -> None:
    inventory = launcher.native_executable_inventory()
    assert inventory
    assert all(Path(str(row["path"])).is_file() for row in inventory)
    assert all(len(str(row["sha256"])) == 64 for row in inventory)
    assert any(Path(str(row["path"])).name.startswith("python") for row in inventory)
