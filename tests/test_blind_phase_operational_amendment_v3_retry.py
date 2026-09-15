from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/freeze_operational_amendment_v3_retry.py"
TEMPLATE = ROOT / "work/blind-phase-confirmatory-v2/operational-amendment-review-template-v3-retry.json"
SPEC = importlib.util.spec_from_file_location("freeze_operational_amendment_v3_retry", SOURCE)
assert SPEC and SPEC.loader
FREEZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FREEZER)


def _write_bytes(path: Path, payload: bytes) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_json(path: Path, value: dict[str, object]) -> dict[str, object]:
    return _write_bytes(path, (json.dumps(value, sort_keys=True) + "\n").encode())


def _self_hashed(schema: str, status: str, field: str, **extra: object) -> dict[str, object]:
    value: dict[str, object] = {"schema_version": schema, "status": status, **extra}
    value[field] = FREEZER.sha256_document(value)
    return value


def _base_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> argparse.Namespace:
    values: dict[str, object] = {"created_at": "2026-09-06T04:00:00Z"}
    identities: dict[str, dict[str, object]] = {}

    for name in (
        "attempt1-incident", "attempt1-recovery", "attempt1-quarantine-provenance",
    ):
        identity = _write_json(tmp_path / f"{name}.json", {"artifact": name})
        identities[name] = identity

    for name in (
        "attempt1-recovery-runner", "attempt1-recovery-runner-test",
        "attempt1-quarantine-provenance-generator",
        "attempt1-quarantine-provenance-generator-test",
        "attempt1-no-decoder-evidence",
        "attempt1-no-decoder-evidence-generator",
        "attempt1-no-decoder-evidence-generator-test",
        "attempt2-recovery-runner", "attempt2-recovery-runner-test",
        "attempt2-remediation-runner", "attempt2-remediation-runner-test",
        "attempt2-quarantine-provenance-generator",
        "attempt2-quarantine-provenance-generator-test",
        "attempt2-no-decoder-evidence-generator",
        "attempt2-no-decoder-evidence-generator-test",
        "runtime-guard-builder", "runtime-guard-builder-test", "retry-freezer-test",
        "retry-review-template",
    ):
        identity = _write_bytes(tmp_path / f"{name}.py", f"# {name}\n".encode())
        identities[name] = identity

    attempt1_no_decoder = _self_hashed(
        "test-attempt1-no-decoder-v1", "PASS", "evidence_payload_sha256",
        decoder_units_executed_since_v2=0,
    )
    identities["attempt1-no-decoder-evidence"] = _write_json(
        tmp_path / "attempt1-no-decoder-evidence.json", attempt1_no_decoder
    )
    attempt1_provenance = _self_hashed(
        "blind-phase-confirmatory-failed-v3-activation-provenance-v1",
        "PASS_METADATA_PROVENANCE_ONLY", "provenance_payload_sha256",
        generator=identities["attempt1-quarantine-provenance-generator"],
        exposure_disclosure={
            "campaign_or_evaluator_role_started": False,
            "decoder_process_started": False,
            "iq_was_used_as_decoder_input": False,
            "scientific_outcome_generated": False,
            "private_results_entry_count_before_recovery": 0,
        },
        quarantined_failed_attempt={"file_count": 0, "files": {}},
        reuse_policy={
            "future_lifecycle_requires_fresh_control_and_results_namespaces": True,
            "prior_attempt_artifacts_must_not_be_reused": True,
            "prior_attempt_control_artifacts_are_quarantined": True,
            "this_dossier_is_publication_evidence_not_runtime_authority": True,
        },
    )
    identities["attempt1-quarantine-provenance"] = _write_json(
        tmp_path / "attempt1-quarantine-provenance.json", attempt1_provenance
    )

    prior_attempt = {
        "status": "PASS_ABORTED_BEFORE_DECODER_OR_ROLE_EXECUTION",
        "incident": identities["attempt1-incident"],
        "recovery": identities["attempt1-recovery"],
        "recovery_runner": identities["attempt1-recovery-runner"],
        "recovery_runner_tests": identities["attempt1-recovery-runner-test"],
        "provenance_dossier": identities["attempt1-quarantine-provenance"],
        "decoder_started": False,
        "campaign_or_evaluator_role_started": False,
        "iq_used_as_decoder_input": False,
        "private_results_entry_count_before_recovery": 0,
        "external_namespace_zero_delta": True,
        "prior_attempt_artifact_reuse_permitted": False,
    }
    exposures = [
        {
            "unit_id": f"4491-component-a-{baud}",
            "observation_id": 4491,
            "decoder_rerun_permitted": False,
        }
        for baud in (9600, 19200)
    ]
    previous = {
        "schema_version": FREEZER.SCHEMA_VERSION,
        "status": FREEZER.STATUS,
        "created_at": "2026-09-05T00:00:00Z",
        "execution_plan": {"path": "/plan", "size_bytes": 1, "sha256": "1" * 64},
        "acquisition_manifest": {"path": "/acq", "size_bytes": 1, "sha256": "2" * 64},
        "source_manifest": {"path": "/source", "size_bytes": 1, "sha256": "3" * 64},
        "campaign_pristine": False,
        "schedule": {
            "unit_count": 6168,
            "units_to_execute": 6166,
            "units_re_normalized_without_decoder_rerun": 2,
            "scientific_schedule_changed": False,
        },
        "prior_decoder_exposures": exposures,
        "sensitivity_exclusion_observation_ids": [4491],
        "sensitivity_analysis": {
            "exclude_observation_ids": [4491],
            "full_frozen_cohort_analysis_retained": True,
            "reason": "observation 4491 component-A signal outcomes were exposed",
            "report_both_without_substitution": True,
            "required": True,
        },
        "runtime_history_disclosure": {
            "full_30_observation_cohort_retained_with_disclosure": True,
            "full_30_observation_cohort_runtime_history": "mixed_and_non_pristine",
            "full_component_closure_for_prior_exposures_cryptographically_established": False,
            "mandatory_sensitivity_excludes_entire_observation_ids": [4491],
            "mandatory_sensitivity_observation_count": 29,
            "prior_exposed_decoder_unit_count": 2,
            "prior_exposed_observation_ids": [4491],
            "remaining_decoder_run_count_with_full_component_closure_bound": 6166,
        },
        "component_runtime_closure": {"path": "/closure", "size_bytes": 1, "sha256": "4" * 64},
        "component_runtime_closure_generator": {"path": "/generator", "size_bytes": 1, "sha256": "5" * 64},
        "amended_normalizer": {"path": "/normalizer", "size_bytes": 1, "sha256": "6" * 64},
        "failed_v3_activation_attempt": prior_attempt,
        "no_decoder_execution_since_v2": {
            "status": "PASS",
            "decoder_units_executed_since_v2": 0,
            "evidence": identities["attempt1-no-decoder-evidence"],
            "evidence_generator": identities["attempt1-no-decoder-evidence-generator"],
            "evidence_generator_tests": identities[
                "attempt1-no-decoder-evidence-generator-test"
            ],
        },
        "amended_implementation": {"tests": [identities["runtime-guard-builder-test"]]},
        "claim_guard": {"publication_ready": False},
        "operational_scope": {"selection_changed": False},
    }
    previous["amendment_payload_sha256"] = FREEZER.sha256_document(previous)
    identities["previous-amendment"] = _write_json(tmp_path / "previous.json", previous)
    old_review = _self_hashed(
        "blind-phase-confirmatory-operational-amendment-review-v3",
        "GO", "review_payload_sha256", checks={"p0": 0},
    )
    identities["previous-review"] = _write_json(tmp_path / "previous-review.json", old_review)
    identities["previous-timestamp-query"] = _write_bytes(tmp_path / "previous.tsq", b"tsq")
    identities["previous-timestamp-reply"] = _write_bytes(tmp_path / "previous.tsr", b"tsr")

    fixed = {
        "EXPECTED_PREVIOUS_AMENDMENT_SHA256": identities["previous-amendment"]["sha256"],
        "EXPECTED_PREVIOUS_REVIEW_SHA256": identities["previous-review"]["sha256"],
        "EXPECTED_PREVIOUS_TSQ_SHA256": identities["previous-timestamp-query"]["sha256"],
        "EXPECTED_PREVIOUS_TSR_SHA256": identities["previous-timestamp-reply"]["sha256"],
        "EXPECTED_RUNTIME_GUARD_BUILDER_SHA256": identities["runtime-guard-builder"]["sha256"],
        "EXPECTED_RUNTIME_GUARD_BUILDER_TEST_SHA256": identities["runtime-guard-builder-test"]["sha256"],
    }
    for name, value in fixed.items():
        monkeypatch.setattr(FREEZER, name, value)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_NO_DECODER_GENERATOR_SHA256",
        identities["attempt2-no-decoder-evidence-generator"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_NO_DECODER_GENERATOR_TEST_SHA256",
        identities["attempt2-no-decoder-evidence-generator-test"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZER,
        "EXPECTED_FIXED_PATHS",
        {
            name: Path(str(identities[name]["path"]))
            for name in FREEZER.EXPECTED_FIXED_PATHS
        },
    )

    quarantine = tmp_path / "attempt2-quarantine"
    recovery_root = tmp_path / "attempt2-recovery"
    remediation_root = tmp_path / "attempt2-remediation"
    v1_control = tmp_path / "attempt2-remediation-v1-control"
    dossier_path = tmp_path / "attempt2-dossier.json"
    no_decoder_path = tmp_path / "attempt2-no-decoder.json"
    for path in (quarantine, recovery_root, remediation_root, v1_control):
        path.mkdir()
        os.chmod(path, 0o755)
    (quarantine / "results-v3").mkdir()
    os.chmod(quarantine / "results-v3", 0o755)

    monkeypatch.setattr(FREEZER, "ATTEMPT2_QUARANTINE_ROOT", quarantine)
    monkeypatch.setattr(FREEZER, "ATTEMPT2_RECOVERY_ROOT", recovery_root)
    monkeypatch.setattr(FREEZER, "ATTEMPT2_REMEDIATION_ROOT", remediation_root)
    monkeypatch.setattr(FREEZER, "SUPERSEDED_REMEDIATION_V1_CONTROL", v1_control)
    monkeypatch.setattr(FREEZER, "ATTEMPT2_DOSSIER_PATH", dossier_path)
    monkeypatch.setattr(FREEZER, "ATTEMPT2_NO_DECODER_PATH", no_decoder_path)

    original_directory_identity = FREEZER._directory_identity
    original_file_identity = FREEZER._inventory_file_identity

    def normalized_directory(path: Path) -> dict[str, object]:
        record = original_directory_identity(path)
        if Path(record["path"]).is_relative_to(tmp_path):
            record.update(uid=0, gid=0)
        return record

    def normalized_file(
        path: Path, expected_sha256: str, *, code: bool = False
    ) -> dict[str, object]:
        record = original_file_identity(path, expected_sha256, code=code)
        if Path(record["path"]).is_relative_to(tmp_path):
            record.update(uid=0, gid=0)
        return record

    monkeypatch.setattr(FREEZER, "_directory_identity", normalized_directory)
    monkeypatch.setattr(FREEZER, "_inventory_file_identity", normalized_file)

    v1_runner = _write_bytes(
        v1_control / "remediate_recovery_pycache_v1.py", b"# v1 preflight\n"
    )
    os.chmod(v1_control / "remediate_recovery_pycache_v1.py", 0o555)
    identities["attempt2-remediation-v1-control-copy"] = v1_runner
    monkeypatch.setattr(
        FREEZER, "EXPECTED_SUPERSEDED_REMEDIATION_V1_SHA256", v1_runner["sha256"]
    )

    dynamic_documents: dict[str, dict[str, object]] = {}
    dynamic_identities: dict[str, dict[str, object]] = {}
    dynamic_contracts: dict[str, dict[str, str]] = {}

    def store_dynamic(
        name: str, path: Path, schema: str, status: str, field: str, **extra: object
    ) -> tuple[dict[str, object], dict[str, object]]:
        document = _self_hashed(schema, status, field, **extra)
        identity = _write_json(path, document)
        os.chmod(path, 0o444)
        key = name.replace("-", "_")
        values[key] = path
        values[f"expected_{key}_sha256"] = identity["sha256"]
        values[f"{key}_schema"] = schema
        values[f"{key}_status"] = status
        values[f"{key}_self_hash_field"] = field
        dynamic_documents[name] = document
        dynamic_identities[name] = identity
        dynamic_contracts[name] = {
            "schema_version": schema,
            "status": status,
            "self_hash_field": field,
        }
        return document, identity

    external = {"inventory_sha256": "7" * 64}
    intent, intent_id = store_dynamic(
        "attempt2-intent", quarantine / "persistent-mount-namespace-intent-v3.json",
        "blind-phase-confirmatory-persistent-mount-namespace-v3",
        "intent_before_unshare", "namespace_contract_payload_sha256",
        attempt_id=FREEZER.ATTEMPT2_ID, external_inventory_before=external,
    )
    registration, registration_id = store_dynamic(
        "attempt2-registration",
        quarantine / "persistent-mount-namespace-registration-v3.json",
        "blind-phase-confirmatory-persistent-mount-namespace-v3",
        "keeper_registered_root_recursively_private",
        "namespace_contract_payload_sha256", attempt_id=FREEZER.ATTEMPT2_ID,
        intent_payload_sha256=intent["namespace_contract_payload_sha256"],
    )
    active, active_id = store_dynamic(
        "attempt2-active", quarantine / "persistent-mount-namespace-active-v3.json",
        "blind-phase-confirmatory-persistent-mount-namespace-v3",
        "active_root_recursively_private", "namespace_contract_payload_sha256",
        attempt_id=FREEZER.ATTEMPT2_ID,
        intent={**intent_id, "path": str(FREEZER.CANONICAL_ATTEMPT2_CONTROL / "persistent-mount-namespace-intent-v3.json")},
        registration={**registration_id, "path": str(FREEZER.CANONICAL_ATTEMPT2_CONTROL / "persistent-mount-namespace-registration-v3.json")},
        external_inventory_before=external, external_inventory_after=external,
        external_namespace_mount_delta=0,
        root_private_attestation={"make_rprivate_completed_before_project_bind": True},
    )
    for name, identity in (
        ("persistent-mount-namespace-intent-v3.json", intent_id),
        ("persistent-mount-namespace-registration-v3.json", registration_id),
        ("persistent-mount-namespace-active-v3.json", active_id),
    ):
        sidecar = quarantine / f"{name}.sha256"
        sidecar.write_text(f"{identity['sha256']}  {name}\n")
        os.chmod(sidecar, 0o444)
    identities["attempt2-builder-control-copy"] = _write_bytes(
        quarantine / "build_runtime_guard_v3.py", b"# attempt2 builder\n"
    )
    os.chmod(quarantine / "build_runtime_guard_v3.py", 0o555)
    for name, payload in (
        ("amendment-v3.tsq", b"tsq"),
        ("amendment-v3.tsr", b"tsr"),
        ("tsa-ca-certificates.pem", b"ca"),
    ):
        _write_bytes(quarantine / name, payload)
        os.chmod(quarantine / name, 0o444)
    quarantine_files: dict[str, dict[str, object]] = {}
    for name in set(FREEZER.ATTEMPT2_QUARANTINE_NAMES) - {"results-v3"}:
        payload = (quarantine / name).read_bytes()
        quarantine_files[name] = normalized_file(
            quarantine / name, hashlib.sha256(payload).hexdigest(), code=name.endswith(".py")
        )
    quarantine_inventory: dict[str, object] = {
        "root": normalized_directory(quarantine),
        "files": quarantine_files,
        "directories": {
            "results-v3": {**normalized_directory(quarantine / "results-v3"), "entry_count": 0}
        },
        "entry_count": len(FREEZER.ATTEMPT2_QUARANTINE_NAMES),
    }
    quarantine_inventory["tree_semantic_sha256"] = FREEZER.sha256_document(
        quarantine_inventory
    )

    # Previous TSQ/TSR CLI identities are logical reports copies with the same bytes.
    identities["previous-timestamp-query"] = _write_bytes(tmp_path / "previous.tsq", b"tsq")
    identities["previous-timestamp-reply"] = _write_bytes(tmp_path / "previous.tsr", b"tsr")
    fixed["EXPECTED_PREVIOUS_TSQ_SHA256"] = identities["previous-timestamp-query"]["sha256"]
    fixed["EXPECTED_PREVIOUS_TSR_SHA256"] = identities["previous-timestamp-reply"]["sha256"]
    monkeypatch.setattr(FREEZER, "EXPECTED_PREVIOUS_TSQ_SHA256", fixed["EXPECTED_PREVIOUS_TSQ_SHA256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_PREVIOUS_TSR_SHA256", fixed["EXPECTED_PREVIOUS_TSR_SHA256"])
    expected_paths = dict(FREEZER.EXPECTED_FIXED_PATHS)
    expected_paths["previous-timestamp-query"] = Path(identities["previous-timestamp-query"]["path"])
    expected_paths["previous-timestamp-reply"] = Path(identities["previous-timestamp-reply"]["path"])
    monkeypatch.setattr(FREEZER, "EXPECTED_FIXED_PATHS", expected_paths)

    prior_evidence = identities["attempt1-no-decoder-evidence"]
    logical = FREEZER.CANONICAL_ATTEMPT2_CONTROL
    incident, incident_id = store_dynamic(
        "attempt2-incident", recovery_root / "start-only-failure-incident-v2.json",
        "blind-phase-confirmatory-start-only-recovery-incident-v2",
        "COMMITTED_BEFORE_EXACT_KEEPER_PIDFD_TERMINATION", "incident_payload_sha256",
        attempt_id=FREEZER.ATTEMPT2_ID,
        bound_artifacts={
            "intent": {**intent_id, "path": str(logical / "persistent-mount-namespace-intent-v3.json")},
            "registration": {**registration_id, "path": str(logical / "persistent-mount-namespace-registration-v3.json")},
            "active": {**active_id, "path": str(logical / "persistent-mount-namespace-active-v3.json")},
            "builder_control_copy": {**identities["attempt2-builder-control-copy"], "path": str(logical / "build_runtime_guard_v3.py")},
            "amendment": identities["previous-amendment"],
            "independent_review": identities["previous-review"],
            "amendment_timestamp_query": {**identities["previous-timestamp-query"], "path": str(logical / "amendment-v3.tsq")},
            "amendment_timestamp_response": {**identities["previous-timestamp-reply"], "path": str(logical / "amendment-v3.tsr")},
            "prior_no_decoder_evidence": prior_evidence,
        },
        project_or_authority_activation_intent_absent=True,
        project_or_authority_activation_mounts_absent=True,
        activation_failure_context={
            "project_or_authority_activation_intent_published": False,
            "project_or_authority_activation_mounts_performed": False,
            "decoder_or_role_started": False, "iq_content_opened": False,
        },
        no_decoder_evidence={
            "active_decoder_or_role_processes": 0,
            "new_decoder_outputs_in_private_results": 0,
            "iq_contents_opened_by_failed_activation": False,
            "bound_prior_evidence": prior_evidence,
        },
        scientific_exposure=dict(FREEZER.RECOVERY_EXPOSURE),
    )
    failure, failure_id = store_dynamic(
        "attempt2-failure-lockout", recovery_root / "start-only-failure-lockout-v2.json",
        "blind-phase-confirmatory-start-only-recovery-v2",
        "FAIL_CLOSED_AFTER_COMMITTED_INCIDENT", "recovery_payload_sha256",
        attempt_id=FREEZER.ATTEMPT2_ID, incident=incident_id,
        pidfd_signal_committed=True, scientific_exposure=dict(FREEZER.RECOVERY_EXPOSURE),
    )
    pycache_root = remediation_root / "quarantine"
    pycache_root.mkdir()
    os.chmod(pycache_root, 0o755)
    pycache_file = pycache_root / "recover_partial_activation_attempt2_v3.cpython-312.pyc"
    pycache_file.write_bytes(b"pycache-forensic-bytes")
    os.chmod(pycache_file, 0o644)
    pycache_file_id = normalized_file(
        pycache_file, hashlib.sha256(pycache_file.read_bytes()).hexdigest()
    )
    pycache_root_id = normalized_directory(pycache_root)
    contamination = {
        "directory": {
            key: pycache_root_id[key]
            for key in ("st_dev", "st_ino", "uid", "gid", "mode", "nlink")
        },
        "file": {
            "name": pycache_file.name,
            **{
                key: pycache_file_id[key]
                for key in (
                    "size_bytes", "sha256", "st_dev", "st_ino", "uid", "gid",
                    "mode", "nlink",
                )
            },
        },
        "entry_count": 1,
    }
    pycache_inventory: dict[str, object] = {
        "root": pycache_root_id,
        "files": {pycache_file.name: pycache_file_id},
        "entry_count": 1,
    }
    pycache_inventory["tree_semantic_sha256"] = FREEZER.sha256_document(
        pycache_inventory
    )
    remediation_incident, remediation_incident_id = store_dynamic(
        "attempt2-remediation-incident",
        remediation_root / "agent-pycache-contamination-incident-v1.json",
        "blind-phase-confirmatory-agent-pycache-contamination-v1",
        "COMMITTED_BEFORE_EXACT_DIRECTORY_RENAME", "incident_payload_sha256",
        bound_recovery_history={
            "recovery_incident": incident_id,
            "recovery_failure": failure_id,
            "recovery_runner": identities["attempt2-recovery-runner"],
        },
        source=str(recovery_root / "__pycache__"), contamination=contamination,
        quarantine=str(pycache_root),
        scientific_exposure=dict(FREEZER.REMEDIATION_EXPOSURE),
    )
    remediation, remediation_id = store_dynamic(
        "attempt2-remediation-receipt",
        remediation_root / "agent-pycache-remediation-v1.json",
        "blind-phase-confirmatory-agent-pycache-remediation-v1", "PASS",
        "remediation_payload_sha256", incident=remediation_incident_id,
        remediation_runner=identities["attempt2-remediation-runner"],
        source_absent=True, quarantine=contamination,
        scientific_exposure=dict(FREEZER.REMEDIATION_EXPOSURE),
        external_namespace_zero_delta=True,
        external_namespace_inventory_before=external,
        external_namespace_inventory_after=external,
    )
    recovery, recovery_id = store_dynamic(
        "attempt2-recovery", recovery_root / "start-only-failure-recovery-v2.json",
        "blind-phase-confirmatory-start-only-recovery-v2", "PASS",
        "recovery_payload_sha256", attempt_id=FREEZER.ATTEMPT2_ID,
        incident=incident_id, failure_resume_authorization=failure_id,
        scientific_exposure=dict(FREEZER.RECOVERY_EXPOSURE),
        namespace_teardown={"private_namespace_absent": True},
        external_namespace_zero_delta={
            "before_inventory_sha256": "7" * 64,
            "after_inventory_sha256": "7" * 64,
            "byte_identical": True,
        },
        no_decoder_evidence={
            "active_decoder_or_role_processes_before": 0,
            "active_decoder_or_role_processes_after": 0,
            "pre_private_results_entry_count": 0, "iq_contents_opened": False,
            "prior": prior_evidence,
        },
    )

    dossier, dossier_id = store_dynamic(
        "attempt2-quarantine-provenance", dossier_path,
        FREEZER.ATTEMPT2_DOSSIER_SCHEMA, FREEZER.ATTEMPT2_DOSSIER_STATUS,
        "provenance_payload_sha256", attempt_id=FREEZER.ATTEMPT2_ID,
        created_at_utc="2026-09-06T03:00:00Z",
        generator=identities["attempt2-quarantine-provenance-generator"],
        frozen_protocol={
            "amendment": identities["previous-amendment"],
            "amendment_payload_sha256": previous["amendment_payload_sha256"],
            "independent_review": identities["previous-review"],
            "review_payload_sha256": old_review["review_payload_sha256"],
            "timestamp_query": quarantine_files["amendment-v3.tsq"],
            "timestamp_response": quarantine_files["amendment-v3.tsr"],
        },
        lifecycle_attempt={
            "artifacts": {
                name: quarantine_files[name] for name in quarantine_files
                if name.startswith("persistent-mount-namespace-")
            },
            "documents": {"intent": intent, "registration": registration, "active": active},
            "external_inventory_sha256": "7" * 64,
            "external_inventory_unchanged": True,
        },
        failed_activation={
            "incident": incident_id, "failure": failure_id,
            "incident_payload_sha256": incident["incident_payload_sha256"],
            "failure_payload_sha256": failure["recovery_payload_sha256"],
            "executed_historical_runner": identities["attempt2-recovery-runner"],
            "post_incident_corrected_runner_source_not_executed": identities["attempt2-recovery-runner"],
            "post_incident_corrected_runner_tests": identities["attempt2-recovery-runner-test"],
            "project_or_authority_activation_mounts_absent": True,
            "activation_intent_absent_from_quarantined_control_tree": True,
            "activation_intent_name": FREEZER.ATTEMPT2_FORBIDDEN_ACTIVATION_NAMES[0],
            "start_created_results_tmpfs_only": True,
        },
        audit_contamination_remediation={
            "intent": remediation_incident_id, "pass": remediation_id,
            "intent_payload_sha256": remediation_incident["incident_payload_sha256"],
            "pass_payload_sha256": remediation["remediation_payload_sha256"],
            "intent_contract": {
                "schema_version": "blind-phase-confirmatory-agent-pycache-contamination-v1",
                "status": "COMMITTED_BEFORE_EXACT_DIRECTORY_RENAME",
                "self_hash_field": "incident_payload_sha256",
            },
            "pass_contract": {
                "schema_version": "blind-phase-confirmatory-agent-pycache-remediation-v1",
                "status": "PASS", "self_hash_field": "remediation_payload_sha256",
            },
            "quarantined_pycache": pycache_inventory,
        },
        recovery={
            "pass": recovery_id,
            "recovery_payload_sha256": recovery["recovery_payload_sha256"],
            "pass_contract": {
                "schema_version": "blind-phase-confirmatory-start-only-recovery-v2",
                "status": "PASS", "self_hash_field": "recovery_payload_sha256",
            },
            "private_namespace_absent": True, "external_namespace_zero_delta": True,
        },
        quarantined_canonical_control_tree=quarantine_inventory,
        current_implementation={
            "attempt_builder_control_copy": quarantine_files["build_runtime_guard_v3.py"],
            "runtime_guard_builder_v3": identities["runtime-guard-builder"],
            "runtime_guard_builder_v3_tests": identities["runtime-guard-builder-test"],
        },
        no_decoder_evidence={
            "artifact": prior_evidence, "immutable_recovery_receipt": recovery_id,
            "role_started": False, "decoder_started": False,
            "outcome_generated": False, "quarantined_results_entry_count": 0,
        },
        iq_access_disclosure={
            "iq_source_records_were_inventory_metadata_only": True,
            "failed_activation_opened_copied_or_hashed_iq_bytes": False,
            "remediation_opened_copied_or_hashed_iq_bytes": False,
            "recovery_opened_copied_or_hashed_iq_bytes": False,
            "dossier_generator_accepts_iq_paths": False,
            "dossier_generator_opened_copied_or_hashed_iq_bytes": False,
        },
        scope={
            "publication_provenance_only": True, "runtime_guard_modified": False,
            "remediation_modified": False, "retry_freezer_modified": False,
            "scientific_protocol_or_results_changed": False,
        },
    )
    no_decoder, no_decoder_id = store_dynamic(
        "attempt2-no-decoder-evidence", no_decoder_path,
        FREEZER.ATTEMPT2_NO_DECODER_SCHEMA, "PASS", "evidence_payload_sha256",
        created_at_utc="2026-09-06T03:30:00Z", attempt_id=FREEZER.ATTEMPT2_ID,
        generator=identities["attempt2-no-decoder-evidence-generator"],
        dossier=dossier_id, dossier_sidecar={"path": str(dossier_path) + ".sha256"},
        dossier_payload_sha256=dossier["provenance_payload_sha256"],
        bindings={
            "previous_evidence": prior_evidence, "incident": incident_id,
            "failure": failure_id, "remediation_intent": remediation_incident_id,
            "remediation_pass": remediation_id, "recovery_pass": recovery_id,
            "quarantined_pycache": pycache_inventory,
            "quarantined_control_tree": quarantine_inventory,
        },
        checks={
            "quarantined_results_unit_count": 0, "role_artifact_count": 0,
            "decoder_output_count": 0, "outcome_count": 0,
            "active_decoder_or_role_process_count": 0,
            "lifecycle_intent_registration_active_revalidated": True,
            "incident_failure_remediation_recovery_chain_revalidated": True,
            "previous_no_decoder_evidence_bound": True,
            "executed_runner_distinguished_from_post_incident_correction": True,
            "quarantined_control_tree_semantic_sha256": quarantine_inventory["tree_semantic_sha256"],
            "quarantined_pycache_tree_semantic_sha256": pycache_inventory["tree_semantic_sha256"],
            "external_namespace_inventory_sha256": "7" * 64,
            "process_fixed_point": {
                "before": {"active_decoder_or_role_process_count": 0, "matches": []},
                "after": {"active_decoder_or_role_process_count": 0, "matches": []},
                "unchanged": True,
            },
        },
        iq_access_disclosure={
            "source_IQ_was_inventoried_as_metadata_only": True,
            "IQ_content_opened_by_generator": False,
            "IQ_content_copied_by_generator": False,
            "IQ_content_hashed_by_generator": False,
            "generator_accepts_IQ_path": False,
        },
        scientific_exposure={
            "new_unit_processed": False, "role_started": False,
            "decoder_started": False, "outcome_generated": False,
        },
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_DOSSIER_SHA256", dossier_id["sha256"]
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_DOSSIER_PAYLOAD_SHA256",
        dossier["provenance_payload_sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_NO_DECODER_SHA256", no_decoder_id["sha256"]
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_NO_DECODER_PAYLOAD_SHA256",
        no_decoder["evidence_payload_sha256"],
    )

    for name, identity in identities.items():
        key = name.replace("-", "_")
        values[key] = Path(str(identity["path"]))
        values[f"expected_{key}_sha256"] = identity["sha256"]
    values["attempt2_quarantine_root"] = quarantine
    values["attempt2_remediation_v1_control_root"] = v1_control

    _payload, freezer_identity = FREEZER.read_regular_bytes(
        SOURCE, maximum_bytes=FREEZER.MAX_CODE_BYTES
    )
    state = FREEZER._validate_attempt2_semantics(
        dynamic_documents, {**identities, **dynamic_identities},
        quarantine_root=quarantine,
        superseded_v1_control_root=v1_control,
        previous_amendment=previous,
        previous_review=old_review,
    )
    bindings = FREEZER._review_bindings(
        identities, dynamic_identities, dynamic_contracts, freezer_identity,
        {
            "quarantined_canonical_control_tree": state[0],
            "quarantined_agent_pycache_tree": state[1],
            "superseded_read_only_v1_preflight_control": state[2],
        },
    )
    review = _self_hashed(
        FREEZER.REVIEW_SCHEMA_VERSION, "GO", "review_payload_sha256",
        reviewed_bindings=bindings, checks=FREEZER.expected_review_checks(),
    )
    review_identity = _write_json(tmp_path / "review.json", review)
    values["review"] = Path(str(review_identity["path"]))
    values["expected_review_sha256"] = review_identity["sha256"]
    monkeypatch.setattr(FREEZER, "REVIEW_OUTPUT", Path(str(review_identity["path"])))
    values["output"] = tmp_path / "output.json"
    return argparse.Namespace(**values)


def _rewrite_dynamic(
    args: argparse.Namespace, name: str, mutate: object
) -> dict[str, object]:
    key = name.replace("-", "_")
    path = Path(getattr(args, key))
    os.chmod(path, 0o644)
    document = json.loads(path.read_text())
    mutate(document)
    field = getattr(args, f"{key}_self_hash_field")
    document[field] = FREEZER.sha256_document(
        {item: value for item, value in document.items() if item != field}
    )
    identity = _write_json(path, document)
    os.chmod(path, 0o444)
    setattr(args, f"expected_{key}_sha256", identity["sha256"])
    return {**document, "_test_file_sha256": identity["sha256"]}


def test_mirror_contract_is_exact_two_entry_runtime_contract() -> None:
    value = FREEZER.planned_artifact_mirror_contract()
    assert value["entry_count"] == 2
    assert len(value["entries"]) == 2
    assert value["planned_artifact_mirror_payload_sha256"] == FREEZER.EXPECTED_MIRROR_PAYLOAD_SHA256
    assert {Path(item["logical_source"]["path"]).name for item in value["entries"]} == {
        "cli.py", "manuscript.py"
    }


def test_live_immutable_previous_amendment_is_science_contract_compatible() -> None:
    path = ROOT / "reports/blind-phase-confirmatory-operational-amendment-v3.json"
    document, identity = FREEZER._json(
        path, FREEZER.EXPECTED_PREVIOUS_AMENDMENT_SHA256
    )
    assert identity["sha256"] == (
        "1d7b8624bf62b957118a02ebafec6851e98f02541d88d22afa0170784926c8dd"
    )
    document = FREEZER._self_hashed(
        document,
        schema=FREEZER.SCHEMA_VERSION,
        status=FREEZER.STATUS,
        field="amendment_payload_sha256",
    )
    FREEZER._validate_science(document)
    history = document["runtime_history_disclosure"]
    assert history["full_30_observation_cohort_runtime_history"] == "mixed_and_non_pristine"
    assert history["mandatory_sensitivity_observation_count"] == 29
    assert document["schedule"]["units_to_execute"] == 6166


def test_live_immutable_attempt1_extended_provenance_reference_is_compatible() -> None:
    previous_path = ROOT / "reports/blind-phase-confirmatory-operational-amendment-v3.json"
    previous, _ = FREEZER._json(
        previous_path, FREEZER.EXPECTED_PREVIOUS_AMENDMENT_SHA256
    )
    prior = previous["failed_v3_activation_attempt"]
    no_decoder = previous["no_decoder_execution_since_v2"]

    def identity(reference: dict[str, object], *, code: bool = False) -> dict[str, object]:
        return FREEZER._identity(
            Path(reference["path"]), str(reference["sha256"]), code=code
        )

    identities = {
        "attempt1-incident": identity(prior["incident"]),
        "attempt1-recovery": identity(prior["recovery"]),
        "attempt1-recovery-runner": identity(prior["recovery_runner"], code=True),
        "attempt1-recovery-runner-test": identity(
            prior["recovery_runner_tests"], code=True
        ),
        "attempt1-quarantine-provenance": identity(prior["provenance_dossier"]),
        "attempt1-no-decoder-evidence": identity(no_decoder["evidence"]),
        "attempt1-no-decoder-evidence-generator": identity(
            no_decoder["evidence_generator"], code=True
        ),
        "attempt1-no-decoder-evidence-generator-test": identity(
            no_decoder["evidence_generator_tests"], code=True
        ),
    }
    dossier, _ = FREEZER._json(
        Path(prior["provenance_dossier"]["path"]),
        str(prior["provenance_dossier"]["sha256"]),
    )
    extended_generator = dossier["generator"]
    assert set(extended_generator) > {"path", "size_bytes", "sha256"}
    identities["attempt1-quarantine-provenance-generator"] = identity(
        extended_generator, code=True
    )

    projection = FREEZER._attempt1_from_previous(previous, identities)
    assert projection["provenance_dossier"]["sha256"] == (
        "94ada32bdaf6813f10c11c4a83fb2015a02ea1263f8e023b71cf72cb6e073026"
    )
    FREEZER._validate_attempt1_provenance(
        argparse.Namespace(
            attempt1_quarantine_provenance=Path(prior["provenance_dossier"]["path"]),
            expected_attempt1_quarantine_provenance_sha256=prior[
                "provenance_dossier"
            ]["sha256"],
        ),
        identities,
    )


def test_live_attempt2_dossier_and_no_decoder_tree_encodings_crosslink() -> None:
    dossier_path = ROOT / (
        "reports/blind-phase-confirmatory-failed-v3-activation-attempt2-"
        "provenance-v2.json"
    )
    evidence_path = ROOT / (
        "reports/blind-phase-confirmatory-attempt2-no-decoder-evidence-v1.json"
    )
    dossier, dossier_identity = FREEZER._json(
        dossier_path, FREEZER.EXPECTED_ATTEMPT2_DOSSIER_SHA256
    )
    dossier = FREEZER._self_hashed(
        dossier, schema=FREEZER.ATTEMPT2_DOSSIER_SCHEMA,
        status=FREEZER.ATTEMPT2_DOSSIER_STATUS,
        field="provenance_payload_sha256",
    )
    evidence, _ = FREEZER._json(
        evidence_path, FREEZER.EXPECTED_ATTEMPT2_NO_DECODER_SHA256
    )
    evidence = FREEZER._self_hashed(
        evidence, schema=FREEZER.ATTEMPT2_NO_DECODER_SCHEMA,
        status="PASS", field="evidence_payload_sha256",
    )
    assert FREEZER._reference_matches(evidence["dossier"], dossier_identity)
    assert evidence["dossier_payload_sha256"] == dossier["provenance_payload_sha256"]
    dossier_tree = dossier["audit_contamination_remediation"]["quarantined_pycache"]
    evidence_tree = evidence["bindings"]["quarantined_pycache"]
    assert "directories" not in dossier_tree
    assert evidence_tree["directories"] == {}
    assert FREEZER._canonical_single_file_tree(dossier_tree) == (
        FREEZER._canonical_single_file_tree(evidence_tree)
    )


def test_live_attempt2_complete_semantic_preflight_is_compatible() -> None:
    previous, _ = FREEZER._json(
        FREEZER.EXPECTED_FIXED_PATHS["previous-amendment"],
        FREEZER.EXPECTED_PREVIOUS_AMENDMENT_SHA256,
    )
    previous_review, _ = FREEZER._json(
        FREEZER.EXPECTED_FIXED_PATHS["previous-review"],
        FREEZER.EXPECTED_PREVIOUS_REVIEW_SHA256,
    )
    dossier, _ = FREEZER._json(
        FREEZER.ATTEMPT2_DOSSIER_PATH, FREEZER.EXPECTED_ATTEMPT2_DOSSIER_SHA256
    )
    evidence, _ = FREEZER._json(
        FREEZER.ATTEMPT2_NO_DECODER_PATH,
        FREEZER.EXPECTED_ATTEMPT2_NO_DECODER_SHA256,
    )

    dynamic_paths = {
        "attempt2-intent": FREEZER.ATTEMPT2_QUARANTINE_ROOT / "persistent-mount-namespace-intent-v3.json",
        "attempt2-registration": FREEZER.ATTEMPT2_QUARANTINE_ROOT / "persistent-mount-namespace-registration-v3.json",
        "attempt2-active": FREEZER.ATTEMPT2_QUARANTINE_ROOT / "persistent-mount-namespace-active-v3.json",
        "attempt2-incident": FREEZER.ATTEMPT2_RECOVERY_ROOT / "start-only-failure-incident-v2.json",
        "attempt2-failure-lockout": FREEZER.ATTEMPT2_RECOVERY_ROOT / "start-only-failure-lockout-v2.json",
        "attempt2-remediation-incident": FREEZER.ATTEMPT2_REMEDIATION_ROOT / "agent-pycache-contamination-incident-v1.json",
        "attempt2-remediation-receipt": FREEZER.ATTEMPT2_REMEDIATION_ROOT / "agent-pycache-remediation-v1.json",
        "attempt2-recovery": FREEZER.ATTEMPT2_RECOVERY_ROOT / "start-only-failure-recovery-v2.json",
        "attempt2-quarantine-provenance": FREEZER.ATTEMPT2_DOSSIER_PATH,
        "attempt2-no-decoder-evidence": FREEZER.ATTEMPT2_NO_DECODER_PATH,
    }
    documents: dict[str, dict[str, object]] = {}
    identities: dict[str, dict[str, object]] = {}
    for name, path in dynamic_paths.items():
        schema, status, field = FREEZER.ATTEMPT2_REQUIRED_DYNAMIC_CONTRACTS[name]
        raw = json.loads(path.read_text())
        document, identity = FREEZER._json(
            path, hashlib.sha256(path.read_bytes()).hexdigest()
        )
        documents[name] = FREEZER._self_hashed(
            document, schema=schema, status=status, field=field
        )
        identities[name] = identity
        assert raw == document

    def live(reference: dict[str, object], *, code: bool = False) -> dict[str, object]:
        return FREEZER._identity(
            Path(reference["path"]), str(reference["sha256"]), code=code
        )

    first = previous["failed_v3_activation_attempt"]
    prior_no_decoder = previous["no_decoder_execution_since_v2"]
    first_dossier = json.loads(Path(first["provenance_dossier"]["path"]).read_text())
    remediation = documents["attempt2-remediation-receipt"]
    identities.update({
        "previous-amendment": live({
            "path": str(FREEZER.EXPECTED_FIXED_PATHS["previous-amendment"]),
            "size_bytes": FREEZER.EXPECTED_FIXED_PATHS["previous-amendment"].stat().st_size,
            "sha256": FREEZER.EXPECTED_PREVIOUS_AMENDMENT_SHA256,
        }),
        "previous-review": live({
            "path": str(FREEZER.EXPECTED_FIXED_PATHS["previous-review"]),
            "size_bytes": FREEZER.EXPECTED_FIXED_PATHS["previous-review"].stat().st_size,
            "sha256": FREEZER.EXPECTED_PREVIOUS_REVIEW_SHA256,
        }),
        "previous-timestamp-query": FREEZER._identity(
            FREEZER.EXPECTED_FIXED_PATHS["previous-timestamp-query"],
            FREEZER.EXPECTED_PREVIOUS_TSQ_SHA256,
        ),
        "previous-timestamp-reply": FREEZER._identity(
            FREEZER.EXPECTED_FIXED_PATHS["previous-timestamp-reply"],
            FREEZER.EXPECTED_PREVIOUS_TSR_SHA256,
        ),
        "attempt1-no-decoder-evidence": live(prior_no_decoder["evidence"]),
        "attempt2-builder-control-copy": live(
            dossier["current_implementation"]["attempt_builder_control_copy"], code=True
        ),
        "attempt2-recovery-runner": live(
            dossier["failed_activation"]["executed_historical_runner"], code=True
        ),
        "attempt2-remediation-runner": live(remediation["remediation_runner"], code=True),
        "attempt2-remediation-v1-control-copy": FREEZER._identity(
            FREEZER.SUPERSEDED_REMEDIATION_V1_CONTROL / "remediate_recovery_pycache_v1.py",
            FREEZER.EXPECTED_SUPERSEDED_REMEDIATION_V1_SHA256, code=True,
        ),
        "attempt2-quarantine-provenance-generator": live(dossier["generator"], code=True),
        "attempt2-no-decoder-evidence-generator": live(evidence["generator"], code=True),
        "runtime-guard-builder": live(
            dossier["current_implementation"]["runtime_guard_builder_v3"], code=True
        ),
        "runtime-guard-builder-test": live(
            dossier["current_implementation"]["runtime_guard_builder_v3_tests"], code=True
        ),
        "attempt1-quarantine-provenance-generator": live(first_dossier["generator"], code=True),
    })
    FREEZER._validate_attempt2_semantics(
        documents, identities,
        quarantine_root=FREEZER.ATTEMPT2_QUARANTINE_ROOT,
        superseded_v1_control_root=FREEZER.SUPERSEDED_REMEDIATION_V1_CONTROL,
        previous_amendment=previous,
        previous_review=previous_review,
    )


def test_single_file_tree_normalization_rejects_nonempty_or_extra_directories() -> None:
    root = {"path": "/root", "uid": 0}
    file_record = {"entry.pyc": {"path": "/root/entry.pyc", "sha256": "a" * 64}}
    base: dict[str, object] = {"root": root, "files": file_record, "entry_count": 1}
    base["tree_semantic_sha256"] = FREEZER.sha256_document(base)
    with pytest.raises(ValueError, match="directories are not exact empty"):
        FREEZER._canonical_single_file_tree({**base, "directories": {"unexpected": {}}})
    with pytest.raises(ValueError, match="unexpected keys"):
        FREEZER._canonical_single_file_tree({**base, "unexpected": False})


def test_build_preserves_science_and_binds_retry_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    assert document["schema_version"] == FREEZER.SCHEMA_VERSION
    assert document["status"] == FREEZER.STATUS
    assert document["schedule"]["unit_count"] == 6168
    assert document["schedule"]["units_to_execute"] == 6166
    assert document["sensitivity_exclusion_observation_ids"] == [4491]
    assert len(document["prior_decoder_exposures"]) == 2
    assert document["failed_v3_activation_attempts"]["attempt_count"] == 2
    assert document["failed_v3_activation_attempts"]["both_attempts_scientific_outcome_count"] == 0
    assert document["planned_artifact_mirror_contract"] == FREEZER.planned_artifact_mirror_contract()
    assert document["amended_implementation"]["runtime_guard_builder"]["sha256"] == args.expected_runtime_guard_builder_sha256
    unhashed = dict(document)
    assert unhashed.pop("amendment_payload_sha256") == FREEZER.sha256_document(unhashed)


@pytest.mark.parametrize("field", [
    "expected_previous_amendment_sha256",
    "expected_previous_review_sha256",
    "expected_previous_timestamp_query_sha256",
    "expected_previous_timestamp_reply_sha256",
    "expected_runtime_guard_builder_sha256",
    "expected_runtime_guard_builder_test_sha256",
    "expected_attempt2_remediation_v1_control_copy_sha256",
    "expected_attempt2_no_decoder_evidence_generator_sha256",
    "expected_attempt2_no_decoder_evidence_generator_test_sha256",
])
def test_compiled_fixed_identity_is_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    setattr(args, field, "a" * 64)
    with pytest.raises(ValueError, match="compiled exact identity"):
        FREEZER.build_amendment(args)


def test_attempt1_must_match_previous_embedded_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    replacement = _write_bytes(tmp_path / "other-runner.py", b"different")
    args.attempt1_recovery_runner = Path(str(replacement["path"]))
    args.expected_attempt1_recovery_runner_sha256 = replacement["sha256"]
    with pytest.raises(ValueError, match="attempt1 explicit identities"):
        FREEZER.build_amendment(args)


def test_dynamic_json_requires_exact_sha_schema_status_and_self_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    args.attempt2_recovery_status = "NOT_PASS"
    with pytest.raises(ValueError, match="declared contract is not exact"):
        FREEZER.build_amendment(args)


def test_dynamic_json_rejects_placeholder_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    args.expected_attempt2_recovery_sha256 = "0" * 64
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        FREEZER.build_amendment(args)


def test_attempt2_activation_absence_must_be_proven_inside_real_dossier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    def contradict(document: dict[str, object]) -> None:
        document["failed_activation"][
            "activation_intent_absent_from_quarantined_control_tree"
        ] = False

    changed = _rewrite_dynamic(args, "attempt2-quarantine-provenance", contradict)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_DOSSIER_SHA256", changed["_test_file_sha256"]
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_DOSSIER_PAYLOAD_SHA256",
        changed["provenance_payload_sha256"],
    )
    with pytest.raises(ValueError, match="provenance dossier cross-links"):
        FREEZER.build_amendment(args)


def test_disconnected_remediation_receipt_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)

    def disconnect(document: dict[str, object]) -> None:
        document["incident"] = {
            "path": "/unrelated.json", "size_bytes": 1, "sha256": "a" * 64,
        }

    _rewrite_dynamic(args, "attempt2-remediation-receipt", disconnect)
    with pytest.raises(ValueError, match="recovered non-scientific abort"):
        FREEZER.build_amendment(args)


def test_scientific_extra_in_no_decoder_evidence_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)

    def contradict(document: dict[str, object]) -> None:
        document["checks"]["decoder_started"] = True

    changed = _rewrite_dynamic(args, "attempt2-no-decoder-evidence", contradict)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_NO_DECODER_SHA256",
        changed["_test_file_sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_ATTEMPT2_NO_DECODER_PAYLOAD_SHA256",
        changed["evidence_payload_sha256"],
    )
    with pytest.raises(ValueError, match="scientific contradiction"):
        FREEZER.build_amendment(args)


