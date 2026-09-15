from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "work/positive-real-iq/r5-v4-runtime"
MANIFEST = ROOT / "reports/real-iq-legal-transfer-r5-runtime-manifest-v4.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_freezes_gr_satellites_config_and_v4_generator() -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in document["files"]}
    for relative in ("home/.gr_satellites/config.ini", "tools/make_legal_transfer_r5_v4_null.py"):
        path = RUNTIME / relative
        assert sha256_file(path) == rows[relative]["sha256"]
        assert not path.stat().st_mode & 0o222
    editable = RUNTIME / "native_env/lib/python3.12/site-packages/__editable__.telemetry_yield-0.1.0.pth"
    assert editable.read_text(encoding="utf-8").strip() == str(RUNTIME / "native_src")


def test_frozen_home_allows_read_only_gr_satellites_open_config() -> None:
    environment = {
        "HOME": str(RUNTIME / "home"), "XDG_CONFIG_HOME": str(RUNTIME / "home/.config"),
        "XDG_CACHE_HOME": str(RUNTIME / "home/.cache"), "PATH": "/usr/bin:/bin",
        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
    }
    completed = subprocess.run(
        [str(RUNTIME / "golden_env/bin/python"), "-c", "from satellites.utils.config import open_config; c=open_config(); print(c['Groundstation']['submit_tlm'])"],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "yes"


def test_v4_runner_binds_snapshot_argv0_and_manifest() -> None:
    runner = load(ROOT / "work/positive-real-iq/run_legal_transfer_r5_v4.py", "test_r5_v4_runner")
    plan = {"runtime_snapshot": {"manifest_path": str(MANIFEST.relative_to(ROOT)), "manifest_sha256": sha256_file(MANIFEST)}}
    row = {"observation_id": 17, "mission": "TEST", "sample_rate_hz": 57600, "baudrate": 9600, "native_decimation": 2, "native_g3ruh": True}
    generator = runner.generator_command(plan, ROOT / "placeholder.json", "f" * 64, row, "all-zero")
    native = runner.native_command(plan, row, ROOT / "placeholder.json")
    base = runner.load_base()
    baseline = base.baseline_command(row, ROOT / "placeholder.kiss")
    assert generator[0] == native[0] == str(RUNTIME / "native_env/bin/python")
    assert baseline[0] == str(RUNTIME / "golden_env/bin/python")
    assert generator[1] == str(RUNTIME / "tools/make_legal_transfer_r5_v4_null.py")
    assert native[1] == str(RUNTIME / "tools/decode_legal_fsk_transfer_rate_v3.py")
    assert "--expected-runtime-manifest-sha256" in generator and "--expected-runtime-manifest-sha256" in native
