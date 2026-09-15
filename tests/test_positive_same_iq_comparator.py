from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from telemetry_yield.ax25_validation import parse_ax25_ui
from telemetry_yield.crc import append_ax25_fcs, validate_ax25_fcs
from telemetry_yield.positive_same_iq_comparator import (
    POSITIVE_G3RUH_OBSERVATION_IDS,
    compare_positive_same_iq,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return payload


def _address(callsign: str, *, final: bool) -> bytes:
    return bytes(value << 1 for value in callsign.ljust(6).encode()) + bytes(
        [0x60 | int(final)]
    )


def _ax25(information: bytes) -> bytes:
    return (
        _address("CQ", final=False)
        + _address("N0CALL", final=True)
        + b"\x03\xf0"
        + information
    )


def _official_audit(
    tmp_path: Path, candidates: dict[int, list[bytes]] | None = None
) -> tuple[Path, dict[int, str]]:
    candidates = candidates or {}
    root = tmp_path / "official-campaign"
    manifest_path = root / "manifest.json"
    manifest_payload = _write_json(
        manifest_path, {"schema_version": "gr-satellites-batch-v1"}
    )
    input_hashes: dict[int, str] = {}
    observations = []
    raw_count = 0
    rejected_count = 0
    trusted_count = 0
    for observation_id in POSITIVE_G3RUH_OBSERVATION_IDS:
        attempt = root / f"obs-{observation_id}" / "attempt-test"
        input_sha = _sha256(f"iq-{observation_id}".encode())
        input_hashes[observation_id] = input_sha
        audited_candidates = []
        for index, payload in enumerate(candidates.get(observation_id, [])):
            payload_path = attempt / "candidates" / f"{index}.bin"
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            payload_path.write_bytes(payload)
            valid = parse_ax25_ui(payload) is not None
            audited_candidates.append(
                {
                    "candidate_index": index,
                    "classification": "trusted_ax25_ui" if valid else "rejected",
                    "raw_pdu": {
                        "path": str(payload_path),
                        "actual_size_bytes": len(payload),
                        "actual_sha256": _sha256(payload),
                        "verified": True,
                        "payload_hex": payload.hex(),
                    },
                }
            )
            raw_count += 1
            trusted_count += int(valid)
            rejected_count += int(not valid)
        result = {
            "schema_version": "gr-satellites-attempt-v1",
            "observation_id": observation_id,
            "status": "completed",
            "input": {"sha256": input_sha, "size_bytes": 32},
            "candidate_count": len(audited_candidates),
        }
        result_payload = _write_json(attempt / "result.json", result)
        observations.append(
            {
                "observation_id": observation_id,
                "manifest_status": "completed",
                "audit_status": "audited",
                "result_metadata_valid": True,
                "artifact_integrity_valid": True,
                "source_result": {
                    "path": str(attempt / "result.json"),
                    "size_bytes": len(result_payload),
                    "sha256": _sha256(result_payload),
                },
                "raw_pdu_count": len(audited_candidates),
                "candidates": audited_candidates,
            }
        )
    audit = {
        "schema_version": "gr-satellites-batch-audit-v1",
        "campaign_complete": True,
        "audit_complete": True,
        "manifest_consistent": True,
        "source_manifest": {
            "path": str(manifest_path),
            "size_bytes": len(manifest_payload),
            "sha256": _sha256(manifest_payload),
        },
        "raw_pdu_count": raw_count,
        "trusted_ax25_ui_count": trusted_count,
        "rejected_pdu_count": rejected_count,
        "observations": observations,
    }
    audit_path = tmp_path / "official-audit.json"
    _write_json(audit_path, audit)
    return audit_path, input_hashes


def _native_manifest(
    tmp_path: Path,
    input_hashes: dict[int, str],
    frames: dict[int, list[bytes]],
    *,
    complete: bool,
    selected_ids: tuple[int, ...] | None = None,
    input_override: dict[int, str] | None = None,
) -> Path:
    root = tmp_path / "native-campaign"
    selected_ids = selected_ids or POSITIVE_G3RUH_OBSERVATION_IDS
    input_override = input_override or {}
    results = []
    for observation_id in selected_ids:
        phase_path = root / f"obs-{observation_id}" / "phase.json"
        raw_frames = [
            {
                "frame_with_fcs_hex": frame.hex(),
                "frame_with_fcs_sha256": _sha256(frame),
            }
            for frame in frames.get(observation_id, [])
        ]
        phase = {
            "stage": "decode-only",
            "observation_id": observation_id,
            "unique_crc_valid_frame_count": len(raw_frames),
            "unique_crc_valid_frames": raw_frames,
        }
        phase_payload = _write_json(phase_path, phase)
        trusted = []
        for frame in frames.get(observation_id, []):
            if validate_ax25_fcs(frame) and parse_ax25_ui(frame[:-2]) is not None:
                trusted.append(_sha256(frame[:-2]))
        comparison = {
            "schema_version": "matched-iq-positive-observation-v1",
            "complete": True,
            "observation_id": observation_id,
            "input": {
                "ci16_sha256": input_override.get(
                    observation_id, input_hashes[observation_id]
                )
            },
            "native": {
                "source_artifact": str(phase_path),
                "source_artifact_sha256": _sha256(phase_payload),
                "raw_crc_valid_candidate_count": len(raw_frames),
                "trusted_payload_sha256": sorted(set(trusted)),
                "strict_audit": {},
            },
        }
        comparison_path = root / f"obs-{observation_id}" / "comparison.json"
        comparison_payload = _write_json(comparison_path, comparison)
        results.append(
            {
                "observation_id": observation_id,
                "status": "processed",
                "comparison_path": str(comparison_path),
                "comparison_sha256": _sha256(comparison_payload),
            }
        )
    manifest = {
        "schema_version": "matched-iq-positive-benchmark-manifest-v1",
        "complete": complete,
        "selection_contract": {
            "mode_exact": "FSK AX.25 G3RUH",
            "frames_recovered": True,
            "expected_observation_count": 19,
        },
        "candidate_count": 19,
        "status_counts": {"processed": len(results)},
        "results": results,
    }
    manifest_path = root / "benchmark-manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def test_missing_native_manifest_yields_deterministic_incomplete_report(
    tmp_path: Path,
) -> None:
    valid = _ax25(b"baseline")
    official_path, _ = _official_audit(
        tmp_path, {POSITIVE_G3RUH_OBSERVATION_IDS[0]: [valid, b"rejected"]}
    )

    report = compare_positive_same_iq(
        official_path, tmp_path / "native-campaign" / "benchmark-manifest.json"
    )

    assert report["complete"] is False
    assert report["status"] == "incomplete_native"
    assert report["summary"]["official_observation_count"] == 19
    assert report["summary"]["baseline_raw_candidate_count"] == 2
    assert report["summary"]["baseline_raw_rejected_count"] == 1
    assert report["summary"]["baseline_trusted_unique_count"] == 1
    assert report["summary"]["native_completed_observation_count"] == 0
    first = report["observations"][0]
    assert first["sets"]["baseline_count"] == 1
    assert first["sets"]["native_count"] is None
    assert first["sets"]["union_count"] is None


