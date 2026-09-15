from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "work/rml24/run_blind_origin_v321.py"
LAUNCHER = ROOT / "work/rml24/launch_blind_origin_v321.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v321 = _load("run_blind_origin_v321_test", RUNNER)


def test_plan_was_frozen_before_v321_replay_and_is_not_new_holdout() -> None:
    plan = json.loads(
        (ROOT / "reports/rml24-blind-origin-v3.2.1-prereg.json").read_text()
    )
    assert plan["status"] == "frozen_replay_protocol_before_v3.2.1_execution"
    assert plan["not_a_new_holdout"] is True
    assert plan["claim_boundary"]["independent_pass"] is False
    assert plan["snapshot_contract"]["hardlinks_forbidden"] is True
    assert plan["snapshot_contract"]["all_IQ_and_truth_reads_from_snapshot"] is True


def test_launcher_physically_copies_code_support_and_data_and_checks_inode() -> None:
    tree = ast.parse(LAUNCHER.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "snapshot_all"
    )
    sections = {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.endswith("_files")
    }
    calls = {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert sections >= {"code_files", "support_files", "data_files"}
    assert "copyfile" in calls
    assert "link" not in calls
    assert "st_ino" in LAUNCHER.read_text()


def test_runner_postflight_happens_before_exclusive_final_output() -> None:
    tree = ast.parse(RUNNER.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "evaluate"
    )
    post = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "postflight"
    )
    write = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_protected_json_with_sidecar"
    )
    assert post < write


def test_postflight_detects_origin_mutation_and_missing_native(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"before")
    row = {
        "path": str(target),
        "sha256": v321.sha256_file(target),
        "bytes": target.stat().st_size,
    }
    class V32:
        @staticmethod
        def native_executable_inventory():
            return []
    lock = {
        "code_files": [],
        "support_files": [],
        "data_files": [row],
        "native_runtime": {"child": {"files": [{"path": "/missing.so"}]}},
    }
    target.write_bytes(b"after!")
    try:
        v321.postflight(lock, V32)
    except ValueError as exc:
        text = str(exc)
        assert "origin_data_post" in text
        assert "native_post" in text
    else:
        raise AssertionError("postflight accepted mutated data and missing native mapping")


def test_no_filesystem_immutable_claim_for_mode_0444() -> None:
    plan = (ROOT / "reports/rml24-blind-origin-v3.2.1-prereg.json").read_text()
    runner = RUNNER.read_text()
    assert '"filesystem_immutable_claimed": false' in plan
    assert '"filesystem_immutable_claimed": False' in runner
    assert "write_protected_mode" in runner
