"""Immutable source/config manifest for the observation-planning study."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Sequence

from .v4_protocol import (
    validate_v4_method_contract,
    validate_v4_publication_config_contract,
)


_ANTENNA_TRUTH_SCHEMA = "observation-planning-historical-antenna-truth-audit-v1"


def _sha256_text_valid(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _verified_local_artifact(
    project_root: Path, record: object, *, label: str
) -> Path:
    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
        raise ValueError(f"{label} artifact declaration is malformed")
    path = (project_root / str(record["path"])).resolve()
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"{label} artifact leaves project root") from exc
    if not path.is_file() or not _sha256_text_valid(record.get("sha256")):
        raise ValueError(f"{label} artifact is absent or has an invalid SHA-256")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
        raise ValueError(f"{label} artifact SHA-256 mismatch: {path}")
    return path


def _historical_antenna_truth_audit_paths(project_root: Path) -> list[Path]:
    """Validate and return the single v4 no-backfill antenna audit."""

    if not (project_root / "configs/observation-planning-publication-v4.json").is_file():
        return []
    paths = sorted(
        (project_root / "reports/observation-planning-publication-v4").glob(
            "historical-antenna-truth-audit-*.json"
        )
    )
    if len(paths) != 1:
        raise ValueError("v4 requires exactly one historical antenna truth audit")
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != _ANTENNA_TRUTH_SCHEMA
        or payload.get("pass") is not True
    ):
        raise ValueError("historical antenna truth audit did not pass")
    findings = payload.get("findings")
    policy = payload.get("implementation_policy")
    boundary = payload.get("publication_claim_boundary")
    upstream = payload.get("upstream_source")
    coverage = payload.get("prospective_coverage")
    required_forbidden_claims = {
        "historical causal or predictive effect of antenna type",
        "historical gain or receiver-noise effect",
        "claim that the current public antenna profile was active for an older observation",
    }
    if not (
        isinstance(findings, Mapping)
        and findings.get("historical_public_antenna_profile_available") is False
        and findings.get("measured_gain_available_from_public_station_api") is False
        and findings.get(
            "measured_system_noise_temperature_available_from_public_station_api"
        )
        is False
        and findings.get("public_station_antenna_temporal_fields") == []
        and findings.get("station_configuration_history_publicly_retrievable")
        is False
        and findings.get("client_metadata_capture_location_available") is True
        and findings.get("client_metadata_receiver_configuration_available") is True
        and findings.get("client_metadata_physical_antenna_profile_available")
        is False
        and findings.get("observation_serializer_exposes_cached_station_antennas")
        is False
        and isinstance(policy, Mapping)
        and policy.get("historical_current_profile_backfill") == "forbidden"
        and policy.get("historical_hardware_missingness") == "explicit"
        and policy.get("same_observation_receiver_metadata_prediction_use")
        == "forbidden"
        and policy.get("client_metadata_paths_and_device_identifiers")
        == "discarded during normalization"
        and isinstance(boundary, Mapping)
        and isinstance(
            boundary.get("forbidden_without_additional_versioned_data"), list
        )
        and required_forbidden_claims
        <= set(boundary["forbidden_without_additional_versioned_data"])
    ):
        raise ValueError("historical antenna truth boundary is incomplete")
    if not isinstance(upstream, Mapping):
        raise ValueError("historical antenna upstream source is absent")
    commit = upstream.get("commit")
    source_files = upstream.get("source_files")
    expected_paths = {
        "network/base/models.py",
        "network/base/scheduling.py",
        "network/api/serializers.py",
        "network/api/views.py",
    }
    if not (
        upstream.get("repository")
        == "https://gitlab.com/librespacefoundation/satnogs/satnogs-network"
        and isinstance(commit, str)
        and len(commit) == 40
        and all(character in "0123456789abcdef" for character in commit)
        and isinstance(source_files, list)
        and {item.get("path") for item in source_files if isinstance(item, Mapping)}
        == expected_paths
        and all(
            isinstance(item, Mapping)
            and _sha256_text_valid(item.get("sha256"))
            and item.get("url")
            == (
                "https://gitlab.com/librespacefoundation/satnogs/"
                f"satnogs-network/-/raw/{commit}/{item.get('path')}"
            )
            for item in source_files
        )
    ):
        raise ValueError("historical antenna upstream source binding is invalid")
    if not (
        isinstance(coverage, Mapping)
        and int(coverage.get("covered_target_count", 0)) >= 50
        and int(coverage.get("minimum_compatible_station_count_per_target", 0)) >= 2
    ):
        raise ValueError("prospective antenna coverage boundary is insufficient")
    _verified_local_artifact(
        project_root,
        {
            "path": coverage.get("station_selection_path"),
            "sha256": coverage.get("station_selection_sha256"),
        },
        label="station selection",
    )
    for field in ("antenna_evidence_artifacts", "antenna_snapshot_artifacts"):
        records = coverage.get(field)
        if not isinstance(records, list) or not records:
            raise ValueError(f"historical antenna audit lacks {field}")
        for record in records:
            _verified_local_artifact(project_root, record, label=field)
    return paths


def build_planning_source_manifest(
    project_root: Path,
    *,
    extra_paths: Sequence[Path] = (),
) -> dict[str, object]:
    project_root = project_root.resolve()
    publication_config_paths = sorted(
        (project_root / "configs").glob("observation-planning-publication-v*.json")
    )
    if not publication_config_paths:
        raise ValueError("no observation-planning publication config found")
    prospective_config_paths = sorted(
        (project_root / "configs").glob("observation-planning-prospective-*.json")
    )
    release_attestation_template_paths = sorted(
        (project_root / "configs").glob(
            "observation-planning-release-attestation-template*.json"
        )
    )
    api_contact_template_paths = sorted(
        (project_root / "configs").glob(
            "observation-planning-api-contact-template*.json"
        )
    )
    config_paths = sorted(
        set(publication_config_paths)
        | set(prospective_config_paths)
        | set(release_attestation_template_paths)
        | set(api_contact_template_paths)
    )
    selection_paths: list[Path] = []
    for config_path in config_paths:
        config_payload = json.loads(config_path.read_text(encoding="utf-8"))
        for snapshot in config_payload.get("selection_snapshots", []):
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get("path"), str):
                raise ValueError("malformed selection snapshot declaration")
            path = (project_root / snapshot["path"]).resolve()
            try:
                path.relative_to(project_root)
            except ValueError as exc:
                raise ValueError("selection snapshot leaves project root") from exc
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != snapshot.get("sha256"):
                raise ValueError(f"selection snapshot SHA-256 mismatch: {path}")
            selection_paths.append(path)
    paths = list((project_root / "src/telemetry_yield/planning").glob("*.py"))
    paths.extend((project_root / "tests").glob("test_planning*.py"))
    paths.extend((project_root / "db/migrations").glob("*planning*.sql"))
    paths.extend((project_root / "configs/prospective").glob("*.json"))
    paths.extend((project_root / "deploy/systemd").glob("telemetry-yield-*.service"))
    paths.extend((project_root / "deploy/systemd").glob("telemetry-yield-*.timer"))
    paths.extend((project_root / "scripts").glob("*publication-v4*.sh"))
    paths.extend((project_root / "scripts").glob("*prospective-v4*.sh"))
    method_contract_path = validate_v4_method_contract(project_root)
    if method_contract_path is not None:
        paths.append(method_contract_path)
        validate_v4_publication_config_contract(
            project_root / "configs/observation-planning-publication-v4.json"
        )
    # The antenna truth boundary is a methodological input, not a result that
    # may drift after a campaign starts. Bind the pinned upstream-source audit
    # into every runtime and final source identity when it is present.
    paths.extend(_historical_antenna_truth_audit_paths(project_root))
    paths.extend(
        [
            project_root / "src/telemetry_yield/cli.py",
            project_root / "src/telemetry_yield/satnogs.py",
            project_root / "docs/observation-planning.md",
            project_root / "docs/observation-planning-experiment-protocol-v1.md",
            project_root / "docs/observation-planning-data-card.md",
            project_root / "docs/observation-planning-methods.md",
            project_root / "docs/observation-planning-reproduction.md",
            project_root / "docs/observation-planning-literature-comparison.md",
            project_root / "docs/observation-planning-v4.md",
            project_root / "THIRD_PARTY_ATTRIBUTION.md",
            project_root / "tests/test_observation_planning.py",
            project_root / "tests/test_satnogs.py",
            project_root / "pyproject.toml",
            project_root / "uv.lock",
        ]
    )
    paths.extend(config_paths)
    paths.extend(selection_paths)
    paths.extend(path.resolve() for path in extra_paths)
    records: list[dict[str, object]] = []
    for path in sorted(set(paths)):
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(f"manifest path leaves project root: {path}") from exc
        body = resolved.read_bytes()
        records.append(
            {
                "path": relative.as_posix(),
                "byte_length": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    identity = hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "schema_version": "observation-planning-source-manifest-v1",
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "project_root": str(project_root),
        "file_count": len(records),
        "selection_snapshot_count": len(set(selection_paths)),
        "source_identity_sha256": identity,
        "files": records,
    }


def write_planning_source_manifest(
    project_root: Path,
    output_path: Path,
    *,
    extra_paths: Sequence[Path] = (),
) -> dict[str, object]:
    manifest = build_planning_source_manifest(project_root, extra_paths=extra_paths)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
