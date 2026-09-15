from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from telemetry_yield.ax25_validation import parse_ax25_ui
from telemetry_yield.ccsds_validation import parse_ccsds_space_packet
from telemetry_yield.crc import append_ax25_fcs, validate_ax25_fcs
from telemetry_yield.zero_g3ruh_same_iq_comparator import (
    ZERO_G3RUH_OBSERVATION_IDS,
    compare_zero_g3ruh_same_iq,
    render_zero_g3ruh_comparison_markdown,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, document: object) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    return payload


def _address(callsign: str, *, final: bool) -> bytes:
    return bytes(value << 1 for value in callsign.ljust(6).encode()) + bytes(
        (0x60 | int(final),)
    )


def _ax25(information: bytes) -> bytes:
    return (
        _address("CQ", final=False)
        + _address("N0CALL", final=True)
        + b"\x03\xf0"
        + information
    )


def _space_packet(apid: int) -> bytes:
    packet_id = (apid & 0x07FF).to_bytes(2, "big")
    return packet_id + bytes.fromhex("c0010002") + b"abc"


def _input_identity(observation_id: int) -> tuple[str, int]:
    return _sha256(f"iq-{observation_id}".encode()), 1000 + observation_id


def _native_fixture(
    tmp_path: Path,
    frames: dict[int, list[bytes]] | None = None,
) -> tuple[Path, Path]:
    frames = frames or {}
    root = tmp_path / "native"
    results = []
    audit_observations = []
    raw_total = trusted_total = inner_total = 0
    for observation_id in ZERO_G3RUH_OBSERVATION_IDS:
        observation_frames = frames.get(observation_id, [])
        records = [
            {
                "frame_with_fcs_hex": frame.hex(),
                "frame_with_fcs_sha256": _sha256(frame),
            }
            for frame in observation_frames
        ]
        artifact = {
            "stage": "decode-only",
            "observation_id": observation_id,
            "unique_crc_valid_frame_count": len(records),
            "unique_crc_valid_frames": records,
        }
        artifact_path = root / f"obs-{observation_id}" / "phase.json"
        artifact_payload = _write_json(artifact_path, artifact)
        input_sha, input_size = _input_identity(observation_id)
        results.append(
            {
                "schema_version": "archive-g3ruh-processing-checkpoint-v1",
                "observation_id": observation_id,
                "status": "processed",
                "ci16_sha256": input_sha,
                "ci16_size_bytes": input_size,
                "decode_output_path": str(artifact_path),
                "decode_output_size_bytes": len(artifact_payload),
                "decode_output_sha256": _sha256(artifact_payload),
                "crc_valid_frame_count": len(records),
            }
        )
        audited_candidates = []
        trusted_count = inner_count = 0
        for frame in observation_frames:
            fcs_valid = validate_ax25_fcs(frame)
            parsed = parse_ax25_ui(frame[:-2]) if fcs_valid else None
            ccsds = (
                parse_ccsds_space_packet(
                    parsed.information, require_exact_length=True
                )
                if parsed is not None
                else None
            )
            trusted_count += int(parsed is not None)
            inner_count += int(ccsds is not None)
            audited_candidates.append(
                {
                    "frame_with_fcs_sha256": _sha256(frame),
                    "frame_length_bytes": len(frame),
                    "crc16_x25_valid": fcs_valid,
                    "ax25_ui_structurally_valid": parsed is not None,
                    "trusted_for_explicit_ax25_campaign": parsed is not None,
                    "inner_ccsds_space_packet": {} if ccsds is not None else None,
                }
            )
        raw_total += len(observation_frames)
        trusted_total += trusted_count
        inner_total += inner_count
        audit_observations.append(
            {
                "schema_version": "archive-g3ruh-protocol-audit-v1",
                "observation_id": observation_id,
                "source_artifact": str(artifact_path),
                "source_artifact_sha256": _sha256(artifact_payload),
                "raw_crc_valid_candidate_count": len(observation_frames),
                "trusted_ax25_ui_frame_count": trusted_count,
                "rejected_crc_collision_count": len(observation_frames)
                - trusted_count,
                "ax25_with_exact_inner_ccsds_count": inner_count,
                "candidates": audited_candidates,
            }
        )
    manifest = {
        "schema_version": "archive-g3ruh-processing-manifest-v1",
        "complete": True,
        "expected_observation_ids": list(ZERO_G3RUH_OBSERVATION_IDS),
        "selection_contract": {
            "mode_exact": "FSK AX.25 G3RUH",
            "frames_recovered": False,
            "generic_fsk_implies_ax25": False,
            "input_datatype": "ci16_le interleaved I,Q",
            "sample_rate_hz": 57_600,
        },
        "results": results,
    }
    manifest_path = root / "processing-merged-manifest.json"
    manifest_payload = _write_json(manifest_path, manifest)
    audit = {
        "schema_version": "archive-g3ruh-protocol-audit-v1",
        "campaign_complete": True,
        "source_manifests": [
            {"path": str(manifest_path), "sha256": _sha256(manifest_payload)}
        ],
        "audited_observation_count": 28,
        "raw_crc_valid_candidate_count": raw_total,
        "trusted_ax25_ui_frame_count": trusted_total,
        "rejected_crc_collision_count": raw_total - trusted_total,
        "ax25_with_exact_inner_ccsds_count": inner_total,
        "observations": audit_observations,
    }
    audit_path = root / "audit.json"
    _write_json(audit_path, audit)
    return manifest_path, audit_path


