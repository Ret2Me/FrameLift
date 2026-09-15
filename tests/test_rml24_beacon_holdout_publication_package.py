from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/rml24/build_beacon_holdout_publication_manifest_v1.py"
SPEC = importlib.util.spec_from_file_location("rml24_publication_manifest", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_checked_in_manifest_rebuilds_exactly_and_keeps_narrow_claim() -> None:
    result = MODULE.build_manifest(ROOT)
    stored = json.loads(
        (ROOT / "reports/rml24-beacon-holdout-publication-manifest-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert result == stored
    assert result["status"] == "PASS"
    assert result["physical_layer_claim_package_ready"] is True
    assert result["system_publication_ready"] is False
    assert result["system_deployment_ready"] is False
    scope = result["checked_claim"]["scope"]
    assert scope == {
        "dataset": "RML24 HIL plus synthetic-space-channel",
        "modulations": ["BPSK", "GMSK", "OQPSK", "QPSK"],
        "metric": "micro_BER",
        "fully_blind": False,
        "packet_yield": False,
        "over_the_air_satellite_telemetry": False,
        "satnogs_comparison": False,
        "prior_dataset_wide_aggregate_exposure": True,
        "external_or_organizational_audit": False,
    }


def test_publication_numbers_and_wtl_are_bound_to_independent_audit() -> None:
    result = MODULE.build_manifest(ROOT)
    primary = result["checked_claim"]["primary"]
    assert primary["baseline_errors"] == 420529
    assert primary["candidate_errors"] == 384563
    assert primary["truth_bits"] == 877464
    assert primary["baseline_ber"] == 0.4792549893784816
    assert primary["candidate_ber"] == 0.43826641320897497
    assert primary["candidate_minus_baseline"] == -0.04098857616950663
    assert primary["ci95_candidate_minus_baseline"] == [
        -0.05644367855817216,
        -0.026643939647130008,
    ]
    assert primary["record_win_tie_loss"] == {"wins": 329, "ties": 221, "losses": 458}
    independent = next(
        row
        for row in result["source_artifacts"]
        if row["role"] == "same_host_independent_score_audit"
    )
    assert independent["sha256"] == "02f9ca88341ba8193acd8f31b1e7345831299b733d6c4539bcdd3556fe9a4b72"


def test_docs_disclose_every_required_limitation_and_source() -> None:
    combined = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "docs/rml24-beacon-holdout-publication-v1.md",
            "docs/rml24-beacon-holdout-data-card-v1.md",
            "docs/rml24-beacon-holdout-reproducibility-v1.md",
        )
    ).lower()
    for required in (
        "10.1038/s41597-026-07182-7",
        "hil plus synthetic-space-channel",
        "bpsk",
        "gmsk",
        "oqpsk",
        "qpsk",
        "micro-ber",
        "329/221/458",
        "prior dataset-wide aggregate exposure",
        "transfer-only pre-score",
        "not external",
        "deployment-ready",
    ):
        assert required in combined

    methods = (ROOT / "docs/rml24-beacon-holdout-publication-v1.md").read_text(
        encoding="utf-8"
    )
    for exact_result in (
        "877,464",
        "420,529",
        "384,563",
        "0.4792549894",
        "0.4382664132",
        "-0.0409885762",
        "[-0.0564436786, -0.0266439396]",
        "329/221/458",
    ):
        assert exact_result in methods


def test_expected_artifact_verifier_fails_closed_on_missing_mutated_and_symlink(tmp_path: Path) -> None:
    expected_bytes = b"frozen\n"
    digest = hashlib.sha256(expected_bytes).hexdigest()
    artifact = MODULE.ExpectedArtifact("evidence.json", "test", digest)
    with pytest.raises(FileNotFoundError):
        MODULE.verify_expected_artifacts(tmp_path, [artifact])

    path = tmp_path / "evidence.json"
    path.write_bytes(b"mutated\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        MODULE.verify_expected_artifacts(tmp_path, [artifact])

    path.unlink()
    target = tmp_path / "target.json"
    target.write_bytes(expected_bytes)
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        MODULE.verify_expected_artifacts(tmp_path, [artifact])


def test_sidecar_and_idempotent_writer_fail_closed(tmp_path: Path) -> None:
    payload = tmp_path / "result.json"
    payload.write_text("{}\n", encoding="utf-8")
    payload_hash = hashlib.sha256(payload.read_bytes()).hexdigest()
    sidecar = tmp_path / "result.json.sha256"
    sidecar.write_text(f"{payload_hash}  result.json\n", encoding="utf-8")
    specs = [
        MODULE.ExpectedArtifact("result.json", "result", payload_hash),
        MODULE.ExpectedArtifact(
            "result.json.sha256", "sidecar", hashlib.sha256(sidecar.read_bytes()).hexdigest()
        ),
    ]
    MODULE.verify_sidecars(tmp_path, specs)
    sidecar.write_text(f"{'0' * 64}  result.json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sidecar mismatch"):
        MODULE.verify_sidecars(tmp_path, specs)

    destination = tmp_path / "manifest.json"
    assert MODULE.publish_idempotent(destination, b"one\n") == "created"
    assert MODULE.publish_idempotent(destination, b"one\n") == "already_exact"
    with pytest.raises(ValueError, match="refusing to overwrite"):
        MODULE.publish_idempotent(destination, b"two\n")


def test_manifest_sidecar_is_exact() -> None:
    manifest = ROOT / "reports/rml24-beacon-holdout-publication-manifest-v1.json"
    expected = f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  {manifest.name}\n"
    assert Path(str(manifest) + ".sha256").read_text(encoding="ascii") == expected
