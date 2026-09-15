from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/freeze_operational_amendment_v3.py"
EVALUATOR_SCRIPT = (
    ROOT / "work/blind-phase-confirmatory-v2/evaluate_campaign_amended_v3.py"
)
REVIEW_TEMPLATE = (
    ROOT
    / "work/blind-phase-confirmatory-v2/operational-amendment-review-template-v3.json"
)


def _module():
    name = "blind_phase_operational_amendment_v3_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FREEZER = _module()


def _evaluator_module():
    name = "blind_phase_operational_amendment_v3_cross_evaluator"
    spec = importlib.util.spec_from_file_location(name, EVALUATOR_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EVALUATOR = _evaluator_module()


def _self_hashed(schema: str, field: str, **values: object) -> dict[str, object]:
    document = {"schema_version": schema, **values}
    document[field] = FREEZER.sha256_document(document)
    return document


def _write(path: Path, value: object) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    payload = path.read_bytes()
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _full_identity(path: Path) -> dict[str, object]:
    status = path.lstat()
    payload = path.read_bytes()
    return {
        "path": str(path.absolute()),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "st_dev": status.st_dev,
        "st_ino": status.st_ino,
        "uid": status.st_uid,
        "gid": status.st_gid,
        "mode": format(stat.S_IMODE(status.st_mode), "04o"),
        "nlink": status.st_nlink,
    }


def _directory_identity(path: Path, **extra: object) -> dict[str, object]:
    status = path.lstat()
    return {
        "path": str(path.absolute()),
        "st_dev": status.st_dev,
        "st_ino": status.st_ino,
        "uid": status.st_uid,
        "gid": status.st_gid,
        "mode": format(stat.S_IMODE(status.st_mode), "04o"),
        "nlink": status.st_nlink,
        **extra,
    }


def _recovery_inventory() -> dict[str, object]:
    project = str(FREEZER.PROJECT_ROOT)

    def snapshot(target_count: int) -> list[dict[str, object]]:
        result = []
        for index in range(11):
            lines = [f"{900 + index} 1 0:5 / /proc rw - proc proc rw"]
            for offset in range(target_count if index < 10 else 0):
                mount_id = 1000 + index * 10 + offset
                point = project if offset == 0 else f"{project}/runtime-{offset}"
                lines.append(
                    f"{mount_id} 1 252:0 / {point} rw - ext4 /dev/root rw"
                )
            result.append({"namespace_inode": 5000 + index, "mountinfo": lines})
        return result

    before = snapshot(3)
    candidate = snapshot(1)
    after = snapshot(0)
    return {
        "before": before,
        "after_candidate_cleanup": candidate,
        "after": after,
        "before_sha256": FREEZER.sha256_document(before),
        "after_sha256": FREEZER.sha256_document(after),
        "all_live_mount_namespaces_clear": True,
    }


def _bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    files: dict[str, Path] = {}
    implementation_names = (
        "no_decoder_generator", "no_decoder_generator_test",
        "launcher_v3", "launcher_v3_test",
        "runtime_guard_builder_v3", "runtime_guard_builder_v3_test",
        "evaluator_v3", "evaluator_lock_freezer_v2", "evaluator_v3_test",
        "evaluator_lock_freezer_v2_test",
        "sealed_tmpfs_inputs_v3_tests", "private_authorities_v3_tests",
        "small_artifact_authority_v3_tests", "results_export_v3_tests",
        "normalizer_v2", "component_closure_generator", "component_closure_test",
        "amendment_v3_test",
    )
    canonical_names = {
        "launcher_v3": "campaign_launcher_amended_v3.py",
        "runtime_guard_builder_v3": "build_runtime_guard_v3.py",
        "evaluator_v3": "evaluate_campaign_amended_v3.py",
        "evaluator_lock_freezer_v2": "freeze_amended_evaluator_lock_v2.py",
        "sealed_tmpfs_inputs_v3_tests": "test_blind_phase_sealed_tmpfs_inputs_v3.py",
        "private_authorities_v3_tests": "test_blind_phase_private_authorities_v3.py",
        "small_artifact_authority_v3_tests": "test_blind_phase_small_artifact_authority_v3.py",
        "results_export_v3_tests": "test_blind_phase_results_export_v3.py",
        "normalizer_v2": "normalize_result_amended_v2.py",
    }
    for index, name in enumerate(implementation_names):
        path = tmp_path / canonical_names.get(name, f"{name}.py")
        path.write_text(f"# reviewed v3 artifact {index}\n", encoding="utf-8")
        files[name] = path
    identities = {name: _write(path, path.read_bytes()) for name, path in files.items()}

    plan_identity = {"path": "/plan.json", "size_bytes": 1, "sha256": "1" * 64}
    acquisition_identity = {"path": "/acq.json", "size_bytes": 1, "sha256": "2" * 64}
    source_identity = {"path": "/source.json", "size_bytes": 1, "sha256": "3" * 64}
    exposures = [
        {"unit_id": value * 64, "observation_id": 4491, "decoder_rerun_permitted": False}
        for value in "ab"
    ]
    runtime_history = {
        "remaining_decoder_run_count_with_full_component_closure_bound": 6166,
        "prior_exposed_decoder_unit_count": 2,
        "prior_exposed_observation_ids": [4491],
        "full_component_closure_for_prior_exposures_cryptographically_established": False,
        "full_30_observation_cohort_runtime_history": "mixed_and_non_pristine",
        "full_30_observation_cohort_retained_with_disclosure": True,
        "mandatory_sensitivity_observation_count": 29,
        "mandatory_sensitivity_excludes_entire_observation_ids": [4491],
    }
    expected_inventory = {
        "root": "/old-output",
        "directory_count": 9,
        "file_count": 6,
        "exposed_decoder_unit_count": 2,
        "new_decoder_unit_count": 0,
        "files": {str(index): {"sha256": str(index) * 64} for index in range(6)},
    }
    expected_inventory["inventory_sha256"] = FREEZER.sha256_document(
        expected_inventory
    )
    amendment_v2 = _self_hashed(
        "blind-phase-confirmatory-operational-amendment-v2",
        "amendment_payload_sha256",
        status="approved_after_acquisition_before_remaining_decoder_execution",
        execution_plan=plan_identity,
        acquisition_manifest=acquisition_identity,
        source_manifest=source_identity,
        schedule={
            "unit_count": 6168,
            "scientific_schedule_changed": False,
            "units_to_execute": 6166,
            "units_re_normalized_without_decoder_rerun": 2,
        },
        prior_decoder_exposures=exposures,
        sensitivity_analysis={
            "required": True,
            "exclude_observation_ids": [4491],
            "full_frozen_cohort_analysis_retained": True,
            "report_both_without_substitution": True,
        },
        sensitivity_exclusion_observation_ids=[4491],
        runtime_history_disclosure=runtime_history,
        no_decoder_execution_since_v1={
            "unchanged_output_inventory": expected_inventory,
        },
        operational_scope={"only_additional_changes": ["v2 hardening"]},
    )
    amendment_v2_path = tmp_path / "amendment-v2.json"
    amendment_v2_identity = _write(amendment_v2_path, amendment_v2)
    timestamp_query_path = tmp_path / "amendment-v2.tsq"
    timestamp_reply_path = tmp_path / "amendment-v2.tsr"
    timestamp_query_identity = _write(timestamp_query_path, b"query")
    timestamp_reply_identity = _write(timestamp_reply_path, b"reply")
    incident = _self_hashed(
        "blind-phase-confirmatory-activation-incident-corrected-v1",
        "incident_payload_sha256",
        status="fail_closed_before_seal_or_decoder",
        scientific_exposure={
            "iq_opened": False,
            "decoder_started": False,
            "seal_started": False,
            "runtime_root_swapped": False,
            "runtime_guard_frozen": False,
        },
        recovery_policy={"v2_campaign_resume_forbidden": True},
    )
    incident_path = tmp_path / "incident.json"
    incident_identity = _write(incident_path, incident)
    runner_path = tmp_path / "recover_activation_mounts_v11.py"
    runner_test_path = tmp_path / "test_recover_activation_mounts_v11.py"
    runner_identity = _write(runner_path, b"# runner v11\n")
    runner_test_identity = _write(runner_test_path, b"# runner v11 tests\n")
    intent = _self_hashed(
        FREEZER.RECOVERY_INTENT_SCHEMA_VERSION,
        "recovery_payload_sha256",
        status="committed_before_first_unmount",
    )
    intent_path = tmp_path / "intent.json"
    intent_identity = _write(intent_path, intent)
    candidate = _self_hashed(
        FREEZER.RECOVERY_CANDIDATE_SCHEMA_VERSION,
        "recovery_payload_sha256",
        status="PASS",
        transition="executed",
        intent=intent_identity,
        scientific_exposure=dict(FREEZER.NO_SCIENTIFIC_EXPOSURE),
        unrelated_mountinfo_records_byte_stable=True,
    )
    candidate_path = tmp_path / "candidate.json"
    candidate_identity = _write(candidate_path, candidate)
    refusal_exposure = dict(FREEZER.NO_SCIENTIFIC_EXPOSURE)
    refusal_exposure.update(
        {"candidate_unmount_succeeded": True, "project_unmount_attempted": False}
    )
    refusal = _self_hashed(
        FREEZER.RECOVERY_REFUSAL_SCHEMA_VERSION,
        "incident_payload_sha256",
        status="fail_closed_after_candidate_cleanup_before_project_unmount",
        go_review_revoked_for_retry=True,
        scientific_exposure=refusal_exposure,
    )
    refusal_path = tmp_path / "refusal.json"
    refusal_identity = _write(refusal_path, refusal)
    resume = _self_hashed(
        FREEZER.RECOVERY_RESUME_SCHEMA_VERSION,
        "recovery_payload_sha256",
        status="authorized_post_command_complete_project_only_after_v3_candidate_cleanup",
        resume_scope="project_reference_poll_and_project_unmount_only_no_candidate_transition",
        v3_intent=intent_identity,
        v3_candidate_phase=candidate_identity,
        v3_refusal_incident=refusal_identity,
        runtime_recovery_runner_v11=runner_identity,
        scientific_exposure=dict(FREEZER.NO_SCIENTIFIC_EXPOSURE),
    )
    resume_path = tmp_path / "resume.json"
    resume_identity = _write(resume_path, resume)
    recovery_plan = _self_hashed(
        FREEZER.RECOVERY_PLAN_V11_SCHEMA_VERSION,
        "recovery_plan_payload_sha256",
        status="frozen_pending_independent_go",
        working_directory="/",
        runtime_recovery_runner_v11={
            "sha256": runner_identity["sha256"],
            "test_sha256": runner_test_identity["sha256"],
        },
        direct_immutable_dependencies=[
            intent_identity, candidate_identity, refusal_identity
        ],
    )
    recovery_plan_path = tmp_path / "recovery-plan-v11.json"
    recovery_plan_identity = _write(recovery_plan_path, recovery_plan)
    pre_review = _self_hashed(
        FREEZER.RECOVERY_PRE_REVIEW_V11_SCHEMA_VERSION,
        "review_payload_sha256",
        status="GO",
        execution_authority=True,
        reviewed_plan=recovery_plan_identity,
        reviewed_runner={"sha256": runner_identity["sha256"]},
        reviewed_tests={"sha256": runner_test_identity["sha256"]},
        severity_counts={"P0": 0, "P1": 0, "P2": 0},
        verdict={"go": True},
    )
    pre_review_path = tmp_path / "pre-review-v11.json"
    pre_review_identity = _write(pre_review_path, pre_review)
    ready = _self_hashed(
        FREEZER.RECOVERY_PROJECT_READY_SCHEMA_VERSION,
        "recovery_payload_sha256",
        status="PASS",
        intent=intent_identity,
        candidate_phase=candidate_identity,
        project_reference_count=0,
        scientific_exposure=dict(FREEZER.NO_SCIENTIFIC_EXPOSURE),
    )
    ready_path = tmp_path / "project-ready.json"
    ready_identity = _write(ready_path, ready)
    recovery = _self_hashed(
        FREEZER.RECOVERY_SCHEMA_VERSION,
        "recovery_payload_sha256",
        status="PASS",
        intent=intent_identity,
        candidate_phase=candidate_identity,
        project_ready=ready_identity,
        transition="executed",
        command={
            "argv": ["/usr/bin/umount", "--no-canonicalize", str(FREEZER.PROJECT_ROOT)],
            "exit_code": 0,
            "stdout_size_bytes": 0,
            "stderr_size_bytes": 0,
        },
        namespace_inventory=_recovery_inventory(),
        recovery_policy={
            "force_or_lazy": False,
            "recursive": False,
            "nsenter_mutation": False,
            "v2_artifacts_preserved": True,
        },
        scientific_exposure={
            "iq_contents_opened": False,
            "decoder_started": False,
            "seal_started": False,
            "runtime_root_swapped": False,
            "runtime_guard_frozen": False,
        },
        metadata_only_preservation_gate={
            "iq_contents_read": False,
            "target_identities_before": {"project": "same"},
            "target_identities_after": {"project": "same"},
            "target_identities_unchanged": True,
        },
        unrelated_mountinfo_records_byte_stable=True,
    )
    recovery_path = tmp_path / "recovery.json"
    recovery_identity = _write(recovery_path, recovery)
    recovery_post_review = _self_hashed(
        FREEZER.RECOVERY_POST_REVIEW_SCHEMA_VERSION,
        "review_payload_sha256",
        status="GO",
        reviewed_execution_artifacts={
            "resume": resume_identity,
            "project_ready": ready_identity,
            "project_complete": recovery_identity,
        },
        live_namespace_validation={
            "double_capture_equal": True,
            "live_and_pinned_equal": True,
            "exactly_matches_project_complete_journal": True,
            "live_mount_namespace_count": 11,
            "exact_project_mount_count": 0,
            "exact_candidate_runtime_r4_mount_count": 0,
            "project_descendant_mount_count": 0,
            "candidate_descendant_mount_count": 0,
            "all_unrelated_mountinfo_records_byte_identical_to_journal": True,
        },
        target_identity_validation={"unchanged": True},
        command_receipt={
            "argv": ["/usr/bin/umount", "--no-canonicalize", str(FREEZER.PROJECT_ROOT)],
            "exit_code": 0,
            "stdout_size_bytes": 0,
            "stderr_size_bytes": 0,
            "exact_receipt_validated": True,
        },
        scientific_exposure=dict(FREEZER.NO_SCIENTIFIC_EXPOSURE),
        severity_counts={"P0": 0, "P1": 0, "P2": 0},
        verdict={
            "go": True,
            "recovery_v11_execution_valid": True,
            "post_execution_state_valid": True,
            "further_recovery_mount_mutation_required": False,
        },
    )
    recovery_post_review_path = tmp_path / "post-review-v11.json"
    recovery_post_review_identity = _write(
        recovery_post_review_path, recovery_post_review
    )
    closure = _self_hashed(
        "blind-phase-confirmatory-component-closure-v1",
        "manifest_payload_sha256",
        status="complete",
        content_manifest_sha256="5" * 64,
        raw_manifest_sha256="6" * 64,
        entry_count=26003,
    )
    closure_path = tmp_path / "component-closure.json"
    closure_identity = _write(closure_path, closure)
    evidence = _self_hashed(
        FREEZER.EVIDENCE_SCHEMA_VERSION,
        "evidence_payload_sha256",
        status="PASS",
        generated_at_utc="2026-09-04T20:00:00Z",
        generator=identities["no_decoder_generator"],
        amendment_v2=amendment_v2_identity,
        amendment_v2_timestamp={
            "status": "PASS",
            "query": timestamp_query_identity,
            "response": timestamp_reply_identity,
            "message_imprint_sha256": amendment_v2_identity["sha256"],
        },
        failed_activation_incident=incident_identity,
        recovery_v11_chain={
            "intent_v3": intent_identity,
            "candidate_phase_v3": candidate_identity,
            "refusal_incident_v3": refusal_identity,
            "resume_authorization_v11": resume_identity,
            "project_only_plan_v11": recovery_plan_identity,
            "pre_execution_review_v11": pre_review_identity,
            "project_ready_v3": ready_identity,
            "project_complete_v3": recovery_identity,
            "post_execution_review_v11": recovery_post_review_identity,
            "runner_v11": runner_identity,
            "runner_v11_tests": runner_test_identity,
        },
        campaign_output_inventory=expected_inventory,
        checks={
            "v2_rfc3161_timestamp_verified": True,
            "corrected_incident_bound": True,
            "incident_preceded_seal_iq_and_decoder": True,
            "complete_recovery_v11_chain_bound": True,
            "recovery_v11_pre_execution_review_go_p0_p1_p2_zero": True,
            "namespace_complete_recovery_v11_passed": True,
            "immutable_post_v11_review_go_p0_p1_p2_zero": True,
            "post_v11_live_namespace_count": 11,
            "post_v11_project_or_candidate_mount_count": 0,
            "post_v11_target_identities_unchanged": True,
            "exact_two_units_six_files_unchanged": True,
            "new_decoder_output_count_since_v2": 0,
            "active_decoder_process_count": 0,
            "iq_opened_by_generator": False,
            "candidate_or_component_runtime_opened_by_generator": False,
        },
        claim_scope={
            "proves_no_new_persisted_decoder_outputs_since_v2_timestamp": True,
            "binds_complete_independently_reviewed_recovery_v11_chain": True,
            "does_not_claim_runtime_closure_for_two_prior_exposures": True,
            "v2_campaign_resume_permitted": False,
        },
    )
    evidence_path = tmp_path / "no-decoder-evidence.json"
    evidence_identity = _write(evidence_path, evidence)

    partial_runner_path = tmp_path / "recover_partial_activation_v3.py"
    partial_runner_test_path = tmp_path / "test_partial_activation_recovery_v3.py"
    partial_runner_identity = _write(partial_runner_path, b"# partial recovery\n")
    partial_runner_test_identity = _write(
        partial_runner_test_path, b"# partial recovery tests\n"
    )
    partial_incident = _self_hashed(
        FREEZER.PARTIAL_V3_INCIDENT_SCHEMA_VERSION,
        "incident_payload_sha256",
        status="COMMITTED_BEFORE_EXACT_KEEPER_PIDFD_TERMINATION",
        scientific_exposure={
            "decoder_started_by_recovery": False,
            "iq_contents_opened_by_recovery": False,
            "launcher_or_evaluator_started_by_recovery": False,
            "seal_started_by_recovery": False,
        },
    )
    partial_incident_path = tmp_path / "partial-v3-incident.json"
    partial_incident_identity = _write(partial_incident_path, partial_incident)
    partial_recovery = _self_hashed(
        FREEZER.PARTIAL_V3_RECOVERY_SCHEMA_VERSION,
        "recovery_payload_sha256",
        status="PASS",
        completed_at_utc="2026-09-04T20:00:10Z",
        incident=partial_incident_identity,
        keeper_termination={
            "exact_generation_absent": True,
            "namespace_holder_count_after": 0,
        },
        namespace_teardown={"private_namespace_absent": True},
        external_namespace_zero_delta={"byte_identical": True},
        no_decoder_evidence={
            "active_decoder_or_role_processes_before": 0,
            "active_decoder_or_role_processes_after": 0,
            "pre_private_results_entry_count": 0,
            "prior": evidence_identity,
        },
        scientific_exposure={
            "decoder_started_by_recovery": False,
            "iq_contents_opened_by_recovery": False,
            "launcher_or_evaluator_started_by_recovery": False,
            "seal_started_by_recovery": False,
        },
    )
    partial_recovery_path = tmp_path / "partial-v3-recovery.json"
    partial_recovery_identity = _write(partial_recovery_path, partial_recovery)

    quarantine = tmp_path / "telemetry-yield-confirmatory-v3-aborted-test"
    publication = quarantine / "publication-v3-original"
    results = quarantine / "results-v3"
    sealed_input = quarantine / "sealed-input-v3"
    sealed_project = quarantine / "sealed-project-artifacts-v3"
    for directory in (publication, results, sealed_input, sealed_project):
        directory.mkdir(parents=True, exist_ok=True)
    os.chmod(quarantine, 0o755)
    os.chmod(publication, 0o755)
    os.chmod(results, 0o755)
    os.chmod(sealed_input, 0o700)
    os.chmod(sealed_project, 0o700)
    quarantine_names = (
        "amendment-v3.tsq", "amendment-v3.tsr", "build_runtime_guard_v3.py",
        "persistent-mount-namespace-active-v3.json",
        "persistent-mount-namespace-active-v3.json.sha256",
        "persistent-mount-namespace-intent-v3.json",
        "persistent-mount-namespace-intent-v3.json.sha256",
        "persistent-mount-namespace-registration-v3.json",
        "persistent-mount-namespace-registration-v3.json.sha256",
        "readonly-project-activation-intent-v3.json",
        "readonly-project-activation-intent-v3.json.sha256",
        "tsa-ca-certificates.pem",
        "publication-v3-original/blind-phase-confirmatory-operational-amendment-v3-independent-review.json",
        "publication-v3-original/blind-phase-confirmatory-operational-amendment-v3.json",
        "publication-v3-original/blind-phase-confirmatory-operational-amendment-v3.json.sha256",
        "publication-v3-original/blind-phase-confirmatory-operational-amendment-v3.tsq",
        "publication-v3-original/blind-phase-confirmatory-operational-amendment-v3.tsr",
    )
    quarantine_files: dict[str, object] = {}
    for index, relative in enumerate(quarantine_names):
        path = quarantine / relative
        path.write_bytes(f"quarantined {index}\n".encode())
        os.chmod(path, 0o444)
        quarantine_files[relative] = _full_identity(path)
    quarantine_directories = {
        "publication-v3-original": _directory_identity(publication),
        "results-v3": _directory_identity(results, entry_count=0),
        "sealed-input-v3": _directory_identity(
            sealed_input, contents_opened_by_generator=False
        ),
        "sealed-project-artifacts-v3": _directory_identity(
            sealed_project, contents_opened_by_generator=False
        ),
    }
    provenance_generator = identities["no_decoder_generator"]
    dossier = _self_hashed(
        FREEZER.FAILED_V3_PROVENANCE_SCHEMA_VERSION,
        "provenance_payload_sha256",
        status="PASS_METADATA_PROVENANCE_ONLY",
        created_at_utc="2026-09-04T20:00:20Z",
        generator=provenance_generator,
        recovery={
            "incident": partial_incident_identity,
            "incident_payload_sha256": partial_incident["incident_payload_sha256"],
            "pass_result": partial_recovery_identity,
            "recovery_payload_sha256": partial_recovery["recovery_payload_sha256"],
            "runner": partial_runner_identity,
            "private_namespace_absent": True,
            "external_namespace_zero_delta": True,
        },
        current_implementation={
            "current_bytes_are_distinct_from_quarantined_failed_builder": True,
            "runtime_guard_builder_v3": identities["runtime_guard_builder_v3"],
            "runtime_guard_builder_v3_tests": identities[
                "runtime_guard_builder_v3_test"
            ],
        },
        exposure_disclosure={
            "campaign_or_evaluator_role_started": False,
            "decoder_process_started": False,
            "generator_opened_candidate_or_component_runtime_contents": False,
            "generator_opened_iq_contents": False,
            "iq_was_mechanically_read_and_copied_into_private_sealed_input_tmpfs": True,
            "iq_was_used_as_decoder_input": False,
            "private_results_entry_count_before_recovery": 0,
            "scientific_outcome_generated": False,
        },
        quarantined_failed_attempt={
            "root": _directory_identity(quarantine),
            "directories": quarantine_directories,
            "file_count": len(quarantine_files),
            "files": quarantine_files,
        },
        reuse_policy={
            "future_lifecycle_requires_fresh_control_and_results_namespaces": True,
            "prior_attempt_artifacts_must_not_be_reused": True,
            "prior_attempt_control_artifacts_are_quarantined": True,
            "this_dossier_is_publication_evidence_not_runtime_authority": True,
        },
    )
    dossier_path = tmp_path / "failed-v3-provenance.json"
    dossier_identity = _write(dossier_path, dossier)

    monkeypatch.setattr(FREEZER, "FAILED_V3_QUARANTINE_ROOT", quarantine)

    monkeypatch.setattr(FREEZER, "EXPECTED_AMENDMENT_V2_SHA256", amendment_v2_identity["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_TIMESTAMP_QUERY_SHA256", timestamp_query_identity["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_TIMESTAMP_REPLY_SHA256", timestamp_reply_identity["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_INCIDENT_SHA256", incident_identity["sha256"])
    compiled_recovery = {
        "RECOVERY_INTENT": (intent_identity, intent["recovery_payload_sha256"]),
        "RECOVERY_CANDIDATE": (candidate_identity, candidate["recovery_payload_sha256"]),
        "RECOVERY_REFUSAL": (refusal_identity, refusal["incident_payload_sha256"]),
        "RECOVERY_RESUME": (resume_identity, resume["recovery_payload_sha256"]),
        "RECOVERY_PLAN_V11": (recovery_plan_identity, recovery_plan["recovery_plan_payload_sha256"]),
        "RECOVERY_PRE_REVIEW_V11": (pre_review_identity, pre_review["review_payload_sha256"]),
        "RECOVERY_PROJECT_READY": (ready_identity, ready["recovery_payload_sha256"]),
        "RECOVERY": (recovery_identity, recovery["recovery_payload_sha256"]),
        "RECOVERY_POST_REVIEW": (recovery_post_review_identity, recovery_post_review["review_payload_sha256"]),
    }
    for stem, (identity, payload) in compiled_recovery.items():
        monkeypatch.setattr(FREEZER, f"EXPECTED_{stem}_SHA256", identity["sha256"])
        monkeypatch.setattr(FREEZER, f"EXPECTED_{stem}_PAYLOAD_SHA256", payload)
    monkeypatch.setattr(FREEZER, "EXPECTED_RECOVERY_RUNNER_V11_SHA256", runner_identity["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_RECOVERY_RUNNER_V11_TEST_SHA256", runner_test_identity["sha256"])
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PARTIAL_V3_INCIDENT_SHA256",
        partial_incident_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PARTIAL_V3_INCIDENT_PAYLOAD_SHA256",
        partial_incident["incident_payload_sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PARTIAL_V3_RECOVERY_SHA256",
        partial_recovery_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PARTIAL_V3_RECOVERY_PAYLOAD_SHA256",
        partial_recovery["recovery_payload_sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PARTIAL_V3_RECOVERY_TOOL_SHA256",
        partial_runner_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PARTIAL_V3_RECOVERY_TOOL_TEST_SHA256",
        partial_runner_test_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_FAILED_V3_PROVENANCE_SHA256",
        dossier_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_FAILED_V3_PROVENANCE_PAYLOAD_SHA256",
        dossier["provenance_payload_sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_FAILED_V3_PROVENANCE_GENERATOR_SHA256",
        provenance_generator["sha256"],
    )
    monkeypatch.setattr(FREEZER, "EXPECTED_NORMALIZER_V2_SHA256", identities["normalizer_v2"]["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_COMPONENT_CLOSURE_SHA256", closure_identity["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_COMPONENT_CLOSURE_GENERATOR_SHA256", identities["component_closure_generator"]["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_COMPONENT_CLOSURE_CONTENT_SHA256", "5" * 64)
    monkeypatch.setattr(FREEZER, "EXPECTED_COMPONENT_CLOSURE_RAW_SHA256", "6" * 64)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_SEALED_TMPFS_INPUTS_V3_TEST_SHA256",
        identities["sealed_tmpfs_inputs_v3_tests"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PRIVATE_AUTHORITIES_V3_TEST_SHA256",
        identities["private_authorities_v3_tests"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_SMALL_ARTIFACT_AUTHORITY_V3_TEST_SHA256",
        identities["small_artifact_authority_v3_tests"]["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_RESULTS_EXPORT_V3_TEST_SHA256",
        identities["results_export_v3_tests"]["sha256"],
    )

    freezer_identity = FREEZER.read_regular_bytes(SCRIPT)[1]
    frozen_launcher_identity = FREEZER.read_regular_bytes(
        ROOT / "work/blind-phase-confirmatory-v2/campaign_launcher.py"
    )[1]
    bindings = {
        "amendment_v2": amendment_v2_identity,
        "amendment_v2_timestamp_query": timestamp_query_identity,
        "amendment_v2_timestamp_response": timestamp_reply_identity,
        "failed_v2_incident": incident_identity,
        "recovery_intent_v3": intent_identity,
        "recovery_candidate_phase_v3": candidate_identity,
        "recovery_refusal_incident_v3": refusal_identity,
        "recovery_resume_authorization_v11": resume_identity,
        "recovery_project_only_plan_v11": recovery_plan_identity,
        "recovery_pre_execution_review_v11": pre_review_identity,
        "recovery_project_ready_v3": ready_identity,
        "recovery_project_complete_v3": recovery_identity,
        "recovery_post_execution_review_v11": recovery_post_review_identity,
        "recovery_runner_v11": runner_identity,
        "recovery_runner_v11_tests": runner_test_identity,
        "no_decoder_evidence": evidence_identity,
        "component_runtime_closure": closure_identity,
        "frozen_campaign_launcher": frozen_launcher_identity,
        "freezer_v3": freezer_identity,
        "failed_v3_partial_activation_incident": partial_incident_identity,
        "failed_v3_partial_activation_recovery": partial_recovery_identity,
        "failed_v3_partial_activation_recovery_runner": partial_runner_identity,
        "failed_v3_partial_activation_recovery_tests": partial_runner_test_identity,
        "failed_v3_activation_provenance": dossier_identity,
        "recovery_tool_v11": runner_identity,
        "recovery_tool_v11_tests": runner_test_identity,
        "no_decoder_generator": identities["no_decoder_generator"],
        "no_decoder_generator_tests": identities["no_decoder_generator_test"],
        "launcher_v3": identities["launcher_v3"],
        "launcher_v3_tests": identities["launcher_v3_test"],
        "runtime_guard_builder_v3": identities["runtime_guard_builder_v3"],
        "runtime_guard_builder_v3_tests": identities["runtime_guard_builder_v3_test"],
        "evaluator_v3": identities["evaluator_v3"],
        "evaluator_lock_freezer_v2": identities["evaluator_lock_freezer_v2"],
        "evaluator_v3_tests": identities["evaluator_v3_test"],
        "evaluator_lock_freezer_v2_tests": identities[
            "evaluator_lock_freezer_v2_test"
        ],
        "sealed_tmpfs_inputs_v3_tests": identities["sealed_tmpfs_inputs_v3_tests"],
        "private_authorities_v3_tests": identities["private_authorities_v3_tests"],
        "small_artifact_authority_v3_tests": identities[
            "small_artifact_authority_v3_tests"
        ],
        "results_export_v3_tests": identities["results_export_v3_tests"],
        "normalizer_v2": identities["normalizer_v2"],
        "component_closure_generator": identities["component_closure_generator"],
        "component_closure_tests": identities["component_closure_test"],
        "amendment_v3_tests": identities["amendment_v3_test"],
    }
    review = _self_hashed(
        FREEZER.REVIEW_SCHEMA_VERSION,
        "review_payload_sha256",
        status="GO",
        reviewed_bindings=bindings,
        checks=FREEZER._expected_review_checks(),
    )
    review_path = tmp_path / "v3-review.json"
    review_identity = _write(review_path, review)
    values: dict[str, object] = {
        "amendment_v2_path": amendment_v2_path,
        "expected_amendment_v2_sha256": amendment_v2_identity["sha256"],
        "timestamp_query_path": timestamp_query_path,
        "expected_timestamp_query_sha256": timestamp_query_identity["sha256"],
        "timestamp_reply_path": timestamp_reply_path,
        "expected_timestamp_reply_sha256": timestamp_reply_identity["sha256"],
        "incident_path": incident_path,
        "expected_incident_sha256": incident_identity["sha256"],
        "recovery_intent_path": intent_path,
        "expected_recovery_intent_sha256": intent_identity["sha256"],
        "recovery_candidate_path": candidate_path,
        "expected_recovery_candidate_sha256": candidate_identity["sha256"],
        "recovery_refusal_path": refusal_path,
        "expected_recovery_refusal_sha256": refusal_identity["sha256"],
        "recovery_resume_path": resume_path,
        "expected_recovery_resume_sha256": resume_identity["sha256"],
        "recovery_plan_v11_path": recovery_plan_path,
        "expected_recovery_plan_v11_sha256": recovery_plan_identity["sha256"],
        "recovery_pre_review_v11_path": pre_review_path,
        "expected_recovery_pre_review_v11_sha256": pre_review_identity["sha256"],
        "recovery_project_ready_path": ready_path,
        "expected_recovery_project_ready_sha256": ready_identity["sha256"],
        "recovery_path": recovery_path,
        "expected_recovery_sha256": recovery_identity["sha256"],
        "recovery_post_review_path": recovery_post_review_path,
        "expected_recovery_post_review_sha256": recovery_post_review_identity["sha256"],
        "recovery_tool_v11_path": runner_path,
        "expected_recovery_tool_v11_sha256": runner_identity["sha256"],
        "recovery_tool_v11_test_path": runner_test_path,
        "expected_recovery_tool_v11_test_sha256": runner_test_identity["sha256"],
        "partial_v3_incident_path": partial_incident_path,
        "expected_partial_v3_incident_sha256": partial_incident_identity["sha256"],
        "partial_v3_recovery_path": partial_recovery_path,
        "expected_partial_v3_recovery_sha256": partial_recovery_identity["sha256"],
        "partial_v3_recovery_tool_path": partial_runner_path,
        "expected_partial_v3_recovery_tool_sha256": partial_runner_identity["sha256"],
        "partial_v3_recovery_tool_test_path": partial_runner_test_path,
        "expected_partial_v3_recovery_tool_test_sha256": (
            partial_runner_test_identity["sha256"]
        ),
        "failed_v3_provenance_path": dossier_path,
        "expected_failed_v3_provenance_sha256": dossier_identity["sha256"],
        "no_decoder_evidence_path": evidence_path,
        "expected_no_decoder_evidence_sha256": evidence_identity["sha256"],
        "component_closure_path": closure_path,
        "expected_component_closure_sha256": closure_identity["sha256"],
        "review_path": review_path,
        "expected_review_sha256": review_identity["sha256"],
    }
    for name in implementation_names:
        parameter_name = {
            "no_decoder_generator_test": "no_decoder_generator_test",
            "launcher_v3_test": "launcher_v3_test",
            "runtime_guard_builder_v3_test": "runtime_guard_builder_v3_test",
            "component_closure_test": "component_closure_test",
            "amendment_v3_test": "amendment_v3_test",
            "sealed_tmpfs_inputs_v3_tests": "sealed_tmpfs_inputs_v3_test",
            "private_authorities_v3_tests": "private_authorities_v3_test",
            "small_artifact_authority_v3_tests": "small_artifact_authority_v3_test",
            "results_export_v3_tests": "results_export_v3_test",
        }.get(name, name)
        values[f"{parameter_name}_path"] = files[name]
        values[f"expected_{parameter_name}_sha256"] = identities[name]["sha256"]
    return values


def test_build_supersedes_failed_v2_and_preserves_science(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _bundle(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(
        **values, created_at="2026-09-04T20:01:00Z"
    )
    assert document["status"].startswith("approved_after_namespace_complete_recovery")
    assert document["supersedes"]["v2_campaign_resume_permitted"] is False
    assert document["schedule"]["unit_count"] == 6168
    assert document["schedule"]["units_to_execute"] == 6166
    assert document["sensitivity_exclusion_observation_ids"] == [4491]
    assert document["failed_v2_activation"]["decoder_started"] is False
    contract = document["persistent_mount_namespace_contract"]
    assert contract["first_mount_mutation"] == [
        "/usr/bin/mount", "--make-rprivate", "/"
    ]
    assert contract["root_recursively_private_before_project_bind"] is True
    assert contract["control_parent"].endswith("v3")
    assert len(contract["expected_external_namespace_inodes"]) == 10
    assert contract["remaining_decoder_unit_count"] == 6166
    assert contract["prior_exposed_units_rerun"] is False
    assert document["mount_propagation_contract"] == {
        "rprivate_completed_before_first_runtime_parent_child_bind": True,
        "all_live_mount_namespaces_scanned_before_and_after_each_activation_mutation": True,
        "other_namespace_project_or_descendant_mount_delta_required": 0,
        "v2_control_or_results_namespace_reuse_permitted": False,
    }
    capacity = document["capacity_preflight_contract"]
    assert capacity == FREEZER._capacity_preflight_contract()
    assert len(capacity) == 17
    assert capacity["results_tmpfs_capacity_bytes"] == 2 * 1024**3
    assert capacity["results_minimum_available_bytes_before_work"] == 1536 * 1024**2
    assert capacity["persistent_export_filesystem_minimum_available_bytes"] == 5 * 1024**3
    authority = document["private_authority_mount_contract"]
    assert authority["guard_fields"][-2:] == [
        "capacity_contract", "capacity_preflight_at_guard_freeze"
    ]
    assert authority["role_handoff_and_completion_direct_fields"][-1] == (
        "capacity_contract"
    )
    assert document["failed_v2_activation"]["post_v11_independent_review_go"] is True
    failed_v3 = document["failed_v3_activation_attempt"]
    assert failed_v3["status"] == "PASS_ABORTED_BEFORE_DECODER_OR_ROLE_EXECUTION"
    assert failed_v3["incident"]["sha256"] == values[
        "expected_partial_v3_incident_sha256"
    ]
    assert failed_v3["recovery"]["sha256"] == values[
        "expected_partial_v3_recovery_sha256"
    ]
    assert failed_v3["private_results_entry_count_before_recovery"] == 0
    assert failed_v3["prior_attempt_artifact_reuse_permitted"] is False
    execution = document["execution_identity_and_runtime_contract"]
    assert execution["campaign_entrypoint"].endswith("exec-campaign")
    assert execution["evaluator_entrypoint"].endswith("exec-evaluator")
    assert execution["campaign_and_evaluator_roles_are_not_interchangeable"] is True
    assert execution[
        "root_enters_exact_pinned_mount_namespace_before_identity_drop"
    ] is True
    assert execution["frozen_evaluator_lock_required_before_campaign_entry"] is True
    assert execution["no_new_privs_required"] is True
    assert execution[
        "effective_permitted_inheritable_ambient_capability_sets_required_empty"
    ] is True
    assert execution["direct_launcher_invocation_outside_active_namespace_forbidden"] is True
    assert execution["direct_evaluator_invocation_outside_active_namespace_forbidden"] is True
    assert execution["shell_entrypoint_permitted"] is False
    assert execution["exec_evaluator_requires_exact_guard_bound_evaluator_identity"] == document[
        "amended_implementation"
    ]["evaluator"]
    assert document["amended_implementation"]["evaluator_lock_freezer"][
        "sha256"
    ] == values["expected_evaluator_lock_freezer_v2_sha256"]
    test_hashes = {
        identity["sha256"] for identity in document["amended_implementation"]["tests"]
    }
    assert values["expected_evaluator_v3_test_sha256"] in test_hashes
    assert values["expected_evaluator_lock_freezer_v2_test_sha256"] in test_hashes
    assert values["expected_sealed_tmpfs_inputs_v3_test_sha256"] in test_hashes
    assert values["expected_private_authorities_v3_test_sha256"] in test_hashes
    assert values["expected_small_artifact_authority_v3_test_sha256"] in test_hashes
    assert values["expected_results_export_v3_test_sha256"] in test_hashes
    receipts = document["role_handoff_receipt_contract"]
    assert receipts["roles"] == ["campaign", "evaluator"]
    assert receipts["completion_receipt_published_only_after_child_exit_or_signal"] is True
    assert receipts["distinct_immutable_pre_exec_failure_receipt_required"] is True
    assert receipts["positive_post_exec_role_ack_required"] is True
    assert receipts["campaign_completion_required_by_evaluator"] is True
    assert receipts["campaign_completion_requires_campaign_exit_zero"] is True
    assert receipts["campaign_output_attestation_binds_canonical_unit_manifest"] is True
    assert receipts["post_uid_gid_drop_role_script_identity_recheck_required"] is True
    assert receipts[
        "role_script_opened_and_sha256_verified_via_inherited_fd_before_compile"
    ] is True
    assert receipts["pinned_script_bootstrap_schema"] == (
        "pinned-source-fd-sha256-compile-exec-v1"
    )
    assert receipts["pinned_role_script_fd_closed_before_outcome_access"] is True
    assert receipts["pipe_or_child_leak_permitted"] is False
    assert receipts[
        "capacity_contract_bound_in_guard_handoff_completion_and_output_attestation"
    ] is True
    export = document["results_export_contract"]
    assert export["required_before_persistent_namespace_stop"] is True
    assert export["private_source_authority_root"] == str(FREEZER.RESULTS_PARENT_V3)
    assert export["persistent_export_root"] == str(
        FREEZER.CONTROL_PARENT_V3 / "results-export-v3"
    )
    assert export["schemas"] == {
        "intent_and_attestation": "blind-phase-confirmatory-results-export-v3",
        "tree_manifest": "blind-phase-confirmatory-results-export-manifest-v3",
        "execution_receipt": "blind-phase-confirmatory-results-export-receipt-v3",
    }
    assert export["manifest_receipt_and_attestation_bind_source_mount_and_role_chain"] is True
    assert export["private_source_and_persistent_export_full_tree_equality_required"] is True
    assert export["copy_publication_no_clobber_and_crash_recoverable"] is True
    assert export["stop_refuses_without_exact_export_attestation"] is True
    assert export["dead_keeper_stop_recovery_requires_durable_intent_and_exact_export"] is True
    consumer = document["consumer_side_handoff_validation_contract"]
    assert consumer["roles"] == ["campaign", "evaluator"]
    assert consumer["handoff_read_o_nofollow"] is True
    assert consumer["handoff_read_single_fd_bytes_hash_and_parse"] is True
    assert consumer["exact_active_namespace_artifact_identity_revalidated_after_exec"] is True
    assert consumer["post_exec_uid_gid_required"] == [1000, 1000]
    assert consumer["post_exec_supplementary_groups_required"] == []
    assert consumer["post_exec_dumpable_required"] is False
    assert consumer["ty_handoff_environment_names_removed"] == [
        "TY_NAMESPACE_ROLE",
        "TY_NAMESPACE_ROLE_HANDOFF",
        "TY_NAMESPACE_ROLE_NONCE",
        "TY_ROLE_ACK_FD",
        "TY_ROLE_ACK_NONCE",
    ]
    assert document["evaluator_output_contract"]["canonical_filename"] == (
        "amended-evaluation-v3.json"
    )
    assert document["evaluator_output_contract"][
        "temporary_file_hardlink_or_mkdir_publication_forbidden"
    ] is True
    assert not {
        "determinism_projection_fix",
        "runtime_materialization_contract",
        "no_decoder_execution_since_v1",
    }.intersection(document)
    assert consumer["ty_handoff_nonce_path_and_role_removed_before_worker_or_output"] is True
    assert consumer["worker_or_output_before_all_consumer_checks_permitted"] is False
    assert document["claim_guard"]["publication_ready"] is False
    unhashed = dict(document)
    assert unhashed.pop("amendment_payload_sha256") == FREEZER.sha256_document(unhashed)


def test_real_amendment_freezer_output_is_accepted_by_real_evaluator_validator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _bundle(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(
        **values, created_at="2026-09-04T20:01:00Z"
    )
    monkeypatch.setattr(EVALUATOR, "HERE", tmp_path)
    monkeypatch.setattr(
        EVALUATOR,
        "EXPECTED_NORMALIZER_V2_SHA256",
        document["amended_normalizer"]["sha256"],
    )
    monkeypatch.setattr(
        EVALUATOR,
        "EXPECTED_OPERATIONAL_CHANGES",
        tuple(document["operational_scope"]["inherited_v2_operational_changes_retained"]),
    )
    monkeypatch.setattr(
        EVALUATOR,
        "EXPECTED_INDEPENDENT_SECURITY_TESTS",
        document["amended_implementation"]["independent_security_tests"],
    )
    no_decoder = document["no_decoder_execution_since_v2"]
    assert EVALUATOR._validate_amendment(
        document,
        plan_identity=document["execution_plan"],
        acquisition_identity=document["acquisition_manifest"],
        source_identity=document["source_manifest"],
        launcher_identity=document["amended_launcher"],
        normalizer_identity=document["amended_normalizer"],
        no_decoder_evidence_identity=no_decoder["evidence"],
        no_decoder_generator_identity=no_decoder["evidence_generator"],
        no_decoder_evidence_payload_sha256=no_decoder["evidence_payload_sha256"],
        no_decoder_output_inventory=no_decoder["unchanged_output_inventory"],
        component_closure_identity=document["component_runtime_closure"],
        component_closure_generator_identity=document[
            "component_runtime_closure_generator"
        ],
    ) == document


def test_build_rejects_nonpass_recovery_before_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _bundle(tmp_path, monkeypatch)
    recovery_path = values["recovery_path"]
    recovery = json.loads(recovery_path.read_text())
    recovery["namespace_inventory"]["after"][0]["mountinfo"].append(
        "2000 1 252:0 / /home/ubuntu/telemetry-yield rw - ext4 /dev/root rw"
    )
    recovery["namespace_inventory"]["after_sha256"] = FREEZER.sha256_document(
        recovery["namespace_inventory"]["after"]
    )
    recovery["recovery_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in recovery.items() if key != "recovery_payload_sha256"}
    )
    identity = _write(recovery_path, recovery)
    values["expected_recovery_sha256"] = identity["sha256"]
    monkeypatch.setattr(FREEZER, "EXPECTED_RECOVERY_SHA256", identity["sha256"])
    monkeypatch.setattr(
        FREEZER, "EXPECTED_RECOVERY_PAYLOAD_SHA256",
        recovery["recovery_payload_sha256"],
    )
    with pytest.raises(ValueError, match="recovery inventory is not exact"):
        FREEZER.build_amendment(**values, created_at="2026-09-04T20:01:00Z")


def test_review_requires_exact_bindings_and_zero_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _bundle(tmp_path, monkeypatch)
    review_path = values["review_path"]
    review = json.loads(review_path.read_text())
    review["checks"]["p2_findings"] = 1
    review["review_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in review.items() if key != "review_payload_sha256"}
    )
    values["expected_review_sha256"] = _write(review_path, review)["sha256"]
    with pytest.raises(ValueError, match="review is not exact GO"):
        FREEZER.build_amendment(**values, created_at="2026-09-04T20:01:00Z")


def test_build_rejects_canonical_v3_path_recreated_after_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _bundle(tmp_path, monkeypatch)
    used = tmp_path / "already-used-control"
    used.mkdir()
    monkeypatch.setattr(FREEZER, "CONTROL_PARENT_V3", used)
    monkeypatch.setattr(FREEZER, "RESULTS_PARENT_V3", tmp_path / "unused-results")
    with pytest.raises(ValueError, match="canonical v3 path exists"):
        FREEZER.build_amendment(**values, created_at="2026-09-04T20:01:00Z")


def test_build_rejects_quarantine_inventory_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _bundle(tmp_path, monkeypatch)
    quarantine = FREEZER.FAILED_V3_QUARANTINE_ROOT
    target = quarantine / "amendment-v3.tsq"
    os.chmod(target, 0o644)
    with pytest.raises(ValueError, match="quarantined file changed"):
        FREEZER.build_amendment(**values, created_at="2026-09-04T20:01:00Z")


def test_build_rejects_partial_recovery_role_or_decoder_exposure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = _bundle(tmp_path, monkeypatch)
    path = values["partial_v3_recovery_path"]
    recovery = json.loads(path.read_text())
    recovery["no_decoder_evidence"]["active_decoder_or_role_processes_before"] = 1
    recovery["recovery_payload_sha256"] = FREEZER.sha256_document(
        {key: value for key, value in recovery.items() if key != "recovery_payload_sha256"}
    )
    identity = _write(path, recovery)
    values["expected_partial_v3_recovery_sha256"] = identity["sha256"]
    monkeypatch.setattr(FREEZER, "EXPECTED_PARTIAL_V3_RECOVERY_SHA256", identity["sha256"])
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PARTIAL_V3_RECOVERY_PAYLOAD_SHA256",
        recovery["recovery_payload_sha256"],
    )
    with pytest.raises(ValueError, match="provenance is not exact PASS"):
        FREEZER.build_amendment(**values, created_at="2026-09-04T20:01:00Z")


def test_publish_is_immutable_no_clobber(tmp_path: Path) -> None:
    output = tmp_path / "amendment-v3.json"
    FREEZER.publish_no_clobber(output, {"status": "approved"})
    assert output.stat().st_mode & 0o777 == 0o444
    assert output.with_name(output.name + ".sha256").stat().st_mode & 0o777 == 0o444
    with pytest.raises(ValueError, match="already exists"):
        FREEZER.publish_no_clobber(output, {"status": "changed"})


def test_cli_binds_recovery_and_runtime_v3_hashes(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        FREEZER.main(["--help"])
    help_text = capsys.readouterr().out
    for option in (
        "--expected-recovery-sha256",
        "--expected-recovery-post-review-sha256",
        "--expected-recovery-plan-v11-sha256",
        "--expected-recovery-resume-sha256",
        "--expected-recovery-tool-v11-sha256",
        "--expected-partial-v3-incident-sha256",
        "--expected-partial-v3-recovery-sha256",
        "--expected-partial-v3-recovery-tool-sha256",
        "--expected-partial-v3-recovery-tool-test-sha256",
        "--expected-failed-v3-provenance-sha256",
        "--expected-runtime-guard-builder-v3-sha256",
        "--expected-launcher-v3-sha256",
        "--expected-evaluator-v3-sha256",
        "--expected-evaluator-lock-freezer-v2-sha256",
        "--expected-evaluator-v3-test-sha256",
        "--expected-evaluator-lock-freezer-v2-test-sha256",
        "--expected-sealed-tmpfs-inputs-v3-test-sha256",
        "--expected-private-authorities-v3-test-sha256",
        "--expected-small-artifact-authority-v3-test-sha256",
        "--expected-results-export-v3-test-sha256",
        "--expected-no-decoder-evidence-sha256",
    ):
        assert option in help_text
    assert "--iq" not in help_text


def test_review_template_has_exact_fail_closed_shape() -> None:
    template = json.loads(REVIEW_TEMPLATE.read_text(encoding="utf-8"))
    assert template["schema_version"] == FREEZER.REVIEW_SCHEMA_VERSION
    assert template["status"] == "REVIEW_REQUIRED"
    assert template["review_payload_sha256"] is None
    assert set(template["checks"]) == set(FREEZER._expected_review_checks())
    assert all(value is None for value in template["checks"].values())
    assert set(template["reviewed_bindings"]) == {
        "amendment_v2",
        "amendment_v2_timestamp_query",
        "amendment_v2_timestamp_response",
        "failed_v2_incident",
        "failed_v3_partial_activation_incident",
        "failed_v3_partial_activation_recovery",
        "failed_v3_partial_activation_recovery_runner",
        "failed_v3_partial_activation_recovery_tests",
        "failed_v3_activation_provenance",
        "recovery_intent_v3",
        "recovery_candidate_phase_v3",
        "recovery_refusal_incident_v3",
        "recovery_resume_authorization_v11",
        "recovery_project_only_plan_v11",
        "recovery_pre_execution_review_v11",
        "recovery_project_ready_v3",
        "recovery_project_complete_v3",
        "recovery_post_execution_review_v11",
        "recovery_runner_v11",
        "recovery_runner_v11_tests",
        "no_decoder_evidence",
        "component_runtime_closure",
        "frozen_campaign_launcher",
        "freezer_v3",
        "recovery_tool_v11",
        "recovery_tool_v11_tests",
        "no_decoder_generator",
        "no_decoder_generator_tests",
        "launcher_v3",
        "launcher_v3_tests",
        "runtime_guard_builder_v3",
        "runtime_guard_builder_v3_tests",
        "evaluator_v3",
        "evaluator_lock_freezer_v2",
        "evaluator_v3_tests",
        "evaluator_lock_freezer_v2_tests",
        "sealed_tmpfs_inputs_v3_tests",
        "private_authorities_v3_tests",
        "small_artifact_authority_v3_tests",
        "results_export_v3_tests",
        "normalizer_v2",
        "component_closure_generator",
        "component_closure_tests",
        "amendment_v3_tests",
    }
    for name in (
        "no_decoder_evidence",
        "freezer_v3",
        "no_decoder_generator",
        "no_decoder_generator_tests",
        "amendment_v3_tests",
        "launcher_v3",
        "launcher_v3_tests",
        "runtime_guard_builder_v3",
        "runtime_guard_builder_v3_tests",
        "evaluator_v3",
        "evaluator_lock_freezer_v2",
        "evaluator_v3_tests",
        "evaluator_lock_freezer_v2_tests",
    ):
        expected = template["reviewed_bindings"][name]
        assert expected == FREEZER.read_regular_bytes(Path(expected["path"]))[1]
    for name, expected in {
        "sealed_tmpfs_inputs_v3_tests": FREEZER.EXPECTED_SEALED_TMPFS_INPUTS_V3_TEST_SHA256,
        "private_authorities_v3_tests": FREEZER.EXPECTED_PRIVATE_AUTHORITIES_V3_TEST_SHA256,
        "small_artifact_authority_v3_tests": FREEZER.EXPECTED_SMALL_ARTIFACT_AUTHORITY_V3_TEST_SHA256,
        "results_export_v3_tests": FREEZER.EXPECTED_RESULTS_EXPORT_V3_TEST_SHA256,
    }.items():
        assert template["reviewed_bindings"][name]["sha256"] == expected
