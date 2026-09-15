"""Validated milestone report for an in-progress generic receiver campaign."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
from typing import Any


REPORT_SCHEMA = "generic-receiver-milestone-v1"
ARCHIVE_SCHEMA = "polyitan-archive-inventory-v1"
LIVE_BUCKET_SCHEMA = "polyitan-live-bucket-inventory-v1"
DOWNLOAD_SCHEMA = "archive-iq-download-manifest-v1"
PROCESSING_SCHEMA = "archive-g3ruh-processing-manifest-v1"
PROTOCOL_AUDIT_SCHEMA = "archive-g3ruh-protocol-audit-v1"
TRIAGE_SCHEMA = "polyitan-live-delta-signal-triage-v1"
COMPARISON_SCHEMA = "polyitan-iq-comparison-v1"
SATYAML_ROUTING_SCHEMA = 1
CW_PROBE_SCHEMA = "polyitan-live-delta-cw-probe-v1"
RML24_SCHEMA = 1


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        raise ValueError(f"{name} must be an array")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _schema(document: Mapping[str, Any], expected: object, name: str) -> None:
    if document.get("schema_version") != expected:
        raise ValueError(f"{name} schema_version must be {expected}")


def _count_rows(value: object, *, label_key: str, name: str) -> Counter[str]:
    rows = _sequence(value, name)
    output: Counter[str] = Counter()
    for index, raw in enumerate(rows):
        row = _mapping(raw, f"{name}[{index}]")
        label = row.get(label_key)
        if not isinstance(label, str) or not label:
            raise ValueError(f"{name}[{index}].{label_key} must be text")
        if label in output:
            raise ValueError(f"duplicate {label_key} in {name}: {label}")
        output[label] = _integer(row.get("count"), f"{name}[{index}].count")
    return output


def validate_archive_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    _schema(document, ARCHIVE_SCHEMA, "archive inventory")
    summary = _mapping(document.get("summary"), "archive summary")
    observations = _sequence(document.get("observations"), "archive observations")
    total = _integer(summary.get("observation_count"), "archive observation_count")
    if len(observations) != total:
        raise ValueError("archive observation_count differs from observations length")
    iq_available = _integer(summary.get("iq_available_count"), "iq_available_count")
    iq_missing = _integer(summary.get("iq_missing_count"), "iq_missing_count")
    if iq_available + iq_missing != total:
        raise ValueError("archive IQ counters do not sum to observation_count")
    statuses = _count_rows(
        summary.get("status_counts"), label_key="status", name="archive status_counts"
    )
    if sum(statuses.values()) != total:
        raise ValueError("archive status_counts do not sum to observation_count")

    observed_iq = 0
    observed_statuses: Counter[str] = Counter()
    required_plugins: Counter[str] = Counter()
    eligible_g3ruh = 0
    for index, raw in enumerate(observations):
        observation = _mapping(raw, f"archive observations[{index}]")
        iq = _mapping(observation.get("iq"), f"archive observations[{index}].iq")
        reference = _mapping(
            observation.get("reference_frames"),
            f"archive observations[{index}].reference_frames",
        )
        route = _mapping(
            observation.get("route"), f"archive observations[{index}].route"
        )
        available = _boolean(iq.get("available"), "archive iq.available")
        observed_iq += int(available)
        status = observation.get("status")
        if not isinstance(status, str):
            raise ValueError("archive observation status must be text")
        observed_statuses[status] += 1
        if status == "needs_plugin" and available:
            for plugin in _sequence(route.get("required_plugins"), "required_plugins"):
                if not isinstance(plugin, str) or not plugin:
                    raise ValueError("required plugin must be text")
                required_plugins[plugin] += 1
        if (
            observation.get("source_mode") == "FSK AX.25 G3RUH"
            and available
            and reference.get("status") == "none"
        ):
            eligible_g3ruh += 1
    if observed_iq != iq_available or observed_statuses != statuses:
        raise ValueError("archive per-observation counters disagree with summary")

    capabilities = _mapping(
        document.get("capability_contract"), "archive capability_contract"
    )
    if capabilities.get("fsk_implies_ax25") is not False:
        raise ValueError("archive capability contract must not infer AX.25 from FSK")
    return {
        "complete": True,
        "observation_count": total,
        "iq_available_count": iq_available,
        "iq_missing_count": iq_missing,
        "status_counts": dict(sorted(statuses.items())),
        "eligible_explicit_g3ruh_zero_reference_count": eligible_g3ruh,
        "required_plugins": [
            {"plugin": plugin, "observation_count": count}
            for plugin, count in sorted(
                required_plugins.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "current_demodulators": list(capabilities.get("current_demodulators", [])),
        "current_waveform_families": list(
            capabilities.get("current_waveform_families", [])
        ),
    }


def validate_live_bucket_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    _schema(document, LIVE_BUCKET_SCHEMA, "live bucket inventory")
    summary = _mapping(document.get("summary"), "live bucket summary")
    objects = _sequence(document.get("objects"), "live bucket objects")
    comparison = _mapping(document.get("comparison"), "live bucket comparison")
    anomalies = _sequence(document.get("anomalies"), "live bucket anomalies")
    exact_count = _integer(summary.get("exact_iq_key_count"), "exact_iq_key_count")
    unique_count = _integer(
        summary.get("listed_unique_key_count"), "listed_unique_key_count"
    )
    new_ids = _sequence(
        comparison.get("new_in_bucket_observation_ids"), "new bucket IDs"
    )
    missing_ids = _sequence(
        comparison.get("missing_from_bucket_observation_ids"), "missing bucket IDs"
    )
    if len(objects) != exact_count or unique_count != exact_count:
        raise ValueError("live bucket object counters disagree")
    if _integer(summary.get("new_in_bucket_count"), "new_in_bucket_count") != len(
        new_ids
    ):
        raise ValueError("live bucket new-object counter disagrees")
    if _integer(
        summary.get("missing_from_bucket_count"), "missing_from_bucket_count"
    ) != len(missing_ids):
        raise ValueError("live bucket missing-object counter disagrees")
    if _integer(summary.get("anomaly_count"), "anomaly_count") != len(anomalies):
        raise ValueError("live bucket anomaly counter disagrees")
    return {
        "complete": True,
        "iq_object_count": exact_count,
        "total_iq_size_bytes": _integer(
            summary.get("total_iq_size_bytes"), "total_iq_size_bytes"
        ),
        "new_in_bucket_count": len(new_ids),
        "missing_from_bucket_count": len(missing_ids),
        "anomaly_count": len(anomalies),
        "zero_size_count": _integer(summary.get("zero_size_count"), "zero_size_count"),
    }


def validate_download_manifest(
    document: Mapping[str, Any], *, name: str
) -> dict[str, Any]:
    _schema(document, DOWNLOAD_SCHEMA, name)
    candidates = _integer(document.get("candidate_count"), f"{name} candidate_count")
    known_size = _integer(
        document.get("known_size_candidate_count"),
        f"{name} known_size_candidate_count",
    )
    latest_count = _integer(
        document.get("latest_result_count"), f"{name} latest_result_count"
    )
    if known_size > candidates or latest_count > candidates:
        raise ValueError(f"{name} counters exceed candidate_count")
    results = _sequence(document.get("results"), f"{name} results")
    if len(results) != latest_count:
        raise ValueError(f"{name} results length differs from latest_result_count")
    declared_statuses = _mapping(document.get("status_counts"), f"{name} status_counts")
    statuses: Counter[str] = Counter()
    seen: set[int] = set()
    downloaded_bytes = 0
    for index, raw in enumerate(results):
        result = _mapping(raw, f"{name} results[{index}]")
        observation_id = _integer(
            result.get("observation_id"), f"{name} observation_id", minimum=1
        )
        if observation_id in seen:
            raise ValueError(f"{name} contains duplicate observation results")
        seen.add(observation_id)
        status = result.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError(f"{name} result status must be text")
        statuses[status] += 1
        if status in {"downloaded", "skipped_valid"}:
            size = _integer(result.get("size_bytes"), f"{name} result size_bytes")
            expected = _integer(
                result.get("expected_size_bytes"), f"{name} expected_size_bytes"
            )
            digest = result.get("sha256")
            if size != expected:
                raise ValueError(f"{name} successful result size mismatch")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(f"{name} successful result lacks SHA-256")
            downloaded_bytes += size
    normalized_declared = {
        str(key): _integer(value, f"{name} status_counts.{key}")
        for key, value in declared_statuses.items()
    }
    if dict(statuses) != normalized_declared:
        raise ValueError(f"{name} status_counts disagree with results")
    error_count = statuses["error"]
    attempt_complete = latest_count == candidates
    complete = attempt_complete and error_count == 0
    return {
        "complete": complete,
        "attempt_complete": attempt_complete,
        "candidate_count": candidates,
        "known_size_candidate_count": known_size,
        "expected_size_bytes": _integer(
            document.get("expected_size_bytes"), f"{name} expected_size_bytes"
        ),
        "latest_result_count": latest_count,
        "remaining_count": candidates - latest_count,
        "status_counts": dict(sorted(statuses.items())),
        "downloaded_bytes": downloaded_bytes,
        "error_count": error_count,
    }


def validate_processing_manifest(document: Mapping[str, Any]) -> dict[str, Any]:
    _schema(document, PROCESSING_SCHEMA, "G3RUH processing manifest")
    selection = _mapping(document.get("selection_contract"), "processing selection")
    if (
        selection.get("frames_recovered") is not False
        or selection.get("mode_exact") != "FSK AX.25 G3RUH"
        or selection.get("generic_fsk_implies_ax25") is not False
    ):
        raise ValueError("processing selection contract is broader than explicit G3RUH")
    candidates = _integer(document.get("candidate_count"), "processing candidate_count")
    latest_count = _integer(
        document.get("latest_result_count"), "processing latest_result_count"
    )
    if latest_count > candidates:
        raise ValueError("processing latest_result_count exceeds candidate_count")
    results = _sequence(document.get("results"), "processing results")
    if len(results) != latest_count:
        raise ValueError("processing results length differs from latest_result_count")
    declared = _mapping(document.get("status_counts"), "processing status_counts")
    statuses: Counter[str] = Counter()
    seen: set[int] = set()
    raw_crc_candidates = 0
    for index, raw in enumerate(results):
        result = _mapping(raw, f"processing results[{index}]")
        observation_id = _integer(
            result.get("observation_id"), "processing observation_id", minimum=1
        )
        if observation_id in seen:
            raise ValueError("processing results contain duplicate observation IDs")
        seen.add(observation_id)
        status = result.get("status")
        if not isinstance(status, str):
            raise ValueError("processing status must be text")
        statuses[status] += 1
        if status in {"processed", "skipped_complete"}:
            raw_crc_candidates += _integer(
                result.get("crc_valid_frame_count"), "crc_valid_frame_count"
            )
    normalized_declared = {
        str(key): _integer(value, f"processing status_counts.{key}")
        for key, value in declared.items()
    }
    if dict(statuses) != normalized_declared:
        raise ValueError("processing status_counts disagree with results")
    error_count = statuses["error"]
    attempt_complete = latest_count == candidates
    complete = attempt_complete and error_count == 0
    return {
        "started": latest_count > 0,
        "complete": complete,
        "attempt_complete": attempt_complete,
        "candidate_count": candidates,
        "latest_result_count": latest_count,
        "remaining_count": candidates - latest_count,
        "status_counts": dict(sorted(statuses.items())),
        "error_count": error_count,
        "raw_crc_valid_candidate_count": raw_crc_candidates,
        "trusted_protocol_valid_frame_count": None,
        "evidence_classification": "CRC-valid candidates only; no protocol audit in this manifest",
    }


def _sha256_text(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def validate_protocol_audit(
    document: Mapping[str, Any],
    *,
    processing_manifest: Mapping[str, Any],
    processing_complete: bool,
    eligible_observation_count: int,
) -> dict[str, Any]:
    """Validate protocol evidence against the exact completed decode artifacts."""

    _schema(document, PROTOCOL_AUDIT_SCHEMA, "G3RUH protocol audit")
    processing_results = _sequence(
        processing_manifest.get("results"), "processing results for protocol audit"
    )
    completed: dict[int, Mapping[str, Any]] = {}
    for index, raw in enumerate(processing_results):
        result = _mapping(raw, f"processing results for audit[{index}]")
        if result.get("status") not in {"processed", "skipped_complete"}:
            continue
        observation_id = _integer(
            result.get("observation_id"), "processing audit observation_id", minimum=1
        )
        artifact_path = result.get("decode_output_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            raise ValueError("completed processing result lacks decode_output_path")
        _sha256_text(
            result.get("decode_output_sha256"), "processing decode_output_sha256"
        )
        _integer(result.get("crc_valid_frame_count"), "processing CRC candidate count")
        completed[observation_id] = result

    source_manifests = _sequence(
        document.get("source_manifests"), "protocol audit source_manifests"
    )
    for index, raw in enumerate(source_manifests):
        source = _mapping(raw, f"protocol audit source_manifests[{index}]")
        if not isinstance(source.get("path"), str) or not source.get("path"):
            raise ValueError("protocol audit source manifest path must be text")
        _sha256_text(source.get("sha256"), "protocol audit source manifest sha256")

    observations = _sequence(
        document.get("observations"), "protocol audit observations"
    )
    audited_count = _integer(
        document.get("audited_observation_count"), "audited_observation_count"
    )
    if audited_count != len(observations):
        raise ValueError("protocol audit observation counter disagrees")
    seen_ids: set[int] = set()
    raw_total = 0
    trusted_total = 0
    rejected_total = 0
    ccsds_total = 0
    for index, raw in enumerate(observations):
        observation = _mapping(raw, f"protocol audit observations[{index}]")
        observation_id = _integer(
            observation.get("observation_id"),
            "protocol audit observation_id",
            minimum=1,
        )
        if observation_id in seen_ids:
            raise ValueError("protocol audit contains duplicate observation IDs")
        seen_ids.add(observation_id)
        processing_result = completed.get(observation_id)
        if processing_result is None:
            raise ValueError(
                "protocol-audited observation is not a completed processing result"
            )
        source_artifact = observation.get("source_artifact")
        if not isinstance(source_artifact, str) or not source_artifact:
            raise ValueError("protocol audit source_artifact must be text")
        if Path(source_artifact).resolve() != Path(
            str(processing_result["decode_output_path"])
        ).resolve():
            raise ValueError("protocol audit source artifact path mismatch")
        source_hash = _sha256_text(
            observation.get("source_artifact_sha256"),
            "protocol audit source_artifact_sha256",
        )
        if source_hash != processing_result["decode_output_sha256"]:
            raise ValueError("protocol audit source artifact hash mismatch")

        candidates = _sequence(
            observation.get("candidates"), "protocol audit frame candidates"
        )
        raw_count = _integer(
            observation.get("raw_crc_valid_candidate_count"),
            "protocol audit raw CRC count",
        )
        trusted_count = _integer(
            observation.get("trusted_ax25_ui_frame_count"),
            "protocol audit trusted AX.25 count",
        )
        rejected_count = _integer(
            observation.get("rejected_crc_collision_count"),
            "protocol audit rejected CRC collision count",
        )
        ccsds_count = _integer(
            observation.get("ax25_with_exact_inner_ccsds_count"),
            "protocol audit inner CCSDS count",
        )
        if raw_count != len(candidates) or raw_count != processing_result[
            "crc_valid_frame_count"
        ]:
            raise ValueError("protocol audit raw count differs from source artifact")
        if trusted_count + rejected_count != raw_count or ccsds_count > trusted_count:
            raise ValueError("protocol audit classification counters disagree")

        candidate_trusted = 0
        candidate_ccsds = 0
        digests: set[str] = set()
        for candidate_index, candidate_raw in enumerate(candidates):
            candidate = _mapping(
                candidate_raw,
                f"protocol audit observations[{index}].candidates[{candidate_index}]",
            )
            digest = _sha256_text(
                candidate.get("frame_with_fcs_sha256"),
                "protocol audit frame_with_fcs_sha256",
            )
            if digest in digests:
                raise ValueError("protocol audit contains duplicate frame candidates")
            digests.add(digest)
            if candidate.get("crc16_x25_valid") is not True:
                raise ValueError("protocol audit raw candidate is not CRC-valid")
            structural = candidate.get("ax25_ui_structurally_valid")
            trusted = candidate.get("trusted_for_explicit_ax25_campaign")
            if not isinstance(structural, bool) or not isinstance(trusted, bool):
                raise ValueError("protocol audit candidate flags must be boolean")
            if trusted != structural:
                raise ValueError("protocol audit trusted/AX.25 flags disagree")
            candidate_trusted += int(trusted)
            has_ccsds = candidate.get("inner_ccsds_space_packet") is not None
            if has_ccsds and not trusted:
                raise ValueError("inner CCSDS cannot exist outside trusted AX.25")
            candidate_ccsds += int(has_ccsds)
        if candidate_trusted != trusted_count or candidate_ccsds != ccsds_count:
            raise ValueError("protocol audit per-candidate counters disagree")
        raw_total += raw_count
        trusted_total += trusted_count
        rejected_total += rejected_count
        ccsds_total += ccsds_count

    declared = {
        "raw_crc_valid_candidate_count": raw_total,
        "trusted_ax25_ui_frame_count": trusted_total,
        "rejected_crc_collision_count": rejected_total,
        "ax25_with_exact_inner_ccsds_count": ccsds_total,
    }
    for key, calculated in declared.items():
        if _integer(document.get(key), f"protocol audit {key}") != calculated:
            raise ValueError(f"protocol audit aggregate {key} disagrees")

    declared_complete = _boolean(
        document.get("campaign_complete"), "protocol audit campaign_complete"
    )
    full_coverage = (
        processing_complete
        and eligible_observation_count == 28
        and audited_count == eligible_observation_count
        and audited_count == len(completed)
    )
    if declared_complete and not full_coverage:
        raise ValueError("protocol audit cannot be complete before 28/28 coverage")
    return {
        "available": True,
        "complete": bool(declared_complete and full_coverage),
        "declared_campaign_complete": declared_complete,
        "audited_observation_count": audited_count,
        "completed_processing_observation_count": len(completed),
        "eligible_observation_count": eligible_observation_count,
        "remaining_completed_observations_to_audit": len(completed) - audited_count,
        **declared,
        "evidence_classification": (
            "AX.25 UI structure validated; inner CCSDS counted only on exact packet validation"
        ),
    }
def validate_live_delta_triage(document: Mapping[str, Any]) -> dict[str, Any]:
    _schema(document, TRIAGE_SCHEMA, "live-delta triage")
    summary = _mapping(document.get("summary"), "live-delta triage summary")
    ranking = _sequence(document.get("ranking"), "live-delta triage ranking")
    recommended_ids = _sequence(
        document.get("recommended_for_expensive_bank_observation_ids"),
        "live-delta triage recommended IDs",
    )
    file_count = _integer(summary.get("file_count"), "triage file_count")
    recommended_count = _integer(
        summary.get("recommended_count"), "triage recommended_count"
    )
    if len(ranking) != file_count or len(recommended_ids) != recommended_count:
        raise ValueError("triage ranking counters disagree")
    seen_ids: set[int] = set()
    ranks: list[int] = []
    total_size = 0
    total_windows = 0
    total_active = 0
    total_duration = 0.0
    for index, raw in enumerate(ranking):
        result = _mapping(raw, f"triage ranking[{index}]")
        observation_id = _integer(
            result.get("observation_id"), "triage observation_id", minimum=1
        )
        if observation_id in seen_ids:
            raise ValueError("triage ranking contains duplicate observation IDs")
        seen_ids.add(observation_id)
        ranks.append(_integer(result.get("rank"), "triage rank", minimum=1))
        total_size += _integer(result.get("file_size_bytes"), "triage file_size_bytes")
        total_windows += _integer(
            result.get("one_second_window_count"), "triage one_second_window_count"
        )
        total_active += _integer(
            result.get("active_window_count"), "triage active_window_count"
        )
        duration = result.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            raise ValueError("triage duration_seconds must be numeric")
        total_duration += float(duration)
    if sorted(ranks) != list(range(1, file_count + 1)):
        raise ValueError("triage ranks must be exactly 1..file_count")
    recommended = [
        _integer(value, "triage recommended observation_id", minimum=1)
        for value in recommended_ids
    ]
    if len(set(recommended)) != len(recommended) or not set(recommended) <= seen_ids:
        raise ValueError("triage recommended IDs are duplicate or absent from ranking")
    if total_size != _integer(summary.get("total_size_bytes"), "triage total_size_bytes"):
        raise ValueError("triage total_size_bytes disagrees with ranking")
    if total_windows != _integer(
        summary.get("total_one_second_windows"), "triage total_one_second_windows"
    ):
        raise ValueError("triage total window counter disagrees with ranking")
    if total_active != _integer(
        summary.get("total_active_windows"), "triage total_active_windows"
    ):
        raise ValueError("triage active window counter disagrees with ranking")
    declared_duration = summary.get("total_duration_seconds")
    if (
        not isinstance(declared_duration, (int, float))
        or isinstance(declared_duration, bool)
        or not math.isclose(
            total_duration,
            float(declared_duration),
            rel_tol=1e-12,
            abs_tol=1e-9,
        )
    ):
        raise ValueError("triage total duration disagrees with ranking")
    return {
        "available": True,
        "complete": True,
        "file_count": file_count,
        "recommended_for_expensive_bank_count": recommended_count,
        "recommended_observation_ids": recommended,
        "raw_crc_valid_candidate_count": None,
        "trusted_protocol_valid_frame_count": None,
        "evidence_classification": (
            "signal-presence scheduling triage only; no demodulation or protocol audit"
        ),
    }


def validate_satyaml_routing(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate metadata routing without treating compatibility as a decode."""

    _schema(document, SATYAML_ROUTING_SCHEMA, "SatYAML routing")
    summary = _mapping(document.get("summary"), "SatYAML routing summary")
    routes = _sequence(document.get("routes"), "SatYAML routes")
    observation_count = _integer(
        summary.get("observation_count"), "SatYAML observation_count"
    )
    if len(routes) != observation_count:
        raise ValueError("SatYAML observation_count differs from routes length")
    declared_statuses = _mapping(
        summary.get("status_counts"), "SatYAML status_counts"
    )
    statuses: Counter[str] = Counter()
    seen: set[int] = set()
    routable = 0
    profiles: set[str] = set()
    for index, raw in enumerate(routes):
        route = _mapping(raw, f"SatYAML routes[{index}]")
        observation_id = _integer(
            route.get("observation_id"), "SatYAML observation_id", minimum=1
        )
        if observation_id in seen:
            raise ValueError("SatYAML routes contain duplicate observation IDs")
        seen.add(observation_id)
        status = route.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("SatYAML route status must be text")
        statuses[status] += 1
        is_routable = _boolean(route.get("routable"), "SatYAML route routable")
        routable += int(is_routable)
        profile = route.get("capability_profile")
        if is_routable:
            profile_map = _mapping(profile, "routable SatYAML capability_profile")
            name = profile_map.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("routable SatYAML profile needs a name")
            profiles.add(name)
            if status != "routed":
                raise ValueError("routable SatYAML route must have routed status")
        elif profile is not None:
            raise ValueError("non-routable SatYAML route must not expose a profile")
    normalized_declared = {
        str(key): _integer(value, f"SatYAML status_counts.{key}")
        for key, value in declared_statuses.items()
    }
    if dict(statuses) != normalized_declared or sum(statuses.values()) != observation_count:
        raise ValueError("SatYAML status_counts disagree with routes")
    declared_routable = _integer(
        summary.get("existing_profile_routable_count"),
        "SatYAML existing_profile_routable_count",
    )
    declared_not_routable = _integer(
        summary.get("not_routable_to_existing_profile_count"),
        "SatYAML not_routable_to_existing_profile_count",
    )
    if declared_routable != routable or declared_not_routable != observation_count - routable:
        raise ValueError("SatYAML routable counters disagree with routes")
    if _integer(
        summary.get("routable_unique_profile_count"),
        "SatYAML routable_unique_profile_count",
    ) != len(profiles):
        raise ValueError("SatYAML unique profile counter disagrees")
    caveat = summary.get("coverage_caveat")
    if not isinstance(caveat, str) or "does not prove" not in caveat:
        raise ValueError("SatYAML routing must preserve its non-decode caveat")
    return {
        "available": True,
        "complete": True,
        "observation_count": observation_count,
        "routable_to_existing_profile_count": routable,
        "not_routable_to_existing_profile_count": observation_count - routable,
        "unique_profile_count": len(profiles),
        "definite_new_or_updated_profile_count": _integer(
            summary.get("definite_new_or_updated_profile_count"),
            "SatYAML definite_new_or_updated_profile_count",
        ),
        "status_counts": dict(sorted(statuses.items())),
        "raw_crc_valid_candidate_count": None,
        "trusted_protocol_valid_frame_count": None,
        "evidence_classification": (
            "metadata compatibility routing only; profile match is not a decode or telemetry"
        ),
    }