def _official_fixture(
    tmp_path: Path,
    candidates: dict[int, list[bytes]] | None = None,
    *,
    sha_override: dict[int, str] | None = None,
    size_override: dict[int, int] | None = None,
    include_extra: bool = True,
    missing_id: int | None = None,
) -> tuple[Path, Path]:
    candidates = candidates or {}
    sha_override = sha_override or {}
    size_override = size_override or {}
    root = tmp_path / "official"
    ids = [item for item in ZERO_G3RUH_OBSERVATION_IDS if item != missing_id]
    if include_extra:
        ids.append(9999)
    jobs = []
    audit_observations = []
    raw_total = trusted_total = inner_total = 0
    for observation_id in ids:
        attempt = root / f"obs-{observation_id}" / "attempt-test"
        result_candidates = []
        audited_candidates = []
        trusted_count = inner_count = 0
        for index, payload in enumerate(candidates.get(observation_id, [])):
            payload_path = attempt / "candidates" / f"{index}.bin"
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            payload_path.write_bytes(payload)
            result_candidates.append(
                {
                    "candidate_index": index,
                    "payload_path": str(payload_path),
                    "payload_size_bytes": len(payload),
                    "payload_sha256": _sha256(payload),
                }
            )
            parsed = parse_ax25_ui(payload)
            ccsds = (
                parse_ccsds_space_packet(
                    parsed.information, require_exact_length=True
                )
                if parsed is not None
                else None
            )
            trusted_count += int(parsed is not None)
            inner_count += int(ccsds is not None)
            audited_candidates.append(
                {
                    "candidate_index": index,
                    "classification": (
                        "trusted_ax25_ui" if parsed is not None else "rejected"
                    ),
                    "trusted_ax25_ui": {} if parsed is not None else None,
                    "inner_ccsds_space_packet": {} if ccsds is not None else None,
                    "raw_pdu": {
                        "path": str(payload_path),
                        "actual_size_bytes": len(payload),
                        "actual_sha256": _sha256(payload),
                        "verified": True,
                        "payload_hex": payload.hex(),
                    },
                }
            )
        default_sha, default_size = _input_identity(observation_id)
        result = {
            "schema_version": "gr-satellites-attempt-v1",
            "observation_id": observation_id,
            "attempt_fingerprint": f"attempt-{observation_id}",
            "status": "completed",
            "input": {
                "sha256": sha_override.get(observation_id, default_sha),
                "size_bytes": size_override.get(observation_id, default_size),
            },
            "profile": {
                "name": f"profile-{observation_id}",
                "framing": ["AX.25 G3RUH"],
                "modulations": ["FSK"],
            },
            "candidate_count": len(result_candidates),
            "candidates": result_candidates,
        }
        result_path = attempt / "result.json"
        result_payload = _write_json(result_path, result)
        jobs.append(
            {
                "observation_id": observation_id,
                "status": "completed",
                "attempt_fingerprint": f"attempt-{observation_id}",
                "candidate_count": len(result_candidates),
                "artifact_directory": str(attempt),
            }
        )
        raw_total += len(result_candidates)
        trusted_total += trusted_count
        inner_total += inner_count
        audit_observations.append(
            {
                "observation_id": observation_id,
                "manifest_status": "completed",
                "audit_status": "audited",
                "result_metadata_valid": True,
                "artifact_integrity_valid": True,
                "source_result": {
                    "path": str(result_path),
                    "size_bytes": len(result_payload),
                    "sha256": _sha256(result_payload),
                },
                "raw_pdu_count": len(result_candidates),
                "verified_raw_pdu_count": len(result_candidates),
                "trusted_ax25_ui_count": trusted_count,
                "rejected_pdu_count": len(result_candidates) - trusted_count,
                "inner_ccsds_count": inner_count,
                "candidates": audited_candidates,
            }
        )
    manifest = {
        "schema_version": "gr-satellites-batch-v1",
        "job_count": len(jobs),
        "status_counts": {"completed": len(jobs)},
        "jobs": jobs,
    }
    manifest_path = root / "manifest.json"
    manifest_payload = _write_json(manifest_path, manifest)
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
        "raw_pdu_count": raw_total,
        "rejected_pdu_count": raw_total - trusted_total,
        "trusted_ax25_ui_count": trusted_total,
        "inner_ccsds_count": inner_total,
        "observations": audit_observations,
    }
    audit_path = root / "audit.json"
    _write_json(audit_path, audit)
    return manifest_path, audit_path


