"""Outcome-blind materialization of a prospective campaign after training gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence

from .api_contact import ApiContactError, load_api_contact
from .dataset import parse_api_datetime
from .learned_probability import FrozenLogitProbabilityEstimator
from .prospective import ProspectiveCampaignConfig


TEMPLATE_SCHEMA = "observation-planning-prospective-template-v1"


class CampaignMaterializationError(ValueError):
    """The historical gates cannot safely produce a prospective config."""


def _read_mapping(path: Path, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignMaterializationError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise CampaignMaterializationError(f"{label} must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as exc:
        raise CampaignMaterializationError("dependency path leaves project root") from exc


def _dependency_records(
    paths: Mapping[str, Path], project_root: Path
) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for name, path in sorted(paths.items()):
        if not path.is_file():
            raise CampaignMaterializationError(f"missing dependency: {name}")
        records[name] = {
            "path": _relative(path, project_root),
            "byte_length": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return records


def _verify_historical_gates(
    *,
    project_root: Path,
    snapshot_manifest_path: Path,
    history_dataset_path: Path,
    dataset_manifest_path: Path,
    evaluation_path: Path,
    independent_audit_path: Path,
    probability_model_path: Path,
    api_contact_path: Path,
) -> tuple[dict[str, dict[str, object]], datetime, str]:
    dependencies = {
        "snapshot_manifest": snapshot_manifest_path,
        "history_dataset": history_dataset_path,
        "dataset_manifest": dataset_manifest_path,
        "evaluation": evaluation_path,
        "independent_audit": independent_audit_path,
        "probability_model": probability_model_path,
        "runtime_api_contact": api_contact_path,
    }
    records = _dependency_records(dependencies, project_root)
    if history_dataset_path.stat().st_size == 0:
        raise CampaignMaterializationError("history dataset is empty")

    snapshot = _read_mapping(snapshot_manifest_path, label="snapshot manifest")
    if snapshot.get("complete") is not True:
        raise CampaignMaterializationError("historical snapshot is incomplete")

    dataset_sha = records["history_dataset"]["sha256"]
    manifest = _read_mapping(dataset_manifest_path, label="dataset manifest")
    if (
        manifest.get("publication_count_gate") != "pass"
        or manifest.get("dataset_sha256") != dataset_sha
    ):
        raise CampaignMaterializationError("dataset manifest gate or hash failed")

    evaluation = _read_mapping(evaluation_path, label="evaluation")
    if (
        evaluation.get("schema_version") != "observation-planning-evaluation-v1"
        or evaluation.get("publication_count_gate_enforced") is not True
        or evaluation.get("dataset_sha256") != dataset_sha
    ):
        raise CampaignMaterializationError("held-out evaluation gate or hash failed")

    audit = _read_mapping(independent_audit_path, label="independent audit")
    audit_inputs = audit.get("input_artifacts")
    audit_bound = (
        isinstance(audit_inputs, Mapping)
        and isinstance(audit_inputs.get("dataset"), Mapping)
        and isinstance(audit_inputs.get("dataset_manifest"), Mapping)
        and isinstance(audit_inputs.get("evaluation"), Mapping)
        and audit_inputs["dataset"].get("sha256") == dataset_sha
        and audit_inputs["dataset_manifest"].get("sha256")
        == records["dataset_manifest"]["sha256"]
        and audit_inputs["evaluation"].get("sha256")
        == records["evaluation"]["sha256"]
    )
    if (
        audit.get("schema_version")
        != "observation-planning-independent-audit-v1"
        or audit.get("passed") is not True
        or audit.get("failed_check_count") != 0
        or not audit_bound
    ):
        raise CampaignMaterializationError("independent audit failed")

    model_payload = _read_mapping(probability_model_path, label="probability model")
    model_bound = (
        model_payload.get("study_id") == manifest.get("study_id")
        and evaluation.get("study_id") == manifest.get("study_id")
        and model_payload.get("training_dataset_sha256") == dataset_sha
        and model_payload.get("dataset_manifest_sha256")
        == records["dataset_manifest"]["sha256"]
        and model_payload.get("evaluation_sha256")
        == records["evaluation"]["sha256"]
    )
    if not model_bound:
        raise CampaignMaterializationError(
            "probability model is not bound to the verified historical artifacts"
        )

    estimator = FrozenLogitProbabilityEstimator.load(
        probability_model_path,
        history_dataset_path=history_dataset_path,
    )
    try:
        runtime_user_agent, _ = load_api_contact(api_contact_path)
    except ApiContactError as exc:
        raise CampaignMaterializationError(
            "runtime API contact artifact is invalid"
        ) from exc
    return records, estimator.training_data_end, runtime_user_agent


def _next_midnight_with_lead(now: datetime, lead_hours: int) -> datetime:
    threshold = now.astimezone(UTC) + timedelta(hours=lead_hours)
    midnight_number = math.ceil(threshold.timestamp() / 86_400)
    return datetime.fromtimestamp(midnight_number * 86_400, tz=UTC)


def _verify_existing(
    output_path: Path,
    *,
    template: Mapping[str, object],
    dependencies: Mapping[str, Mapping[str, object]],
    training_data_end: datetime,
    runtime_user_agent: str,
) -> dict[str, object]:
    existing = _read_mapping(output_path, label="materialized campaign config")
    ProspectiveCampaignConfig.load(output_path)
    if existing.get("materialization_template_sha256") != template.get(
        "template_sha256"
    ):
        raise CampaignMaterializationError("materialized config template binding changed")
    if existing.get("historical_dependencies") != dependencies:
        raise CampaignMaterializationError("materialized historical dependencies changed")
    for key, value in template.items():
        if key in {
            "schema_version",
            "campaign_id_prefix",
            "duration_days",
            "minimum_start_lead_hours",
            "template_sha256",
        }:
            continue
        if key == "protocol_expansion":
            actual_expansion = existing.get(key)
            if not isinstance(value, Mapping) or not isinstance(
                actual_expansion, Mapping
            ):
                raise CampaignMaterializationError(
                    "materialized protocol expansion changed"
                )
            if any(actual_expansion.get(name) != item for name, item in value.items()):
                raise CampaignMaterializationError(
                    "materialized protocol expansion changed"
                )
        elif existing.get(key) != value:
            raise CampaignMaterializationError(
                f"materialized static field changed: {key}"
            )

    registered_at = parse_api_datetime(
        existing.get("registered_at"), name="registered_at"
    )
    materialized_at = parse_api_datetime(
        existing.get("materialized_at"), name="materialized_at"
    )
    start = parse_api_datetime(existing.get("start"), name="start")
    end = parse_api_datetime(existing.get("end"), name="end")
    lead_hours = int(template.get("minimum_start_lead_hours", 48))
    duration_days = int(template.get("duration_days", 31))
    prefix = str(template.get("campaign_id_prefix", "")).strip()
    if registered_at != materialized_at:
        raise CampaignMaterializationError("registration timestamp changed")
    if start != _next_midnight_with_lead(materialized_at, lead_hours):
        raise CampaignMaterializationError("materialized campaign start changed")
    if end - start != timedelta(days=duration_days):
        raise CampaignMaterializationError("materialized campaign duration changed")
    if existing.get("campaign_id") != f"{prefix}-{start:%Y%m%d}":
        raise CampaignMaterializationError("materialized campaign ID changed")
    if training_data_end >= start:
        raise CampaignMaterializationError("training data do not precede campaign start")
    if existing.get("training_data_end") != _utc_text(training_data_end):
        raise CampaignMaterializationError("serialized training boundary changed")
    if existing.get("runtime_user_agent") != runtime_user_agent:
        raise CampaignMaterializationError("runtime API identity changed")
    return existing


def materialize_campaign_config(
    *,
    project_root: Path,
    template_path: Path,
    output_path: Path,
    snapshot_manifest_path: Path,
    history_dataset_path: Path,
    dataset_manifest_path: Path,
    evaluation_path: Path,
    independent_audit_path: Path,
    probability_model_path: Path,
    api_contact_path: Path,
    now: datetime | None = None,
) -> tuple[dict[str, object], bool]:
    """Create the dated config once, or verify its immutable dependencies."""

    template = _read_mapping(template_path, label="campaign template")
    if template.get("schema_version") != TEMPLATE_SCHEMA:
        raise CampaignMaterializationError("unsupported campaign template schema")
    template_hash = _sha256(template_path)
    template_with_hash = dict(template)
    template_with_hash["template_sha256"] = template_hash
    dependencies, training_data_end, runtime_user_agent = _verify_historical_gates(
        project_root=project_root,
        snapshot_manifest_path=snapshot_manifest_path,
        history_dataset_path=history_dataset_path,
        dataset_manifest_path=dataset_manifest_path,
        evaluation_path=evaluation_path,
        independent_audit_path=independent_audit_path,
        probability_model_path=probability_model_path,
        api_contact_path=api_contact_path,
    )
    if output_path.exists():
        return (
            _verify_existing(
                output_path,
                template=template_with_hash,
                dependencies=dependencies,
                training_data_end=training_data_end,
                runtime_user_agent=runtime_user_agent,
            ),
            False,
        )

    current = (now or datetime.now(UTC)).astimezone(UTC)
    lead_hours = int(template.get("minimum_start_lead_hours", 48))
    duration_days = int(template.get("duration_days", 31))
    if lead_hours < 24 or duration_days < 30:
        raise CampaignMaterializationError("template weakens timing gates")
    start = _next_midnight_with_lead(current, lead_hours)
    end = start + timedelta(days=duration_days)
    if training_data_end >= start:
        raise CampaignMaterializationError("training data do not precede campaign start")

    prefix = str(template.get("campaign_id_prefix", "")).strip()
    if not prefix:
        raise CampaignMaterializationError("campaign ID prefix is missing")
    payload = {
        key: value
        for key, value in template.items()
        if key
        not in {
            "campaign_id_prefix",
            "duration_days",
            "minimum_start_lead_hours",
        }
    }
    payload.update(
        {
            "schema_version": "observation-planning-prospective-config-v1",
            "campaign_id": f"{prefix}-{start:%Y%m%d}",
            "registered_at": _utc_text(current),
            "start": _utc_text(start),
            "end": _utc_text(end),
            "materialized_at": _utc_text(current),
            "materialization_template_path": _relative(template_path, project_root),
            "materialization_template_sha256": template_hash,
            "historical_dependencies": dependencies,
            "training_data_end": _utc_text(training_data_end),
            "runtime_user_agent": runtime_user_agent,
        }
    )
    expansion = dict(payload.get("protocol_expansion", {}))
    expansion.update(
        {
            "dynamic_start_policy": (
                "first UTC midnight at least "
                f"{lead_hours} hours after verified materialization"
            ),
            "duration_days": duration_days,
            "materialized_before_campaign_start": True,
        }
    )
    payload["protocol_expansion"] = expansion

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
        ProspectiveCampaignConfig.load(temporary)
        temporary.replace(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return payload, True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--history-dataset", type=Path, required=True)
    parser.add_argument("--dataset-manifest", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--independent-audit", type=Path, required=True)
    parser.add_argument("--probability-model", type=Path, required=True)
    parser.add_argument("--api-contact", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload, created = materialize_campaign_config(
        project_root=args.project_root,
        template_path=args.template,
        output_path=args.output,
        snapshot_manifest_path=args.snapshot_manifest,
        history_dataset_path=args.history_dataset,
        dataset_manifest_path=args.dataset_manifest,
        evaluation_path=args.evaluation,
        independent_audit_path=args.independent_audit,
        probability_model_path=args.probability_model,
        api_contact_path=args.api_contact,
    )
    print(
        f"campaign_id={payload['campaign_id']} created={str(created).lower()} "
        f"start={payload['start']} -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
