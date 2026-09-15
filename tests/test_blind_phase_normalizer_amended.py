from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
FROZEN_PATH = ROOT / "work/blind-phase-confirmatory-v2/normalize_result.py"
AMENDED_PATH = ROOT / "work/blind-phase-confirmatory-v2/normalize_result_amended.py"
FROZEN_SHA256 = "67d9fd3f0464e4be285a788a678a7c2b8f031721322ea6afe2782210fd1f81b7"


def _module():
    spec = importlib.util.spec_from_file_location(
        "blind_phase_normalizer_amended_test", AMENDED_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


NORMALIZER = _module()


def _runtime(root: str) -> dict[str, object]:
    return {
        "golden_root": root,
        "files": {
            "demodulator": {
                "path": f"{root}/lib/demodulator.py",
                "size_bytes": 101,
                "sha256": "1" * 64,
            },
            "python": {
                "path": f"{root}/bin/python",
                "size_bytes": 202,
                "sha256": "2" * 64,
            },
        },
    }


def _validate(raw: object, locked: object) -> None:
    NORMALIZER._validate_component_runtime_identity(raw, locked)


def test_frozen_normalizer_remains_byte_identical() -> None:
    assert hashlib.sha256(FROZEN_PATH.read_bytes()).hexdigest() == FROZEN_SHA256


def test_component_runtime_accepts_exact_sandbox_mount_rebase() -> None:
    _validate(
        _runtime("/runtime/component"),
        _runtime("/home/ubuntu/telemetry-yield/work/golden/env"),
    )


@pytest.mark.parametrize("field,value", [("sha256", "3" * 64), ("size_bytes", 203)])
def test_component_runtime_rejects_identity_drift(field: str, value: object) -> None:
    raw = _runtime("/runtime/component")
    locked = _runtime("/golden/env")
    locked["files"]["python"][field] = value
    with pytest.raises(ValueError, match="differs from frozen lock"):
        _validate(raw, locked)


def test_component_runtime_rejects_relative_path_drift() -> None:
    raw = _runtime("/runtime/component")
    locked = _runtime("/golden/env")
    locked["files"]["python"]["path"] = "/golden/env/sbin/python"
    with pytest.raises(ValueError, match="differs from frozen lock"):
        _validate(raw, locked)


@pytest.mark.parametrize(
    "bad_path,error",
    [
        ("/runtime/outside/python", "escapes golden_root"),
        ("/runtime/component/lib/../bin/python", "entry is malformed"),
        ("runtime/component/bin/python", "entry is malformed"),
    ],
)
def test_component_runtime_rejects_outside_traversal_and_relative_paths(
    bad_path: str, error: str
) -> None:
    raw = _runtime("/runtime/component")
    raw["files"]["python"]["path"] = bad_path
    with pytest.raises(ValueError, match=error):
        _validate(raw, _runtime("/golden/env"))


def test_component_runtime_rejects_duplicate_relative_paths() -> None:
    raw = _runtime("/runtime/component")
    raw["files"]["python"]["path"] = "/runtime/component/lib/demodulator.py"
    with pytest.raises(ValueError, match="duplicated or malformed"):
        _validate(raw, _runtime("/golden/env"))


@pytest.mark.parametrize(
    "mutation",
    [
        "relative_root",
        "root_traversal",
        "missing_file_field",
        "invalid_sha256",
        "boolean_size",
        "empty_files",
    ],
)
def test_component_runtime_rejects_malformed_manifests(mutation: str) -> None:
    raw = _runtime("/runtime/component")
    if mutation == "relative_root":
        raw["golden_root"] = "runtime/component"
    elif mutation == "root_traversal":
        raw["golden_root"] = "/runtime/../component"
    elif mutation == "missing_file_field":
        raw["files"]["python"].pop("sha256")
    elif mutation == "invalid_sha256":
        raw["files"]["python"]["sha256"] = "F" * 64
    elif mutation == "boolean_size":
        raw["files"]["python"]["size_bytes"] = True
    elif mutation == "empty_files":
        raw["files"] = {}
    with pytest.raises(ValueError, match="malformed"):
        _validate(raw, _runtime("/golden/env"))


@pytest.mark.parametrize("level", ["runtime", "record"])
def test_component_runtime_rejects_unexpected_fields(level: str) -> None:
    raw = _runtime("/runtime/component")
    if level == "runtime":
        raw["unexpected"] = "value"
    else:
        raw["files"]["python"]["unexpected"] = "value"
    with pytest.raises(ValueError, match="malformed"):
        _validate(raw, _runtime("/golden/env"))


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_component_runtime_rejects_file_key_set_drift(change: str) -> None:
    raw = _runtime("/runtime/component")
    locked = _runtime("/golden/env")
    if change == "missing":
        locked["files"].pop("python")
    else:
        extra = copy.deepcopy(locked["files"]["python"])
        extra["path"] = "/golden/env/bin/python-extra"
        locked["files"]["python-extra"] = extra
    with pytest.raises(ValueError, match="differs from frozen lock"):
        _validate(raw, locked)
