from __future__ import annotations

import hashlib
import json
from pathlib import Path

from telemetry_yield.gr_satellites_audit import audit_gr_satellites_campaign


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _address(callsign: str, *, final: bool) -> bytes:
    padded = callsign.ljust(6).encode("ascii")
    return bytes(value << 1 for value in padded) + bytes([0x60 | int(final)])


def _ax25_ui(information: bytes) -> bytes:
    return _address("CQ", final=False) + _address("N0CALL", final=True) + b"\x03\xf0" + information


def _ccsds_packet() -> bytes:
    # Version 0, telemetry, secondary header present, APID 1; one complete
    # three-byte data field (encoded length is N - 1).
    return b"\x08\x01\xc0\x02\x00\x02abc"


def _artifact_reference(path: Path, payload: bytes) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": str(path),
        "size_bytes": len(payload),
        "sha256": _sha256(payload),
    }


def _completed_campaign(
    tmp_path: Path, payloads: list[bytes]
) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "campaign"
    attempt = root / "obs-7" / "attempt-test"
    stdout = _artifact_reference(attempt / "stdout.bin", b"stdout")
    stderr = _artifact_reference(attempt / "stderr.bin", b"")
    candidates = []
    for index, payload in enumerate(payloads):
        artifact = _artifact_reference(attempt / "candidates" / f"{index}.bin", payload)
        candidates.append(
            {
                "candidate_index": index,
                "classification": "unvalidated_pdu_candidate",
                "validated": False,
                "validation_layers": [],
                "payload_path": artifact["path"],
                "payload_size_bytes": artifact["size_bytes"],
                "payload_sha256": artifact["sha256"],
                "provenance": {
                    "backend_id": "gr_satellites",
                    "backend_version": "test",
                    "parser": {"container": "kiss", "kiss_port": 0},
                },
            }
        )
    result = {
        "schema_version": "gr-satellites-attempt-v1",
        "observation_id": 7,
        "attempt_fingerprint": "attempt-fingerprint",
        "status": "completed",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "stdout": stdout,
        "stderr": stderr,
        "profile": {
            "name": "TESTSAT",
            "norad_id": 99999,
            "framing": ["AX.25 G3RUH"],
            "modulations": ["FSK"],
            "transmitters": [{"transmitter_id": "downlink"}],
        },
        "route": {"observation_id": 7, "satellite_id": "SAT-UUID"},
    }
    attempt.mkdir(parents=True, exist_ok=True)
    (attempt / "result.json").write_text(json.dumps(result), encoding="utf-8")
    job = {
        "observation_id": 7,
        "status": "completed",
        "candidate_count": len(candidates),
        "attempt_fingerprint": "attempt-fingerprint",
        "artifact_directory": str(attempt),
        "resumed": False,
    }
    manifest = {
        "schema_version": "gr-satellites-batch-v1",
        "campaign_fingerprint": "campaign-fingerprint",
        "config": {"max_output_bytes": 4096, "max_kiss_bytes": 4096},
        "job_count": 1,
        "status_counts": {"completed": 1},
        "jobs": [job],
    }
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, result


def test_audit_classifies_strict_ax25_and_exact_inner_ccsds(tmp_path: Path) -> None:
    valid = _ax25_ui(_ccsds_packet())
    manifest_path, _ = _completed_campaign(tmp_path, [valid, b"not AX.25"])

    audit = audit_gr_satellites_campaign(manifest_path)

    assert audit["campaign_complete"] is True
    assert audit["audit_complete"] is True
    assert audit["raw_pdu_count"] == 2
    assert audit["verified_raw_pdu_count"] == 2
    assert audit["trusted_ax25_ui_count"] == 1
    assert audit["rejected_pdu_count"] == 1
    assert audit["inner_ccsds_count"] == 1
    observation = audit["observations"][0]
    assert observation["profile"]["name"] == "TESTSAT"
    accepted = observation["candidates"][0]
    assert accepted["classification"] == "trusted_ax25_ui"
    assert accepted["raw_pdu"]["payload_hex"] == valid.hex()
    assert accepted["trusted_ax25_ui"]["source"]["callsign"] == "N0CALL"
    assert accepted["inner_ccsds_space_packet"]["apid"] == 1
    assert accepted["provenance"]["backend_id"] == "gr_satellites"


def test_audit_rejects_payload_sha_mismatch_without_parsing_it(tmp_path: Path) -> None:
    valid = _ax25_ui(b"telemetry")
    manifest_path, result = _completed_campaign(tmp_path, [valid])
    candidate_path = Path(result["candidates"][0]["payload_path"])
    candidate_path.write_bytes(_ax25_ui(b"tampered!"))

    audit = audit_gr_satellites_campaign(manifest_path)

    assert audit["trusted_ax25_ui_count"] == 0
    assert audit["rejected_pdu_count"] == 1
    candidate = audit["observations"][0]["candidates"][0]
    assert candidate["classification"] == "rejected"
    assert candidate["raw_pdu"]["verified"] is False
    assert any("SHA-256" in reason for reason in candidate["rejection_reasons"])
    assert audit["audit_complete"] is False


def test_incomplete_active_manifest_is_auditable_and_explicit(tmp_path: Path) -> None:
    manifest_path, _ = _completed_campaign(tmp_path, [])
    manifest = json.loads(manifest_path.read_text())
    manifest["jobs"].append(
        {"observation_id": 8, "status": "running", "candidate_count": 0}
    )
    manifest["job_count"] = 2
    manifest["status_counts"] = {"completed": 1, "running": 1}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    audit = audit_gr_satellites_campaign(manifest_path)

    assert audit["campaign_complete"] is False
    assert audit["audit_complete"] is False
    assert audit["terminal_observation_count"] == 1
    assert audit["audited_observation_count"] == 1
    assert audit["observations"][1]["audit_status"] == "not_terminal"


def test_manifest_artifact_path_escape_is_rejected(tmp_path: Path) -> None:
    manifest_path, _ = _completed_campaign(tmp_path, [])
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text("{}", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text())
    manifest["jobs"][0]["artifact_directory"] = str(outside)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    audit = audit_gr_satellites_campaign(manifest_path)

    observation = audit["observations"][0]
    assert observation["audit_status"] == "result_rejected"
    assert any("escapes" in item for item in observation["diagnostics"])
    assert audit["audit_complete"] is False


def test_result_symlink_escape_is_rejected(tmp_path: Path) -> None:
    manifest_path, _ = _completed_campaign(tmp_path, [])
    manifest = json.loads(manifest_path.read_text())
    attempt = Path(manifest["jobs"][0]["artifact_directory"])
    outside_result = tmp_path / "outside-result.json"
    outside_result.write_bytes((attempt / "result.json").read_bytes())
    (attempt / "result.json").unlink()
    (attempt / "result.json").symlink_to(outside_result)

    audit = audit_gr_satellites_campaign(manifest_path)

    observation = audit["observations"][0]
    assert observation["audit_status"] == "result_rejected"
    assert any("escapes" in item for item in observation["diagnostics"])
