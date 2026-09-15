"""Independent, fail-closed audit of a gr-satellites batch manifest.

The auditor never runs a demodulator and never reads the source IQ captures.
Its trust boundary is the atomic batch manifest snapshot, the result files
bound to that snapshot, and the binary artifacts referenced by those results.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ax25_validation import AX25Address, AX25UIFrame, parse_ax25_ui
from .ccsds_validation import CCSDSPrimaryHeader, parse_ccsds_space_packet


SCHEMA_VERSION = "gr-satellites-batch-audit-v1"
_BATCH_SCHEMA_VERSION = "gr-satellites-batch-v1"
_RESULT_SCHEMA_VERSION = "gr-satellites-attempt-v1"
_TERMINAL_STATUSES = frozenset({"completed", "backend_failure", "input_error"})
_SHA256_HEX_LENGTH = 64
_MAX_JSON_BYTES = 16 * 1024 * 1024


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _read_json_object(path: Path) -> tuple[dict[str, Any], bytes]:
    size = path.stat().st_size
    if size > _MAX_JSON_BYTES:
        raise ValueError(f"JSON artifact exceeds {_MAX_JSON_BYTES} bytes")
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ValueError("JSON artifact must contain an object")
    return document, payload


def _resolve_inside(
    raw_path: object, *, base: Path, allowed_root: Path
) -> tuple[Path | None, str | None]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "path is missing or is not a non-empty string"
    path = Path(raw_path)
    if not path.is_absolute():
        path = base / path
    try:
        resolved = path.resolve(strict=False)
        resolved.relative_to(allowed_root)
    except (OSError, ValueError):
        return None, "path escapes the manifest campaign root"
    return resolved, None


def _verify_binary_reference(
    reference: object,
    *,
    base: Path,
    campaign_root: Path,
    max_bytes: int,
) -> tuple[dict[str, Any], bytes | None]:
    check: dict[str, Any] = {
        "path": None,
        "expected_size_bytes": None,
        "actual_size_bytes": None,
        "expected_sha256": None,
        "actual_sha256": None,
        "verified": False,
        "errors": [],
    }
    errors: list[str] = check["errors"]
    if not isinstance(reference, Mapping):
        errors.append("artifact reference is not an object")
        return check, None

    check["path"] = reference.get("path")
    check["expected_size_bytes"] = reference.get("size_bytes")
    check["expected_sha256"] = reference.get("sha256")
    path, path_error = _resolve_inside(
        reference.get("path"), base=base, allowed_root=campaign_root
    )
    if path_error is not None:
        errors.append(path_error)
        return check, None

    expected_size = reference.get("size_bytes")
    expected_digest = reference.get("sha256")
    if not _is_int(expected_size) or expected_size < 0:
        errors.append("declared artifact size is not a non-negative integer")
    if not _is_sha256(expected_digest):
        errors.append("declared artifact SHA-256 is invalid")
    else:
        check["expected_sha256"] = expected_digest.casefold()

    try:
        if not path.is_file():
            errors.append("artifact path is not a regular file")
            return check, None
        actual_size = path.stat().st_size
        check["actual_size_bytes"] = actual_size
        if actual_size > max_bytes:
            errors.append(f"artifact exceeds the audit limit of {max_bytes} bytes")
            return check, None
        if _is_int(expected_size) and actual_size != expected_size:
            errors.append("artifact size does not match its result record")
            return check, None
        payload = path.read_bytes()
    except OSError as error:
        errors.append(f"artifact read failed: {type(error).__name__}: {error}")
        return check, None

    actual_digest = _sha256(payload)
    check["actual_sha256"] = actual_digest
    if _is_sha256(expected_digest) and actual_digest != expected_digest.casefold():
        errors.append("artifact SHA-256 does not match its result record")
    if not errors:
        check["verified"] = True
        return check, payload
    return check, None


def _address_document(address: AX25Address) -> dict[str, Any]:
    return {
        "callsign": address.callsign,
        "ssid": address.ssid,
        "command_or_repeated": address.command_or_repeated,
        "final": address.final,
    }


def _ax25_document(frame: AX25UIFrame) -> dict[str, Any]:
    return {
        "destination": _address_document(frame.destination),
        "source": _address_document(frame.source),
        "digipeaters": [_address_document(item) for item in frame.digipeaters],
        "pid": frame.pid,
        "information_length_bytes": len(frame.information),
        "information_sha256": _sha256(frame.information),
        "information_hex": frame.information.hex(),
    }


def _ccsds_document(header: CCSDSPrimaryHeader) -> dict[str, Any]:
    return {
        "exact_length": True,
        "version": header.version,
        "packet_type": header.packet_type,
        "secondary_header": header.secondary_header,
        "apid": header.apid,
        "sequence_flags": header.sequence_flags,
        "sequence_count": header.sequence_count,
        "packet_data_length": header.packet_data_length,
        "total_packet_length": header.total_packet_length,
    }


def _profile_declares_ax25(profile: object) -> bool:
    if not isinstance(profile, Mapping):
        return False
    framing = profile.get("framing")
    if not isinstance(framing, Sequence) or isinstance(
        framing, (str, bytes, bytearray, memoryview)
    ):
        return False
    return any(isinstance(item, str) and "AX.25" in item.upper() for item in framing)


def _candidate_audit(
    raw: object,
    *,
    list_index: int,
    attempt_directory: Path,
    campaign_root: Path,
    max_payload_bytes: int,
    result_metadata_valid: bool,
    profile_declares_ax25: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "list_index": list_index,
        "candidate_index": None,
        "classification": "rejected",
        "raw_pdu": None,
        "source_classification": None,
        "source_validated": None,
        "source_validation_layers": None,
        "validation_layers": [],
        "rejection_reasons": [],
        "trusted_ax25_ui": None,
        "inner_ccsds_space_packet": None,
        "provenance": None,
    }
    rejection_reasons: list[str] = result["rejection_reasons"]
    validation_layers: list[str] = result["validation_layers"]
    if not isinstance(raw, Mapping):
        rejection_reasons.append("candidate record is not an object")
        return result

    candidate_index = raw.get("candidate_index")
    result["candidate_index"] = candidate_index
    result["source_classification"] = raw.get("classification")
    result["source_validated"] = raw.get("validated")
    result["source_validation_layers"] = raw.get("validation_layers")
    result["provenance"] = raw.get("provenance")

    if not result_metadata_valid:
        rejection_reasons.append("result metadata failed consistency checks")
    if not _is_int(candidate_index) or candidate_index < 0:
        rejection_reasons.append("candidate_index is not a non-negative integer")
    if raw.get("classification") != "unvalidated_pdu_candidate":
        rejection_reasons.append("unexpected source candidate classification")
    if raw.get("validated") is not False or raw.get("validation_layers") != []:
        rejection_reasons.append("source candidate is not explicitly unvalidated")
    provenance = raw.get("provenance")
    if not isinstance(provenance, Mapping):
        rejection_reasons.append("candidate provenance is missing")
    elif provenance.get("backend_id") != "gr_satellites":
        rejection_reasons.append("candidate provenance is not gr_satellites")
    if not profile_declares_ax25:
        rejection_reasons.append("result profile does not declare AX.25 framing")

    reference = {
        "path": raw.get("payload_path"),
        "size_bytes": raw.get("payload_size_bytes"),
        "sha256": raw.get("payload_sha256"),
    }
    artifact, payload = _verify_binary_reference(
        reference,
        base=attempt_directory,
        campaign_root=campaign_root,
        max_bytes=max_payload_bytes,
    )
    result["raw_pdu"] = {
        **artifact,
        "payload_hex": payload.hex() if payload is not None else None,
    }
    if payload is None:
        rejection_reasons.extend(
            f"raw PDU artifact: {error}" for error in artifact["errors"]
        )
        return result
    validation_layers.append("payload_size_and_sha256")

    # gr-satellites KISS data PDUs are handed to this layer after the profile's
    # deframer has consumed its link FCS.  Parse the PDU exactly as delivered;
    # never strip arbitrary trailing bytes to make a frame pass.
    parsed = parse_ax25_ui(payload)
    if parsed is None:
        rejection_reasons.append("strict parse_ax25_ui rejected the complete PDU")
        return result
    validation_layers.append("strict_parse_ax25_ui_complete_pdu")
    result["trusted_ax25_ui"] = _ax25_document(parsed)

    ccsds = parse_ccsds_space_packet(parsed.information, require_exact_length=True)
    if ccsds is not None:
        validation_layers.append("exact_inner_ccsds_space_packet_length")
        result["inner_ccsds_space_packet"] = _ccsds_document(ccsds)

    if rejection_reasons:
        result["trusted_ax25_ui"] = None
        result["inner_ccsds_space_packet"] = None
        return result
    result["classification"] = "trusted_ax25_ui"
    return result


def _terminal_observation_audit(
    job: Mapping[str, Any],
    *,
    campaign_root: Path,
    max_output_bytes: int,
    max_payload_bytes: int,
) -> dict[str, Any]:
    observation_id = job.get("observation_id")
    record: dict[str, Any] = {
        "observation_id": observation_id,
        "manifest_status": job.get("status"),
        "manifest_job": dict(job),
        "audit_status": "result_rejected",
        "source_result": None,
        "result_metadata_valid": False,
        "artifact_integrity_valid": False,
        "diagnostics": [],
        "profile": None,
        "route": None,
        "raw_pdu_count": 0,
        "verified_raw_pdu_count": 0,
        "trusted_ax25_ui_count": 0,
        "rejected_pdu_count": 0,
        "inner_ccsds_count": 0,
        "candidates": [],
    }
    diagnostics: list[str] = record["diagnostics"]
    attempt_directory, path_error = _resolve_inside(
        job.get("artifact_directory"), base=campaign_root, allowed_root=campaign_root
    )
    if path_error is not None:
        diagnostics.append(f"artifact_directory: {path_error}")
        return record

    raw_result_path = job.get("result_path")
    if raw_result_path is None:
        result_path = (attempt_directory / "result.json").resolve(strict=False)
        try:
            result_path.relative_to(campaign_root)
            result_path.relative_to(attempt_directory)
        except ValueError:
            diagnostics.append("derived result path escapes its manifest artifact directory")
            return record
    else:
        result_path, path_error = _resolve_inside(
            raw_result_path, base=attempt_directory, allowed_root=campaign_root
        )
        if path_error is not None:
            diagnostics.append(f"result_path: {path_error}")
            return record
        try:
            result_path.relative_to(attempt_directory)
        except ValueError:
            diagnostics.append("result_path is outside its manifest artifact_directory")
            return record

    try:
        result_document, result_payload = _read_json_object(result_path)
    except (OSError, ValueError) as error:
        diagnostics.append(f"result read failed: {type(error).__name__}: {error}")
        return record
    record["source_result"] = {
        "path": str(result_path),
        "size_bytes": len(result_payload),
        "sha256": _sha256(result_payload),
        "path_bound_to_manifest": True,
        "expected_digest_available_in_manifest": False,
    }
    record["profile"] = result_document.get("profile")
    record["route"] = result_document.get("route")

    candidates = result_document.get("candidates")
    result_errors: list[str] = []
    if result_document.get("schema_version") != _RESULT_SCHEMA_VERSION:
        result_errors.append("unsupported result schema_version")
    if result_document.get("observation_id") != observation_id:
        result_errors.append("result observation_id does not match manifest")
    if result_document.get("attempt_fingerprint") != job.get("attempt_fingerprint"):
        result_errors.append("result attempt_fingerprint does not match manifest")
    if result_document.get("status") != job.get("status"):
        result_errors.append("result status does not match manifest")
    if not isinstance(candidates, list):
        result_errors.append("result candidates is not a list")
        candidates = []
    declared_candidate_count = result_document.get("candidate_count")
    if not _is_int(declared_candidate_count) or declared_candidate_count != len(
        candidates
    ):
        result_errors.append("result candidate_count does not match candidates")
    if job.get("candidate_count", 0) != declared_candidate_count:
        result_errors.append("manifest candidate_count does not match result")
    diagnostics.extend(result_errors)
    result_metadata_valid = not result_errors
    record["result_metadata_valid"] = result_metadata_valid

    artifact_checks: dict[str, Any] = {}
    result_status = result_document.get("status")
    if result_status in {"completed", "backend_failure"}:
        for name in ("stdout", "stderr"):
            check, _ = _verify_binary_reference(
                result_document.get(name),
                base=attempt_directory,
                campaign_root=campaign_root,
                max_bytes=max_output_bytes,
            )
            artifact_checks[name] = check
            diagnostics.extend(
                f"{name} artifact: {error}" for error in check["errors"]
            )
    record["process_artifacts"] = artifact_checks

    profile_declares_ax25 = _profile_declares_ax25(record["profile"])
    audited_candidates = [
        _candidate_audit(
            candidate,
            list_index=index,
            attempt_directory=attempt_directory,
            campaign_root=campaign_root,
            max_payload_bytes=max_payload_bytes,
            result_metadata_valid=result_metadata_valid,
            profile_declares_ax25=profile_declares_ax25,
        )
        for index, candidate in enumerate(candidates)
    ]
    record["candidates"] = audited_candidates
    record["raw_pdu_count"] = len(audited_candidates)
    record["verified_raw_pdu_count"] = sum(
        bool(item["raw_pdu"] and item["raw_pdu"]["verified"])
        for item in audited_candidates
    )
    record["trusted_ax25_ui_count"] = sum(
        item["classification"] == "trusted_ax25_ui" for item in audited_candidates
    )
    record["rejected_pdu_count"] = sum(
        item["classification"] == "rejected" for item in audited_candidates
    )
    record["inner_ccsds_count"] = sum(
        item["inner_ccsds_space_packet"] is not None
        for item in audited_candidates
    )
    process_artifacts_valid = all(
        check["verified"] for check in artifact_checks.values()
    )
    candidate_artifacts_valid = all(
        item["raw_pdu"] is not None and item["raw_pdu"]["verified"]
        for item in audited_candidates
    )
    record["artifact_integrity_valid"] = (
        process_artifacts_valid and candidate_artifacts_valid
    )
    if result_metadata_valid:
        record["audit_status"] = (
            "audited" if record["artifact_integrity_valid"] else "artifact_rejected"
        )
    return record


def audit_gr_satellites_campaign(manifest_path: str | Path) -> dict[str, Any]:
    """Audit one atomic manifest snapshot without executing gr-satellites."""

    manifest_path = Path(manifest_path).resolve()
    manifest, manifest_payload = _read_json_object(manifest_path)
    if manifest.get("schema_version") != _BATCH_SCHEMA_VERSION:
        raise ValueError("unsupported gr-satellites batch manifest")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("batch manifest jobs must be a list")
    campaign_root = manifest_path.parent.resolve()
    config = manifest.get("config")
    config = config if isinstance(config, Mapping) else {}
    max_output_bytes = config.get("max_output_bytes", 4 * 1024 * 1024)
    max_payload_bytes = config.get("max_kiss_bytes", 16 * 1024 * 1024)
    if not _is_int(max_output_bytes) or max_output_bytes < 1:
        max_output_bytes = 4 * 1024 * 1024
    if not _is_int(max_payload_bytes) or max_payload_bytes < 1:
        max_payload_bytes = 16 * 1024 * 1024

    observations: list[dict[str, Any]] = []
    invalid_job_count = 0
    for manifest_index, raw_job in enumerate(jobs):
        if not isinstance(raw_job, Mapping):
            invalid_job_count += 1
            observations.append(
                {
                    "observation_id": None,
                    "manifest_index": manifest_index,
                    "manifest_status": None,
                    "audit_status": "manifest_job_rejected",
                    "diagnostics": ["manifest job is not an object"],
                    "raw_pdu_count": 0,
                    "verified_raw_pdu_count": 0,
                    "trusted_ax25_ui_count": 0,
                    "rejected_pdu_count": 0,
                    "inner_ccsds_count": 0,
                    "candidates": [],
                }
            )
            continue
        status = raw_job.get("status")
        if status not in _TERMINAL_STATUSES:
            observations.append(
                {
                    "observation_id": raw_job.get("observation_id"),
                    "manifest_index": manifest_index,
                    "manifest_status": status,
                    "manifest_job": dict(raw_job),
                    "audit_status": "not_terminal",
                    "diagnostics": [],
                    "profile": None,
                    "route": None,
                    "raw_pdu_count": 0,
                    "verified_raw_pdu_count": 0,
                    "trusted_ax25_ui_count": 0,
                    "rejected_pdu_count": 0,
                    "inner_ccsds_count": 0,
                    "candidates": [],
                }
            )
            continue
        audited = _terminal_observation_audit(
            raw_job,
            campaign_root=campaign_root,
            max_output_bytes=max_output_bytes,
            max_payload_bytes=max_payload_bytes,
        )
        audited["manifest_index"] = manifest_index
        observations.append(audited)

    observations.sort(
        key=lambda item: (
            not _is_int(item.get("observation_id")),
            item.get("observation_id") if _is_int(item.get("observation_id")) else 0,
            item["manifest_index"],
        )
    )
    actual_status_counts = Counter(
        item.get("status") if isinstance(item, Mapping) else None for item in jobs
    )
    actual_status_counts_document = {
        str(key): value
        for key, value in sorted(actual_status_counts.items(), key=lambda pair: str(pair[0]))
    }
    declared_job_count = manifest.get("job_count")
    declared_status_counts = manifest.get("status_counts")
    observation_ids = [
        item.get("observation_id") for item in jobs if isinstance(item, Mapping)
    ]
    unique_valid_observation_ids = (
        all(_is_int(value) and value > 0 for value in observation_ids)
        and len(set(observation_ids)) == len(observation_ids)
    )
    manifest_consistent = (
        invalid_job_count == 0
        and _is_int(declared_job_count)
        and declared_job_count == len(jobs)
        and isinstance(declared_status_counts, Mapping)
        and dict(declared_status_counts) == actual_status_counts_document
        and unique_valid_observation_ids
    )
    campaign_complete = manifest_consistent and all(
        isinstance(item, Mapping) and item.get("status") in _TERMINAL_STATUSES
        for item in jobs
    )
    terminal_observations = [
        item for item in observations if item["manifest_status"] in _TERMINAL_STATUSES
    ]
    audit_complete = campaign_complete and all(
        item["audit_status"] == "audited" for item in terminal_observations
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_complete": campaign_complete,
        "audit_complete": audit_complete,
        "source_manifest": {
            "path": str(manifest_path),
            "size_bytes": len(manifest_payload),
            "sha256": _sha256(manifest_payload),
            "campaign_fingerprint": manifest.get("campaign_fingerprint"),
        },
        "acceptance_policy": {
            "pdu_source": "manifest-bound gr-satellites KISS candidate artifact",
            "pdu_fcs_convention": "link FCS consumed by the profile deframer",
            "ax25": (
                "artifact size+SHA-256, unvalidated gr_satellites provenance, "
                "AX.25-declaring profile, and strict parse_ax25_ui of the complete PDU"
            ),
            "inner_ccsds": (
                "parse_ccsds_space_packet over the complete AX.25 information field "
                "with exact declared length"
            ),
            "limitation": (
                "the KISS PDU does not retain the consumed link FCS, so the audit "
                "does not claim an independent FCS recomputation"
            ),
        },
        "manifest_consistent": manifest_consistent,
        "declared_job_count": declared_job_count,
        "manifest_job_count": len(jobs),
        "manifest_status_counts": actual_status_counts_document,
        "terminal_observation_count": len(terminal_observations),
        "audited_observation_count": sum(
            item["audit_status"] in {"audited", "artifact_rejected"}
            for item in terminal_observations
        ),
        "raw_pdu_count": sum(item["raw_pdu_count"] for item in observations),
        "verified_raw_pdu_count": sum(
            item["verified_raw_pdu_count"] for item in observations
        ),
        "trusted_ax25_ui_count": sum(
            item["trusted_ax25_ui_count"] for item in observations
        ),
        "rejected_pdu_count": sum(
            item["rejected_pdu_count"] for item in observations
        ),
        "inner_ccsds_count": sum(
            item["inner_ccsds_count"] for item in observations
        ),
        "observations": observations,
    }
