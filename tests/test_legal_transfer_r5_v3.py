from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "work/positive-real-iq/r5-v3-runtime"
MANIFEST = ROOT / "reports/real-iq-legal-transfer-r5-runtime-manifest-v3.json"


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


def test_runtime_manifest_binds_frozen_tools_and_current_crc() -> None:
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in document["files"]}
    assert document["construction"]["current_crc_table_sha256"] == "f93c4eca1f91e3d71e30606a90a988a16b2c660e44a8e307181c160bc07849a7"
    required = {
        "tools/make_legal_transfer_r5_v3_null.py",
        "tools/decode_legal_fsk_transfer_rate_v3.py",
        "tools/make_legal_transfer_r5_v2_null.py",
        "tools/decode_legal_fsk_transfer.py",
        "native_src/telemetry_yield/crc.py",
        "native_env/bin/python",
        "golden_env/bin/python",
        "golden_env/bin/gr_satellites",
    }
    assert required <= rows.keys()
    for relative in required:
        row = rows[relative]
        path = RUNTIME / relative
        if row["type"] == "file":
            assert sha256_file(path) == row["sha256"]
        else:
            assert path.is_symlink() and os.readlink(path) == row["target"]
    editable = RUNTIME / "native_env/lib/python3.12/site-packages/__editable__.telemetry_yield-0.1.0.pth"
    assert editable.read_text(encoding="utf-8").strip() == str(RUNTIME / "native_src")
    assert not any(path.stat().st_mode & 0o222 for path in [RUNTIME, RUNTIME / "native_src", RUNTIME / "tools"])


def test_frozen_transform_primitive_is_deterministic_and_destructive() -> None:
    primitive = load(RUNTIME / "tools/make_legal_transfer_r5_v2_null.py", "test_r5_v3_primitive")
    block = np.arange(64, dtype="<i2").reshape(-1, 2)
    outputs = []
    for transform in primitive.TRANSFORMS:
        if transform == "full-complex-reversal":
            output = block[::-1]
        else:
            first = primitive.transform_block(
                block, transform=transform,
                rng=np.random.Generator(np.random.PCG64(primitive.seed_for(17, transform))),
            )
            second = primitive.transform_block(
                block, transform=transform,
                rng=np.random.Generator(np.random.PCG64(primitive.seed_for(17, transform))),
            )
            assert np.array_equal(first, second)
            output = first
        assert not np.array_equal(output, block)
        outputs.append(output.tobytes())
    assert len(set(outputs)) == len(primitive.TRANSFORMS)


def test_runner_commands_bind_snapshot_argv0_and_full_runtime_arguments() -> None:
    runner = load(ROOT / "work/positive-real-iq/run_legal_transfer_r5_v3.py", "test_r5_v3_runner")
    plan = {
        "runtime_snapshot": {
            "manifest_path": str(MANIFEST.relative_to(ROOT)),
            "manifest_sha256": sha256_file(MANIFEST),
        }
    }
    row = {
        "observation_id": 17, "mission": "TEST", "sample_rate_hz": 57600,
        "baudrate": 9600, "native_decimation": 2, "native_g3ruh": True,
    }
    plan_path = ROOT / "reports/placeholder-r5-v3-plan.json"
    generator = runner.generator_command(plan, plan_path, "f" * 64, row, "all-zero")
    baseline = runner.baseline_command(row, ROOT / "placeholder.kiss")
    native = runner.native_command(plan, row, ROOT / "placeholder.json")
    assert generator[0] == native[0] == str(RUNTIME / "native_env/bin/python")
    assert baseline[0] == str(RUNTIME / "golden_env/bin/python")
    assert generator[1].startswith(str(RUNTIME / "tools/"))
    assert native[1].startswith(str(RUNTIME / "tools/"))
    assert baseline[1] == str(RUNTIME / "golden_env/bin/gr_satellites")
    for command in (generator, native):
        assert "--runtime-root" in command and str(RUNTIME) in command
        assert "--expected-runtime-manifest-sha256" in command
