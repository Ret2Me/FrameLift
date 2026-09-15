from __future__ import annotations

import json
from pathlib import Path

from telemetry_yield.archive_protocol_audit import audit_phase_first_artifact
from telemetry_yield.crc import append_ax25_fcs


def _address(callsign: str, *, final: bool) -> bytes:
    return bytes(ord(character) << 1 for character in callsign.ljust(6)) + bytes(
        (0x60 | int(final),)
    )


def _artifact(path: Path, frames: list[bytes]) -> None:
    path.write_text(
        json.dumps(
            {
                "stage": "decode-only",
                "observation_id": 42,
                "unique_crc_valid_frame_count": len(frames),
                "unique_crc_valid_frames": [
                    {
                        "frame_with_fcs_hex": frame.hex(),
                        "frame_with_fcs_sha256": __import__("hashlib")
                        .sha256(frame)
                        .hexdigest(),
                    }
                    for frame in frames
                ],
            }
        ),
        encoding="utf-8",
    )


def test_audit_separates_structural_ax25_from_crc_collision(tmp_path: Path) -> None:
    ccsds = bytes.fromhex("0820d5bf0003") + b"data"
    payload = (
        _address("CANVAS", final=False)
        + _address("LASP", final=True)
        + b"\x03\xf0"
        + ccsds
    )
    valid = append_ax25_fcs(payload)
    collision = append_ax25_fcs(bytes.fromhex("e0470c2aab79e4"))
    artifact = tmp_path / "decode.json"
    _artifact(artifact, [valid, collision])

    audit = audit_phase_first_artifact(artifact)

    assert audit["raw_crc_valid_candidate_count"] == 2
    assert audit["trusted_ax25_ui_frame_count"] == 1
    assert audit["ax25_with_exact_inner_ccsds_count"] == 1
    assert audit["rejected_crc_collision_count"] == 1
    assert audit["candidates"][0]["ax25"]["destination"] == "CANVAS"


def test_empty_artifact_is_a_valid_zero_result(tmp_path: Path) -> None:
    artifact = tmp_path / "decode.json"
    _artifact(artifact, [])

    audit = audit_phase_first_artifact(artifact)

    assert audit["raw_crc_valid_candidate_count"] == 0
    assert audit["trusted_ax25_ui_frame_count"] == 0