def test_artifact_role_path_alias_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "same"
    path.write_bytes(b"x")
    identity = {"path": str(path), "size_bytes": 1, "sha256": "a" * 64}
    with pytest.raises(ValueError, match="artifact role/path alias"):
        FREEZER._validate_distinct_artifact_paths({"one": identity, "two": identity})


def test_artifact_role_hardlink_alias_is_rejected(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"x")
    os.link(first, second)
    identities = {
        "one": {"path": str(first), "size_bytes": 1, "sha256": "a" * 64},
        "two": {"path": str(second), "size_bytes": 1, "sha256": "a" * 64},
    }
    with pytest.raises(ValueError, match="artifact role/inode alias"):
        FREEZER._validate_distinct_artifact_paths(identities)


def test_quarantine_rejects_late_activation_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    forbidden = args.attempt2_quarantine_root / "readonly-project-activation-intent-v3.json"
    forbidden.write_text("{}\n")
    with pytest.raises(ValueError, match="quarantined canonical inventory"):
        FREEZER.build_amendment(args)


def test_superseded_v1_control_rejects_unrecorded_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    (args.attempt2_remediation_v1_control_root / "unexpected-pass.json").write_text("{}\n")
    with pytest.raises(ValueError, match="superseded remediation-v1 control inventory"):
        FREEZER.build_amendment(args)