def test_complete_native_manifest_reports_union_and_incremental(tmp_path: Path) -> None:
    first_id, second_id = POSITIVE_G3RUH_OBSERVATION_IDS[:2]
    overlap = _ax25(b"overlap")
    incremental = _ax25(b"native-only")
    official_path, hashes = _official_audit(
        tmp_path, {first_id: [overlap, b"official-rejected"]}
    )
    native_path = _native_manifest(
        tmp_path,
        hashes,
        {
            first_id: [append_ax25_fcs(overlap), append_ax25_fcs(b"crc-collision")],
            second_id: [append_ax25_fcs(incremental)],
        },
        complete=True,
    )

    report = compare_positive_same_iq(official_path, native_path)

    assert report["complete"] is True
    assert report["summary"]["native_completed_observation_count"] == 19
    assert report["summary"]["native_raw_candidate_count_completed_subset"] == 3
    assert report["summary"]["native_raw_rejected_count_completed_subset"] == 1
    assert report["summary"]["native_trusted_unique_count_completed_subset"] == 2
    assert report["summary"]["union_count_full_cohort"] == 2
    assert report["summary"]["incremental_over_baseline_full_cohort"] == 1
    first = report["observations"][0]
    assert first["identical_input_sha256"] is True
    assert first["sets"]["baseline_count"] == 1
    assert first["sets"]["native_count"] == 1
    assert first["sets"]["overlap_count"] == 1
    assert first["sets"]["union_count"] == 1
    assert first["sets"]["incremental_over_baseline"] == 0
    assert first["native"]["raw_rejected_count"] == 1
    second = report["observations"][1]
    assert second["sets"]["incremental_over_baseline"] == 1


def test_partial_native_manifest_computes_only_completed_subset(tmp_path: Path) -> None:
    first_id = POSITIVE_G3RUH_OBSERVATION_IDS[0]
    native_only = _ax25(b"partial")
    official_path, hashes = _official_audit(tmp_path)
    native_path = _native_manifest(
        tmp_path,
        hashes,
        {first_id: [append_ax25_fcs(native_only)]},
        complete=False,
        selected_ids=(first_id,),
    )

    report = compare_positive_same_iq(official_path, native_path)

    assert report["complete"] is False
    assert report["summary"]["native_completed_observation_count"] == 1
    assert report["summary"]["union_count_completed_subset"] == 1
    assert report["summary"]["union_count_full_cohort"] is None
    assert report["observations"][1]["native"]["available"] is False


def test_input_sha_mismatch_is_rejected(tmp_path: Path) -> None:
    first_id = POSITIVE_G3RUH_OBSERVATION_IDS[0]
    official_path, hashes = _official_audit(tmp_path)
    native_path = _native_manifest(
        tmp_path,
        hashes,
        {},
        complete=False,
        selected_ids=(first_id,),
        input_override={first_id: "0" * 64},
    )

    with pytest.raises(ValueError, match="input SHA-256 mismatch"):
        compare_positive_same_iq(official_path, native_path)