def _compare(
    native: tuple[Path, Path], official: tuple[Path, Path]
) -> dict[str, object]:
    native_manifest, native_audit = native
    official_manifest, official_audit = official
    return compare_zero_g3ruh_same_iq(
        native_manifest_path=native_manifest,
        native_audit_path=native_audit,
        official_manifest_path=official_manifest,
        official_audit_path=official_audit,
    )


def test_zero_trusted_on_both_sides_is_parity_not_a_victory(tmp_path: Path) -> None:
    report = _compare(_native_fixture(tmp_path), _official_fixture(tmp_path))

    assert report["complete"] is True
    assert report["cohort"]["observation_count"] == 28
    assert report["cohort"]["official_full_campaign_observation_count"] == 29
    assert report["cohort"]["official_observations_excluded_from_comparison"] == 1
    assert report["summary"]["official_trusted_ax25_unique_count"] == 0
    assert report["summary"]["native_trusted_ax25_unique_count"] == 0
    assert report["summary"]["trusted_union_count"] == 0
    assert report["outcome"] == {
        "classification": "parity_zero_equals_zero",
        "parity": True,
        "winner": None,
        "statement": (
            "Both branches recovered zero strictly trusted AX.25 frames on the "
            "same 28 IQ captures; this is 0=0 parity, not a decoder victory."
        ),
    }
    assert "0=0 parity, not a decoder victory" in render_zero_g3ruh_comparison_markdown(
        report
    )


def test_reports_raw_rejected_trusted_inner_and_set_arithmetic(tmp_path: Path) -> None:
    observation_id = ZERO_G3RUH_OBSERVATION_IDS[0]
    overlap = _ax25(b"overlap")
    official_only = _ax25(_space_packet(7))
    native_only = _ax25(_space_packet(8))
    native = _native_fixture(
        tmp_path,
        {
            observation_id: [
                append_ax25_fcs(overlap),
                append_ax25_fcs(native_only),
                append_ax25_fcs(b"short"),
            ]
        },
    )
    official = _official_fixture(
        tmp_path,
        {observation_id: [overlap, official_only, b"short"]},
    )

    report = _compare(native, official)
    summary = report["summary"]
    assert summary["official_raw_candidate_count"] == 3
    assert summary["official_strict_rejected_count"] == 1
    assert summary["official_trusted_ax25_unique_count"] == 2
    assert summary["official_exact_inner_ccsds_unique_count"] == 1
    assert summary["native_raw_candidate_count"] == 3
    assert summary["native_strict_rejected_count"] == 1
    assert summary["native_trusted_ax25_unique_count"] == 2
    assert summary["native_exact_inner_ccsds_unique_count"] == 1
    assert summary["trusted_overlap_count"] == 1
    assert summary["trusted_union_count"] == 3
    assert summary["incremental_native_over_official"] == 1
    assert summary["official_only_trusted_count"] == 1
    first = report["observations"][0]
    assert first["sets"]["overlap_count"] == 1
    assert first["sets"]["union_count"] == 3
    assert first["official_gr_satellites"]["raw_candidates"][2][
        "classification"
    ] == "strict_rejected"


@pytest.mark.parametrize("identity", ["sha", "size"])
def test_input_identity_mismatch_is_fail_closed(
    tmp_path: Path, identity: str
) -> None:
    observation_id = ZERO_G3RUH_OBSERVATION_IDS[0]
    native = _native_fixture(tmp_path)
    official = _official_fixture(
        tmp_path,
        sha_override={observation_id: "0" * 64} if identity == "sha" else None,
        size_override={observation_id: 99} if identity == "size" else None,
    )

    with pytest.raises(ValueError, match="SHA-256 or size mismatch"):
        _compare(native, official)


def test_missing_official_cohort_member_is_rejected(tmp_path: Path) -> None:
    native = _native_fixture(tmp_path)
    official = _official_fixture(
        tmp_path,
        missing_id=ZERO_G3RUH_OBSERVATION_IDS[-1],
    )

    with pytest.raises(ValueError, match="lacks part of the exact 28-ID cohort"):
        _compare(native, official)


def test_native_audit_disagreement_is_rejected(tmp_path: Path) -> None:
    native_manifest, native_audit = _native_fixture(tmp_path)
    audit = json.loads(native_audit.read_text())
    audit["observations"][0]["raw_crc_valid_candidate_count"] = 1
    _write_json(native_audit, audit)
    official = _official_fixture(tmp_path)

    with pytest.raises(ValueError, match="native audit counts mismatch"):
        _compare((native_manifest, native_audit), official)


def test_report_and_markdown_are_deterministic(tmp_path: Path) -> None:
    native = _native_fixture(tmp_path)
    official = _official_fixture(tmp_path)

    first = _compare(native, official)
    second = _compare(native, official)
    assert first == second
    assert render_zero_g3ruh_comparison_markdown(first) == (
        render_zero_g3ruh_comparison_markdown(second)
    )