def test_review_must_bind_every_exact_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    review = json.loads(args.review.read_text())
    review["reviewed_bindings"].pop("attempt2_recovery")
    review["review_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    identity = _write_json(args.review, review)
    args.expected_review_sha256 = identity["sha256"]
    with pytest.raises(ValueError, match="review is not exact GO"):
        FREEZER.build_amendment(args)


def test_science_regression_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    previous = json.loads(args.previous_amendment.read_text())
    previous["schedule"]["units_to_execute"] = 6167
    previous["amendment_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in previous.items() if key != "amendment_payload_sha256"}
    )
    identity = _write_json(args.previous_amendment, previous)
    args.expected_previous_amendment_sha256 = identity["sha256"]
    monkeypatch.setattr(FREEZER, "EXPECTED_PREVIOUS_AMENDMENT_SHA256", identity["sha256"])
    with pytest.raises(ValueError, match="scientific contract"):
        FREEZER.build_amendment(args)


def test_symlink_input_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _base_args(tmp_path, monkeypatch)
    alias = tmp_path / "runner-alias.py"
    alias.symlink_to(args.runtime_guard_builder)
    args.runtime_guard_builder = alias
    with pytest.raises(ValueError, match="canonical"):
        FREEZER.build_amendment(args)


@pytest.mark.skipif(os.geteuid() != 0, reason="O_TMPFILE AT_EMPTY_PATH publication needs root")
def test_publish_no_clobber_and_byte_identical_resume(tmp_path: Path) -> None:
    output = tmp_path / "amendment.json"
    document = {"status": "approved"}
    digest = FREEZER.publish_no_clobber(output, document)
    assert output.stat().st_mode & 0o777 == 0o444
    assert output.with_name(output.name + ".sha256").stat().st_mode & 0o777 == 0o444
    assert FREEZER.publish_no_clobber(output, document) == digest
    with pytest.raises(ValueError, match="different amendment output"):
        FREEZER.publish_no_clobber(output, {"status": "changed"})


@pytest.mark.skipif(os.geteuid() != 0, reason="O_TMPFILE AT_EMPTY_PATH publication needs root")
def test_publish_recovers_exact_sidecar_only_partial(tmp_path: Path) -> None:
    output = tmp_path / "amendment.json"
    document = {"status": "approved"}
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    digest = hashlib.sha256(payload).hexdigest()
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.write_text(f"{digest}  {output.name}\n")
    os.chmod(sidecar, 0o444)
    digest = FREEZER.publish_no_clobber(output, document)
    assert output.read_bytes() == payload
    assert sidecar.read_text() == f"{digest}  amendment.json\n"


def test_publish_rejects_orphan_sidecar(tmp_path: Path) -> None:
    output = tmp_path / "amendment.json"
    output.with_name(output.name + ".sha256").write_text("orphan\n")
    with pytest.raises(ValueError, match="foreign or inconsistent"):
        FREEZER.publish_no_clobber(output, {"status": "approved"})


def test_otmpfile_failure_leaves_no_named_staging_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_link(_descriptor: int, _directory: int, _name: str) -> None:
        raise RuntimeError("injected link failure")

    monkeypatch.setattr(FREEZER, "_link_tmpfile", fail_link)
    with pytest.raises(RuntimeError, match="injected link failure"):
        FREEZER._publish_one(tmp_path / "target.json", b"payload")
    assert tuple(tmp_path.iterdir()) == ()


def test_cleanup_exception_never_masks_primary_publication_error() -> None:
    primary = RuntimeError("primary")
    cleanup = OSError("cleanup")
    with pytest.raises(ExceptionGroup) as raised:
        FREEZER._finish_publication_cleanup(primary, [cleanup])
    assert raised.value.exceptions == (primary, cleanup)


def test_publish_rejects_writable_identical_partial(tmp_path: Path) -> None:
    output = tmp_path / "amendment.json"
    document = {"status": "approved"}
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.chmod(output, 0o644)
    with pytest.raises(ValueError, match="immutable publication metadata"):
        FREEZER.publish_no_clobber(output, document)


def test_cli_requires_every_dynamic_contract_and_has_no_iq_argument(
    capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as raised:
        FREEZER.parser().parse_args(["--help"])
    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    for option in (
        "--expected-attempt2-recovery-sha256",
        "--attempt2-recovery-schema",
        "--attempt2-recovery-status",
        "--attempt2-recovery-self-hash-field",
        "--expected-attempt2-remediation-receipt-sha256",
        "--expected-attempt2-quarantine-provenance-sha256",
        "--expected-attempt2-no-decoder-evidence-sha256",
        "--expected-runtime-guard-builder-sha256",
        "--expected-runtime-guard-builder-test-sha256",
        "--attempt2-quarantine-root",
        "--attempt2-remediation-v1-control-root",
    ):
        assert option in help_text
    assert "--iq" not in help_text


def test_review_template_exact_fail_closed_shape() -> None:
    document = json.loads(TEMPLATE.read_text())
    assert document["schema_version"] == FREEZER.REVIEW_SCHEMA_VERSION
    assert document["status"] == "REVIEW_REQUIRED"
    assert document["review_payload_sha256"] is None
    assert set(document["checks"]) == set(FREEZER.expected_review_checks())
    assert all(value is None for value in document["checks"].values())
    assert all(value is None for value in document["reviewed_bindings"].values())
    expected_bindings = {
        name.replace("-", "_") for name in FREEZER.STATIC_NAMES
    } | {
        name.replace("-", "_") for name in FREEZER.DYNAMIC_JSON_NAMES
    } | {
        "attempt2_declared_contracts", "retry_freezer",
        "planned_artifact_mirror_contract", "attempt2_validated_state",
    }
    assert set(document["reviewed_bindings"]) == expected_bindings
