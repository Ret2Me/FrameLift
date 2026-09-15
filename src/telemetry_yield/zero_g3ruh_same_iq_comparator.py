"""Artifact-only same-IQ comparator for the 28 zero-frame G3RUH captures.

The comparator executes no decoder.  It verifies the native merged manifest,
the independent native protocol audit, the final official gr-satellites
manifest, and its independent audit before recomputing strict AX.25 and exact
inner CCSDS interpretations from their referenced candidate artifacts.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ax25_validation import AX25UIFrame, parse_ax25_ui
from .ccsds_validation import CCSDSPrimaryHeader, parse_ccsds_space_packet
from .crc import validate_ax25_fcs


SCHEMA_VERSION = "zero-g3ruh-official-native-comparator-v1"
NATIVE_MANIFEST_SCHEMA = "archive-g3ruh-processing-manifest-v1"
NATIVE_AUDIT_SCHEMA = "archive-g3ruh-protocol-audit-v1"
OFFICIAL_MANIFEST_SCHEMA = "gr-satellites-batch-v1"
OFFICIAL_AUDIT_SCHEMA = "gr-satellites-batch-audit-v1"
OFFICIAL_RESULT_SCHEMA = "gr-satellites-attempt-v1"

ZERO_G3RUH_OBSERVATION_IDS = (
    4012,
    4013,
    4014,
    4016,
    4017,
    4018,
    4051,
    4052,
    4083,
    4089,
    4090,
    4129,
    4130,
    4132,
    4135,
    4136,
    4162,
    4164,
    4166,
    4168,
    4169,
    4270,
    4272,
    4275,
    4297,
    4423,
    4425,
    4426,
)

_SUCCESS_NATIVE_STATUSES = frozenset({"processed", "skipped_complete"})
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_CANDIDATE_BYTES = 16 * 1024 * 1024


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"JSON artifact is not a regular file: {path}")
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError(f"JSON artifact exceeds comparator limit: {path}")
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON artifact {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"JSON artifact must contain an object: {path}")
    return document, payload


def _resolve(raw: object, *, base: Path) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("artifact path is missing")
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


def _verified_file(
    raw_path: object,
    *,
    expected_size: object,
    expected_sha256: object,
    base: Path,
    maximum_bytes: int,
) -> tuple[Path, bytes]:
    if not _is_int(expected_size) or expected_size < 0:
        raise ValueError("artifact expected size is invalid")
    if not _is_sha256(expected_sha256):
        raise ValueError("artifact expected SHA-256 is invalid")
    path = _resolve(raw_path, base=base)
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {path}")
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise ValueError(f"artifact size mismatch: {path}")
    if actual_size > maximum_bytes:
        raise ValueError(f"artifact exceeds comparator limit: {path}")
    payload = path.read_bytes()
    if _sha256(payload) != str(expected_sha256).casefold():
        raise ValueError(f"artifact SHA-256 mismatch: {path}")
    return path, payload


def _source(path: Path, payload: bytes, schema: object) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size_bytes": len(payload),
        "sha256": _sha256(payload),
        "schema_version": schema,
    }


def _records_by_id(raw: object, *, label: str) -> dict[int, Mapping[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be a list")
    output: dict[int, Mapping[str, Any]] = {}
    for record in raw:
        if not isinstance(record, Mapping):
            raise ValueError(f"{label} contains a non-object record")
        observation_id = record.get("observation_id")
        if not _is_int(observation_id) or observation_id in output:
            raise ValueError(f"{label} observation IDs are invalid or duplicated")
        output[observation_id] = record
    return output


def _ax25_document(frame: AX25UIFrame) -> dict[str, Any]:
    return {
        "destination": frame.destination.callsign,
        "destination_ssid": frame.destination.ssid,
        "source": frame.source.callsign,
        "source_ssid": frame.source.ssid,
        "digipeater_count": len(frame.digipeaters),
        "pid": frame.pid,
        "information_length_bytes": len(frame.information),
        "information_sha256": _sha256(frame.information),
    }


def _ccsds_document(header: CCSDSPrimaryHeader, payload: bytes) -> dict[str, Any]:
    return {
        "payload_sha256": _sha256(payload),
        "payload_size_bytes": len(payload),
        "exact_length": True,
        "version": header.version,
        "packet_type": header.packet_type,
        "secondary_header": header.secondary_header,
        "apid": header.apid,
        "sequence_flags": header.sequence_flags,
        "sequence_count": header.sequence_count,
        "total_packet_length": header.total_packet_length,
    }


def _strict_candidate(
    payload: bytes,
    *,
    branch: str,
    observation_id: int,
    candidate_index: int,
    source_path: Path,
    require_fcs: bool,
) -> tuple[dict[str, Any], bytes | None, bytes | None]:
    reasons: list[str] = []
    normalized = payload
    fcs_valid: bool | None = None
    if require_fcs:
        fcs_valid = validate_ax25_fcs(payload)
        if not fcs_valid:
            reasons.append("invalid_crc16_x25_fcs")
        else:
            normalized = payload[:-2]
    parsed = parse_ax25_ui(normalized) if not reasons else None
    if parsed is None and not reasons:
        reasons.append("strict_parse_ax25_ui_rejected_complete_frame")
    ccsds_header = (
        parse_ccsds_space_packet(parsed.information, require_exact_length=True)
        if parsed is not None
        else None
    )
    record: dict[str, Any] = {
        "candidate_index": candidate_index,
        "raw_sha256": _sha256(payload),
        "raw_size_bytes": len(payload),
        "source_path": str(source_path),
        "source_branch": branch,
        "crc16_x25_valid": fcs_valid,
        "classification": "trusted_ax25_ui" if parsed is not None else "strict_rejected",
        "rejection_reasons": reasons,
        "normalized_payload_sha256": _sha256(normalized) if parsed is not None else None,
        "normalized_payload_size_bytes": len(normalized) if parsed is not None else None,
        "ax25": _ax25_document(parsed) if parsed is not None else None,
        "exact_inner_ccsds_space_packet": (
            _ccsds_document(ccsds_header, parsed.information)
            if ccsds_header is not None and parsed is not None
            else None
        ),
        "provenance_key": f"{branch}:{observation_id}:{candidate_index}",
    }
    return record, normalized if parsed is not None else None, (
        parsed.information if ccsds_header is not None and parsed is not None else None
    )


def _finalize_side(
    *,
    input_sha256: str,
    input_size_bytes: int,
    candidates: list[dict[str, Any]],
    trusted_payloads: set[bytes],
    inner_payloads: set[bytes],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    candidates.sort(key=lambda item: (item["candidate_index"], item["raw_sha256"]))
    rejected = [item for item in candidates if item["classification"] == "strict_rejected"]
    return {
        "input_sha256": input_sha256,
        "input_size_bytes": input_size_bytes,
        "raw_candidate_count": len(candidates),
        "strict_rejected_count": len(rejected),
        "trusted_ax25_unique_count": len(trusted_payloads),
        "trusted_payload_sha256": sorted(_sha256(value) for value in trusted_payloads),
        "exact_inner_ccsds_unique_count": len(inner_payloads),
        "exact_inner_ccsds_payload_sha256": sorted(
            _sha256(value) for value in inner_payloads
        ),
        "raw_candidates": candidates,
        "artifacts": dict(artifacts),
    }


def _load_native(
    manifest_path: Path,
    audit_path: Path,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    manifest, manifest_payload = _read_json(manifest_path)
    audit, audit_payload = _read_json(audit_path)
    if manifest.get("schema_version") != NATIVE_MANIFEST_SCHEMA:
        raise ValueError("unsupported native merged manifest schema")
    contract = manifest.get("selection_contract")
    if (
        manifest.get("complete") is not True
        or tuple(manifest.get("expected_observation_ids", ()))
        != ZERO_G3RUH_OBSERVATION_IDS
        or not isinstance(contract, Mapping)
        or contract.get("mode_exact") != "FSK AX.25 G3RUH"
        or contract.get("frames_recovered") is not False
        or contract.get("input_datatype") != "ci16_le interleaved I,Q"
        or contract.get("sample_rate_hz") != 57_600
    ):
        raise ValueError("native manifest is not the exact complete zero-frame 28 cohort")
    results = _records_by_id(manifest.get("results"), label="native results")
    if tuple(sorted(results)) != ZERO_G3RUH_OBSERVATION_IDS:
        raise ValueError("native manifest results do not equal the exact 28-ID cohort")

    if (
        audit.get("schema_version") != NATIVE_AUDIT_SCHEMA
        or audit.get("campaign_complete") is not True
    ):
        raise ValueError("native protocol audit is not complete")
    audit_observations = _records_by_id(
        audit.get("observations"), label="native audit observations"
    )
    if tuple(sorted(audit_observations)) != ZERO_G3RUH_OBSERVATION_IDS:
        raise ValueError("native audit does not equal the exact 28-ID cohort")
    source_manifests = audit.get("source_manifests")
    if not isinstance(source_manifests, list) or not any(
        isinstance(item, Mapping)
        and item.get("sha256") == _sha256(manifest_payload)
        for item in source_manifests
    ):
        raise ValueError("native audit is not bound to the supplied merged manifest")

    by_id: dict[int, dict[str, Any]] = {}
    for observation_id in ZERO_G3RUH_OBSERVATION_IDS:
        result = results[observation_id]
        audited = audit_observations[observation_id]
        if result.get("status") not in _SUCCESS_NATIVE_STATUSES:
            raise ValueError(f"native observation {observation_id} is not successful")
        input_sha = result.get("ci16_sha256")
        input_size = result.get("ci16_size_bytes")
        if not _is_sha256(input_sha) or not _is_int(input_size) or input_size < 1:
            raise ValueError(f"native observation {observation_id} has invalid input identity")
        artifact_path, artifact_payload = _verified_file(
            result.get("decode_output_path"),
            expected_size=result.get("decode_output_size_bytes"),
            expected_sha256=result.get("decode_output_sha256"),
            base=manifest_path.parent,
            maximum_bytes=_MAX_JSON_BYTES,
        )
        try:
            artifact = json.loads(artifact_payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"native decode artifact is invalid: {artifact_path}") from error
        raw_frames = artifact.get("unique_crc_valid_frames") if isinstance(artifact, Mapping) else None
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("stage") != "decode-only"
            or artifact.get("observation_id") != observation_id
            or not isinstance(raw_frames, list)
            or artifact.get("unique_crc_valid_frame_count") != len(raw_frames)
            or result.get("crc_valid_frame_count") != len(raw_frames)
        ):
            raise ValueError(f"native observation {observation_id} decode artifact is inconsistent")
        if (
            audited.get("source_artifact_sha256") != _sha256(artifact_payload)
            or _resolve(audited.get("source_artifact"), base=audit_path.parent)
            != artifact_path
        ):
            raise ValueError(f"native audit artifact binding mismatch for {observation_id}")
        audited_candidates = audited.get("candidates")
        if not isinstance(audited_candidates, list) or len(audited_candidates) != len(raw_frames):
            raise ValueError(f"native audit candidate list mismatch for {observation_id}")

        candidates: list[dict[str, Any]] = []
        trusted: set[bytes] = set()
        inner: set[bytes] = set()
        for index, (raw, audited_candidate) in enumerate(
            zip(raw_frames, audited_candidates, strict=True)
        ):
            if not isinstance(raw, Mapping) or not isinstance(audited_candidate, Mapping):
                raise ValueError("native candidate is not an object")
            encoded = raw.get("frame_with_fcs_hex")
            if not isinstance(encoded, str):
                raise ValueError("native candidate frame hex is missing")
            try:
                frame = bytes.fromhex(encoded)
            except ValueError as error:
                raise ValueError("native candidate frame hex is invalid") from error
            declared_digest = raw.get("frame_with_fcs_sha256")
            if declared_digest is not None and declared_digest != _sha256(frame):
                raise ValueError("native candidate digest mismatch")
            candidate, normalized, inner_payload = _strict_candidate(
                frame,
                branch="native",
                observation_id=observation_id,
                candidate_index=index,
                source_path=artifact_path,
                require_fcs=True,
            )
            expected_trusted = normalized is not None
            if (
                audited_candidate.get("frame_with_fcs_sha256") != candidate["raw_sha256"]
                or audited_candidate.get("frame_length_bytes") != len(frame)
                or audited_candidate.get("crc16_x25_valid")
                is not candidate["crc16_x25_valid"]
                or audited_candidate.get("ax25_ui_structurally_valid")
                is not expected_trusted
                or audited_candidate.get("trusted_for_explicit_ax25_campaign")
                is not expected_trusted
                or (audited_candidate.get("inner_ccsds_space_packet") is not None)
                is not (inner_payload is not None)
            ):
                raise ValueError(f"native strict audit mismatch for {observation_id}")
            candidates.append(candidate)
            if normalized is not None:
                trusted.add(normalized)
            if inner_payload is not None:
                inner.add(inner_payload)

        raw_count = len(candidates)
        trusted_count = sum(item["classification"] == "trusted_ax25_ui" for item in candidates)
        inner_count = sum(item["exact_inner_ccsds_space_packet"] is not None for item in candidates)
        if (
            audited.get("raw_crc_valid_candidate_count") != raw_count
            or audited.get("trusted_ax25_ui_frame_count") != trusted_count
            or audited.get("rejected_crc_collision_count") != raw_count - trusted_count
            or audited.get("ax25_with_exact_inner_ccsds_count") != inner_count
        ):
            raise ValueError(f"native audit counts mismatch for {observation_id}")
        by_id[observation_id] = _finalize_side(
            input_sha256=str(input_sha).casefold(),
            input_size_bytes=input_size,
            candidates=candidates,
            trusted_payloads=trusted,
            inner_payloads=inner,
            artifacts={
                "decode_output_path": str(artifact_path),
                "decode_output_sha256": _sha256(artifact_payload),
            },
        )

    aggregate_fields = {
        "audited_observation_count": len(by_id),
        "raw_crc_valid_candidate_count": sum(item["raw_candidate_count"] for item in by_id.values()),
        "rejected_crc_collision_count": sum(item["strict_rejected_count"] for item in by_id.values()),
        "trusted_ax25_ui_frame_count": sum(
            sum(candidate["classification"] == "trusted_ax25_ui" for candidate in item["raw_candidates"])
            for item in by_id.values()
        ),
        "ax25_with_exact_inner_ccsds_count": sum(
            sum(candidate["exact_inner_ccsds_space_packet"] is not None for candidate in item["raw_candidates"])
            for item in by_id.values()
        ),
    }
    if any(audit.get(key) != value for key, value in aggregate_fields.items()):
        raise ValueError("native audit aggregate counts are inconsistent")
    return by_id, {
        "manifest": _source(manifest_path, manifest_payload, manifest.get("schema_version")),
        "audit": _source(audit_path, audit_payload, audit.get("schema_version")),
    }


def _profile_is_g3ruh(profile: object) -> bool:
    if not isinstance(profile, Mapping):
        return False
    framing = profile.get("framing")
    modulations = profile.get("modulations")
    return (
        isinstance(framing, Sequence)
        and not isinstance(framing, (str, bytes))
        and any(item == "AX.25 G3RUH" for item in framing)
        and isinstance(modulations, Sequence)
        and not isinstance(modulations, (str, bytes))
        and any(isinstance(item, str) and item.casefold() == "fsk" for item in modulations)
    )


def _load_official(
    manifest_path: Path,
    audit_path: Path,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any], int]:
    manifest, manifest_payload = _read_json(manifest_path)
    audit, audit_payload = _read_json(audit_path)
    if manifest.get("schema_version") != OFFICIAL_MANIFEST_SCHEMA:
        raise ValueError("unsupported official gr-satellites manifest schema")
    jobs = _records_by_id(manifest.get("jobs"), label="official manifest jobs")
    status_counts = Counter(str(item.get("status")) for item in jobs.values())
    if (
        manifest.get("job_count") != len(jobs)
        or manifest.get("status_counts") != dict(sorted(status_counts.items()))
    ):
        raise ValueError("official manifest aggregate is inconsistent")
    if not set(ZERO_G3RUH_OBSERVATION_IDS).issubset(jobs):
        raise ValueError("official manifest lacks part of the exact 28-ID cohort")

    source_manifest = audit.get("source_manifest")
    if (
        audit.get("schema_version") != OFFICIAL_AUDIT_SCHEMA
        or audit.get("campaign_complete") is not True
        or audit.get("audit_complete") is not True
        or audit.get("manifest_consistent") is not True
        or not isinstance(source_manifest, Mapping)
        or source_manifest.get("size_bytes") != len(manifest_payload)
        or source_manifest.get("sha256") != _sha256(manifest_payload)
    ):
        raise ValueError("official audit is not complete or bound to the supplied manifest")
    audited_by_id = _records_by_id(
        audit.get("observations"), label="official audit observations"
    )
    if set(audited_by_id) != set(jobs):
        raise ValueError("official manifest/audit observation IDs differ")
    if set(jobs) & set(ZERO_G3RUH_OBSERVATION_IDS) != set(ZERO_G3RUH_OBSERVATION_IDS):
        raise ValueError("official/native cohort intersection is not exactly 28 IDs")

    by_id: dict[int, dict[str, Any]] = {}
    for observation_id in ZERO_G3RUH_OBSERVATION_IDS:
        job = jobs[observation_id]
        audited = audited_by_id[observation_id]
        if (
            job.get("status") != "completed"
            or audited.get("manifest_status") != "completed"
            or audited.get("audit_status") != "audited"
            or audited.get("result_metadata_valid") is not True
            or audited.get("artifact_integrity_valid") is not True
        ):
            raise ValueError(f"official observation {observation_id} is not fully audited")
        source_result = audited.get("source_result")
        if not isinstance(source_result, Mapping):
            raise ValueError("official audit lacks a source result reference")
        result_path, result_payload = _verified_file(
            source_result.get("path"),
            expected_size=source_result.get("size_bytes"),
            expected_sha256=source_result.get("sha256"),
            base=manifest_path.parent,
            maximum_bytes=_MAX_JSON_BYTES,
        )
        try:
            result = json.loads(result_payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"official result is invalid: {result_path}") from error
        raw_candidates = result.get("candidates") if isinstance(result, Mapping) else None
        if (
            not isinstance(result, Mapping)
            or result.get("schema_version") != OFFICIAL_RESULT_SCHEMA
            or result.get("observation_id") != observation_id
            or result.get("status") != "completed"
            or result.get("attempt_fingerprint") != job.get("attempt_fingerprint")
            or not isinstance(raw_candidates, list)
            or result.get("candidate_count") != len(raw_candidates)
            or job.get("candidate_count") != len(raw_candidates)
            or not _profile_is_g3ruh(result.get("profile"))
        ):
            raise ValueError(f"official result metadata mismatch for {observation_id}")
        input_document = result.get("input")
        if not isinstance(input_document, Mapping):
            raise ValueError("official result input identity is missing")
        input_sha = input_document.get("sha256")
        input_size = input_document.get("size_bytes")
        if not _is_sha256(input_sha) or not _is_int(input_size) or input_size < 1:
            raise ValueError("official result input identity is invalid")
        audited_candidates = audited.get("candidates")
        if not isinstance(audited_candidates, list) or len(audited_candidates) != len(raw_candidates):
            raise ValueError(f"official audit candidate list mismatch for {observation_id}")

        candidates: list[dict[str, Any]] = []
        trusted: set[bytes] = set()
        inner: set[bytes] = set()
        for index, (raw, audited_candidate) in enumerate(
            zip(raw_candidates, audited_candidates, strict=True)
        ):
            if not isinstance(raw, Mapping) or not isinstance(audited_candidate, Mapping):
                raise ValueError("official candidate is not an object")
            raw_pdu = audited_candidate.get("raw_pdu")
            if not isinstance(raw_pdu, Mapping) or raw_pdu.get("verified") is not True:
                raise ValueError("official candidate artifact is not audit-verified")
            candidate_path, payload = _verified_file(
                raw.get("payload_path"),
                expected_size=raw.get("payload_size_bytes"),
                expected_sha256=raw.get("payload_sha256"),
                base=result_path.parent,
                maximum_bytes=_MAX_CANDIDATE_BYTES,
            )
            if (
                raw_pdu.get("actual_size_bytes") != len(payload)
                or raw_pdu.get("actual_sha256") != _sha256(payload)
                or raw_pdu.get("payload_hex") != payload.hex()
                or _resolve(raw_pdu.get("path"), base=result_path.parent) != candidate_path
            ):
                raise ValueError("official audit candidate binding mismatch")
            candidate, normalized, inner_payload = _strict_candidate(
                payload,
                branch="official_gr_satellites",
                observation_id=observation_id,
                candidate_index=index,
                source_path=candidate_path,
                require_fcs=False,
            )
            expected_classification = (
                "trusted_ax25_ui" if normalized is not None else "rejected"
            )
            if (
                audited_candidate.get("classification") != expected_classification
                or (audited_candidate.get("trusted_ax25_ui") is not None)
                is not (normalized is not None)
                or (audited_candidate.get("inner_ccsds_space_packet") is not None)
                is not (inner_payload is not None)
            ):
                raise ValueError(f"official strict audit mismatch for {observation_id}")
            candidates.append(candidate)
            if normalized is not None:
                trusted.add(normalized)
            if inner_payload is not None:
                inner.add(inner_payload)

        raw_count = len(candidates)
        trusted_count = sum(item["classification"] == "trusted_ax25_ui" for item in candidates)
        inner_count = sum(item["exact_inner_ccsds_space_packet"] is not None for item in candidates)
        if (
            audited.get("raw_pdu_count") != raw_count
            or audited.get("verified_raw_pdu_count") != raw_count
            or audited.get("trusted_ax25_ui_count") != trusted_count
            or audited.get("rejected_pdu_count") != raw_count - trusted_count
            or audited.get("inner_ccsds_count") != inner_count
        ):
            raise ValueError(f"official audit counts mismatch for {observation_id}")
        by_id[observation_id] = _finalize_side(
            input_sha256=str(input_sha).casefold(),
            input_size_bytes=input_size,
            candidates=candidates,
            trusted_payloads=trusted,
            inner_payloads=inner,
            artifacts={
                "result_path": str(result_path),
                "result_sha256": _sha256(result_payload),
                "profile_name": result["profile"].get("name"),
                "backend_version": result.get("backend", {}).get("backend_version")
                if isinstance(result.get("backend"), Mapping)
                else None,
            },
        )

    aggregate_pairs = (
        ("raw_pdu_count", "raw_pdu_count"),
        ("rejected_pdu_count", "rejected_pdu_count"),
        ("trusted_ax25_ui_count", "trusted_ax25_ui_count"),
        ("inner_ccsds_count", "inner_ccsds_count"),
    )
    for top_level, per_observation in aggregate_pairs:
        if audit.get(top_level) != sum(
            int(item.get(per_observation, 0)) for item in audited_by_id.values()
        ):
            raise ValueError("official audit aggregate counts are inconsistent")
    return by_id, {
        "manifest": _source(manifest_path, manifest_payload, manifest.get("schema_version")),
        "audit": _source(audit_path, audit_payload, audit.get("schema_version")),
    }, len(jobs)


def _payload_set(side: Mapping[str, Any]) -> set[str]:
    return set(side["trusted_payload_sha256"])


def compare_zero_g3ruh_same_iq(
    *,
    native_manifest_path: str | Path,
    native_audit_path: str | Path,
    official_manifest_path: str | Path,
    official_audit_path: str | Path,
) -> dict[str, Any]:
    """Compare the exact 28-event cohort without re-running either decoder."""

    native_manifest = Path(native_manifest_path).resolve()
    native_audit = Path(native_audit_path).resolve()
    official_manifest = Path(official_manifest_path).resolve()
    official_audit = Path(official_audit_path).resolve()
    native, native_sources = _load_native(native_manifest, native_audit)
    official, official_sources, official_full_count = _load_official(
        official_manifest, official_audit
    )

    observations: list[dict[str, Any]] = []
    official_event_payloads: set[tuple[int, str]] = set()
    native_event_payloads: set[tuple[int, str]] = set()
    for observation_id in ZERO_G3RUH_OBSERVATION_IDS:
        official_side = official[observation_id]
        native_side = native[observation_id]
        if (
            official_side["input_sha256"] != native_side["input_sha256"]
            or official_side["input_size_bytes"] != native_side["input_size_bytes"]
        ):
            raise ValueError(
                f"observation {observation_id} official/native input SHA-256 or size mismatch"
            )
        official_payloads = _payload_set(official_side)
        native_payloads = _payload_set(native_side)
        overlap = official_payloads & native_payloads
        union = official_payloads | native_payloads
        native_incremental = native_payloads - official_payloads
        official_only = official_payloads - native_payloads
        official_event_payloads.update((observation_id, value) for value in official_payloads)
        native_event_payloads.update((observation_id, value) for value in native_payloads)
        observations.append(
            {
                "observation_id": observation_id,
                "same_input": True,
                "input_sha256": native_side["input_sha256"],
                "input_size_bytes": native_side["input_size_bytes"],
                "official_gr_satellites": official_side,
                "native": native_side,
                "sets": {
                    "official_count": len(official_payloads),
                    "native_count": len(native_payloads),
                    "overlap_count": len(overlap),
                    "union_count": len(union),
                    "incremental_native_over_official": len(native_incremental),
                    "official_only_count": len(official_only),
                    "overlap_payload_sha256": sorted(overlap),
                    "union_payload_sha256": sorted(union),
                    "incremental_native_payload_sha256": sorted(native_incremental),
                    "official_only_payload_sha256": sorted(official_only),
                },
            }
        )

    overlap_events = official_event_payloads & native_event_payloads
    union_events = official_event_payloads | native_event_payloads
    native_incremental_events = native_event_payloads - official_event_payloads
    official_only_events = official_event_payloads - native_event_payloads
    if not official_event_payloads and not native_event_payloads:
        outcome = {
            "classification": "parity_zero_equals_zero",
            "parity": True,
            "winner": None,
            "statement": (
                "Both branches recovered zero strictly trusted AX.25 frames on the "
                "same 28 IQ captures; this is 0=0 parity, not a decoder victory."
            ),
        }
    elif official_event_payloads == native_event_payloads:
        outcome = {
            "classification": "parity_equal_nonzero",
            "parity": True,
            "winner": None,
            "statement": "Both branches recovered the same non-zero trusted event/payload set.",
        }
    else:
        outcome = {
            "classification": "non_parity_trusted_sets",
            "parity": False,
            "winner": None,
            "statement": (
                "Trusted event/payload sets differ; branch-specific increments are "
                "reported without converting raw rejected candidates into telemetry."
            ),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "complete": True,
        "status": "complete",
        "cohort": {
            "observation_count": len(ZERO_G3RUH_OBSERVATION_IDS),
            "observation_ids": list(ZERO_G3RUH_OBSERVATION_IDS),
            "mode_exact": "FSK AX.25 G3RUH",
            "frames_recovered_in_catalogue": False,
            "same_iq_sha_and_size_required": True,
            "official_full_campaign_observation_count": official_full_count,
            "official_observations_excluded_from_comparison": official_full_count
            - len(ZERO_G3RUH_OBSERVATION_IDS),
        },
        "acceptance_policy": {
            "official_gr_satellites": (
                "audit-verified KISS PDU after the profile deframer consumed link "
                "FCS, then strict parse_ax25_ui over the complete PDU"
            ),
            "native": (
                "manifest/audit-bound phase candidate, independently verified "
                "CRC-16/X-25 FCS, then strict parse_ax25_ui after removing FCS"
            ),
            "inner_ccsds": (
                "parse_ccsds_space_packet over the complete trusted AX.25 information "
                "field with exact declared packet length"
            ),
            "normalization": "complete AX.25 UI bytes without link FCS",
            "event_key": "observation_id",
            "dedupe": "exact normalized bytes within each observation and branch",
            "decoder_execution": False,
        },
        "sources": {
            "native": native_sources,
            "official_gr_satellites": official_sources,
        },
        "summary": {
            "same_iq_verified_observation_count": len(observations),
            "official_raw_candidate_count": sum(
                item["official_gr_satellites"]["raw_candidate_count"]
                for item in observations
            ),
            "official_strict_rejected_count": sum(
                item["official_gr_satellites"]["strict_rejected_count"]
                for item in observations
            ),
            "official_trusted_ax25_unique_count": len(official_event_payloads),
            "official_exact_inner_ccsds_unique_count": sum(
                item["official_gr_satellites"]["exact_inner_ccsds_unique_count"]
                for item in observations
            ),
            "native_raw_candidate_count": sum(
                item["native"]["raw_candidate_count"] for item in observations
            ),
            "native_strict_rejected_count": sum(
                item["native"]["strict_rejected_count"] for item in observations
            ),
            "native_trusted_ax25_unique_count": len(native_event_payloads),
            "native_exact_inner_ccsds_unique_count": sum(
                item["native"]["exact_inner_ccsds_unique_count"]
                for item in observations
            ),
            "trusted_overlap_count": len(overlap_events),
            "trusted_union_count": len(union_events),
            "incremental_native_over_official": len(native_incremental_events),
            "official_only_trusted_count": len(official_only_events),
        },
        "outcome": outcome,
        "observations": observations,
    }


def render_zero_g3ruh_comparison_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact deterministic human-readable companion report."""

    summary = report["summary"]
    cohort = report["cohort"]
    outcome = report["outcome"]
    lines = [
        "# PolyITAN zero-frame G3RUH: official gr-satellites vs native",
        "",
        "This is an artifact-only comparison on the exact same IQ bytes. No decoder was rerun.",
        "",
        "## Cohort integrity",
        "",
        f"- Compared observations: {cohort['observation_count']} (SHA-256 and byte size matched for every input).",
        f"- Official full campaign: {cohort['official_full_campaign_observation_count']} observations; excluded from this comparison: {cohort['official_observations_excluded_from_comparison']}.",
        "- Selection: catalogue `frames_recovered=false`, exact mode `FSK AX.25 G3RUH`.",
        "",
        "## Results",
        "",
        "| Metric | official gr-satellites | native |",
        "|---|---:|---:|",
        f"| Raw candidates | {summary['official_raw_candidate_count']} | {summary['native_raw_candidate_count']} |",
        f"| Strict rejected | {summary['official_strict_rejected_count']} | {summary['native_strict_rejected_count']} |",
        f"| Trusted AX.25 (unique per event) | {summary['official_trusted_ax25_unique_count']} | {summary['native_trusted_ax25_unique_count']} |",
        f"| Exact inner CCSDS Space Packets | {summary['official_exact_inner_ccsds_unique_count']} | {summary['native_exact_inner_ccsds_unique_count']} |",
        "",
        f"- Trusted overlap: {summary['trusted_overlap_count']}",
        f"- Trusted union: {summary['trusted_union_count']}",
        f"- Incremental native over official: {summary['incremental_native_over_official']}",
        f"- Official-only trusted: {summary['official_only_trusted_count']}",
        "",
        "## Interpretation",
        "",
        f"**{outcome['classification']}** — {outcome['statement']}",
        "",
        "Raw candidates that fail strict AX.25 remain listed as rejected evidence and are not counted as telemetry. The official KISS path does not retain the consumed link FCS; the native path independently verifies its retained FCS before normalization.",
        "",
    ]
    return "\n".join(lines)
