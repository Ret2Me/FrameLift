"""Protocol-aware audit of phase-first archive campaign candidates.

The expensive phase search emits CRC-valid HDLC candidates.  This module is a
separate, fail-closed acceptance layer: an explicit AX.25 G3RUH campaign frame
counts as telemetry only when its address chain and UI control fields are also
structurally valid.  An exact inner CCSDS Space Packet is reported as an
additional interpretation, never inferred from the modulation alone.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .archive_download import sha256_file
from .ax25_validation import parse_ax25_ui
from .ccsds_validation import parse_ccsds_space_packet
from .crc import validate_ax25_fcs


SCHEMA_VERSION = "archive-g3ruh-protocol-audit-v1"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def audit_phase_first_artifact(path: Path) -> dict[str, object]:
    """Audit one complete decode-only artifact without mission assumptions."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping) or document.get("stage") != "decode-only":
        raise ValueError("phase-first artifact must be a decode-only object")
    observation_id = document.get("observation_id")
    records = document.get("unique_crc_valid_frames")
    declared_count = document.get("unique_crc_valid_frame_count")
    if (
        not isinstance(observation_id, int)
        or isinstance(observation_id, bool)
        or not isinstance(records, Sequence)
        or isinstance(records, (str, bytes, bytearray, memoryview))
        or not isinstance(declared_count, int)
        or isinstance(declared_count, bool)
        or declared_count != len(records)
    ):
        raise ValueError("malformed phase-first artifact summary")

    audited: list[dict[str, object]] = []
    trusted_ax25 = 0
    inner_ccsds = 0
    seen: set[str] = set()
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ValueError("phase-first candidate must be an object")
        encoded = raw.get("frame_with_fcs_hex")
        if not isinstance(encoded, str):
            raise ValueError("candidate frame hex is missing")
        try:
            frame = bytes.fromhex(encoded)
        except ValueError as error:
            raise ValueError("candidate frame hex is invalid") from error
        digest = _sha256(frame)
        if digest in seen:
            raise ValueError("duplicate candidate frame in artifact")
        seen.add(digest)
        declared_digest = raw.get("frame_with_fcs_sha256")
        if declared_digest is not None and declared_digest != digest:
            raise ValueError("candidate frame digest mismatch")

        fcs_valid = validate_ax25_fcs(frame)
        parsed = parse_ax25_ui(frame[:-2]) if fcs_valid else None
        ccsds = (
            parse_ccsds_space_packet(parsed.information)
            if parsed is not None
            else None
        )
        if parsed is not None:
            trusted_ax25 += 1
        if ccsds is not None:
            inner_ccsds += 1
        audited.append(
            {
                "frame_with_fcs_sha256": digest,
                "frame_length_bytes": len(frame),
                "crc16_x25_valid": fcs_valid,
                "ax25_ui_structurally_valid": parsed is not None,
                "trusted_for_explicit_ax25_campaign": parsed is not None,
                "ax25": (
                    {
                        "destination": parsed.destination.callsign,
                        "destination_ssid": parsed.destination.ssid,
                        "source": parsed.source.callsign,
                        "source_ssid": parsed.source.ssid,
                        "digipeater_count": len(parsed.digipeaters),
                        "pid": parsed.pid,
                        "information_length_bytes": len(parsed.information),
                    }
                    if parsed is not None
                    else None
                ),
                "inner_ccsds_space_packet": (
                    {
                        "apid": ccsds.apid,
                        "sequence_flags": ccsds.sequence_flags,
                        "sequence_count": ccsds.sequence_count,
                        "packet_type": ccsds.packet_type,
                        "secondary_header": ccsds.secondary_header,
                        "total_packet_length": ccsds.total_packet_length,
                    }
                    if ccsds is not None
                    else None
                ),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "observation_id": observation_id,
        "source_artifact": str(path),
        "source_artifact_sha256": sha256_file(path),
        "raw_crc_valid_candidate_count": len(audited),
        "trusted_ax25_ui_frame_count": trusted_ax25,
        "ax25_with_exact_inner_ccsds_count": inner_ccsds,
        "rejected_crc_collision_count": len(audited) - trusted_ax25,
        "candidates": audited,
    }


def audit_processing_manifests(paths: Sequence[Path]) -> dict[str, object]:
    """Audit the latest unique completed observation from several manifests."""

    latest: dict[int, Mapping[str, Any]] = {}
    manifest_hashes: list[dict[str, str]] = []
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_version") != "archive-g3ruh-processing-manifest-v1":
            raise ValueError(f"unsupported processing manifest: {path}")
        results = document.get("results")
        if not isinstance(results, list):
            raise ValueError(f"processing manifest results are invalid: {path}")
        manifest_hashes.append({"path": str(path), "sha256": sha256_file(path)})
        for result in results:
            if not isinstance(result, Mapping):
                raise ValueError("processing result must be an object")
            observation_id = result.get("observation_id")
            if isinstance(observation_id, int) and result.get("status") in (
                "processed",
                "skipped_complete",
            ):
                latest[observation_id] = result

    observations: list[dict[str, object]] = []
    for observation_id, result in sorted(latest.items()):
        raw_path = result.get("decode_output_path")
        if not isinstance(raw_path, str):
            raise ValueError(f"observation {observation_id} has no decode output")
        audit = audit_phase_first_artifact(Path(raw_path))
        if audit["observation_id"] != observation_id:
            raise ValueError("processing manifest/artifact observation mismatch")
        observations.append(audit)

    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_complete": False,
        "source_manifests": manifest_hashes,
        "audited_observation_count": len(observations),
        "raw_crc_valid_candidate_count": sum(
            int(item["raw_crc_valid_candidate_count"]) for item in observations
        ),
        "trusted_ax25_ui_frame_count": sum(
            int(item["trusted_ax25_ui_frame_count"]) for item in observations
        ),
        "ax25_with_exact_inner_ccsds_count": sum(
            int(item["ax25_with_exact_inner_ccsds_count"]) for item in observations
        ),
        "rejected_crc_collision_count": sum(
            int(item["rejected_crc_collision_count"]) for item in observations
        ),
        "observations": observations,
    }
