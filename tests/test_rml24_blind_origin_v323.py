from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "work/rml24/run_blind_origin_v323.py"
LAUNCHER = ROOT / "work/rml24/launch_blind_origin_v323.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v323 = _load("run_blind_origin_v323_test", RUNNER)


def test_v323_plan_precedes_freeze_and_requires_both_attestations() -> None:
    plan = json.loads(
        (ROOT / "reports/rml24-blind-origin-v3.2.3-prereg.json").read_text()
    )
    assert plan["status"] == "frozen_protocol_before_v3.2.3_code_freeze_or_evaluation"
    assert plan["not_a_new_holdout"] is True
    assert plan["required_gates"]["successful_freeze_and_evaluate_attestations"] is True
    assert plan["historical_series"]["v3.2.2"].startswith("FAIL_CLOSED")


def test_full_snapshot_covers_code_support_and_data_without_hardlinks() -> None:
    source = LAUNCHER.read_text()
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "snapshot_all")
    strings = {node.value for node in ast.walk(function) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert {"code_files", "support_files", "data_files"} <= strings
    assert "shutil.copyfile" in source
    assert "st_ino" in source
    assert "os.link" not in source


def test_launcher_resolves_all_external_artifact_paths_before_attestation() -> None:
    source = LAUNCHER.read_text()
    assert "output_targets = tuple(path.resolve()" in source
    assert "trace_path.relative_to(ROOT)" in source


def test_final_postflight_precedes_O_EXCL_output() -> None:
    tree = ast.parse(RUNNER.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "evaluate")
    calls = [
        (node.lineno, node.func.id)
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert min(line for line, name in calls if name == "postflight") < min(
        line for line, name in calls if name == "write_json"
    )


def test_mutated_origin_and_missing_native_fail_postflight(tmp_path: Path) -> None:
    target = tmp_path / "input.bin"
    target.write_bytes(b"locked")
    row = {"path": str(target), "sha256": v323.sha256_file(target), "bytes": target.stat().st_size}
    lock = {
        "code_files": [],
        "support_files": [],
        "data_files": [row],
        "native_runtime": {"child": {"files": [{"path": "/missing.so"}]}},
    }
    target.write_bytes(b"drift!")

    class V32:
        @staticmethod
        def native_executable_inventory():
            return []

    try:
        v323.postflight(lock, V32)
    except ValueError as exc:
        assert "origin_data_post" in str(exc)
        assert "native_post" in str(exc)
    else:
        raise AssertionError("postflight accepted TOCTOU drift")


def test_0444_is_not_described_as_filesystem_immutable() -> None:
    plan = (ROOT / "reports/rml24-blind-origin-v3.2.3-prereg.json").read_text()
    runner = RUNNER.read_text()
    assert '"mode_0444_is_write_protected_not_filesystem_immutable": true' in plan
    assert '"filesystem_immutable_claimed": False' in runner


def test_mmap_loader_warmup_is_frozen_before_native_inventory() -> None:
    source = RUNNER.read_text()
    assert 'mmap_mode="r"' in source
    tree = ast.parse(source)
    freeze_fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "freeze")
    calls = [(node.lineno, node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else "") for node in ast.walk(freeze_fn) if isinstance(node, ast.Call)]
    assert min(line for line, name in calls if name == "_warm_locked_mmap_loader") < min(line for line, name in calls if name == "native_executable_inventory")
