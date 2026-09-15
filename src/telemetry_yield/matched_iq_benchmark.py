"""Resumable matched-IQ benchmark for positive explicit G3RUH observations.

Catalogue references and phase-first output are independently subjected to
strict AX.25 UI structure checks.  Native candidates additionally require a
valid AX.25 FCS.  CRC validity by itself is never counted as telemetry.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from .archive_download import sha256_file
from .archive_processing import (
    EXPLICIT_MODE,
    ProcessingCandidate,
    prepare_conversion,
    run_phase_first_decoder,
    validate_decode_artifact,
)
from .archive_protocol_audit import audit_phase_first_artifact
from .ax25_validation import parse_ax25_ui
from .canonical import canonical_json


COMPARISON_SCHEMA = "matched-iq-positive-observation-v1"
CHECKPOINT_SCHEMA = "matched-iq-positive-checkpoint-v1"
MANIFEST_SCHEMA = "matched-iq-positive-benchmark-manifest-v1"

# Primary-source contracts for bytes that reach a SatNOGS ``demoddata``
# object. They intentionally document transformations that have *already*
# happened upstream; none licenses stripping a prefix from the stored PDU.
SATNOGS_CLIENT_PDU_UPLOAD_SOURCE = (
    "https://gitlab.com/librespacefoundation/satnogs/satnogs-client/-/blob/"
    "14561eff1d8f598ebc71f49e1347b16c3290a770/satnogsclient/scheduler/"
    "tasks.py#L101-116"
)
GR_SATELLITES_KISS_SOURCE = (
    "https://gitlab.com/librespacefoundation/satnogs/satnogs-client/-/blob/"
    "203379c/satnogsclient/radio/grsat.py#L25-72"
)
GR_SATNOGS_AX25_PDU_SOURCE = (
    "https://gitlab.com/librespacefoundation/satnogs/gr-satnogs/-/blob/"
    "8d94dc292ace327dcd15dba66dd2020031b2d5c7/lib/ax25_decoder.cc#L307-339"
)


@dataclass(frozen=True, slots=True)
class ReferencePayload:
    frame_id: int
    payload: bytes
    source_url: str | None
    filename: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True, slots=True)
class PositiveBenchmarkCandidate:
    observation_id: int
    expected_ci16_size_bytes: int
    satellite_id: str | None
    references: tuple[ReferencePayload, ...]

    @property
    def reference_commitment_sha256(self) -> str:
        document = [
            {
                "frame_id": item.frame_id,
                "payload_length_bytes": len(item.payload),
                "payload_sha256": item.sha256,
                "source_url": item.source_url,
                "filename": item.filename,
            }
            for item in self.references
        ]
        return hashlib.sha256(canonical_json(document).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReferencePayloadVariant:
    """One explicitly allowed interpretation of catalogue payload bytes."""

    kind: str
    offset_bytes: int
    payload: bytes
    container_contract: str
    contract_sources: tuple[str, ...]


def documented_reference_payload_variants(
    reference: ReferencePayload,
) -> tuple[ReferencePayloadVariant, ...]:
    """Return only source-backed interpretations, never guessed byte offsets.

    SatNOGS Client uploads the decoded ``pdu`` bytes themselves. Native
    gr-satnogs JSON metadata is base64-decoded before upload; gr-satellites
    KISS framing, command byte, and escaping are likewise removed before its
    JSON ``pdu`` is uploaded. Consequently a catalogue/bucket ``data_*``
    object has no remaining generic SatNOGS envelope to remove.
    """

    filename = reference.filename or ""
    if re.search(r"_g\d+$", filename):
        contract = "gr_satellites_kiss_already_unwrapped_before_upload"
        sources = (GR_SATELLITES_KISS_SOURCE, SATNOGS_CLIENT_PDU_UPLOAD_SOURCE)
    elif re.fullmatch(
        r"data_\d+_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}", filename
    ):
        contract = "gr_satnogs_metadata_pdu_already_decoded_before_upload"
        sources = (GR_SATNOGS_AX25_PDU_SOURCE, SATNOGS_CLIENT_PDU_UPLOAD_SOURCE)
    else:
        contract = "catalogue_raw_payload_no_documented_container"
        sources = ()
    return (
        ReferencePayloadVariant(
            kind="raw_uploaded_pdu",
            offset_bytes=0,
            payload=reference.payload,
            container_contract=contract,
            contract_sources=sources,
        ),
    )


def select_positive_explicit_g3ruh(
    catalogue: Mapping[str, Any],
) -> tuple[PositiveBenchmarkCandidate, ...]:
    observations = catalogue.get("observations")
    if not isinstance(observations, Sequence) or isinstance(
        observations, (str, bytes, bytearray, memoryview)
    ):
        raise ValueError("observations must be an array")
    selected: list[PositiveBenchmarkCandidate] = []
    seen: set[int] = set()
    for raw in observations:
        if not isinstance(raw, Mapping):
            raise ValueError("observation must be an object")
        links = raw.get("links")
        iq_url = links.get("iq") if isinstance(links, Mapping) else None
        if (
            raw.get("mode") != EXPLICIT_MODE
            or raw.get("frames_recovered") is not True
            or not isinstance(iq_url, str)
            or not iq_url.strip()
        ):
            continue
        observation_id = raw.get("observation_id")
        if not isinstance(observation_id, int) or isinstance(observation_id, bool):
            raise ValueError("observation_id must be an integer")
        if observation_id in seen:
            raise ValueError("observation_id values must be unique")
        seen.add(observation_id)
        artifacts = raw.get("artifacts")
        iq_artifact = artifacts.get("iq") if isinstance(artifacts, Mapping) else None
        size = iq_artifact.get("size_bytes") if isinstance(iq_artifact, Mapping) else None
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError(f"observation {observation_id} lacks exact IQ size")
        raw_frames = raw.get("frames")
        if not isinstance(raw_frames, Sequence) or isinstance(
            raw_frames, (str, bytes, bytearray, memoryview)
        ):
            raise ValueError("positive observation frames must be an array")
        references: list[ReferencePayload] = []
        for frame_index, raw_frame in enumerate(raw_frames):
            if not isinstance(raw_frame, Mapping):
                raise ValueError("reference frame must be an object")
            encoded = raw_frame.get("payload_hex")
            if encoded is None:
                continue
            if not isinstance(encoded, str):
                raise ValueError("reference payload_hex must be text")
            try:
                payload = bytes.fromhex(encoded)
            except ValueError as error:
                raise ValueError("reference payload_hex is malformed") from error
            frame_id = raw_frame.get("frame_id")
            if not isinstance(frame_id, int) or isinstance(frame_id, bool):
                raise ValueError("reference frame_id must be an integer")
            declared_size = raw_frame.get("size_bytes")
            if declared_size != len(payload) or not payload:
                raise ValueError(
                    f"reference frame {observation_id}:{frame_index} size mismatch"
                )
            url = raw_frame.get("url")
            references.append(
                ReferencePayload(
                    frame_id=frame_id,
                    payload=payload,
                    source_url=url if isinstance(url, str) and url else None,
                    filename=(
                        raw_frame.get("filename")
                        if isinstance(raw_frame.get("filename"), str)
                        and raw_frame.get("filename")
                        else None
                    ),
                )
            )
        if not references:
            raise ValueError(
                f"positive observation {observation_id} has no raw reference bytes"
            )
        satellite_id = raw.get("satellite_id")
        selected.append(
            PositiveBenchmarkCandidate(
                observation_id=observation_id,
                expected_ci16_size_bytes=size,
                satellite_id=(
                    satellite_id
                    if isinstance(satellite_id, str) and satellite_id
                    else None
                ),
                references=tuple(references),
            )
        )
    return tuple(sorted(selected, key=lambda item: item.observation_id))


def _payload_record(payload: bytes) -> dict[str, object]:
    return {
        "payload_length_bytes": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }


def audit_catalogue_references(
    candidate: PositiveBenchmarkCandidate,
) -> tuple[dict[str, object], set[bytes]]:
    records: list[dict[str, object]] = []
    trusted: set[bytes] = set()
    trusted_variant_counts: Counter[str] = Counter()
    ambiguous_valid_variant_count = 0
    for reference in candidate.references:
        attempts: list[dict[str, object]] = []
        valid_payloads: dict[bytes, list[ReferencePayloadVariant]] = {}
        for variant in documented_reference_payload_variants(reference):
            valid = parse_ax25_ui(variant.payload) is not None
            attempts.append(
                {
                    "variant_kind": variant.kind,
                    "offset_bytes": variant.offset_bytes,
                    **_payload_record(variant.payload),
                    "container_contract": variant.container_contract,
                    "contract_sources": list(variant.contract_sources),
                    "ax25_ui_structurally_valid": valid,
                }
            )
            if valid:
                valid_payloads.setdefault(variant.payload, []).append(variant)
        # More than one distinct valid extraction would make normalization
        # ambiguous. Reject it rather than choosing whichever happens first.
        selected: ReferencePayloadVariant | None = None
        if len(valid_payloads) == 1:
            selected = next(iter(valid_payloads.values()))[0]
            trusted.add(selected.payload)
            trusted_variant_counts[selected.kind] += 1
        elif len(valid_payloads) > 1:
            ambiguous_valid_variant_count += 1
        records.append(
            {
                "frame_id": reference.frame_id,
                "filename": reference.filename,
                "source_url": reference.source_url,
                **_payload_record(reference.payload),
                "ax25_ui_structurally_valid": selected is not None,
                "trusted_for_benchmark": selected is not None,
                "fcs_bytes_available_in_catalogue_payload": False,
                "selected_variant": (
                    {
                        "variant_kind": selected.kind,
                        "offset_bytes": selected.offset_bytes,
                        **_payload_record(selected.payload),
                    }
                    if selected is not None
                    else None
                ),
                "validation_attempts": attempts,
            }
        )
    raw_unique = {item.payload for item in candidate.references}
    return (
        {
            "raw_reference_record_count": len(candidate.references),
            "raw_unique_payload_count": len(raw_unique),
            "trusted_unique_payload_count": len(trusted),
            "rejected_non_ax25_unique_payload_count": len(raw_unique - trusted),
            "ambiguous_valid_variant_record_count": ambiguous_valid_variant_count,
            "trusted_variant_counts": dict(sorted(trusted_variant_counts.items())),
            "trusted_set_comparable": bool(trusted),
            "comparison_status": (
                "comparable"
                if trusted
                else "non_comparable_no_trusted_baseline"
            ),
            "trusted_payload_sha256": sorted(
                hashlib.sha256(value).hexdigest() for value in trusted
            ),
            "validation": (
                "catalogue source provenance plus independent strict AX.25 UI "
                "structure over raw and source-documented variants only; arbitrary "
                "byte-offset scanning is forbidden; catalogue payload omits FCS bytes"
            ),
            "arbitrary_byte_offset_scan": False,
            "records": records,
        },
        trusted,
    )


def audit_native_phase(
    phase_artifact_path: Path,
) -> tuple[dict[str, object], set[bytes]]:
    strict_audit = audit_phase_first_artifact(phase_artifact_path)
    phase = json.loads(phase_artifact_path.read_text(encoding="utf-8"))
    trusted_digests = {
        str(record["frame_with_fcs_sha256"])
        for record in strict_audit["candidates"]
        if record["trusted_for_explicit_ax25_campaign"] is True
    }
    trusted: set[bytes] = set()
    for raw in phase["unique_crc_valid_frames"]:
        frame = bytes.fromhex(str(raw["frame_with_fcs_hex"]))
        digest = hashlib.sha256(frame).hexdigest()
        if digest in trusted_digests:
            trusted.add(frame[:-2])
    return (
        {
            "source_artifact": str(phase_artifact_path),
            "source_artifact_sha256": strict_audit["source_artifact_sha256"],
            "raw_crc_valid_candidate_count": strict_audit[
                "raw_crc_valid_candidate_count"
            ],
            "trusted_ax25_ui_unique_payload_count": len(trusted),
            "rejected_crc_collision_count": strict_audit[
                "rejected_crc_collision_count"
            ],
            "ax25_with_exact_inner_ccsds_count": strict_audit[
                "ax25_with_exact_inner_ccsds_count"
            ],
            "trusted_payload_sha256": sorted(
                hashlib.sha256(value).hexdigest() for value in trusted
            ),
            "validation": "CRC-16/X-25 plus independent strict AX.25 UI structure",
            "strict_audit": strict_audit,
        },
        trusted,
    )


def build_observation_comparison(
    candidate: PositiveBenchmarkCandidate,
    *,
    iq_path: Path,
    iq_sha256: str,
    cf32_path: Path,
    cf32_sha256: str,
    phase_artifact_path: Path,
) -> dict[str, object]:
    baseline, baseline_payloads = audit_catalogue_references(candidate)
    native, native_payloads = audit_native_phase(phase_artifact_path)
    overlap = baseline_payloads & native_payloads
    union = baseline_payloads | native_payloads
    incremental = native_payloads - baseline_payloads
    baseline_only = baseline_payloads - native_payloads
    comparable = bool(baseline_payloads)
    return {
        "schema_version": COMPARISON_SCHEMA,
        "complete": True,
        "observation_id": candidate.observation_id,
        "satellite_id": candidate.satellite_id,
        "input": {
            "ci16_path": str(iq_path),
            "ci16_sha256": iq_sha256,
            "ci16_size_bytes": iq_path.stat().st_size,
            "cf32_path": str(cf32_path),
            "cf32_sha256": cf32_sha256,
            "phase_artifact_path": str(phase_artifact_path),
            "phase_artifact_sha256": sha256_file(phase_artifact_path),
            "reference_commitment_sha256": candidate.reference_commitment_sha256,
        },
        "acceptance_policy": {
            "mode_exact": EXPLICIT_MODE,
            "raw_crc_is_telemetry": False,
            "baseline": (
                "raw catalogue payload plus independent strict AX.25 UI structure"
            ),
            "native": "valid FCS plus independent strict AX.25 UI structure",
            "normalization": "complete AX.25 payload bytes without FCS",
        },
        "baseline": baseline,
        "native": native,
        "sets": {
            "comparison_status": (
                "comparable"
                if comparable
                else "non_comparable_no_trusted_baseline"
            ),
            "baseline_count": len(baseline_payloads),
            "native_count": len(native_payloads),
            "overlap_count": len(overlap),
            "union_count": len(union),
            "incremental_over_baseline": len(incremental) if comparable else None,
            "native_not_in_trusted_baseline_count": len(incremental),
            "baseline_only_count": len(baseline_only),
            "overlap_payload_sha256": sorted(
                hashlib.sha256(value).hexdigest() for value in overlap
            ),
            "incremental_payload_sha256": sorted(
                hashlib.sha256(value).hexdigest() for value in incremental
            ),
            "baseline_only_payload_sha256": sorted(
                hashlib.sha256(value).hexdigest() for value in baseline_only
            ),
            "union_payload_sha256": sorted(
                hashlib.sha256(value).hexdigest() for value in union
            ),
        },
    }


def _write_json_atomic(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def validate_observation_comparison(
    path: Path,
    *,
    candidate: PositiveBenchmarkCandidate,
    iq_sha256: str,
    cf32_sha256: str,
    phase_artifact_sha256: str,
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        inputs = document["input"]
        sets = document["sets"]
        baseline_count = int(sets["baseline_count"])
        native_count = int(sets["native_count"])
        overlap_count = int(sets["overlap_count"])
        union_count = int(sets["union_count"])
        native_not_in_baseline = int(sets["native_not_in_trusted_baseline_count"])
        comparison_status = sets.get("comparison_status")
        incremental = sets.get("incremental_over_baseline")
        if comparison_status == "comparable":
            if not isinstance(incremental, int) or isinstance(incremental, bool):
                return None
            incremental_consistent = union_count == baseline_count + incremental
        elif comparison_status == "non_comparable_no_trusted_baseline":
            incremental_consistent = baseline_count == 0 and incremental is None
        else:
            return None
        if (
            document.get("schema_version") != COMPARISON_SCHEMA
            or document.get("complete") is not True
            or document.get("observation_id") != candidate.observation_id
            or inputs.get("ci16_sha256") != iq_sha256
            or inputs.get("cf32_sha256") != cf32_sha256
            or inputs.get("phase_artifact_sha256") != phase_artifact_sha256
            or inputs.get("reference_commitment_sha256")
            != candidate.reference_commitment_sha256
            or document["baseline"].get("comparison_status") != comparison_status
            or not incremental_consistent
            or union_count != baseline_count + native_count - overlap_count
            or native_not_in_baseline != native_count - overlap_count
            or union_count < max(baseline_count, native_count)
        ):
            return None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return document


def _result_from_comparison(
    document: Mapping[str, Any], path: Path, *, status: str
) -> dict[str, object]:
    sets = document["sets"]
    native = document["native"]
    baseline = document["baseline"]
    return {
        "observation_id": document["observation_id"],
        "status": status,
        "comparison_path": str(path),
        "comparison_sha256": sha256_file(path),
        "comparison_status": sets["comparison_status"],
        "baseline_count": sets["baseline_count"],
        "native_count": sets["native_count"],
        "union_count": sets["union_count"],
        "incremental_over_baseline": sets["incremental_over_baseline"],
        "native_not_in_trusted_baseline_count": sets[
            "native_not_in_trusted_baseline_count"
        ],
        "baseline_rejected_non_ax25_count": baseline[
            "rejected_non_ax25_unique_payload_count"
        ],
        "native_raw_crc_candidate_count": native[
            "raw_crc_valid_candidate_count"
        ],
        "native_rejected_crc_collision_count": native[
            "rejected_crc_collision_count"
        ],
        "error": None,
    }


def process_positive_candidate(
    candidate: PositiveBenchmarkCandidate,
    positive_root: Path,
    *,
    legacy_root: Path | None,
    decoder_script: Path,
    python_executable: Path,
    workers: int,
    timeout_seconds: float,
    runner: Callable[..., Any],
) -> dict[str, object]:
    input_path = positive_root / f"observation_{candidate.observation_id}.iq"
    if not input_path.is_file():
        raise FileNotFoundError(f"missing positive IQ: {input_path}")
    if input_path.stat().st_size != candidate.expected_ci16_size_bytes:
        raise ValueError("positive IQ size differs from catalogue")
    processing_candidate = ProcessingCandidate(
        candidate.observation_id,
        candidate.expected_ci16_size_bytes,
        EXPLICIT_MODE,
        candidate.satellite_id,
    )
    conversion = prepare_conversion(
        processing_candidate,
        positive_root,
        legacy_root=legacy_root,
    )
    work_dir = positive_root / f"obs-{candidate.observation_id}"
    phase_path = work_dir / "phase-first-positive.json"
    existing_phase = validate_decode_artifact(
        phase_path,
        observation_id=candidate.observation_id,
        input_sha256=conversion.cf32_sha256,
    )
    decoder_reused = existing_phase is not None
    if existing_phase is None:
        run_phase_first_decoder(
            python_executable=python_executable,
            decoder_script=decoder_script,
            input_path=conversion.cf32_path,
            output_path=phase_path,
            observation_id=candidate.observation_id,
            workers=workers,
            timeout_seconds=timeout_seconds,
            runner=runner,
        )
    phase_hash = sha256_file(phase_path)
    comparison_path = work_dir / "matched-iq-comparison.json"
    existing_comparison = validate_observation_comparison(
        comparison_path,
        candidate=candidate,
        iq_sha256=conversion.ci16_sha256,
        cf32_sha256=conversion.cf32_sha256,
        phase_artifact_sha256=phase_hash,
    )
    if existing_comparison is not None:
        return _result_from_comparison(
            existing_comparison, comparison_path, status="skipped_complete"
        )
    comparison = build_observation_comparison(
        candidate,
        iq_path=input_path,
        iq_sha256=conversion.ci16_sha256,
        cf32_path=conversion.cf32_path,
        cf32_sha256=conversion.cf32_sha256,
        phase_artifact_path=phase_path,
    )
    _write_json_atomic(
        work_dir / "strict-ax25-audit.json",
        comparison["native"]["strict_audit"],
    )
    _write_json_atomic(comparison_path, comparison)
    status = "skipped_complete" if conversion.reused and decoder_reused else "processed"
    return _result_from_comparison(comparison, comparison_path, status=status)


def _append_checkpoint(path: Path, result: Mapping[str, object]) -> None:
    event = {
        "schema_version": CHECKPOINT_SCHEMA,
        "recorded_at": datetime.now(UTC).isoformat(),
        **result,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_benchmark_events(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                break
            raise ValueError(f"malformed benchmark checkpoint line {index + 1}")
        if not isinstance(event, dict):
            raise ValueError("benchmark checkpoint event must be an object")
        observation_id = event.get("observation_id")
        status = event.get("status")
        if (
            event.get("schema_version") != CHECKPOINT_SCHEMA
            or not isinstance(observation_id, int)
            or isinstance(observation_id, bool)
            or status not in {"processed", "skipped_complete", "error"}
        ):
            raise ValueError(
                f"invalid benchmark checkpoint event on line {index + 1}"
            )
        output.append(event)
    return tuple(output)


def write_benchmark_manifest(
    path: Path,
    *,
    expected_candidates: Sequence[PositiveBenchmarkCandidate],
    checkpoint_path: Path,
) -> dict[str, object]:
    expected_ids = {candidate.observation_id for candidate in expected_candidates}
    latest: dict[int, dict[str, Any]] = {}
    for event in load_benchmark_events(checkpoint_path):
        observation_id = event.get("observation_id")
        if isinstance(observation_id, int) and observation_id in expected_ids:
            latest[observation_id] = event
    results = [latest[key] for key in sorted(latest)]
    statuses = Counter(str(result["status"]) for result in results)
    successful = {
        int(result["observation_id"])
        for result in results
        if result["status"] in {"processed", "skipped_complete"}
    }
    successful_results = [
        result
        for result in results
        if result["status"] in {"processed", "skipped_complete"}
    ]
    comparison_statuses = Counter(
        str(result["comparison_status"]) for result in successful_results
    )
    comparable_results = [
        result
        for result in successful_results
        if result["comparison_status"] == "comparable"
    ]
    complete = successful == expected_ids and not statuses["error"]
    document: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA,
        "complete": complete,
        "selection_contract": {
            "mode_exact": EXPLICIT_MODE,
            "frames_recovered": True,
            "raw_crc_is_telemetry": False,
            "expected_observation_count": len(expected_ids),
        },
        "candidate_count": len(expected_ids),
        "latest_result_count": len(results),
        "remaining_observation_ids": sorted(expected_ids - successful),
        "status_counts": dict(sorted(statuses.items())),
        "comparison_status_counts": dict(sorted(comparison_statuses.items())),
        "aggregate": {
            key: sum(int(result.get(key, 0) or 0) for result in successful_results)
            for key in (
                "baseline_count",
                "native_count",
                "union_count",
                "native_not_in_trusted_baseline_count",
                "baseline_rejected_non_ax25_count",
                "native_raw_crc_candidate_count",
                "native_rejected_crc_collision_count",
            )
        },
        "results": results,
    }
    aggregate = document["aggregate"]
    if not isinstance(aggregate, dict):
        raise AssertionError("benchmark aggregate must be an object")
    aggregate["incremental_over_baseline"] = (
        sum(int(result["incremental_over_baseline"]) for result in comparable_results)
        if comparable_results
        else None
    )
    _write_json_atomic(path, document)
    return document


def run_positive_benchmark(
    scheduled_candidates: Sequence[PositiveBenchmarkCandidate],
    expected_candidates: Sequence[PositiveBenchmarkCandidate],
    positive_root: Path,
    *,
    legacy_root: Path | None,
    decoder_script: Path,
    checkpoint_path: Path,
    manifest_path: Path,
    python_executable: Path = Path(sys.executable),
    workers: int = 4,
    timeout_seconds: float = 7_200.0,
    runner: Callable[..., Any] = subprocess.run,
) -> tuple[dict[str, object], ...]:
    expected_ids = [item.observation_id for item in expected_candidates]
    scheduled_ids = [item.observation_id for item in scheduled_candidates]
    if len(expected_ids) != len(set(expected_ids)):
        raise ValueError("expected benchmark observation IDs must be unique")
    if len(scheduled_ids) != len(set(scheduled_ids)):
        raise ValueError("scheduled benchmark observation IDs must be unique")
    if not set(scheduled_ids) <= set(expected_ids):
        raise ValueError("scheduled observations must belong to the expected campaign")

    # A zero-length or interrupted shard still has a useful, explicit campaign
    # snapshot.  Subsequent per-observation writes replace it atomically.
    write_benchmark_manifest(
        manifest_path,
        expected_candidates=expected_candidates,
        checkpoint_path=checkpoint_path,
    )
    results: list[dict[str, object]] = []
    for candidate in scheduled_candidates:
        try:
            result = process_positive_candidate(
                candidate,
                positive_root,
                legacy_root=legacy_root,
                decoder_script=decoder_script,
                python_executable=python_executable,
                workers=workers,
                timeout_seconds=timeout_seconds,
                runner=runner,
            )
        except Exception as error:
            result = {
                "observation_id": candidate.observation_id,
                "status": "error",
                "comparison_path": None,
                "comparison_sha256": None,
                "comparison_status": None,
                "baseline_count": None,
                "native_count": None,
                "union_count": None,
                "incremental_over_baseline": None,
                "native_not_in_trusted_baseline_count": None,
                "baseline_rejected_non_ax25_count": None,
                "native_raw_crc_candidate_count": None,
                "native_rejected_crc_collision_count": None,
                "error": f"{type(error).__name__}: {error}",
            }
        _append_checkpoint(checkpoint_path, result)
        write_benchmark_manifest(
            manifest_path,
            expected_candidates=expected_candidates,
            checkpoint_path=checkpoint_path,
        )
        results.append(result)
    return tuple(results)
