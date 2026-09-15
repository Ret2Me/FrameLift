"""Deterministic artifact-only comparator for the positive same-IQ cohort."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ax25_validation import AX25UIFrame, parse_ax25_ui
from .crc import validate_ax25_fcs


SCHEMA_VERSION = "positive-g3ruh-official-native-comparator-v1"
OFFICIAL_AUDIT_SCHEMA = "gr-satellites-batch-audit-v1"
OFFICIAL_BASELINE_SCHEMA = "positive-g3ruh-gr-satellites-baseline-v1"
NATIVE_MANIFEST_SCHEMA = "matched-iq-positive-benchmark-manifest-v1"
NATIVE_COMPARISON_SCHEMA = "matched-iq-positive-observation-v1"

POSITIVE_G3RUH_OBSERVATION_IDS = (
    3291,
    3413,
    3697,
    3701,
    3721,
    3864,
    3928,
    3929,
    4015,
    4048,
    4050,
    4084,
    4091,
    4134,
    4206,
    4233,
    4234,
    4273,
    4276,
)

_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_FRAME_BYTES = 16 * 1024 * 1024
_SUCCESS_NATIVE_STATUSES = frozenset({"processed", "skipped_complete"})


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
    if not path.is_file():
        raise ValueError(f"JSON artifact is not a regular file: {path}")
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError(f"JSON artifact exceeds {_MAX_JSON_BYTES} bytes: {path}")
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON artifact {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"JSON artifact must contain an object: {path}")
    return document, payload


def _resolve_inside(raw: object, *, base: Path, root: Path) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("artifact path is missing")
    path = Path(raw)
    if not path.is_absolute():
        path = base / path
    path = path.resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"artifact path escapes its campaign root: {path}") from error
    return path


def _verified_file(
    raw_path: object,
    *,
    expected_size: object,
    expected_sha256: object,
    base: Path,
    root: Path,
    max_bytes: int,
) -> tuple[Path, bytes]:
    if not _is_int(expected_size) or expected_size < 0:
        raise ValueError("artifact expected size is invalid")
    if not _is_sha256(expected_sha256):
        raise ValueError("artifact expected SHA-256 is invalid")
    path = _resolve_inside(raw_path, base=base, root=root)
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {path}")
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise ValueError(f"artifact size mismatch: {path}")
    if actual_size > max_bytes:
        raise ValueError(f"artifact exceeds comparator limit: {path}")
    payload = path.read_bytes()
    if _sha256(payload) != str(expected_sha256).casefold():
        raise ValueError(f"artifact SHA-256 mismatch: {path}")
    return path, payload


def _verified_sha_file(
    raw_path: object,
    *,
    expected_sha256: object,
    base: Path,
    root: Path,
    max_bytes: int,
) -> tuple[Path, bytes]:
    if not _is_sha256(expected_sha256):
        raise ValueError("artifact expected SHA-256 is invalid")
    path = _resolve_inside(raw_path, base=base, root=root)
    if not path.is_file():
        raise ValueError(f"artifact is not a regular file: {path}")
    if path.stat().st_size > max_bytes:
        raise ValueError(f"artifact exceeds comparator limit: {path}")
    payload = path.read_bytes()
    if _sha256(payload) != str(expected_sha256).casefold():
        raise ValueError(f"artifact SHA-256 mismatch: {path}")
    return path, payload


def _address_label(frame: AX25UIFrame) -> dict[str, Any]:
    return {
        "destination": frame.destination.callsign,
        "destination_ssid": frame.destination.ssid,
        "source": frame.source.callsign,
        "source_ssid": frame.source.ssid,
        "digipeater_count": len(frame.digipeaters),
        "pid": frame.pid,
        "information_length_bytes": len(frame.information),
    }


def _strict_pdu_set(
    candidates: Sequence[tuple[bytes, str]], *, require_fcs: bool
) -> tuple[dict[bytes, dict[str, Any]], list[dict[str, Any]]]:
    trusted: dict[bytes, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for raw_payload, provenance in candidates:
        raw_digest = _sha256(raw_payload)
        reasons: list[str] = []
        normalized = raw_payload
        if require_fcs:
            if not validate_ax25_fcs(raw_payload):
                reasons.append("invalid_crc16_x25_fcs")
            else:
                normalized = raw_payload[:-2]
        parsed = parse_ax25_ui(normalized) if not reasons else None
        if parsed is None and not reasons:
            reasons.append("strict_parse_ax25_ui_rejected")
        if reasons:
            rejected.append(
                {
                    "raw_sha256": raw_digest,
                    "raw_size_bytes": len(raw_payload),
                    "provenance": provenance,
                    "reasons": reasons,
                }
            )
            continue
        assert parsed is not None
        normalized_digest = _sha256(normalized)
        trusted.setdefault(
            normalized,
            {
                "normalized_payload_sha256": normalized_digest,
                "normalized_payload_size_bytes": len(normalized),
                "ax25": _address_label(parsed),
                "origins": [],
            },
        )["origins"].append(
            {
                "raw_sha256": raw_digest,
                "raw_size_bytes": len(raw_payload),
                "provenance": provenance,
            }
        )
    for record in trusted.values():
        record["origins"].sort(
            key=lambda item: (item["raw_sha256"], item["provenance"])
        )
    rejected.sort(key=lambda item: (item["raw_sha256"], item["provenance"]))
    return trusted, rejected


def _load_official_audit(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    outer, outer_payload = _read_json(path)
    if outer.get("schema_version") == OFFICIAL_BASELINE_SCHEMA:
        audit = outer.get("audit")
        if not isinstance(audit, dict):
            raise ValueError("official baseline report does not embed an audit")
    elif outer.get("schema_version") == OFFICIAL_AUDIT_SCHEMA:
        audit = outer
    else:
        raise ValueError("unsupported official audit/baseline schema")
    source = {
        "path": str(path),
        "size_bytes": len(outer_payload),
        "sha256": _sha256(outer_payload),
        "schema_version": outer.get("schema_version"),
    }
    return audit, source


def _official_observations(
    audit: Mapping[str, Any]
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    if (
        audit.get("schema_version") != OFFICIAL_AUDIT_SCHEMA
        or audit.get("campaign_complete") is not True
        or audit.get("audit_complete") is not True
        or audit.get("manifest_consistent") is not True
    ):
        raise ValueError("official gr-satellites audit is not complete and consistent")
    source_manifest = audit.get("source_manifest")
    if not isinstance(source_manifest, Mapping):
        raise ValueError("official audit source_manifest is missing")
    manifest_path = Path(str(source_manifest.get("path"))).resolve(strict=False)
    _, manifest_payload = _read_json(manifest_path)
    if (
        source_manifest.get("size_bytes") != len(manifest_payload)
        or source_manifest.get("sha256") != _sha256(manifest_payload)
    ):
        raise ValueError("official source manifest no longer matches the audit")
    campaign_root = manifest_path.parent.resolve()

    raw_observations = audit.get("observations")
    if not isinstance(raw_observations, list):
        raise ValueError("official audit observations must be a list")
    by_id: dict[int, dict[str, Any]] = {}
    total_raw = 0
    total_rejected = 0
    total_trusted = 0
    for raw in raw_observations:
        if not isinstance(raw, Mapping):
            raise ValueError("official audit observation is not an object")
        observation_id = raw.get("observation_id")
        if not _is_int(observation_id) or observation_id in by_id:
            raise ValueError("official audit observation IDs are invalid or duplicated")
        if (
            raw.get("manifest_status") != "completed"
            or raw.get("audit_status") != "audited"
            or raw.get("result_metadata_valid") is not True
            or raw.get("artifact_integrity_valid") is not True
        ):
            raise ValueError(f"official observation {observation_id} is not fully audited")
        source_result = raw.get("source_result")
        if not isinstance(source_result, Mapping):
            raise ValueError(f"official observation {observation_id} has no result reference")
        result_path, result_payload = _verified_file(
            source_result.get("path"),
            expected_size=source_result.get("size_bytes"),
            expected_sha256=source_result.get("sha256"),
            base=campaign_root,
            root=campaign_root,
            max_bytes=_MAX_JSON_BYTES,
        )
        try:
            result_document = json.loads(result_payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"official result JSON is invalid: {result_path}") from error
        if (
            not isinstance(result_document, Mapping)
            or result_document.get("observation_id") != observation_id
        ):
            raise ValueError("official result/observation identity mismatch")
        result_input = result_document.get("input")
        capture_sha = result_input.get("sha256") if isinstance(result_input, Mapping) else None
        if not _is_sha256(capture_sha):
            raise ValueError(f"official observation {observation_id} lacks input SHA-256")

        raw_candidates = raw.get("candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("official candidates must be a list")
        candidates: list[tuple[bytes, str]] = []
        for list_index, candidate in enumerate(raw_candidates):
            if not isinstance(candidate, Mapping):
                raise ValueError("official candidate is not an object")
            artifact = candidate.get("raw_pdu")
            if not isinstance(artifact, Mapping) or artifact.get("verified") is not True:
                raise ValueError("official raw PDU artifact is not verified")
            payload_path, payload = _verified_file(
                artifact.get("path"),
                expected_size=artifact.get("actual_size_bytes"),
                expected_sha256=artifact.get("actual_sha256"),
                base=result_path.parent,
                root=campaign_root,
                max_bytes=_MAX_FRAME_BYTES,
            )
            if artifact.get("payload_hex") != payload.hex():
                raise ValueError("official audit payload_hex differs from its artifact")
            candidates.append((payload, f"official:{observation_id}:{list_index}:{payload_path}"))
        trusted, rejected = _strict_pdu_set(candidates, require_fcs=False)
        if len(candidates) != raw.get("raw_pdu_count"):
            raise ValueError("official raw candidate count mismatch")
        by_id[observation_id] = {
            "input_sha256": str(capture_sha).casefold(),
            "result_path": str(result_path),
            "result_sha256": _sha256(result_payload),
            "raw_candidate_count": len(candidates),
            "trusted": trusted,
            "rejected": rejected,
        }
        total_raw += len(candidates)
        total_rejected += len(rejected)
        total_trusted += len(trusted)
    if tuple(sorted(by_id)) != POSITIVE_G3RUH_OBSERVATION_IDS:
        raise ValueError("official audit does not contain the exact 19-ID cohort")
    if (
        audit.get("raw_pdu_count") != total_raw
        or audit.get("rejected_pdu_count") != total_rejected
        or audit.get("trusted_ax25_ui_count") != total_trusted
    ):
        raise ValueError("official audit aggregate candidate counts are inconsistent")
    return by_id, {
        "path": str(manifest_path),
        "size_bytes": len(manifest_payload),
        "sha256": _sha256(manifest_payload),
    }


def _native_observation(
    result: Mapping[str, Any], *, native_root: Path
) -> dict[str, Any]:
    observation_id = result.get("observation_id")
    comparison_path, comparison_payload = _verified_sha_file(
        result.get("comparison_path"),
        expected_sha256=result.get("comparison_sha256"),
        base=native_root,
        root=native_root,
        max_bytes=_MAX_JSON_BYTES,
    )
    try:
        comparison = json.loads(comparison_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"native comparison JSON is invalid: {comparison_path}") from error
    if (
        not isinstance(comparison, Mapping)
        or comparison.get("schema_version") != NATIVE_COMPARISON_SCHEMA
        or comparison.get("complete") is not True
        or comparison.get("observation_id") != observation_id
    ):
        raise ValueError("native comparison artifact identity/schema mismatch")
    input_document = comparison.get("input")
    native_document = comparison.get("native")
    if not isinstance(input_document, Mapping) or not isinstance(native_document, Mapping):
        raise ValueError("native comparison input/native sections are missing")
    capture_sha = input_document.get("ci16_sha256")
    if not _is_sha256(capture_sha):
        raise ValueError("native comparison lacks ci16_sha256")
    phase_path = _resolve_inside(
        native_document.get("source_artifact"), base=comparison_path.parent, root=native_root
    )
    expected_phase_sha = native_document.get("source_artifact_sha256")
    phase, phase_payload = _read_json(phase_path)
    if not _is_sha256(expected_phase_sha) or _sha256(phase_payload) != expected_phase_sha:
        raise ValueError("native phase artifact SHA-256 mismatch")
    raw_frames = phase.get("unique_crc_valid_frames")
    if (
        phase.get("stage") != "decode-only"
        or phase.get("observation_id") != observation_id
        or not isinstance(raw_frames, list)
        or phase.get("unique_crc_valid_frame_count") != len(raw_frames)
    ):
        raise ValueError("native phase artifact structure/identity mismatch")
    candidates: list[tuple[bytes, str]] = []
    for index, raw in enumerate(raw_frames):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("frame_with_fcs_hex"), str):
            raise ValueError("native phase candidate is malformed")
        try:
            frame = bytes.fromhex(raw["frame_with_fcs_hex"])
        except ValueError as error:
            raise ValueError("native phase candidate hex is malformed") from error
        declared_digest = raw.get("frame_with_fcs_sha256")
        if declared_digest is not None and declared_digest != _sha256(frame):
            raise ValueError("native phase candidate digest mismatch")
        candidates.append((frame, f"native:{observation_id}:{index}:{phase_path}"))
    trusted, rejected = _strict_pdu_set(candidates, require_fcs=True)
    recomputed_digests = sorted(_sha256(payload) for payload in trusted)
    if native_document.get("trusted_payload_sha256") != recomputed_digests:
        raise ValueError("native stored strict audit differs from comparator revalidation")
    if native_document.get("raw_crc_valid_candidate_count") != len(candidates):
        raise ValueError("native raw candidate count mismatch")
    return {
        "input_sha256": str(capture_sha).casefold(),
        "comparison_path": str(comparison_path),
        "comparison_sha256": _sha256(comparison_payload),
        "phase_artifact_path": str(phase_path),
        "phase_artifact_sha256": str(expected_phase_sha),
        "raw_candidate_count": len(candidates),
        "trusted": trusted,
        "rejected": rejected,
    }


def _load_native_manifest(
    path: Path | None,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any], bool]:
    if path is None or not path.is_file():
        return {}, {"path": str(path) if path is not None else None, "available": False}, False
    path = path.resolve()
    manifest, payload = _read_json(path)
    if manifest.get("schema_version") != NATIVE_MANIFEST_SCHEMA:
        raise ValueError("unsupported native benchmark manifest schema")
    contract = manifest.get("selection_contract")
    if (
        not isinstance(contract, Mapping)
        or contract.get("mode_exact") != "FSK AX.25 G3RUH"
        or contract.get("frames_recovered") is not True
        or contract.get("expected_observation_count") != 19
        or manifest.get("candidate_count") != 19
    ):
        raise ValueError("native manifest is not the exact positive 19-IQ cohort")
    raw_results = manifest.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("native manifest results must be a list")
    native_root = path.parent.resolve()
    by_id: dict[int, dict[str, Any]] = {}
    seen: set[int] = set()
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            raise ValueError("native manifest result is not an object")
        observation_id = raw.get("observation_id")
        if (
            not _is_int(observation_id)
            or observation_id not in POSITIVE_G3RUH_OBSERVATION_IDS
            or observation_id in seen
        ):
            raise ValueError("native manifest result IDs are invalid or duplicated")
        seen.add(observation_id)
        if raw.get("status") in _SUCCESS_NATIVE_STATUSES:
            by_id[observation_id] = _native_observation(raw, native_root=native_root)
    manifest_complete = manifest.get("complete") is True
    if manifest_complete and tuple(sorted(by_id)) != POSITIVE_G3RUH_OBSERVATION_IDS:
        raise ValueError("complete native manifest lacks the exact 19 successful results")
    return by_id, {
        "path": str(path),
        "available": True,
        "size_bytes": len(payload),
        "sha256": _sha256(payload),
        "declared_complete": manifest_complete,
        "status_counts": manifest.get("status_counts"),
    }, manifest_complete


def _side_document(side: Mapping[str, Any] | None) -> dict[str, Any]:
    if side is None:
        return {
            "available": False,
            "input_sha256": None,
            "raw_candidate_count": None,
            "trusted_unique_count": None,
            "trusted_payload_sha256": None,
            "raw_rejected_count": None,
            "raw_rejected": None,
        }
    trusted = side["trusted"]
    rejected = side["rejected"]
    return {
        "available": True,
        "input_sha256": side["input_sha256"],
        "raw_candidate_count": side["raw_candidate_count"],
        "trusted_unique_count": len(trusted),
        "trusted_payload_sha256": sorted(_sha256(payload) for payload in trusted),
        "trusted": [trusted[payload] for payload in sorted(trusted, key=_sha256)],
        "raw_rejected_count": len(rejected),
        "raw_rejected": rejected,
        "artifacts": {
            key: value
            for key, value in side.items()
            if key.endswith("_path") or key.endswith("_sha256")
        },
    }


def compare_positive_same_iq(
    official_audit_or_baseline_path: str | Path,
    native_manifest_path: str | Path | None,
) -> dict[str, Any]:
    """Compare trusted byte sets without executing either decoder."""

    official_path = Path(official_audit_or_baseline_path).resolve()
    audit, official_source = _load_official_audit(official_path)
    official, official_manifest_source = _official_observations(audit)
    native_path = Path(native_manifest_path) if native_manifest_path is not None else None
    native, native_source, native_declared_complete = _load_native_manifest(native_path)

    observations: list[dict[str, Any]] = []
    for observation_id in POSITIVE_G3RUH_OBSERVATION_IDS:
        baseline = official[observation_id]
        candidate = native.get(observation_id)
        identical_sha: bool | None = None
        if candidate is not None:
            identical_sha = baseline["input_sha256"] == candidate["input_sha256"]
            if not identical_sha:
                raise ValueError(
                    f"observation {observation_id} official/native input SHA-256 mismatch"
                )
        baseline_payloads = set(baseline["trusted"])
        native_payloads = set(candidate["trusted"]) if candidate is not None else None
        if native_payloads is None:
            sets = {
                "baseline_count": len(baseline_payloads),
                "native_count": None,
                "overlap_count": None,
                "union_count": None,
                "incremental_over_baseline": None,
                "baseline_only_count": None,
                "overlap_payload_sha256": None,
                "incremental_payload_sha256": None,
                "baseline_only_payload_sha256": None,
                "union_payload_sha256": None,
            }
        else:
            overlap = baseline_payloads & native_payloads
            union = baseline_payloads | native_payloads
            incremental = native_payloads - baseline_payloads
            baseline_only = baseline_payloads - native_payloads
            sets = {
                "baseline_count": len(baseline_payloads),
                "native_count": len(native_payloads),
                "overlap_count": len(overlap),
                "union_count": len(union),
                "incremental_over_baseline": len(incremental),
                "baseline_only_count": len(baseline_only),
                "overlap_payload_sha256": sorted(_sha256(value) for value in overlap),
                "incremental_payload_sha256": sorted(
                    _sha256(value) for value in incremental
                ),
                "baseline_only_payload_sha256": sorted(
                    _sha256(value) for value in baseline_only
                ),
                "union_payload_sha256": sorted(_sha256(value) for value in union),
            }
        observations.append(
            {
                "observation_id": observation_id,
                "comparison_complete": candidate is not None,
                "identical_input_sha256": identical_sha,
                "baseline": _side_document(baseline),
                "native": _side_document(candidate),
                "sets": sets,
            }
        )

    complete = native_declared_complete and len(native) == len(
        POSITIVE_G3RUH_OBSERVATION_IDS
    )
    completed = [item for item in observations if item["comparison_complete"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "complete": complete,
        "status": "complete" if complete else "incomplete_native",
        "cohort": {
            "observation_count": 19,
            "observation_ids": list(POSITIVE_G3RUH_OBSERVATION_IDS),
            "mode_exact": "FSK AX.25 G3RUH",
            "frames_recovered": True,
            "same_iq_required": True,
        },
        "acceptance_policy": {
            "official": "verified KISS PDU artifact plus strict parse_ax25_ui",
            "native": (
                "verified phase artifact plus CRC-16/X-25 FCS plus strict "
                "parse_ax25_ui after removing the verified FCS"
            ),
            "normalization": "complete AX.25 UI bytes without FCS",
            "dedupe": "exact normalized bytes within each observation and branch",
            "decoder_execution": False,
        },
        "sources": {
            "official_report": official_source,
            "official_manifest": official_manifest_source,
            "native_manifest": native_source,
        },
        "summary": {
            "official_observation_count": len(official),
            "native_completed_observation_count": len(completed),
            "baseline_raw_candidate_count": sum(
                item["baseline"]["raw_candidate_count"] for item in observations
            ),
            "baseline_raw_rejected_count": sum(
                item["baseline"]["raw_rejected_count"] for item in observations
            ),
            "baseline_trusted_unique_count": sum(
                item["sets"]["baseline_count"] for item in observations
            ),
            "native_raw_candidate_count_completed_subset": sum(
                item["native"]["raw_candidate_count"] for item in completed
            ),
            "native_raw_rejected_count_completed_subset": sum(
                item["native"]["raw_rejected_count"] for item in completed
            ),
            "native_trusted_unique_count_completed_subset": sum(
                item["sets"]["native_count"] for item in completed
            ),
            "union_count_completed_subset": sum(
                item["sets"]["union_count"] for item in completed
            ),
            "incremental_over_baseline_completed_subset": sum(
                item["sets"]["incremental_over_baseline"] for item in completed
            ),
            "union_count_full_cohort": (
                sum(item["sets"]["union_count"] for item in observations)
                if complete
                else None
            ),
            "incremental_over_baseline_full_cohort": (
                sum(
                    item["sets"]["incremental_over_baseline"]
                    for item in observations
                )
                if complete
                else None
            ),
        },
        "observations": observations,
    }