def validate_cw_probe(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate pending CW candidates without promoting text to telemetry."""

    _schema(document, CW_PROBE_SCHEMA, "CW probe")
    summary = _mapping(document.get("summary"), "CW probe summary")
    plugin = _mapping(document.get("plugin"), "CW probe plugin")
    contract = _mapping(
        document.get("interpretation_contract"), "CW interpretation_contract"
    )
    if (
        plugin.get("output_kind") != "untrusted_text_candidates"
        or plugin.get("validation_level") != "pending"
        or contract.get("candidate_validation") != "pending"
        or contract.get("decoded_text_is_trusted_telemetry") is not False
        or contract.get("mission_or_protocol_validator_applied") is not False
    ):
        raise ValueError("CW probe trust contract is unsafe")
    results = _sequence(document.get("results"), "CW probe results")
    analyzed = _integer(summary.get("analyzed_count"), "CW analyzed_count")
    if len(results) != analyzed:
        raise ValueError("CW analyzed_count differs from results length")
    declared_classes = _mapping(
        summary.get("classification_counts"), "CW classification_counts"
    )
    classes: Counter[str] = Counter()
    candidate_count = 0
    texts: dict[str, set[int]] = {}
    seen: set[int] = set()
    for index, raw in enumerate(results):
        result = _mapping(raw, f"CW results[{index}]")
        observation_id = _integer(
            result.get("observation_id"), "CW observation_id", minimum=1
        )
        if observation_id in seen:
            raise ValueError("CW results contain duplicate observation IDs")
        seen.add(observation_id)
        classification = result.get("classification")
        if classification not in {"keyed", "non_keyed", "unknown"}:
            raise ValueError("CW classification is not keyed/non_keyed/unknown")
        classes[str(classification)] += 1
        candidates = _sequence(result.get("candidates"), "CW candidates")
        candidate_count += len(candidates)
        for candidate_index, raw_candidate in enumerate(candidates):
            candidate = _mapping(
                raw_candidate, f"CW candidates[{index}][{candidate_index}]"
            )
            text = candidate.get("text")
            if not isinstance(text, str) or not text:
                raise ValueError("CW candidate text must be non-empty")
            if candidate.get("candidate_validation") != "pending":
                raise ValueError("CW candidate validation must remain pending")
            texts.setdefault(text, set()).add(observation_id)
    normalized_classes = {
        str(key): _integer(value, f"CW classification_counts.{key}")
        for key, value in declared_classes.items()
    }
    if dict(classes) != normalized_classes or sum(classes.values()) != analyzed:
        raise ValueError("CW classification_counts disagree with results")
    if _integer(summary.get("candidate_count"), "CW candidate_count") != candidate_count:
        raise ValueError("CW candidate_count disagrees with results")
    repeated = _sequence(
        document.get("cross_observation_repeated_texts"), "CW repeated texts"
    )
    expected_repeated = {text: ids for text, ids in texts.items() if len(ids) > 1}
    if _integer(
        summary.get("cross_observation_repeated_text_count"),
        "CW repeated text count",
    ) != len(repeated) or len(repeated) != len(expected_repeated):
        raise ValueError("CW repeated text counter disagrees")
    repeated_seen: set[str] = set()
    for raw in repeated:
        row = _mapping(raw, "CW repeated text")
        text = row.get("text")
        ids = {
            _integer(item, "CW repeated observation_id", minimum=1)
            for item in _sequence(row.get("observation_ids"), "CW repeated IDs")
        }
        if not isinstance(text, str) or text in repeated_seen or expected_repeated.get(text) != ids:
            raise ValueError("CW repeated text evidence disagrees with candidates")
        repeated_seen.add(text)
        if _integer(row.get("observation_count"), "CW repeated observation_count") != len(ids):
            raise ValueError("CW repeated observation_count disagrees")
    return {
        "available": True,
        "complete": True,
        "analyzed_count": analyzed,
        "classification_counts": dict(sorted(classes.items())),
        "pending_candidate_count": candidate_count,
        "cross_observation_repeated_text_count": len(repeated),
        "trusted_protocol_valid_frame_count": None,
        "candidate_validation": "pending",
        "evidence_classification": (
            "untrusted narrowband/CW text candidates only; no mission or protocol validator"
        ),
    }


def validate_rml24_dataset(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate RML24 metadata as a BER/modulation benchmark, never frame yield."""

    _schema(document, RML24_SCHEMA, "RML24 dataset")
    identity = _mapping(document.get("identity"), "RML24 identity")
    access = _mapping(document.get("access"), "RML24 access")
    dataset = _mapping(document.get("dataset"), "RML24 dataset facts")
    fit = _mapping(
        document.get("fit_for_telemetry_yield_project"), "RML24 fitness contract"
    )
    if identity.get("short_name") != "RML24":
        raise ValueError("RML24 identity short_name disagrees")
    files = _sequence(document.get("files"), "RML24 files")
    names: set[str] = set()
    total_bytes = 0
    ground_truth_files = 0
    for index, raw in enumerate(files):
        item = _mapping(raw, f"RML24 files[{index}]")
        name = item.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("RML24 file names must be unique non-empty text")
        names.add(name)
        total_bytes += _integer(item.get("bytes"), "RML24 file bytes", minimum=1)
        checksum = item.get("zenodo_checksum")
        if not isinstance(checksum, str) or not checksum.startswith("md5:"):
            raise ValueError("RML24 file lacks a Zenodo MD5")
        ground_truth = _boolean(
            item.get("ground_truth_bits"), "RML24 ground_truth_bits"
        )
        ground_truth_files += int(ground_truth)
        members = _sequence(item.get("zip_members"), "RML24 zip_members")
        if not members:
            raise ValueError("RML24 archive must declare at least one member")
        for member in members:
            member_map = _mapping(member, "RML24 zip member")
            _integer(member_map.get("compressed_bytes"), "RML24 member compressed bytes", minimum=1)
            _integer(member_map.get("uncompressed_bytes"), "RML24 member uncompressed bytes", minimum=1)
    paper_records = _integer(
        dataset.get("paper_record_count"), "RML24 paper_record_count", minimum=1
    )
    uploaded_records = _integer(
        dataset.get("currently_uploaded_record_count_inferred"),
        "RML24 uploaded record count",
        minimum=1,
    )
    samples = _integer(
        dataset.get("samples_per_record"), "RML24 samples_per_record", minimum=1
    )
    sample_rate = _integer(
        dataset.get("sample_rate_hz"), "RML24 sample_rate_hz", minimum=1
    )
    duration = dataset.get("duration_per_record_seconds")
    if (
        not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or not math.isclose(float(duration), samples / sample_rate, rel_tol=1e-12)
    ):
        raise ValueError("RML24 record duration disagrees with samples/rate")
    modulations = _mapping(
        document.get("modulations_as_published"), "RML24 modulations"
    )
    single = _sequence(modulations.get("single"), "RML24 single modulations")
    composite = _sequence(modulations.get("composite"), "RML24 composite modulations")
    modulation_names = list(single) + list(composite)
    if any(not isinstance(name, str) or not name for name in modulation_names):
        raise ValueError("RML24 modulation names must be text")
    if len(set(modulation_names)) != len(modulation_names):
        raise ValueError("RML24 modulation names must be unique")
    invalid_uses = _sequence(
        fit.get("not_a_valid_direct_test_of"), "RML24 invalid direct uses"
    )
    invalid_text = " ".join(str(item) for item in invalid_uses).lower()
    evaluation = fit.get("recommended_evaluation_unit")
    if (
        "end-to-end" not in invalid_text
        or "packet" not in invalid_text
        or not isinstance(evaluation, str)
        or "ber" not in evaluation.lower()
    ):
        raise ValueError("RML24 must remain a BER/modulation, not frame, benchmark")
    if access.get("repository") != "Zenodo" or not isinstance(
        access.get("full_download_status"), str
    ):
        raise ValueError("RML24 access metadata is incomplete")
    return {
        "available": True,
        "metadata_complete": True,
        "file_count": len(files),
        "total_archive_bytes": total_bytes,
        "ground_truth_bit_file_count": ground_truth_files,
        "paper_record_count": paper_records,
        "currently_uploaded_record_count_inferred": uploaded_records,
        "samples_per_record": samples,
        "duration_per_record_seconds": float(duration),
        "modulation_class_count_current_upload": len(modulation_names),
        "full_download_status": access["full_download_status"],
        "benchmark_unit": "modulation classification and BER",
        "frame_yield_benchmark": False,
        "trusted_protocol_valid_frame_count": None,
        "evidence_classification": (
            "RML24 provides short waveform/bit examples; it is not an end-to-end frame benchmark"
        ),
    }


def validate_comparison(document: Mapping[str, Any]) -> dict[str, Any]:
    _schema(document, COMPARISON_SCHEMA, "same-IQ comparison")
    raw_observations = _sequence(
        document.get("same_iq_positive_observations"), "same-IQ observations"
    )
    observations = [
        _mapping(item, f"same-IQ observations[{index}]")
        for index, item in enumerate(raw_observations)
    ]
    aggregate = _mapping(
        document.get("positive_observation_aggregate"), "comparison aggregate"
    )
    observation_count = _integer(
        aggregate.get("observation_count"), "comparison observation_count"
    )
    included: list[Mapping[str, Any]] = []
    excluded: list[Mapping[str, Any]] = []
    trusted_keys = (
        "satnogs_trusted_payloads",
        "phase_first_trusted_payloads",
        "hybrid_union_trusted_payloads",
        "phase_first_only_payloads",
    )
    for index, observation in enumerate(observations):
        included_flag = observation.get("trusted_aggregate_included", True)
        if not isinstance(included_flag, bool):
            raise ValueError(
                f"same-IQ observations[{index}].trusted_aggregate_included "
                "must be boolean"
            )
        if included_flag:
            included.append(observation)
            continue
        excluded.append(observation)
        reason = observation.get("trusted_exclusion_reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("excluded same-IQ observation needs an exclusion reason")
        for key in trusted_keys:
            value = observation.get(key)
            if value is not None and _integer(value, f"excluded {key}") != 0:
                raise ValueError("excluded same-IQ observation has trusted payloads")
        raw_agreement = _mapping(
            observation.get("raw_pdu_agreement"), "excluded raw PDU agreement"
        )
        satnogs_raw = _integer(
            raw_agreement.get("satnogs_crc_valid_pdu_count"),
            "excluded SatNOGS raw PDU count",
        )
        phase_raw = _integer(
            raw_agreement.get("phase_first_crc_valid_pdu_count"),
            "excluded phase-first raw PDU count",
        )
        union_raw = _integer(
            raw_agreement.get("raw_union_pdu_count"),
            "excluded raw PDU union count",
        )
        if union_raw < max(satnogs_raw, phase_raw):
            raise ValueError("excluded raw PDU union cannot be smaller than an arm")
        byte_identical = _boolean(
            raw_agreement.get("byte_identical"), "excluded raw PDU byte_identical"
        )
        if byte_identical and not satnogs_raw == phase_raw == union_raw:
            raise ValueError("byte-identical raw PDU agreement counters disagree")
        digest = raw_agreement.get("exact_shared_payload_sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("excluded raw PDU agreement needs a SHA-256")

    if observation_count != len(included):
        raise ValueError(
            "comparison observation_count differs from trusted-included observations"
        )
    listed_count = aggregate.get("listed_observation_count", len(observations))
    if _integer(listed_count, "comparison listed_observation_count") != len(
        observations
    ):
        raise ValueError("comparison listed_observation_count differs from observations")
    if excluded:
        excluded_count = _integer(
            aggregate.get("excluded_from_trusted_aggregate_count"),
            "comparison excluded observation count",
        )
        if excluded_count != len(excluded):
            raise ValueError("comparison excluded observation count disagrees")
        ids: list[int] = []
        for observation in observations:
            ids.append(_integer(observation.get("observation_id"), "observation ID"))
        if len(ids) != len(set(ids)):
            raise ValueError("comparison observation IDs must be unique")
        included_ids = [
            _integer(item, "included observation ID")
            for item in _sequence(
                aggregate.get("included_observation_ids"),
                "included observation IDs",
            )
        ]
        excluded_ids = [
            _integer(item, "excluded observation ID")
            for item in _sequence(
                aggregate.get("excluded_observation_ids"),
                "excluded observation IDs",
            )
        ]
        listed_ids = [
            _integer(item, "listed observation ID")
            for item in _sequence(
                aggregate.get("listed_observation_ids"), "listed observation IDs"
            )
        ]
        expected_included = sorted(
            int(item["observation_id"]) for item in included
        )
        expected_excluded = sorted(
            int(item["observation_id"]) for item in excluded
        )
        if (
            sorted(listed_ids) != sorted(ids)
            or sorted(included_ids) != expected_included
            or sorted(excluded_ids) != expected_excluded
        ):
            raise ValueError("comparison listed/included/excluded IDs disagree")
    satnogs = sum(
        _integer(item.get("satnogs_trusted_payloads"), "satnogs payloads")
        for item in included
    )
    phase = sum(
        _integer(item.get("phase_first_trusted_payloads"), "phase payloads")
        for item in included
    )
    union = sum(
        _integer(item.get("hybrid_union_trusted_payloads"), "union payloads")
        for item in included
    )
    declared = (
        _integer(aggregate.get("satnogs_trusted_payloads"), "aggregate satnogs"),
        _integer(
            aggregate.get("standalone_phase_first_trusted_payloads"),
            "aggregate phase-first",
        ),
        _integer(
            aggregate.get("hybrid_union_trusted_payloads"), "aggregate union"
        ),
    )
    if declared != (satnogs, phase, union):
        raise ValueError("comparison aggregate trusted counts disagree")
    if union < max(satnogs, phase):
        raise ValueError("comparison union cannot be smaller than an input arm")
    delta = union - satnogs
    if aggregate.get("hybrid_union_vs_satnogs") != delta:
        raise ValueError("comparison union delta disagrees")
    if aggregate.get("standalone_phase_first_vs_satnogs") not in (
        None,
        phase - satnogs,
    ):
        raise ValueError("comparison standalone delta disagrees")
    relative = aggregate.get("hybrid_relative_gain_over_satnogs")
    expected_relative = delta / satnogs if satnogs else None
    if not isinstance(relative, (int, float)) or isinstance(relative, bool):
        raise ValueError("comparison relative gain must be numeric")
    if expected_relative is None or not math.isclose(
        float(relative), expected_relative, rel_tol=1e-12, abs_tol=1e-12
    ):
        raise ValueError("comparison relative gain disagrees")
    return {
        "observation_count": observation_count,
        "listed_observation_count": len(observations),
        "excluded_observation_count": len(excluded),
        "satnogs_trusted_payloads": satnogs,
        "standalone_phase_first_trusted_payloads": phase,
        "protocol_validated_union_trusted_payloads": union,
        "union_gain_payloads": delta,
        "union_relative_gain": float(relative),
    }


def build_campaign_report(
    *,
    archive_inventory: Mapping[str, Any],
    live_bucket_inventory: Mapping[str, Any],
    catalogue_download_manifest: Mapping[str, Any],
    live_delta_download_manifest: Mapping[str, Any],
    comparison: Mapping[str, Any],
    processing_manifest: Mapping[str, Any] | None = None,
    protocol_audit: Mapping[str, Any] | None = None,
    live_delta_triage: Mapping[str, Any] | None = None,
    satyaml_routing: Mapping[str, Any] | None = None,
    live_delta_cw_probe: Mapping[str, Any] | None = None,
    rml24_dataset: Mapping[str, Any] | None = None,
    sources: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    archive = validate_archive_inventory(archive_inventory)
    live = validate_live_bucket_inventory(live_bucket_inventory)
    catalogue_download = validate_download_manifest(
        catalogue_download_manifest, name="catalogue download"
    )
    live_download = validate_download_manifest(
        live_delta_download_manifest, name="live-delta download"
    )
    baseline = validate_comparison(comparison)
    if processing_manifest is None:
        processing: dict[str, object] = {
            "started": False,
            "complete": False,
            "attempt_complete": False,
            "candidate_count": None,
            "eligible_inventory_count": archive[
                "eligible_explicit_g3ruh_zero_reference_count"
            ],
            "latest_result_count": 0,
            "remaining_count": None,
            "status_counts": {},
            "error_count": None,
            "raw_crc_valid_candidate_count": None,
            "trusted_protocol_valid_frame_count": None,
            "evidence_classification": "not started; no results inferred",
        }
    else:
        processing = validate_processing_manifest(processing_manifest)
        processing["eligible_inventory_count"] = archive[
            "eligible_explicit_g3ruh_zero_reference_count"
        ]
        if processing["candidate_count"] > processing["eligible_inventory_count"]:
            raise ValueError("processing candidate_count exceeds archive eligibility")
        processing["batch_complete"] = processing["complete"]
        processing["scope_complete"] = (
            processing["candidate_count"] == processing["eligible_inventory_count"]
        )
        processing["unselected_eligible_count"] = (
            processing["eligible_inventory_count"] - processing["candidate_count"]
        )
        processing["complete"] = bool(
            processing["batch_complete"] and processing["scope_complete"]
        )

    if protocol_audit is not None:
        if processing_manifest is None:
            raise ValueError("protocol audit requires a processing manifest")
        audit: dict[str, object] = validate_protocol_audit(
            protocol_audit,
            processing_manifest=processing_manifest,
            processing_complete=bool(processing["complete"]),
            eligible_observation_count=int(
                archive["eligible_explicit_g3ruh_zero_reference_count"]
            ),
        )
        processing["trusted_protocol_valid_frame_count"] = audit[
            "trusted_ax25_ui_frame_count"
        ]
        processing["rejected_crc_collision_count"] = audit[
            "rejected_crc_collision_count"
        ]
        processing["ax25_with_exact_inner_ccsds_count"] = audit[
            "ax25_with_exact_inner_ccsds_count"
        ]
        processing["evidence_classification"] = audit["evidence_classification"]
    else:
        processing_statuses = _mapping(
            processing.get("status_counts"), "processing status_counts"
        )
        completed_processing_count = int(processing_statuses.get("processed", 0)) + int(
            processing_statuses.get("skipped_complete", 0)
        )
        audit = {
            "available": False,
            "complete": False,
            "declared_campaign_complete": None,
            "audited_observation_count": 0,
            "completed_processing_observation_count": completed_processing_count,
            "eligible_observation_count": archive[
                "eligible_explicit_g3ruh_zero_reference_count"
            ],
            "remaining_completed_observations_to_audit": completed_processing_count,
            "raw_crc_valid_candidate_count": None,
            "trusted_ax25_ui_frame_count": None,
            "rejected_crc_collision_count": None,
            "ax25_with_exact_inner_ccsds_count": None,
            "evidence_classification": "not available; no protocol results inferred",
        }

    triage = (
        validate_live_delta_triage(live_delta_triage)
        if live_delta_triage is not None
        else {
            "available": False,
            "complete": None,
            "file_count": None,
            "recommended_for_expensive_bank_count": None,
            "recommended_observation_ids": [],
            "raw_crc_valid_candidate_count": None,
            "trusted_protocol_valid_frame_count": None,
            "evidence_classification": "not available; no results inferred",
        }
    )
    satyaml = (
        validate_satyaml_routing(satyaml_routing)
        if satyaml_routing is not None
        else {
            "available": False,
            "complete": None,
            "observation_count": None,
            "routable_to_existing_profile_count": None,
            "not_routable_to_existing_profile_count": None,
            "unique_profile_count": None,
            "definite_new_or_updated_profile_count": None,
            "status_counts": {},
            "raw_crc_valid_candidate_count": None,
            "trusted_protocol_valid_frame_count": None,
            "evidence_classification": "not available; no routing inferred",
        }
    )
    cw_probe = (
        validate_cw_probe(live_delta_cw_probe)
        if live_delta_cw_probe is not None
        else {
            "available": False,
            "complete": None,
            "analyzed_count": None,
            "classification_counts": {},
            "pending_candidate_count": None,
            "cross_observation_repeated_text_count": None,
            "trusted_protocol_valid_frame_count": None,
            "candidate_validation": None,
            "evidence_classification": "not available; no CW candidates inferred",
        }
    )
    rml24 = (
        validate_rml24_dataset(rml24_dataset)
        if rml24_dataset is not None
        else {
            "available": False,
            "metadata_complete": None,
            "file_count": None,
            "total_archive_bytes": None,
            "ground_truth_bit_file_count": None,
            "paper_record_count": None,
            "currently_uploaded_record_count_inferred": None,
            "samples_per_record": None,
            "duration_per_record_seconds": None,
            "modulation_class_count_current_upload": None,
            "full_download_status": None,
            "benchmark_unit": None,
            "frame_yield_benchmark": False,
            "trusted_protocol_valid_frame_count": None,
            "evidence_classification": "not available; no dataset facts inferred",
        }
    )
    required_complete = {
        "archive_inventory": bool(archive["complete"]),
        "live_bucket_inventory": bool(live["complete"]),
        "catalogue_download": bool(catalogue_download["complete"]),
        "live_delta_download": bool(live_download["complete"]),
        "explicit_g3ruh_processing": bool(processing["complete"]),
    }
    complete = all(required_complete.values())
    return {
        "schema_version": REPORT_SCHEMA,
        "complete": complete,
        "completion_contract": {
            "required_stages": required_complete,
            "live_delta_triage_optional": True,
            "satyaml_routing_optional": True,
            "live_delta_cw_probe_optional": True,
            "rml24_dataset_metadata_optional": True,
            "protocol_audit_complete": audit["complete"],
            "protocol_audit_is_separate_from_processing_completion": True,
        },
        "inventory": {
            "complete": bool(archive["complete"] and live["complete"]),
            "archive": archive,
            "live_bucket": live,
        },
        "downloads": {
            "complete": bool(
                catalogue_download["complete"] and live_download["complete"]
            ),
            "catalogue_zero_frame_campaign": catalogue_download,
            "live_delta": live_download,
        },
        "processing": {
            "complete": bool(processing["complete"]),
            "protocol_audit_complete": audit["complete"],
            "explicit_g3ruh": processing,
            "protocol_audit": audit,
            "live_delta_triage": triage,
            "satyaml_routing": satyaml,
            "live_delta_cw_probe": cw_probe,
        },
        "capabilities": {
            "implemented": {
                "waveform_demodulators": archive["current_demodulators"],
                "waveform_families": archive["current_waveform_families"],
                "protocol_components": [
                    "AX.25/HDLC with optional G3RUH and CRC-16/X-25",
                    "raw CCSDS Space Packet validation after byte recovery",
                    "AX.25 UI carrying an exact-length CCSDS Space Packet",
                    "caller-configured fixed-sync framing with strict validation",
                    "narrowband/CW pending-candidate extraction with Morse timing bank",
                ],
                "generic_fsk_implies_ax25": False,
            },
            "required_plugins": archive["required_plugins"],
        },
        "evidence": {
            "current_campaign": {
                "raw_crc_valid_candidate_count": processing[
                    "raw_crc_valid_candidate_count"
                ],
                "trusted_protocol_valid_frame_count": processing[
                    "trusted_protocol_valid_frame_count"
                ],
                "rejected_crc_collision_count": processing.get(
                    "rejected_crc_collision_count"
                ),
                "ax25_with_exact_inner_ccsds_count": processing.get(
                    "ax25_with_exact_inner_ccsds_count"
                ),
                "protocol_audit_complete": audit["complete"],
                "audited_observation_count": audit["audited_observation_count"],
                "label": processing["evidence_classification"],
            },
            "live_delta_triage": {
                "raw_crc_valid_candidate_count": triage[
                    "raw_crc_valid_candidate_count"
                ],
                "trusted_protocol_valid_frame_count": triage[
                    "trusted_protocol_valid_frame_count"
                ],
                "available": triage["available"],
            },
            "satyaml_routing": {
                "available": satyaml["available"],
                "raw_crc_valid_candidate_count": None,
                "trusted_protocol_valid_frame_count": None,
                "label": satyaml["evidence_classification"],
            },
            "live_delta_cw_probe": {
                "available": cw_probe["available"],
                "pending_candidate_count": cw_probe["pending_candidate_count"],
                "trusted_protocol_valid_frame_count": None,
                "candidate_validation": cw_probe["candidate_validation"],
                "label": cw_probe["evidence_classification"],
            },
            "rml24": {
                "available": rml24["available"],
                "benchmark_unit": rml24["benchmark_unit"],
                "frame_yield_benchmark": False,
                "trusted_protocol_valid_frame_count": None,
                "label": rml24["evidence_classification"],
            },
            "same_iq_protocol_validated_baseline": baseline,
            "counting_rule": (
                "CRC-valid candidates are not called telemetry until a mission/protocol audit validates them"
            ),
        },
        "datasets": {"rml24": rml24},
        "sources": [dict(item) for item in sources],
    }


def render_campaign_markdown(report: Mapping[str, Any]) -> str:
    inventory = _mapping(report["inventory"], "report inventory")
    downloads = _mapping(report["downloads"], "report downloads")
    processing = _mapping(report["processing"], "report processing")
    archive = _mapping(inventory["archive"], "report archive")
    live = _mapping(inventory["live_bucket"], "report live")
    catalogue_download = _mapping(
        downloads["catalogue_zero_frame_campaign"], "catalogue download"
    )
    live_download = _mapping(downloads["live_delta"], "live download")
    g3ruh = _mapping(processing["explicit_g3ruh"], "G3RUH processing")
    protocol_audit = _mapping(processing["protocol_audit"], "G3RUH protocol audit")
    evidence = _mapping(report["evidence"], "report evidence")
    baseline = _mapping(
        evidence["same_iq_protocol_validated_baseline"], "report baseline"
    )
    plugins = _sequence(
        _mapping(report["capabilities"], "capabilities")["required_plugins"],
        "required plugins",
    )
    raw_count = g3ruh.get("raw_crc_valid_candidate_count")
    trusted_count = g3ruh.get("trusted_protocol_valid_frame_count")
    live_triage = _mapping(processing["live_delta_triage"], "live-delta triage")
    satyaml = _mapping(processing["satyaml_routing"], "SatYAML routing")
    cw_probe = _mapping(processing["live_delta_cw_probe"], "CW probe")
    datasets = _mapping(report["datasets"], "report datasets")
    rml24 = _mapping(datasets["rml24"], "RML24 dataset")
    triage_recommended = live_triage.get("recommended_for_expensive_bank_count")
    triage_text = "brak wyniku" if triage_recommended is None else str(triage_recommended)
    satyaml_text = (
        "brak wyniku"
        if satyaml["observation_count"] is None
        else (
            f"{satyaml['routable_to_existing_profile_count']}/"
            f"{satyaml['observation_count']} do istniejących profili"
        )
    )
    cw_text = (
        "brak wyniku"
        if cw_probe["analyzed_count"] is None
        else (
            f"{cw_probe['analyzed_count']} analiz, "
            f"{cw_probe['pending_candidate_count']} kandydatów pending"
        )
    )
    rml24_text = (
        "brak metadanych"
        if rml24["currently_uploaded_record_count_inferred"] is None
        else (
            f"{rml24['currently_uploaded_record_count_inferred']} krótkich rekordów, "
            f"{rml24['duration_per_record_seconds'] * 1000:.3f} ms każdy"
        )
    )
    raw_text = "brak wyniku" if raw_count is None else str(raw_count)
    trusted_text = "brak audytu" if trusted_count is None else str(trusted_count)
    rejected_count = protocol_audit.get("rejected_crc_collision_count")
    rejected_text = "brak audytu" if rejected_count is None else str(rejected_count)
    inner_ccsds_count = protocol_audit.get("ax25_with_exact_inner_ccsds_count")
    inner_ccsds_text = (
        "brak audytu" if inner_ccsds_count is None else str(inner_ccsds_count)
    )
    audit_progress = (
        f"{protocol_audit['audited_observation_count']}/"
        f"{protocol_audit['eligible_observation_count']}"
        if protocol_audit["available"]
        else "niedostępny"
    )
    plugin_text = ", ".join(
        f"{item['plugin']} ({item['observation_count']})" for item in plugins
    ) or "brak"
    processing_progress = (
        "nie rozpoczęto "
        f"(kwalifikujące rekordy: {g3ruh['eligible_inventory_count']})"
        if g3ruh.get("candidate_count") is None
        else f"{g3ruh['latest_result_count']}/{g3ruh['candidate_count']}"
    )
    catalogue_statuses = _mapping(
        catalogue_download["status_counts"], "catalogue download statuses"
    )
    catalogue_successes = int(catalogue_statuses.get("downloaded", 0)) + int(
        catalogue_statuses.get("skipped_valid", 0)
    )
    live_statuses = _mapping(live_download["status_counts"], "live download statuses")
    live_successes = int(live_statuses.get("downloaded", 0)) + int(
        live_statuses.get("skipped_valid", 0)
    )
    yes_no = lambda value: "tak" if value else "nie"
    return (
        "# Generic receiver — milestone v1\n\n"
        f"Stan końcowy: **complete: {str(bool(report['complete'])).lower()}**. "
        "Inwentaryzacja, pobieranie i przetwarzanie są raportowane oddzielnie.\n\n"
        "## Pokrycie danych\n\n"
        f"- Inventory kompletne: {yes_no(inventory['complete'])}; katalog: "
        f"{archive['observation_count']} obserwacji, {archive['iq_available_count']} z IQ.\n"
        f"- Live bucket: {live['iq_object_count']} IQ, w tym "
        f"{live['new_in_bucket_count']} nowych względem katalogu.\n"
        f"- Download katalogowy: próby {catalogue_download['latest_result_count']}/"
        f"{catalogue_download['candidate_count']}, poprawne: {catalogue_successes}, "
        f"błędy: {catalogue_download['error_count']}, complete: "
        f"{str(catalogue_download['complete']).lower()}.\n"
        f"- Download live-delta: próby {live_download['latest_result_count']}/"
        f"{live_download['candidate_count']}, poprawne: {live_successes}, "
        f"błędy: {live_download['error_count']}, complete: "
        f"{str(live_download['complete']).lower()}.\n"
        f"- Processing jawnego G3RUH: {processing_progress}, complete: "
        f"{str(g3ruh['complete']).lower()}.\n"
        f"- Audyt protokołu G3RUH: {audit_progress}, complete: "
        f"{str(protocol_audit['complete']).lower()}.\n"
        f"- Triage sygnałowy live-delta: "
        f"{triage_text} "
        "rekomendowanych nagrań; nie jest to demodulacja ani audyt protokołu.\n"
        f"- Routing SatYAML: {satyaml_text}; to zgodność metadanych, nie dekodowanie.\n"
        f"- Próba CW/narrowband: {cw_text}; tekst bez walidatora nie jest telemetrią.\n"
        f"- RML24: {rml24_text}; benchmark modulacji/BER, nie liczby ramek.\n\n"
        "## Możliwości i braki\n\n"
        "Aktualny tor obejmuje phase-FSK oraz jawnie konfigurowane AX.25/G3RUH, "
        "walidację CCSDS i stos AX.25+CCSDS. Ogólne FSK nie implikuje AX.25.\n\n"
        f"Wymagane pluginy dla dostępnych IQ: {plugin_text}.\n\n"
        "## Wyniki i poziom zaufania\n\n"
        f"Bieżąca kampania G3RUH: kandydaci CRC-valid: **{raw_text}**; "
        f"zaufane ramki AX.25 UI: **{trusted_text}**; "
        f"odrzucone kolizje CRC: **{rejected_text}**; "
        f"ramki z dokładnym wewnętrznym CCSDS: **{inner_ccsds_text}**. "
        "Sam CRC nie jest liczony jako telemetria.\n\n"
        "Kandydaty tekstowe CW mają validation=pending i nie zwiększają licznika "
        "zaufanej telemetrii. RML24 mierzy warstwę modulacji i BER; nie jest "
        "benchmarkiem end-to-end ramek.\n\n"
        f"Znany benchmark same-IQ: SatNOGS {baseline['satnogs_trusted_payloads']}, "
        f"sam phase-first {baseline['standalone_phase_first_trusted_payloads']}, "
        f"unia po walidacji protokołu {baseline['protocol_validated_union_trusted_payloads']} "
        f"({baseline['union_gain_payloads']:+d} względem SatNOGS).\n"
    )


def _load_snapshot(role: str, path: Path) -> tuple[Mapping[str, Any], dict[str, object]]:
    """Read a mutable manifest once so its parsed data and hash are identical."""

    data = path.read_bytes()
    document = _mapping(json.loads(data), str(path))
    source = {
        "role": role,
        "path": str(path.resolve()),
        "schema_version": document.get("schema_version"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    return document, source


def generate_campaign_report(
    *,
    archive_inventory_path: Path,
    live_bucket_inventory_path: Path,
    catalogue_download_manifest_path: Path,
    live_delta_download_manifest_path: Path,
    comparison_path: Path,
    output_json_path: Path,
    output_markdown_path: Path,
    processing_manifest_path: Path | None = None,
    protocol_audit_path: Path | None = None,
    live_delta_triage_path: Path | None = None,
    satyaml_routing_path: Path | None = None,
    live_delta_cw_probe_path: Path | None = None,
    rml24_dataset_path: Path | None = None,
) -> dict[str, object]:
    required_paths = [
        ("archive_inventory", archive_inventory_path),
        ("live_bucket_inventory", live_bucket_inventory_path),
        ("catalogue_download_manifest", catalogue_download_manifest_path),
        ("live_delta_download_manifest", live_delta_download_manifest_path),
        ("same_iq_comparison", comparison_path),
    ]
    snapshots = {
        role: _load_snapshot(role, path) for role, path in required_paths
    }
    sources = [snapshots[role][1] for role, _path in required_paths]
    processing = None
    if processing_manifest_path is not None and processing_manifest_path.exists():
        processing, source = _load_snapshot(
            "g3ruh_processing_manifest", processing_manifest_path
        )
        sources.append(source)
    protocol_audit = None
    if protocol_audit_path is not None and protocol_audit_path.exists():
        protocol_audit, source = _load_snapshot(
            "g3ruh_protocol_audit", protocol_audit_path
        )
        sources.append(source)
    triage = None
    if live_delta_triage_path is not None and live_delta_triage_path.exists():
        triage, source = _load_snapshot("live_delta_triage", live_delta_triage_path)
        sources.append(source)
    satyaml = None
    if satyaml_routing_path is not None and satyaml_routing_path.exists():
        satyaml, source = _load_snapshot("satyaml_routing", satyaml_routing_path)
        sources.append(source)
    cw_probe = None
    if live_delta_cw_probe_path is not None and live_delta_cw_probe_path.exists():
        cw_probe, source = _load_snapshot(
            "live_delta_cw_probe", live_delta_cw_probe_path
        )
        sources.append(source)
    rml24 = None
    if rml24_dataset_path is not None and rml24_dataset_path.exists():
        rml24, source = _load_snapshot("rml24_dataset", rml24_dataset_path)
        sources.append(source)
    report = build_campaign_report(
        archive_inventory=snapshots["archive_inventory"][0],
        live_bucket_inventory=snapshots["live_bucket_inventory"][0],
        catalogue_download_manifest=snapshots["catalogue_download_manifest"][0],
        live_delta_download_manifest=snapshots["live_delta_download_manifest"][0],
        comparison=snapshots["same_iq_comparison"][0],
        processing_manifest=processing,
        protocol_audit=protocol_audit,
        live_delta_triage=triage,
        satyaml_routing=satyaml,
        live_delta_cw_probe=cw_probe,
        rml24_dataset=rml24,
        sources=sources,
    )
    output_json_path.parent.mkdir(parents=True, exist_ok=True)
    output_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_temporary = output_json_path.with_name(output_json_path.name + ".tmp")
    md_temporary = output_markdown_path.with_name(output_markdown_path.name + ".tmp")
    json_temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_temporary.write_text(render_campaign_markdown(report), encoding="utf-8")
    json_temporary.replace(output_json_path)
    md_temporary.replace(output_markdown_path)
    return report
