from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/audit_candidate_lineage.py"
FREEZE = ROOT / "reports/blind-phase-fsk-dev13168691-v4-3-final-freeze.json"
OVERRIDES = (
    ROOT
    / "releases/blind-phase-confirmatory-v2/config/candidate-overrides-v4-3-final.json"
)
CANDIDATE = (
    ROOT
    / "releases/blind-phase-confirmatory-v2/config/candidate-config-v4-3-final-r4.json"
)
GENERATOR = ROOT / "work/blind-phase-confirmatory-v2/build_candidate_release.py"


def _module():
    spec = importlib.util.spec_from_file_location("candidate_lineage_audit_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AUDIT = _module()


def _documents():
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    generator, _ = AUDIT._load_generator(
        candidate,
        generator_path=GENERATOR,
        expected_generator_sha256=candidate["generator"]["sha256"],
    )
    return freeze, overrides, candidate, generator


def test_final_r4_lineage_is_type_exact() -> None:
    freeze, overrides, candidate, generator = _documents()
    checks = AUDIT.validate_documents(
        freeze=freeze,
        overrides=overrides,
        candidate=candidate,
        generator_module=generator,
    )
    assert checks["candidate_9600_type_exact_frozen_v4_3"] is True
    assert set(checks["receiver_baudrate_json_types"].values()) == {"float"}


def test_integer_inner_baudrate_is_rejected() -> None:
    freeze, overrides, candidate, generator = _documents()
    candidate = copy.deepcopy(candidate)
    candidate["rate_configs"][2]["receiver_config"]["baudrate"] = 9_600
    with pytest.raises(ValueError, match="differs beyond the float baudrate"):
        AUDIT.validate_documents(
            freeze=freeze,
            overrides=overrides,
            candidate=candidate,
            generator_module=generator,
        )


def test_receiver_rule_drift_is_rejected() -> None:
    freeze, overrides, candidate, generator = _documents()
    candidate = copy.deepcopy(candidate)
    candidate["rate_configs"][0]["receiver_config"]["phase_bins"] += 1
    with pytest.raises(ValueError, match="differ.*baudrate"):
        AUDIT.validate_documents(
            freeze=freeze,
            overrides=overrides,
            candidate=candidate,
            generator_module=generator,
        )


def test_wrong_expected_sha_is_rejected() -> None:
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        AUDIT._load_json(FREEZE, expected_sha256="0" * 64)


def test_publish_is_self_hashed_immutable_and_no_clobber(tmp_path: Path) -> None:
    report = {"schema_version": "test", "status": "FREEZE_GO"}
    report["report_payload_sha256"] = AUDIT._sha256_bytes(AUDIT._canonical(report))
    output = tmp_path / "audit.json"
    digest = AUDIT.publish(output, report)
    sidecar = Path(str(output) + ".sha256")
    assert output.stat().st_mode & 0o777 == 0o444
    assert sidecar.stat().st_mode & 0o777 == 0o444
    assert sidecar.read_text(encoding="ascii") == f"{digest}  {output.name}\n"
    loaded = json.loads(output.read_text(encoding="utf-8"))
    claimed = loaded.pop("report_payload_sha256")
    assert claimed == AUDIT._sha256_bytes(AUDIT._canonical(loaded))
    with pytest.raises(FileExistsError):
        AUDIT.publish(output, report)
