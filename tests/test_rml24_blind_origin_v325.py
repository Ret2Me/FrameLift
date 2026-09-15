from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "work/rml24/run_blind_origin_v325.py"
LAUNCHER = ROOT / "work/rml24/launch_blind_origin_v325.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v325 = _load("run_blind_origin_v325_test", RUNNER)


def test_preregistered_contract_closes_owner_mutable_snapshot_gap() -> None:
    plan = json.loads(
        (ROOT / "reports/rml24-blind-origin-v3.2.5-prereg.json").read_text()
    )
    assert plan["status"] == "frozen_protocol_before_v3.2.5_code_freeze_or_evaluation"
    assert plan["historical_series"]["v3.2.4"].startswith("FAIL_CLOSED")
    assert plan["required_formal_gates"]["snapshot_owner_root_group_root"] is True
    assert plan["required_formal_gates"]["evaluator_no_new_privs_and_cap_eff_zero"] is True
    assert plan["not_a_new_holdout"] is True


def test_patch_series_preregistered_external_path_fix() -> None:
    plan = json.loads(
        (ROOT / "reports/rml24-blind-origin-v3.2.5.1-prereg.json").read_text()
    )
    assert plan["status"] == "frozen_protocol_before_v3.2.5.1_code_freeze_or_evaluation"
    assert plan["historical_series"]["v3.2.5"].startswith("FAIL_SETUP")
    source = LAUNCHER.read_text()
    assert source.index('for option in (\n        "--prereg"') < source.index(
        "stage2_command = ["
    )
    assert '"--output",\n        "--output-sha"' in source


def test_launcher_uses_physical_runtime_copy_and_root_owned_stage2() -> None:
    source = LAUNCHER.read_text()
    assert "shutil.copyfile" in source
    assert '"runtime_files"' in source
    assert '"-hR", "0:0"' in source
    assert 'str(snapshot / CHILD_RELATIVE.parent / "launch_blind_origin_v325.py")' in source
    assert "os.link" not in source


def test_restricted_command_is_exact_and_fail_closed() -> None:
    tree = ast.parse(LAUNCHER.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "restricted_command"
    )
    constants = {
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {"--reuid=65534", "--regid=65534", "--clear-groups", "--no-new-privs"} <= constants
    assert "/usr/bin/env" in constants
    assert "/usr/bin/setpriv" in constants


def test_runtime_inventory_excludes_reusable_bytecode() -> None:
    rows = v325.runtime_inventory(ROOT)
    assert rows
    assert not any("__pycache__" in Path(str(row["path"])).parts for row in rows)
    assert not any(Path(str(row["path"])).suffix in {".pyc", ".pyo"} for row in rows)
    assert any(str(row["path"]).endswith("numpy/__init__.py") for row in rows)


def test_root_owned_snapshot_denies_mutate_use_restore_context() -> None:
    if subprocess.run(["sudo", "-n", "true"], check=False).returncode != 0:
        pytest.skip("passwordless sudo unavailable")
    test_root = Path(tempfile.mkdtemp(prefix="rml24-v325-test-", dir="/var/tmp"))
    test_root.chmod(0o755)
    snapshot = test_root / "snapshot"
    snapshot.mkdir()
    target = snapshot / "locked.bin"
    target.write_bytes(b"locked")
    subprocess.run(
        ["sudo", "-n", "/usr/bin/chown", "-R", "0:0", str(snapshot)], check=True
    )
    subprocess.run(
        ["sudo", "-n", "/usr/bin/chmod", "0555", str(snapshot)], check=True
    )
    subprocess.run(
        ["sudo", "-n", "/usr/bin/chmod", "0444", str(target)], check=True
    )
    script = (
        "import json,os,pathlib; p=pathlib.Path(os.environ['TARGET']); out={}; "
        "\nfor name,fn in [('write',lambda:p.open('ab').close()),"
        "('chmod',lambda:p.chmod(0o644)),"
        "('unlink',lambda:p.unlink()),"
        "('create',lambda:(p.parent/'new').open('xb').close())]:"
        "\n try: fn(); out[name]='SUCCEEDED'"
        "\n except OSError as e: out[name]=e.errno"
        "\nprint(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'groups':os.getgroups(),'results':out}))"
    )
    command = [
        "sudo",
        "-n",
        "/usr/bin/env",
        "-i",
        f"TARGET={target}",
        "/usr/bin/setpriv",
        "--reuid=65534",
        "--regid=65534",
        "--clear-groups",
        "--no-new-privs",
        "/usr/bin/python3",
        "-B",
        "-c",
        script,
    ]
    try:
        completed = subprocess.run(command, check=True, text=True, capture_output=True)
        result = json.loads(completed.stdout)
        assert (result["uid"], result["gid"], result["groups"]) == (65534, 65534, [])
        assert set(result["results"].values()) <= {1, 13, 30}
        assert target.read_bytes() == b"locked"
        assert not (snapshot / "new").exists()
    finally:
        subprocess.run(
            ["sudo", "-n", "/usr/bin/chown", "-R", f"{os.getuid()}:{os.getgid()}", str(test_root)],
            check=True,
        )
        subprocess.run(
            ["sudo", "-n", "/usr/bin/chmod", "-R", "u+rwX", str(test_root)],
            check=True,
        )
        shutil.rmtree(test_root)
