from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/evaluate_campaign_amended_v3.py"


def _module():
    name = "blind_phase_campaign_evaluation_amended_v3_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EVALUATE = _module()


def _identity(path: str, marker: str = "1") -> dict[str, object]:
    return {"path": path, "size_bytes": 1, "sha256": marker * 64}


def _self_hashed(schema: str, field: str, **values: object) -> dict[str, object]:
    document: dict[str, object] = {"schema_version": schema, **values}
    document[field] = EVALUATE.sha256_document(document)
    return document


def _write_json(path: Path, document: object) -> dict[str, object]:
    path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    return EVALUATE.regular_file_identity(path)


def _inventory() -> dict[str, object]:
    value: dict[str, object] = {
        "root": "/campaign",
        "directory_count": 7,
        "file_count": 6,
        "exposed_decoder_unit_count": 2,
        "new_decoder_unit_count": 0,
        "files": {f"file-{index}": {"sha256": f"{index + 1:x}" * 64} for index in range(6)},
    }
    value["inventory_sha256"] = EVALUATE.sha256_document(value)
    return value


def _no_decoder_evidence(generator: dict[str, object], inventory: dict[str, object]):
    recovery = {
        name: _identity(f"/{name}", hex(index + 1)[2:])
        for index, name in enumerate(
            (
                "intent_v3", "candidate_phase_v3", "refusal_incident_v3",
                "resume_authorization_v11", "project_only_plan_v11",
                "pre_execution_review_v11", "project_ready_v3",
                "project_complete_v3", "post_execution_review_v11",
                "runner_v11", "runner_v11_tests",
            )
        )
    }
    checks = {
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
    }
    return _self_hashed(
        EVALUATE.NO_DECODER_EVIDENCE_SCHEMA_VERSION,
        "evidence_payload_sha256",
        status="PASS",
        generated_at_utc="2026-09-05T00:00:00Z",
        period_start_utc="2026-09-03T00:00:00Z",
        generator=generator,
        amendment_v2=_identity("/amendment-v2", "a"),
        amendment_v2_timestamp={
            "query": _identity("/timestamp.tsq", "b"),
            "response": _identity("/timestamp.tsr", "c"),
            "status": "PASS",
            "message_imprint_sha256": "a" * 64,
            "generation_time_utc": "2026-09-03T00:00:00Z",
        },
        failed_activation_incident=_identity("/incident", "d"),
        recovery_v11_chain=recovery,
        campaign_output_inventory=inventory,
        checks=checks,
        claim_scope={
            "proves_no_new_persisted_decoder_outputs_since_v2_timestamp": True,
            "binds_complete_independently_reviewed_recovery_v11_chain": True,
            "does_not_claim_runtime_closure_for_two_prior_exposures": True,
            "v2_campaign_resume_permitted": False,
        },
    )


def _amendment(
    *,
    plan: dict[str, object],
    acquisition: dict[str, object],
    source: dict[str, object],
    launcher: dict[str, object],
    normalizer: dict[str, object],
    evidence: dict[str, object],
    evidence_generator: dict[str, object],
    inventory: dict[str, object],
    closure: dict[str, object],
    closure_generator: dict[str, object],
) -> dict[str, object]:
    builder = _identity(
        str((EVALUATE.HERE / "build_runtime_guard_v3.py").absolute()), "e"
    )
    frozen_launcher = _identity("/project/work/campaign_launcher.py", "f")
    dependencies = {
        "runtime_guard_builder_v3": builder,
        "campaign_launcher_v3": launcher,
        "frozen_campaign_launcher": frozen_launcher,
        "normalizer_v2": normalizer,
    }
    return _self_hashed(
        EVALUATE.AMENDMENT_SCHEMA_VERSION,
        "amendment_payload_sha256",
        status="approved_after_namespace_complete_recovery_before_remaining_decoder_execution",
        campaign_pristine=False,
        execution_plan=plan,
        acquisition_manifest=acquisition,
        source_manifest=source,
        schedule={
            "unit_count": 6168,
            "scientific_schedule_changed": False,
            "units_to_execute": 6166,
            "units_re_normalized_without_decoder_rerun": 2,
        },
        prior_decoder_exposures=[
            {"unit_id": "1" * 64, "observation_id": 4491, "decoder_rerun_permitted": False},
            {"unit_id": "2" * 64, "observation_id": 4491, "decoder_rerun_permitted": False},
        ],
        sensitivity_analysis={
            "required": True,
            "exclude_observation_ids": [4491],
            "full_frozen_cohort_analysis_retained": True,
            "report_both_without_substitution": True,
        },
        sensitivity_exclusion_observation_ids=[4491],
        runtime_history_disclosure={
            "remaining_decoder_run_count_with_full_component_closure_bound": 6166,
            "prior_exposed_decoder_unit_count": 2,
            "prior_exposed_observation_ids": [4491],
            "full_component_closure_for_prior_exposures_cryptographically_established": False,
            "full_30_observation_cohort_runtime_history": "mixed_and_non_pristine",
            "full_30_observation_cohort_retained_with_disclosure": True,
            "mandatory_sensitivity_observation_count": 29,
            "mandatory_sensitivity_excludes_entire_observation_ids": [4491],
        },
        component_runtime_closure=closure,
        component_runtime_closure_generator=closure_generator,
        amended_launcher=launcher,
        amended_normalizer=normalizer,
        no_decoder_execution_since_v2={
            "status": "PASS",
            "evidence": evidence,
            "evidence_generator": evidence_generator,
            "evidence_payload_sha256": "3" * 64,
            "unchanged_output_inventory": inventory,
            "decoder_units_executed_since_v2": 0,
        },
        operational_scope={
            "inherited_v2_operational_changes_retained": list(EVALUATE.EXPECTED_OPERATIONAL_CHANGES),
            "execution_plan_changed": False,
            "selection_changed": False,
            "candidate_config_changed": False,
            "decoder_changed": False,
            "normalizer_changed_from_v2": False,
            "scientific_normalization_or_scoring_changed": False,
            "scoring_endpoint_or_metric_changed": False,
            "scientific_unit_set_changed": False,
            "scientific_hypotheses_changed": False,
        },
        mount_propagation_contract={
            "rprivate_completed_before_first_runtime_parent_child_bind": True,
            "all_live_mount_namespaces_scanned_before_and_after_each_activation_mutation": True,
            "other_namespace_project_or_descendant_mount_delta_required": 0,
            "v2_control_or_results_namespace_reuse_permitted": False,
        },
        capacity_preflight_contract=EVALUATE._expected_capacity_contract(),
        private_authority_mount_contract={
            "control_parent": str(EVALUATE.CONTROL_PARENT_V3),
            "sealed_input": {
                "snapshot_schema": EVALUATE.SEALED_INPUT_SNAPSHOT_SCHEMA_VERSION,
                "mount_schema": EVALUATE.SEALED_INPUT_MOUNT_SCHEMA_VERSION,
                "source_root": str(EVALUATE.SEALED_INPUT_SOURCE_ROOT.absolute()),
                "authority_root": str(EVALUATE.SEALED_INPUT_TARGET_ROOT),
                "input_count": EVALUATE.EXPECTED_OBSERVATION_COUNT,
                "file_owner": [0, 0], "file_mode": "0444", "file_nlink": 1,
                "mount_type": "tmpfs",
                "mount_options": ["ro", "nosuid", "nodev", "noexec"],
                "evaluator_opens_input_bytes": False,
            },
            "runtime": {
                "authority_roots": {
                    "candidate": str(EVALUATE.RUNTIME_AUTHORITY_ROOT / "candidate"),
                    "component": str(EVALUATE.RUNTIME_AUTHORITY_ROOT / "component"),
                },
                "root_owner": [0, 0], "writable_bits_permitted": False,
                "required_mount_options": ["ro", "nosuid", "nodev"],
            },
            "sealed_project_artifacts": {
                "snapshot_schema": EVALUATE.SEALED_PROJECT_ARTIFACTS_SCHEMA_VERSION,
                "authority_root": str(EVALUATE.SEALED_PROJECT_ARTIFACTS_ROOT),
                "mount_type": "tmpfs", "file_owner": [0, 0],
                "file_mode": "0444", "file_nlink": 1,
                "mount_options": ["ro", "nosuid", "nodev", "noexec"],
                "evaluator_opens_original_project_artifact_bytes": False,
                "lock_freezer_imports_project_python_before_guard_validation": False,
                "lock_freezer_loads_evaluator_only_from_guard_bound_sealed_path": True,
            },
            "results": {
                "authority_root": str(EVALUATE.RESULTS_PARENT_V3),
                "mount_type": "tmpfs", "mount_owner": [1000, 1000],
                "mount_mode": "0700",
                "capacity_bytes": EVALUATE.RESULTS_TMPFS_CAPACITY_BYTES,
                "minimum_available_bytes_before_work": (
                    EVALUATE.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
                ),
                "available_bytes_formula": "statvfs.f_bavail*statvfs.f_frsize",
                "required_mount_options": ["rw", "nosuid", "nodev", "noexec"],
                "global_user_writable_parent_permitted": False,
            },
            "guard_fields": [
                "sealed_input_snapshot", "sealed_input_authority_mapping",
                "sealed_project_artifacts", "sealed_project_artifact_mapping",
                "runtime_authority_mounts", "results_authority_mount",
                "capacity_contract", "capacity_preflight_at_guard_freeze",
            ],
            "role_handoff_and_completion_direct_fields": [
                "sealed_input_snapshot", "sealed_project_artifacts",
                "runtime_authority_mounts",
                "results_authority_mount", "capacity_contract",
            ],
            "pre_ack_and_post_ack_live_revalidation_required": True,
        },
        role_handoff_receipt_contract={
            "roles": ["campaign", "evaluator"],
            "immutable_completion_receipt_required": True,
            "completion_receipt_published_only_after_child_exit_or_signal": True,
            "distinct_immutable_pre_exec_failure_receipt_required": True,
            "completion_and_failure_receipts_bind_exact_handoff_identity": True,
            "positive_post_exec_role_ack_required": True,
            "role_ack_schema": EVALUATE.ROLE_ACK_SCHEMA_VERSION,
            "role_ack_environment_names": ["TY_ROLE_ACK_FD", "TY_ROLE_ACK_NONCE"],
            "role_ack_payload_is_canonical_compact_json_without_newline": True,
            "role_ack_maximum_bytes": 4096,
            "role_ack_written_after_full_consumer_validation_before_outcome_access": True,
            "role_ack_follows_full_control_and_live_private_project_validation": True,
            "campaign_output_reopened_and_compared_to_root_attestation_after_role_ack": True,
            "rfc3161_attestation_required_before_role_ack": True,
            "role_handoffs_and_completion_receipts_bind_rfc3161_attestation": True,
            "campaign_completion_required_by_evaluator": True,
            "campaign_completion_schema": EVALUATE.ROLE_COMPLETION_SCHEMA_VERSION,
            "campaign_completion_requires_campaign_exit_zero": True,
            "campaign_completion_binds_exact_handoff_context_namespace_and_output_root": True,
            "campaign_output_attestation_schema": EVALUATE.ROLE_OUTPUT_ATTESTATION_SCHEMA_VERSION,
            "campaign_output_attestation_binds_canonical_unit_manifest": True,
            "root_output_snapshot_after_zero_exit_required_for_roles": [
                "exec-campaign", "exec-evaluator"
            ],
            "post_uid_gid_drop_role_script_identity_recheck_required": True,
            "role_script_opened_and_sha256_verified_via_inherited_fd_before_compile": True,
            "pinned_script_bootstrap_schema": EVALUATE.PINNED_SCRIPT_BOOTSTRAP_SCHEMA,
            "pinned_role_script_fd_closed_before_outcome_access": True,
            "parent_exception_must_close_all_pipe_fds": True,
            "parent_exception_must_reap_or_terminate_and_reap_child": True,
            "pipe_or_child_leak_permitted": False,
            "capacity_contract_bound_in_guard_handoff_completion_and_output_attestation": True,
        },
        results_export_contract={
            "command": "export-results",
            "required_before_persistent_namespace_stop": True,
            "private_source_authority_root": str(EVALUATE.RESULTS_PARENT_V3),
            "persistent_export_root": str(EVALUATE.RESULTS_EXPORT_ROOT),
            "intent_path": str(EVALUATE.RESULTS_EXPORT_INTENT_PATH),
            "tree_manifest_path": str(EVALUATE.RESULTS_EXPORT_MANIFEST_PATH),
            "attestation_path": str(EVALUATE.RESULTS_EXPORT_ATTESTATION_PATH),
            "execution_receipt_filename_pattern": (
                "results-export-v3-execution-receipt-<64hex>.json"
            ),
            "schemas": {
                "intent_and_attestation": EVALUATE.RESULTS_EXPORT_SCHEMA_VERSION,
                "tree_manifest": EVALUATE.RESULTS_EXPORT_MANIFEST_SCHEMA_VERSION,
                "execution_receipt": EVALUATE.RESULTS_EXPORT_RECEIPT_SCHEMA_VERSION,
            },
            "campaign_and_evaluator_zero_exit_completions_required": True,
            "campaign_and_evaluator_completion_identities_bound": True,
            "role_output_attestations_and_exact_output_identities_bound": True,
            "export_manifest_contains_exact_evaluator_output_bytes": True,
            "manifest_receipt_and_attestation_bind_source_mount_and_role_chain": True,
            "private_source_and_persistent_export_full_tree_equality_required": True,
            "source_rechecked_before_after_copy_and_at_attestation": True,
            "attestation_requires_live_source_equal_at_publication": True,
            "persistent_export_root_owner_mode": [0, 0, "0555"],
            "persistent_export_file_owner_mode_nlink": [0, 0, "0444", 1],
            "symlink_special_file_or_hardlink_permitted": False,
            "copy_publication_no_clobber_and_crash_recoverable": True,
            "stop_refuses_without_exact_export_attestation": True,
            "dead_keeper_stop_recovery_requires_durable_intent_and_exact_export": True,
        },
        rfc3161_attestation_contract={
            "schema_version": EVALUATE.RFC3161_ATTESTATION_SCHEMA_VERSION,
            "canonical_path": str(
                EVALUATE.CONTROL_PARENT_V3 / EVALUATE.RFC3161_ATTESTATION_NAME
            ),
            "required_before_roles": ["exec-campaign", "exec-evaluator"],
            "root_root_0444_nlink1_direct_control_child_required": True,
            "binds_campaign_metadata_amendment_guard_and_evaluator_lock": True,
            "evaluator_lock_freeze_attestation_required": True,
            "evaluator_lock_freeze_attestation_schema": (
                EVALUATE.EVALUATOR_LOCK_FREEZE_ATTESTATION_SCHEMA_VERSION
            ),
            "evaluator_lock_freeze_attestation_canonical_path": str(
                EVALUATE.CONTROL_PARENT_V3
                / EVALUATE.EVALUATOR_LOCK_FREEZE_ATTESTATION_NAME
            ),
            "root_metadata_execution_receipt_parsed_and_self_hash_validated": True,
            "lock_freeze_attestation_binds_freezer_evaluator_guard_lock_and_pinned_bootstrap": True,
            "timestamps_exactly": ["amendment_v3", "evaluator_lock"],
            "message_imprint_must_equal_data_sha256": True,
            "query_and_data_verification_must_equal_ok": True,
            "offline_verification_required": True,
        },
        consumer_side_handoff_validation_contract={
            "roles": ["campaign", "evaluator"],
            "handoff_read_bounded": True,
            "handoff_read_o_nofollow": True,
            "handoff_read_single_fd_bytes_hash_and_parse": True,
            "handoff_leaf_and_ancestor_identity_stable_before_and_after_read": True,
            "exact_active_namespace_artifact_identity_revalidated_after_exec": True,
            "post_exec_no_new_privs_required": True,
            "post_exec_all_capability_sets_required_empty": True,
            "post_exec_uid_gid_required": [1000, 1000],
            "post_exec_supplementary_groups_required": [],
            "post_exec_dumpable_required": False,
            "post_exec_cwd_must_equal_exact_role_bound_handoff_value": True,
            "post_exec_environment_must_equal_exact_sanitized_role_allowlist": True,
            "pinned_script_bootstrap_schema": EVALUATE.PINNED_SCRIPT_BOOTSTRAP_SCHEMA,
            "pinned_role_script_fd_must_be_closed_before_consumer_work": True,
            "ty_handoff_environment_names_removed": [
                "TY_NAMESPACE_ROLE", "TY_NAMESPACE_ROLE_HANDOFF",
                "TY_NAMESPACE_ROLE_NONCE", "TY_ROLE_ACK_FD", "TY_ROLE_ACK_NONCE",
            ],
            "ty_handoff_nonce_path_and_role_removed_before_worker_or_output": True,
            "worker_or_output_before_all_consumer_checks_permitted": False,
        },
        evaluator_output_contract={
            "canonical_filename": EVALUATE.EVALUATION_OUTPUT_NAME,
            "canonical_location": "exact campaign output root direct child",
            "results_authority_root": str(EVALUATE.RESULTS_PARENT_V3),
            "private_namespace_tmpfs_results_authority_required": True,
            "results_authority_mount_options": ["rw", "nosuid", "nodev", "noexec"],
            "same_uid_host_alias_permitted": False,
            "output_root_uid": 1000,
            "output_root_gid": 1000,
            "output_root_mode": "0700",
            "output_root_opened_by_pinned_o_nofollow_directory_fd": True,
            "publication": "direct O_CREAT|O_EXCL|O_NOFOLLOW relative to pinned output-root dirfd",
            "full_write_fsync_and_mode_0444_required": True,
            "same_fd_and_live_dirfd_entry_attestation_required": True,
            "temporary_file_hardlink_or_mkdir_publication_forbidden": True,
            "root_post_exit_output_attestation_required": True,
            "normalized_result_self_hash_validation_required": True,
            "guarded_results_parent_and_attested_output_root_inode_identity_required": True,
            "failed_publication_cleanup_only_exact_created_inode_via_pinned_dirfd": True,
            "failed_publication_cleanup_fsyncs_parent": True,
            "failed_publication_cleanup_preserves_primary_and_cleanup_errors": True,
            "preexisting_or_replacement_inode_cleanup_forbidden": True,
        },
        transitive_code_dependency_contract={
            "verified_from_exact_bytes_before_import": True,
            "dependencies": dependencies,
        },
        amended_implementation={
            "launcher": launcher,
            "runtime_guard_builder": builder,
            "normalizer": normalizer,
            "independent_security_tests": EVALUATE.EXPECTED_INDEPENDENT_SECURITY_TESTS,
            "tests": list(EVALUATE.EXPECTED_INDEPENDENT_SECURITY_TESTS.values()),
            "transitive_code_dependencies": dependencies,
        },
        claim_guard={
            "campaign_pristine": False,
            "full_30_cohort_mixed_runtime_history_non_pristine": True,
            "component_full_closure_claim_limited_to_remaining_6166_runs": True,
            "component_full_closure_for_two_prior_exposures_cryptographically_established": False,
            "sensitivity_29_excludes_entire_observation_4491": True,
            "v2_campaign_resume_permitted": False,
            "results_export_required_before_namespace_stop_and_publication": True,
        },
    )


def test_v3_contract_versions_and_fixed_science_are_exact() -> None:
    assert EVALUATE.SCHEMA_VERSION.endswith("evaluation-v2")
    assert EVALUATE.LOCK_SCHEMA_VERSION.endswith("lock-v2")
    assert EVALUATE.AMENDMENT_SCHEMA_VERSION.endswith("amendment-v3")
    assert EVALUATE.RUNTIME_GUARD_SCHEMA_VERSION.endswith("guard-v3")
    assert EVALUATE.PERSISTENT_NAMESPACE_SCHEMA_VERSION.endswith("namespace-v3")
    assert EVALUATE.EXPECTED_UNIT_COUNT == 6168
    assert EVALUATE.SENSITIVITY_EXCLUSION == (4491,)
    assert EVALUATE.FROZEN_BOOTSTRAP_RESAMPLES == 10_000
    assert EVALUATE.FROZEN_BOOTSTRAP_SEED == 20_260_903
    capacity = EVALUATE._expected_capacity_contract()
    assert len(capacity) == 17
    assert capacity["results_tmpfs_capacity_bytes"] == 2 * 1024**3
    assert capacity["results_minimum_available_bytes_before_work"] == 1536 * 1024**2
    acquisition = json.loads(
        (EVALUATE.HERE / "acquisition-manifest-v4.json").read_text(encoding="utf-8")
    )
    assert sum(int(item["actual_size_bytes"]) for item in acquisition["objects"]) == (
        EVALUATE.EXPECTED_SEALED_INPUT_BYTES
    )
    for expected in EVALUATE.EXPECTED_INDEPENDENT_SECURITY_TESTS.values():
        assert EVALUATE.regular_file_identity(Path(str(expected["path"]))) == expected


def test_artifact_json_is_bounded_same_fd_and_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "value.json"
    target.write_text('{"value":1}\n', encoding="utf-8")
    document, identity = EVALUATE._artifact_json(target, 64)
    assert document == {"value": 1}
    assert identity["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular non-symlink"):
        EVALUATE._artifact_json(link, 64)
    with pytest.raises(ValueError, match="exceeds byte bound"):
        EVALUATE._artifact_json(target, 2)


def test_cli_json_identity_hashes_and_parses_from_one_artifact_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "control.json"
    path.write_text('{"status":"PASS"}\n', encoding="utf-8")
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    calls = 0
    original = EVALUATE._artifact_json

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(EVALUATE, "_artifact_json", counted)
    monkeypatch.setattr(
        EVALUATE,
        "regular_file_identity",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("separate identity read is forbidden")
        ),
    )
    document, identity = EVALUATE._cli_json_identity(
        {"--artifact": str(path), "--sha": expected},
        "--artifact",
        "--sha",
    )
    assert document == {"status": "PASS"}
    assert identity["sha256"] == expected
    assert calls == 1


def test_no_decoder_v3_evidence_requires_exact_recovery_and_inventory() -> None:
    generator = _identity(
        str((EVALUATE.HERE / "build_no_decoder_since_v2_incident_evidence.py").absolute()),
        "7",
    )
    inventory = _inventory()
    evidence = _no_decoder_evidence(generator, inventory)
    assert EVALUATE._validate_no_decoder_evidence(
        evidence,
        generator_identity=generator,
        expected_output_inventory=inventory,
    ) == evidence
    broken = copy.deepcopy(evidence)
    broken["checks"]["new_decoder_output_count_since_v2"] = 1
    broken["evidence_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in broken.items() if key != "evidence_payload_sha256"}
    )
    with pytest.raises(ValueError, match="not an exact PASS"):
        EVALUATE._validate_no_decoder_evidence(
            broken,
            generator_identity=generator,
            expected_output_inventory=inventory,
        )


def test_amendment_v3_enforces_6166_plus_two_and_whole_4491_exclusion() -> None:
    plan = _identity("/plan", "1")
    acquisition = _identity("/acquisition", "2")
    source = _identity("/source", "3")
    launcher = _identity(
        str((EVALUATE.HERE / "campaign_launcher_amended_v3.py").absolute()), "4"
    )
    normalizer = _identity(
        str((EVALUATE.HERE / "normalize_result_amended_v2.py").absolute()), "5"
    )
    normalizer["sha256"] = EVALUATE.EXPECTED_NORMALIZER_V2_SHA256
    evidence = _identity("/evidence", "6")
    evidence_generator = _identity("/evidence-generator", "7")
    closure = _identity("/closure", "8")
    closure_generator = _identity("/closure-generator", "9")
    inventory = _inventory()
    amendment = _amendment(
        plan=plan, acquisition=acquisition, source=source, launcher=launcher,
        normalizer=normalizer, evidence=evidence,
        evidence_generator=evidence_generator, inventory=inventory,
        closure=closure, closure_generator=closure_generator,
    )
    assert EVALUATE._validate_amendment(
        amendment,
        plan_identity=plan,
        acquisition_identity=acquisition,
        source_identity=source,
        launcher_identity=launcher,
        normalizer_identity=normalizer,
        no_decoder_evidence_identity=evidence,
        no_decoder_generator_identity=evidence_generator,
        no_decoder_evidence_payload_sha256="3" * 64,
        no_decoder_output_inventory=inventory,
        component_closure_identity=closure,
        component_closure_generator_identity=closure_generator,
    ) == amendment
    missing_security_test = copy.deepcopy(amendment)
    missing_security_test["amended_implementation"]["tests"].pop()
    missing_security_test["amendment_payload_sha256"] = EVALUATE.sha256_document(
        {
            key: value
            for key, value in missing_security_test.items()
            if key != "amendment_payload_sha256"
        }
    )
    with pytest.raises(ValueError, match="independent v3 security test bindings"):
        EVALUATE._validate_amendment(
            missing_security_test,
            plan_identity=plan,
            acquisition_identity=acquisition,
            source_identity=source,
            launcher_identity=launcher,
            normalizer_identity=normalizer,
            no_decoder_evidence_identity=evidence,
            no_decoder_generator_identity=evidence_generator,
            no_decoder_evidence_payload_sha256="3" * 64,
            no_decoder_output_inventory=inventory,
            component_closure_identity=closure,
            component_closure_generator_identity=closure_generator,
        )
    for mutator in (
        lambda value: value["schedule"].update(units_to_execute=6165),
        lambda value: value["prior_decoder_exposures"][0].update(decoder_rerun_permitted=True),
        lambda value: value["sensitivity_analysis"].update(exclude_observation_ids=[]),
    ):
        broken = copy.deepcopy(amendment)
        mutator(broken)
        broken["amendment_payload_sha256"] = EVALUATE.sha256_document(
            {key: value for key, value in broken.items() if key != "amendment_payload_sha256"}
        )
        with pytest.raises(ValueError, match="does not preserve"):
            EVALUATE._validate_amendment(
                broken,
                plan_identity=plan, acquisition_identity=acquisition,
                source_identity=source, launcher_identity=launcher,
                normalizer_identity=normalizer,
                no_decoder_evidence_identity=evidence,
                no_decoder_generator_identity=evidence_generator,
                no_decoder_evidence_payload_sha256="3" * 64,
                no_decoder_output_inventory=inventory,
                component_closure_identity=closure,
                component_closure_generator_identity=closure_generator,
            )


def _persistent_fixture(tmp_path: Path):
    executable = tmp_path / "python3.12"
    executable.write_bytes(b"python")
    executable_identity = EVALUATE.regular_file_identity(executable)
    keeper = {
        "pid": 777,
        "starttime_ticks": 12345,
        "mount_namespace_inode": 222,
        "mount_namespace_st_dev": 11,
        "euid": 0,
        "egid": 0,
        "executable": executable_identity,
    }
    private = {
        "root_mount_id": 41,
        "mount_record_count": 1,
        "mountinfo_sha256": "4" * 64,
        "forbidden_propagation_fields": [],
        "make_rprivate_completed_before_project_bind": True,
    }
    registration = _self_hashed(
        EVALUATE.PERSISTENT_NAMESPACE_SCHEMA_VERSION,
        "namespace_contract_payload_sha256",
        status="keeper_registered_root_recursively_private",
        keeper=keeper,
        root_private_attestation=private,
        project_mount_count_before_activation=0,
    )
    registration_path = tmp_path / "persistent-mount-namespace-registration-v3.json"
    registration_identity = _write_json(registration_path, registration)
    metadata = {
        "plan": _identity("/plan", "1"),
        "acquisition": _identity("/acquisition", "2"),
        "source": _identity("/source", "3"),
        "amendment": _identity("/amendment", "4"),
    }
    inventory = {"inventory_sha256": "5" * 64}
    active = _self_hashed(
        EVALUATE.PERSISTENT_NAMESPACE_SCHEMA_VERSION,
        "namespace_contract_payload_sha256",
        status="active_root_recursively_private",
        campaign_metadata=metadata,
        registration=registration_identity,
        keeper=keeper,
        root_private_attestation=private,
        external_inventory_before=inventory,
        external_inventory_after=inventory,
        external_namespace_mount_delta=0,
    )
    active_path = tmp_path / "persistent-mount-namespace-active-v3.json"
    active_identity = _write_json(active_path, active)
    project = tmp_path / "project"
    project.mkdir()
    project_status = project.lstat()
    readonly_project = {
        "mount_id": 42,
        "parent_id": 41,
        "major_minor": "0:1",
        "root": str(project),
        "mount_point": str(project),
        "mount_options": ["ro"],
        "optional_fields": [],
        "fs_type": "ext4",
        "mount_source": "/dev/root",
        "super_options": ["rw"],
        "path": str(project),
        "st_dev": project_status.st_dev,
        "st_ino": project_status.st_ino,
        "uid": project_status.st_uid,
        "gid": project_status.st_gid,
        "mode": stat.S_IMODE(project_status.st_mode),
    }
    guard = {
        "persistent_mount_namespace": active_identity,
        "persistent_mount_namespace_attestation": {
            "mount_namespace_inode": 222,
            "root_recursively_private": True,
            "make_rprivate_completed_before_project_bind": True,
            "external_namespace_mount_delta": 0,
            "external_inventory_sha256": "5" * 64,
        },
        "control_parent": {"path": str(tmp_path)},
        "readonly_project_mount": readonly_project,
    }
    return executable, metadata, guard


def test_active_persistent_namespace_is_bound_to_keeper_and_current_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, metadata, guard = _persistent_fixture(tmp_path)
    monkeypatch.setattr(EVALUATE, "_validate_control_artifact_path", lambda *args: {})
    monkeypatch.setattr(EVALUATE, "_proc_starttime", lambda _path: 12345)
    monkeypatch.setattr(EVALUATE, "_namespace_descriptor", lambda _path: (11, 222))
    monkeypatch.setattr(
        EVALUATE,
        "_bounded_proc_read",
        lambda path, maximum_bytes=0: b"Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\n",
    )
    monkeypatch.setattr(
        EVALUATE,
        "_root_private_mountinfo",
        lambda _path: {
            "root_mount_id": 41,
            "mountinfo_sha256": "6" * 64,
            "forbidden_propagation_fields": [],
            "records": [
                {key: guard["readonly_project_mount"][key] for key in (
                    "mount_id", "parent_id", "major_minor", "root",
                    "mount_point", "mount_options", "optional_fields",
                    "fs_type", "mount_source", "super_options",
                )}
            ],
        },
    )
    active, identity, live = EVALUATE._validate_persistent_namespace_contract(
        guard,
        plan_identity=metadata["plan"],
        acquisition_identity=metadata["acquisition"],
        source_identity=metadata["source"],
        amendment_identity=metadata["amendment"],
        proc_root=tmp_path / "proc",
    )
    assert active["status"] == "active_root_recursively_private"
    assert identity == guard["persistent_mount_namespace"]
    assert live["mount_namespace_inode"] == 222
    assert live["root_recursively_private"] is True
    assert live["readonly_project_has_zero_nested_mounts"] is True


def test_active_persistent_namespace_rejects_current_namespace_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, metadata, guard = _persistent_fixture(tmp_path)
    monkeypatch.setattr(EVALUATE, "_validate_control_artifact_path", lambda *args: {})
    monkeypatch.setattr(EVALUATE, "_proc_starttime", lambda _path: 12345)
    monkeypatch.setattr(
        EVALUATE,
        "_namespace_descriptor",
        lambda path: (11, 333) if "self" in str(path) else (11, 222),
    )
    monkeypatch.setattr(
        EVALUATE,
        "_bounded_proc_read",
        lambda path, maximum_bytes=0: b"Uid:\t0\t0\t0\t0\nGid:\t0\t0\t0\t0\n",
    )
    with pytest.raises(ValueError, match="keeper/live execution identity drift"):
        EVALUATE._validate_persistent_namespace_contract(
            guard,
            plan_identity=metadata["plan"],
            acquisition_identity=metadata["acquisition"],
            source_identity=metadata["source"],
            amendment_identity=metadata["amendment"],
            proc_root=tmp_path / "proc",
        )


def test_live_root_mountinfo_rejects_shared_tag(tmp_path: Path) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "41 1 0:1 / / rw shared:7 - ext4 /dev/root rw\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not recursively private"):
        EVALUATE._root_private_mountinfo(mountinfo)
    mountinfo.write_text("41 1 0:1 / / rw - ext4 /dev/root rw\n", encoding="utf-8")
    assert EVALUATE._root_private_mountinfo(mountinfo)["root_mount_id"] == 41


def test_runtime_guard_v3_binds_launcher_builder_normalizer_namespace_and_closure() -> None:
    plan = _identity("/plan", "1")
    acquisition = _identity("/acquisition", "2")
    source = _identity("/source", "3")
    amendment_identity = _identity("/amendment", "4")
    builder = _identity(
        str((EVALUATE.HERE / "build_runtime_guard_v3.py").absolute()), "5"
    )
    launcher = _identity(
        str((EVALUATE.HERE / "campaign_launcher_amended_v3.py").absolute()), "6"
    )
    normalizer = _identity(
        str((EVALUATE.HERE / "normalize_result_amended_v2.py").absolute()), "7"
    )
    frozen = _identity("/project/campaign_launcher.py", "8")
    closure = _identity("/closure", "9")
    closure_generator = _identity("/closure-generator", "a")
    dependencies = {
        "runtime_guard_builder_v3": builder,
        "campaign_launcher_v3": launcher,
        "frozen_campaign_launcher": frozen,
        "normalizer_v2": normalizer,
    }
    amendment = {
        "amended_launcher": launcher,
        "amended_normalizer": normalizer,
        "component_runtime_closure": closure,
        "component_runtime_closure_generator": closure_generator,
        "capacity_preflight_contract": EVALUATE._expected_capacity_contract(),
        "transitive_code_dependency_contract": {
            "verified_from_exact_bytes_before_import": True,
            "dependencies": dependencies,
        },
        "amended_implementation": {
            "runtime_guard_builder": builder,
            "transitive_code_dependencies": dependencies,
        },
    }
    guard = _self_hashed(
        EVALUATE.RUNTIME_GUARD_SCHEMA_VERSION,
        "runtime_guard_payload_sha256",
        status="active",
        execution_plan=plan,
        acquisition_manifest=acquisition,
        source_manifest=source,
        amendment=amendment_identity,
        runtime_guard_builder=builder,
        runtime_guard_builder_control_copy={
            **builder,
            "path": "/control/build_runtime_guard_v3.py",
        },
        transitive_code_dependencies=dependencies,
        control_parent={"path": "/control", "uid": 0, "gid": 0, "mode": 0o755},
        campaign_results_parent={
            "path": str(EVALUATE.RESULTS_PARENT_V3), "st_dev": 1, "st_ino": 2,
            "uid": 1000, "gid": 1000, "mode": 0o700,
            "capacity_bytes": EVALUATE.RESULTS_TMPFS_CAPACITY_BYTES,
            "minimum_available_bytes_before_work": (
                EVALUATE.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
            ),
            "available_bytes_formula": "statvfs.f_bavail*statvfs.f_frsize",
            "mount_record": {},
        },
        results_authority_mount={
            "path": str(EVALUATE.RESULTS_PARENT_V3), "st_dev": 1, "st_ino": 2,
            "uid": 1000, "gid": 1000, "mode": 0o700,
            "capacity_bytes": EVALUATE.RESULTS_TMPFS_CAPACITY_BYTES,
            "minimum_available_bytes_before_work": (
                EVALUATE.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
            ),
            "available_bytes_formula": "statvfs.f_bavail*statvfs.f_frsize",
            "mount_record": {},
        },
        capacity_contract=EVALUATE._expected_capacity_contract(),
        capacity_preflight_at_guard_freeze={},
        sealed_input_snapshot={},
        sealed_input_authority_mapping=[],
        sealed_project_artifacts={},
        sealed_project_artifact_mapping=[],
        runtime_authority_mounts={},
        seal_window_closed=True,
        persistent_mount_namespace=_identity("/control/persistent-mount-namespace-active-v3.json", "b"),
        persistent_mount_namespace_attestation={"mount_namespace_inode": 1},
        component_runtime_closure=closure,
        component_runtime_closure_generator=closure_generator,
        component_runtime_closure_validation={
            "content_manifest_sha256": EVALUATE.EXPECTED_COMPONENT_CLOSURE_CONTENT_SHA256,
            "preseal_raw_manifest_exact": True,
            "sealed_semantic_manifest_exact": True,
            "pre_campaign_semantic_manifest_exact": True,
            "post_campaign_semantic_manifest_required": True,
        },
        component_runtime_python_startup={
            "bubblewrap_clearenv": True,
            "python_no_user_site": "1",
            "script_directory_inside_readonly_runtime_mount": "/runtime",
            "all_interpreter_library_paths_inside_component_runtime": True,
        },
    )
    assert EVALUATE._validate_runtime_guard(
        guard,
        plan_identity=plan,
        acquisition_identity=acquisition,
        source_identity=source,
        amendment_identity=amendment_identity,
        amendment=amendment,
    ) == guard
    broken = copy.deepcopy(guard)
    broken["transitive_code_dependencies"]["campaign_launcher_v3"] = _identity("/wrong", "c")
    broken["runtime_guard_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in broken.items() if key != "runtime_guard_payload_sha256"}
    )
    with pytest.raises(ValueError, match="closed root-control lifecycle"):
        EVALUATE._validate_runtime_guard(
            broken,
            plan_identity=plan,
            acquisition_identity=acquisition,
            source_identity=source,
            amendment_identity=amendment_identity,
            amendment=amendment,
        )


def test_sensitivity_removes_every_unit_and_record_from_observation_4491() -> None:
    calls: list[dict[str, object]] = []

    def evaluate_campaign(**kwargs):
        calls.append(kwargs)
        return {"status": "complete"}

    prior = EVALUATE.FROZEN_SCORING
    EVALUATE.FROZEN_SCORING = SimpleNamespace(evaluate_campaign=evaluate_campaign)
    try:
        observations = [4491, *range(5000, 5029)]
        units = [{"unit_id": str(value), "observation_id": value} for value in observations]
        records = [{"unit": dict(unit)} for unit in units]
        EVALUATE._evaluate_both_cohorts(
            expected_units=units,
            records=records,
            observation_ids=observations,
            null_ids=["null"],
            mission_observation_ids=observations,
            plan_sha256="1" * 64,
            acquisition_sha256="2" * 64,
            normalizer_identity=_identity("/normalizer", "3"),
            far_null_ids=["null"],
            minimum_null_exposure_hours=1.0,
            maximum_null_rate_per_hour=1.0,
        )
    finally:
        EVALUATE.FROZEN_SCORING = prior
    assert len(calls) == 2
    assert len(calls[0]["observation_ids"]) == 30
    assert len(calls[1]["observation_ids"]) == 29
    assert all(item["observation_id"] != 4491 for item in calls[1]["expected_units"])
    assert all(item["unit"]["observation_id"] != 4491 for item in calls[1]["records"])


def test_namespace_and_lock_gates_precede_dependency_activation_and_outcome_reads() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    start = source.index("def evaluate_artifacts(")
    body = source[start:]
    namespace_gate = body.index("_validate_persistent_namespace_contract(")
    lock_gate = body.index("_validate_evaluator_lock(")
    activation = body.index("_activate_frozen_dependencies(")
    outcome = body.index("manifest, manifest_identity = _artifact_json(unit_manifest_path)")
    assert namespace_gate < lock_gate < activation < outcome


def test_source_has_no_iq_or_decoder_input_and_revalidates_namespace() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    signature = source[source.index("def evaluate_artifacts("):source.index(") -> dict[str, object]:", source.index("def evaluate_artifacts("))]
    assert "iq_path" not in signature
    assert "decoder_path" not in signature
    assert "candidate_runtime_path" not in signature
    assert source.count("_validate_persistent_namespace_contract(") >= 3
    assert '"exact_6166_new_plus_2_historical_normalization_only": True' in source


def _role_handoff_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    handoff_change: tuple[str, object] | None = None,
    extra_environment: tuple[str, str] | None = None,
    self_namespace_inode: int = 222,
    invalid_rfc3161_imprint: bool = False,
    invalid_lock_freeze_receipt: bool = False,
) -> list[str]:
    control = tmp_path / "control"
    control.mkdir(parents=True)
    results = tmp_path / "results"
    results.mkdir(parents=True)
    results.chmod(0o700)
    sealed_source_root = tmp_path / "holdout-iq-v4"
    sealed_source_root.mkdir()
    sealed_target_root = control / "sealed-input-v3"
    sealed_target_root.mkdir()
    runtime_authority_root = control / "runtime"
    runtime_authority_root.mkdir()
    candidate_authority = runtime_authority_root / "candidate"
    component_authority = runtime_authority_root / "component"
    candidate_authority.mkdir()
    component_authority.mkdir()
    sealed_project_artifacts_root = control / "sealed-project-artifacts-v3"
    sealed_project_artifacts_root.mkdir()
    monkeypatch.chdir(results)
    monkeypatch.setattr(EVALUATE, "CONTROL_PARENT_V3", control)
    monkeypatch.setattr(EVALUATE, "RESULTS_PARENT_V3", results)
    monkeypatch.setattr(EVALUATE, "SEALED_INPUT_SOURCE_ROOT", sealed_source_root)
    monkeypatch.setattr(EVALUATE, "SEALED_INPUT_TARGET_ROOT", sealed_target_root)
    monkeypatch.setattr(EVALUATE, "RUNTIME_AUTHORITY_ROOT", runtime_authority_root)
    monkeypatch.setattr(
        EVALUATE, "SEALED_PROJECT_ARTIFACTS_ROOT", sealed_project_artifacts_root
    )
    monkeypatch.setattr(EVALUATE.sys, "executable", "/usr/bin/python3.12")
    monkeypatch.setattr(EVALUATE, "_validate_root_control_handoff_file", lambda _path: None)
    monkeypatch.setattr(
        EVALUATE, "_validate_root_control_file", lambda _path, **_kwargs: None
    )
    monkeypatch.setattr(
        EVALUATE,
        "_post_exec_security_state",
        lambda: {
            "euid": 1000, "egid": 1000, "supplementary_gids": [],
            "cap_inheritable": 0, "cap_permitted": 0, "cap_effective": 0,
            "cap_ambient": 0, "no_new_privileges": 1, "dumpable": 0,
        },
    )
    monkeypatch.setattr(
        EVALUATE, "_namespace_descriptor", lambda _path: (11, self_namespace_inode)
    )
    monkeypatch.setattr(EVALUATE.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(EVALUATE.os, "getegid", lambda: 1000)
    monkeypatch.setattr(EVALUATE.os, "getgroups", lambda: [])
    EVALUATE.EVALUATOR_ROLE_HANDOFF = None
    EVALUATE._ACTIVE_PROJECT_ARTIFACT_MAPPING = None
    monkeypatch.setattr(
        EVALUATE, "_configure_project_artifact_mapping", lambda _guard: {}
    )

    def json_artifact(name: str, document: object) -> tuple[Path, dict[str, object]]:
        path = tmp_path / name
        return path, _write_json(path, document)

    def code_artifact(name: str) -> tuple[Path, dict[str, object]]:
        path = tmp_path / name
        path.write_text(f"# {name}\n", encoding="utf-8")
        return path, EVALUATE.regular_file_identity(path)

    plan_path, plan = json_artifact("plan.json", {})
    acquisition_objects = [
        {
            "observation_id": index,
            "local_path": str(sealed_source_root / f"observation-{index}.iq"),
            "actual_size_bytes": index + 1,
            "sha256": f"{index + 1:064x}",
        }
        for index in range(EVALUATE.EXPECTED_OBSERVATION_COUNT)
    ]
    acquisition_path, acquisition = json_artifact(
        "acquisition.json", {"objects": acquisition_objects}
    )
    source_path, source = json_artifact("source.json", {})
    amendment_path, amendment = json_artifact(
        "amendment.json",
        _self_hashed(
            EVALUATE.AMENDMENT_SCHEMA_VERSION,
            "amendment_payload_sha256",
            status="test",
        ),
    )
    evidence_path, evidence = json_artifact("evidence.json", {})
    closure_path, closure = json_artifact("closure.json", {})
    evidence_generator_path, evidence_generator = code_artifact("evidence-generator.py")
    closure_generator_path, closure_generator = code_artifact("closure-generator.py")
    launcher_path, launcher = code_artifact("launcher-v3.py")
    normalizer_path, normalizer = code_artifact("normalizer-v2.py")
    freezer_path, freezer = code_artifact("freeze-amended-evaluator-lock-v2.py")
    evaluator_identity = EVALUATE.regular_file_identity(SCRIPT)
    amendment = _write_json(
        amendment_path,
        _self_hashed(
            EVALUATE.AMENDMENT_SCHEMA_VERSION,
            "amendment_payload_sha256",
            status="test",
            amended_implementation={
                "evaluator_lock_freezer": freezer,
                "evaluator": evaluator_identity,
            },
        ),
    )
    active = _self_hashed(
        EVALUATE.PERSISTENT_NAMESPACE_SCHEMA_VERSION,
        "namespace_contract_payload_sha256",
        status="active_root_recursively_private",
        keeper={"mount_namespace_st_dev": 11, "mount_namespace_inode": 222},
    )
    active_path, active_identity = json_artifact(
        "persistent-mount-namespace-active-v3.json", active
    )
    def mount_record(path: Path, *, readonly: bool, tmpfs: bool) -> dict[str, object]:
        status = path.lstat()
        return {
            "mount_id": 100 + len(str(path)),
            "parent_id": 1,
            "major_minor": f"{status.st_dev >> 8}:{status.st_dev & 255}",
            "root": "/",
            "mount_point": str(path),
            "mount_options": sorted(
                {"ro" if readonly else "rw", "nosuid", "nodev", "noexec"}
            ),
            "optional_fields": [],
            "fs_type": "tmpfs" if tmpfs else "ext4",
            "mount_source": "tmpfs" if tmpfs else "/dev/test",
            "super_options": ["rw"],
        }

    sealed_mount_record = mount_record(
        sealed_target_root, readonly=True, tmpfs=True
    )
    snapshot_mounts = []
    for item in sorted(acquisition_objects, key=lambda value: value["local_path"]):
        source_identity = {
            "path": item["local_path"],
            "size_bytes": item["actual_size_bytes"],
            "sha256": item["sha256"],
        }
        snapshot_mounts.append(
            {
                "schema_version": EVALUATE.SEALED_INPUT_MOUNT_SCHEMA_VERSION,
                "status": "PASS",
                "source": source_identity,
                "mount_point": str(sealed_target_root),
                "sealed_path": str(
                    sealed_target_root / Path(item["local_path"]).relative_to(
                        sealed_source_root
                    )
                ),
                "mounted_inode": {
                    "st_dev": sealed_target_root.lstat().st_dev,
                    "st_ino": 1000 + item["observation_id"],
                    "size_bytes": item["actual_size_bytes"],
                    "uid": 0,
                    "gid": 0,
                    "mode": 0o444,
                    "nlink": 1,
                    "sha256": item["sha256"],
                },
                "mount_options": ["ro", "nosuid", "nodev", "noexec"],
                "mount_record": sealed_mount_record,
            }
        )
    sealed_snapshot = _self_hashed(
        EVALUATE.SEALED_INPUT_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_payload_sha256",
        status="PASS",
        input_count=EVALUATE.EXPECTED_OBSERVATION_COUNT,
        mounts=snapshot_mounts,
    )
    protected_roots: dict[str, object] = {}
    runtime_authorities: dict[str, object] = {}
    for label, authority_path in (
        ("candidate", candidate_authority), ("component", component_authority)
    ):
        authority_identity = {
            **EVALUATE._live_directory_identity(authority_path),
            "recursive_entry_count": 0,
            "recursive_manifest_sha256": hashlib.sha256(label.encode()).hexdigest(),
        }
        source_identity = dict(authority_identity)
        source_identity["path"] = f"/sealed-{label}"
        protected_roots[label] = source_identity
        runtime_authorities[label] = {
            "source": source_identity,
            "authority": authority_identity,
            "mount_record": mount_record(
                authority_path, readonly=True, tmpfs=False
            ),
        }
    results_authority = {
        **EVALUATE._live_directory_identity(results),
        "capacity_bytes": EVALUATE.RESULTS_TMPFS_CAPACITY_BYTES,
        "minimum_available_bytes_before_work": (
            EVALUATE.RESULTS_MINIMUM_AVAILABLE_BYTES_BEFORE_WORK
        ),
        "available_bytes_formula": "statvfs.f_bavail*statvfs.f_frsize",
        "mount_record": mount_record(results, readonly=False, tmpfs=True),
    }
    capacity_contract = EVALUATE._expected_capacity_contract()
    project_artifact_mount_record = mount_record(
        sealed_project_artifacts_root, readonly=True, tmpfs=True
    )
    sealed_evaluator_identity = {
        **evaluator_identity,
        "path": str(
            sealed_project_artifacts_root / SCRIPT.relative_to(EVALUATE.PROJECT_ROOT)
        ),
    }
    sealed_project_artifacts = _self_hashed(
        EVALUATE.SEALED_PROJECT_ARTIFACTS_SCHEMA_VERSION,
        "snapshot_payload_sha256",
        status="PASS",
        artifact_count=1,
        mount_point=str(sealed_project_artifacts_root),
        mount_record=project_artifact_mount_record,
        artifacts=[{
            "source": evaluator_identity,
            "sealed": sealed_evaluator_identity,
            "mounted_inode": {
                "st_dev": sealed_project_artifacts_root.lstat().st_dev,
                "st_ino": 4001,
                "uid": 0, "gid": 0, "mode": 0o444, "nlink": 1,
            },
        }],
    )
    guard_document = _self_hashed(
        EVALUATE.RUNTIME_GUARD_SCHEMA_VERSION,
        "runtime_guard_payload_sha256",
        persistent_mount_namespace=active_identity,
        control_parent=EVALUATE._live_directory_identity(control),
        campaign_results_parent=results_authority,
        results_authority_mount=results_authority,
        capacity_contract=capacity_contract,
        capacity_preflight_at_guard_freeze={"synthetic": True},
        sealed_input_snapshot=sealed_snapshot,
        sealed_input_authority_mapping=[
            {"source": record["source"], "sealed_path": record["sealed_path"]}
            for record in snapshot_mounts
        ],
        sealed_project_artifacts=sealed_project_artifacts,
        sealed_project_artifact_mapping=[{
            "source": evaluator_identity, "sealed": sealed_evaluator_identity,
        }],
        runtime_authority_mounts=runtime_authorities,
        protected_roots=protected_roots,
        readonly_project_mount_contract=_identity("/control/readonly.json", "4"),
        sealed_runtime_state=_identity("/control/sealed.json", "5"),
    )
    guard_path, guard = json_artifact("runtime-guard.json", guard_document)
    lock_document = _self_hashed(
        EVALUATE.LOCK_SCHEMA_VERSION,
        "evaluator_lock_payload_sha256",
        runtime_guard=guard,
        amendment=amendment,
        no_decoder_evidence=evidence,
        no_decoder_evidence_generator=evidence_generator,
        component_runtime_closure=closure,
        component_runtime_closure_generator=closure_generator,
        amended_launcher=launcher,
        amended_normalizer=normalizer,
        amended_evaluator=evaluator_identity,
        persistent_mount_namespace=active_identity,
        campaign_results_parent=results_authority,
        capacity_contract=capacity_contract,
    )
    lock_path, lock = json_artifact("evaluator-lock.json", lock_document)
    campaign_metadata = {
        "plan": plan, "acquisition": acquisition,
        "source": source, "amendment": amendment,
    }
    openssl_path, openssl_identity = code_artifact("openssl")
    monkeypatch.setattr(EVALUATE, "OPENSSL_PATH", openssl_path.absolute())
    receipt_attempt = "b" * 64
    receipt_document = _self_hashed(
        EVALUATE.ROOT_METADATA_RECEIPT_SCHEMA_VERSION,
        "receipt_payload_sha256",
        status="PASS",
        label="evaluator-lock",
        attempt_id=receipt_attempt,
        argv=[str(freezer_path), "--output", str(lock_path)],
        shell_used=False,
        close_fds=True,
        return_code=0,
        stdout_size_bytes=0,
        stdout_sha256=hashlib.sha256(b"").hexdigest(),
        stderr_size_bytes=0,
        stderr_sha256=hashlib.sha256(b"").hexdigest(),
        output=lock,
        error_type=None,
        error=None,
        cleanup_errors=[],
    )
    if invalid_lock_freeze_receipt:
        receipt_document["output"] = _identity("/wrong-lock", "0")
        receipt_document["receipt_payload_sha256"] = EVALUATE.sha256_document(
            {
                key: value
                for key, value in receipt_document.items()
                if key != "receipt_payload_sha256"
            }
        )
    receipt_path = control / (
        f"root-metadata-execution-receipt-v3-evaluator-lock-{receipt_attempt}.json"
    )
    receipt_identity = _write_json(receipt_path, receipt_document)
    freeze_document = _self_hashed(
        EVALUATE.EVALUATOR_LOCK_FREEZE_ATTESTATION_SCHEMA_VERSION,
        "lock_freeze_attestation_payload_sha256",
        status="PASS",
        campaign_metadata=campaign_metadata,
        runtime_guard=guard,
        evaluator_lock=lock,
        execution_receipt=receipt_identity,
        freezer=freezer,
        evaluator=evaluator_identity,
        pinned_script_bootstrap_schema=EVALUATE.PINNED_SCRIPT_BOOTSTRAP_SCHEMA,
        pinned_script_bootstrap_sha256=hashlib.sha256(
            EVALUATE.PINNED_SCRIPT_BOOTSTRAP.encode("utf-8")
        ).hexdigest(),
    )
    freeze_path = control / EVALUATE.EVALUATOR_LOCK_FREEZE_ATTESTATION_NAME
    freeze_identity = _write_json(freeze_path, freeze_document)
    timestamp_records: dict[str, object] = {}
    for label, data_identity in (
        ("amendment_v3", amendment),
        ("evaluator_lock", lock),
    ):
        query_path, query_identity = code_artifact(f"{label}.tsq")
        response_path, response_identity = code_artifact(f"{label}.tsr")
        timestamp_records[label] = {
            "status": "PASS",
            "data": data_identity,
            "query": query_identity,
            "response": response_identity,
            "message_imprint_sha256": data_identity["sha256"],
            "generation_time_utc": "2026-09-06T00:00:00Z",
            "query_verification": "OK",
            "data_verification": "OK",
        }
    if invalid_rfc3161_imprint:
        timestamp_records["amendment_v3"]["message_imprint_sha256"] = "0" * 64
    rfc3161_document = _self_hashed(
        EVALUATE.RFC3161_ATTESTATION_SCHEMA_VERSION,
        "attestation_payload_sha256",
        status="PASS",
        campaign_metadata=campaign_metadata,
        amendment=amendment,
        runtime_guard=guard,
        evaluator_lock=lock,
        evaluator_lock_freeze_attestation=freeze_identity,
        openssl=openssl_identity,
        timestamps=timestamp_records,
        offline_verification=True,
    )
    rfc3161_path = control / EVALUATE.RFC3161_ATTESTATION_NAME
    rfc3161_identity = _write_json(rfc3161_path, rfc3161_document)
    output_root = results / "campaign"
    output_root.mkdir(mode=0o700)
    unit_manifest = output_root / "unit-manifest-amended.json"
    unit_manifest_document = _self_hashed(
        EVALUATE.UNIT_MANIFEST_SCHEMA_VERSION,
        "unit_manifest_payload_sha256",
        status="complete",
    )
    unit_manifest_identity = _write_json(unit_manifest, unit_manifest_document)
    output = output_root / EVALUATE.EVALUATION_OUTPUT_NAME
    campaign_nonce = "c" * 64
    campaign_attempt = hashlib.sha256(campaign_nonce.encode("ascii")).hexdigest()
    campaign_handoff_path = (
        control / f"namespace-role-handoff-v3-campaign-{campaign_attempt}.json"
    )
    campaign_handoff = _self_hashed(
        EVALUATE.ROLE_HANDOFF_SCHEMA_VERSION,
        "handoff_payload_sha256",
        status="authorized_before_uid_drop_and_exec",
        role="campaign",
        attempt_id=campaign_attempt,
        nonce_sha256=campaign_attempt,
        child_pid=321,
        campaign_metadata=campaign_metadata,
        persistent_mount_namespace=active_identity,
        runtime_guard=guard,
        evaluator_lock=lock,
        sealed_input_snapshot=sealed_snapshot,
        sealed_project_artifacts=sealed_project_artifacts,
        runtime_authority_mounts=runtime_authorities,
        results_authority_mount=results_authority,
        capacity_contract=capacity_contract,
        campaign_completion=None,
        rfc3161_attestation=rfc3161_identity,
        no_decoder_evidence=evidence,
        candidate_interpreter=EVALUATE.regular_file_identity(
            Path(sys.executable), maximum_bytes=32 * 1024 * 1024
        ),
        closed_mount_contract=guard_document["readonly_project_mount_contract"],
        sealed_runtime_state=guard_document["sealed_runtime_state"],
        role_script=launcher,
        role_script_fd=18,
        role_script_bootstrap_schema=EVALUATE.PINNED_SCRIPT_BOOTSTRAP_SCHEMA,
        role_script_bootstrap_sha256=hashlib.sha256(
            EVALUATE.PINNED_SCRIPT_BOOTSTRAP.encode("utf-8")
        ).hexdigest(),
        role_arguments=["--synthetic", "campaign"],
        exec_argv=["/synthetic", "campaign"],
        cwd=str(results),
        role_output_root=str(output_root),
        role_output_filename="unit-manifest-amended.json",
        target_identity={"euid": 1000, "egid": 1000, "supplementary_gids": []},
        preserved_stdio_fds=[0, 1, 2],
        role_ack_fd=19,
        role_ack_schema=EVALUATE.ROLE_ACK_SCHEMA_VERSION,
        role_ack_nonce_sha256=campaign_attempt,
        shell_used=False,
        no_new_privileges_required=True,
        zero_capabilities_required=True,
    )
    campaign_handoff_identity = _write_json(
        campaign_handoff_path, campaign_handoff
    )
    attestation_path = control / (
        f"namespace-role-output-attestation-v3-campaign-{campaign_attempt}.json"
    )
    attestation = _self_hashed(
        EVALUATE.ROLE_OUTPUT_ATTESTATION_SCHEMA_VERSION,
        "attestation_payload_sha256",
        status="root_attested_after_zero_exit",
        role="campaign",
        attempt_id=campaign_attempt,
        handoff=campaign_handoff_identity,
        output_root=EVALUATE._live_directory_identity(output_root),
        role_output=unit_manifest_identity,
        role_output_schema=EVALUATE.UNIT_MANIFEST_SCHEMA_VERSION,
        role_output_self_hash_field="unit_manifest_payload_sha256",
        role_output_payload_sha256=unit_manifest_document[
            "unit_manifest_payload_sha256"
        ],
        capacity_contract=capacity_contract,
    )
    attestation_identity = _write_json(attestation_path, attestation)
    campaign_ack = json.dumps(
        {
            "attempt_id": campaign_attempt,
            "handoff_payload_sha256": campaign_handoff[
                "handoff_payload_sha256"
            ],
            "role": "campaign",
            "schema_version": EVALUATE.ROLE_ACK_SCHEMA_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    completion_path = control / (
        f"namespace-role-completion-v3-campaign-{campaign_attempt}.json"
    )
    completion = _self_hashed(
        EVALUATE.ROLE_COMPLETION_SCHEMA_VERSION,
        "completion_payload_sha256",
        status="exec_reached_role_exited_zero",
        role="campaign",
        attempt_id=campaign_attempt,
        handoff=campaign_handoff_identity,
        child_pid=321,
        child_wait_status=0,
        return_code=0,
        exec_failure_diagnostic_bytes=0,
        post_exec_role_acknowledgment_verified=True,
        role_acknowledgment_sha256=hashlib.sha256(campaign_ack).hexdigest(),
        campaign_metadata=campaign_metadata,
        persistent_mount_namespace=active_identity,
        runtime_guard=guard,
        evaluator_lock=lock,
        sealed_input_snapshot=sealed_snapshot,
        sealed_project_artifacts=sealed_project_artifacts,
        runtime_authority_mounts=runtime_authorities,
        results_authority_mount=results_authority,
        capacity_contract=capacity_contract,
        campaign_completion=None,
        rfc3161_attestation=rfc3161_identity,
        role_output_root=str(output_root),
        role_output_attestation=attestation_identity,
        role_output=unit_manifest_identity,
    )
    completion_identity = _write_json(completion_path, completion)
    pairs = (
        ("--execution-plan", plan_path),
        ("--expected-execution-plan-sha256", plan["sha256"]),
        ("--acquisition-manifest", acquisition_path),
        ("--expected-acquisition-manifest-sha256", acquisition["sha256"]),
        ("--source-manifest", source_path),
        ("--expected-source-manifest-sha256", source["sha256"]),
        ("--amendment", amendment_path),
        ("--expected-amendment-sha256", amendment["sha256"]),
        ("--runtime-guard", guard_path),
        ("--expected-runtime-guard-sha256", guard["sha256"]),
        ("--no-decoder-evidence", evidence_path),
        ("--expected-no-decoder-evidence-sha256", evidence["sha256"]),
        ("--no-decoder-evidence-generator", evidence_generator_path),
        ("--expected-no-decoder-evidence-generator-sha256", evidence_generator["sha256"]),
        ("--component-closure", closure_path),
        ("--expected-component-closure-sha256", closure["sha256"]),
        ("--component-closure-generator", closure_generator_path),
        ("--expected-component-closure-generator-sha256", closure_generator["sha256"]),
        ("--amended-launcher", launcher_path),
        ("--expected-amended-launcher-sha256", launcher["sha256"]),
        ("--amended-normalizer", normalizer_path),
        ("--expected-amended-normalizer-sha256", normalizer["sha256"]),
        ("--evaluator-lock", lock_path),
        ("--expected-evaluator-lock-sha256", lock["sha256"]),
        ("--campaign-completion", completion_path),
        ("--expected-campaign-completion-sha256", completion_identity["sha256"]),
        ("--rfc3161-attestation", rfc3161_path),
        ("--expected-rfc3161-attestation-sha256", rfc3161_identity["sha256"]),
        ("--unit-manifest", unit_manifest),
        ("--output", output),
    )
    argv = [value for option, raw in pairs for value in (option, str(raw))]
    nonce = "a" * 64
    attempt = hashlib.sha256(nonce.encode("ascii")).hexdigest()
    role_script_fd = 9999
    handoff_path = control / f"namespace-role-handoff-v3-evaluator-{attempt}.json"
    handoff = _self_hashed(
        EVALUATE.ROLE_HANDOFF_SCHEMA_VERSION,
        "handoff_payload_sha256",
        status="authorized_before_uid_drop_and_exec",
        role="evaluator",
        attempt_id=attempt,
        nonce_sha256=attempt,
        child_pid=EVALUATE.os.getpid(),
        campaign_metadata={
            "plan": plan, "acquisition": acquisition,
            "source": source, "amendment": amendment,
        },
        persistent_mount_namespace=active_identity,
        runtime_guard=guard,
        evaluator_lock=lock,
        sealed_input_snapshot=sealed_snapshot,
        sealed_project_artifacts=sealed_project_artifacts,
        runtime_authority_mounts=runtime_authorities,
        results_authority_mount=results_authority,
        capacity_contract=capacity_contract,
        campaign_completion=completion_identity,
        rfc3161_attestation=rfc3161_identity,
        no_decoder_evidence=evidence,
        candidate_interpreter=EVALUATE.regular_file_identity(
            Path(sys.executable), maximum_bytes=32 * 1024 * 1024
        ),
        closed_mount_contract=guard_document["readonly_project_mount_contract"],
        sealed_runtime_state=guard_document["sealed_runtime_state"],
        role_script=evaluator_identity,
        role_script_fd=role_script_fd,
        role_script_bootstrap_schema=EVALUATE.PINNED_SCRIPT_BOOTSTRAP_SCHEMA,
        role_script_bootstrap_sha256=hashlib.sha256(
            EVALUATE.PINNED_SCRIPT_BOOTSTRAP.encode("utf-8")
        ).hexdigest(),
        role_arguments=argv,
        exec_argv=[
            str(Path(sys.executable).absolute()), "-I", "-c",
            EVALUATE.PINNED_SCRIPT_BOOTSTRAP, str(role_script_fd),
            str(SCRIPT.absolute()), evaluator_identity["sha256"], *argv,
        ],
        cwd=str(results),
        role_output_root=str(output_root),
        role_output_filename=EVALUATE.EVALUATION_OUTPUT_NAME,
        target_identity={"euid": 1000, "egid": 1000, "supplementary_gids": []},
        preserved_stdio_fds=[0, 1, 2],
        role_ack_fd=0,
        role_ack_schema=EVALUATE.ROLE_ACK_SCHEMA_VERSION,
        role_ack_nonce_sha256=attempt,
        shell_used=False,
        no_new_privileges_required=True,
        zero_capabilities_required=True,
    )
    if handoff_change is not None:
        handoff[handoff_change[0]] = handoff_change[1]
        handoff["handoff_payload_sha256"] = EVALUATE.sha256_document(
            {key: value for key, value in handoff.items() if key != "handoff_payload_sha256"}
        )
    _write_json(handoff_path, handoff)
    ack_read, ack_write = EVALUATE.os.pipe()
    handoff["role_ack_fd"] = ack_write
    handoff["handoff_payload_sha256"] = EVALUATE.sha256_document(
        {key: value for key, value in handoff.items() if key != "handoff_payload_sha256"}
    )
    _write_json(handoff_path, handoff)
    monkeypatch.setattr(EVALUATE, "_TEST_ACK_READ_FD", ack_read, raising=False)
    monkeypatch.setattr(EVALUATE, "_TEST_ACK_WRITE_FD", ack_write, raising=False)
    environment = {
        "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
        "PYTHONNOUSERSITE": "1", "TY_NAMESPACE_ROLE": "evaluator",
        "TY_NAMESPACE_ROLE_HANDOFF": str(handoff_path),
        "TY_NAMESPACE_ROLE_NONCE": nonce,
        "TY_ROLE_ACK_FD": str(ack_write),
        "TY_ROLE_ACK_NONCE": nonce,
    }
    if extra_environment is not None:
        environment[extra_environment[0]] = extra_environment[1]
    monkeypatch.setattr(EVALUATE.os, "environ", environment)
    monkeypatch.setattr(
        EVALUATE, "_validate_control_artifact_path", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_persistent_namespace_contract",
        lambda *args, **kwargs: (
            active,
            active_identity,
            {
                "mount_namespace_st_dev": 11,
                "mount_namespace_inode": self_namespace_inode,
                "keeper_pid": 777,
                "keeper_starttime_ticks": 123,
                "root_mount_id": 1,
                "root_recursively_private": True,
                "readonly_project_mount_id": 2,
                "readonly_project_has_zero_nested_mounts": True,
                "live_mountinfo_sha256": "f" * 64,
            },
        ),
    )
    monkeypatch.setattr(
        EVALUATE, "_validate_runtime_guard", lambda guard, **kwargs: guard
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_runtime_authority_contract",
        lambda _guard, _acquisition: {
            "sealed_input_snapshot": sealed_snapshot,
            "sealed_project_artifacts": sealed_project_artifacts,
            "runtime_authority_mounts": runtime_authorities,
            "results_authority_mount": results_authority,
            "capacity_contract": capacity_contract,
        },
    )
    live_records = [
        sealed_mount_record,
        project_artifact_mount_record,
        *(value["mount_record"] for value in runtime_authorities.values()),
        results_authority["mount_record"],
    ]
    monkeypatch.setattr(
        EVALUATE,
        "_root_private_mountinfo",
        lambda *args, **kwargs: {"records": live_records},
    )
    monkeypatch.setattr(
        EVALUATE, "_validate_sealed_candidate_interpreter", lambda *_args: tmp_path
    )
    monkeypatch.setattr(
        EVALUATE, "_validate_evaluator_lock", lambda lock, **kwargs: lock
    )
    return argv


def test_direct_wrong_role_nonce_and_path_invocations_fail_before_cli_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    EVALUATE.EVALUATOR_ROLE_HANDOFF = None
    monkeypatch.setattr(EVALUATE.os, "environ", {})
    with pytest.raises(PermissionError, match="requires"):
        EVALUATE._require_evaluator_role_handoff([])
    monkeypatch.setattr(EVALUATE.os, "environ", {"TY_NAMESPACE_ROLE": "campaign"})
    with pytest.raises(PermissionError, match="requires"):
        EVALUATE._require_evaluator_role_handoff([])
    monkeypatch.setattr(EVALUATE.os, "environ", {
        "TY_NAMESPACE_ROLE": "evaluator", "TY_NAMESPACE_ROLE_HANDOFF": "/wrong",
        "TY_NAMESPACE_ROLE_NONCE": "not-a-nonce",
    })
    with pytest.raises(PermissionError, match="requires"):
        EVALUATE._require_evaluator_role_handoff([])


def test_exact_evaluator_role_handoff_is_consumed_and_authorization_env_cleared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(tmp_path, monkeypatch)
    result = EVALUATE._require_evaluator_role_handoff(argv)
    assert result["role"] == "evaluator"
    acknowledgment = EVALUATE.os.read(EVALUATE._TEST_ACK_READ_FD, 4096)
    EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)
    assert json.loads(acknowledgment) == {
        "attempt_id": result["attempt_id"],
        "handoff_payload_sha256": result["handoff_payload_sha256"],
        "role": "evaluator",
        "schema_version": EVALUATE.ROLE_ACK_SCHEMA_VERSION,
    }
    assert result["campaign_completion"]["completion"]["sha256"]
    assert EVALUATE.EVALUATOR_ROLE_HANDOFF == result
    assert not any(name in EVALUATE.os.environ for name in (
        "TY_NAMESPACE_ROLE", "TY_NAMESPACE_ROLE_HANDOFF", "TY_NAMESPACE_ROLE_NONCE",
        "TY_ROLE_ACK_FD", "TY_ROLE_ACK_NONCE",
    ))
    with pytest.raises(PermissionError, match="only once"):
        EVALUATE._require_evaluator_role_handoff(argv)


def test_evaluator_ack_precedes_first_unit_manifest_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(tmp_path, monkeypatch)
    unit_manifest = Path(argv[argv.index("--unit-manifest") + 1]).absolute()
    original = EVALUATE._artifact_json

    def refuse_outcome(path: Path, *args, **kwargs):
        if Path(path).absolute() == unit_manifest:
            raise AssertionError("unit manifest opened before evaluator ACK")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(EVALUATE, "_artifact_json", refuse_outcome)
    result = EVALUATE._require_evaluator_role_handoff(argv)
    acknowledgment = EVALUATE.os.read(EVALUATE._TEST_ACK_READ_FD, 4096)
    EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)
    assert acknowledgment
    assert result["campaign_completion"]["unit_manifest"]["path"] == str(
        unit_manifest
    )


def test_evaluator_ack_follows_full_control_live_namespace_and_rfc_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(tmp_path, monkeypatch)
    events: list[str] = []
    runtime_guard = EVALUATE._validate_runtime_guard
    namespace = EVALUATE._validate_persistent_namespace_contract
    evaluator_lock = EVALUATE._validate_evaluator_lock
    rfc3161 = EVALUATE._validate_rfc3161_attestation
    write_all = EVALUATE._write_all

    def tracked(label, function):
        def wrapper(*args, **kwargs):
            events.append(label)
            return function(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(
        EVALUATE, "_validate_runtime_guard", tracked("guard", runtime_guard)
    )
    monkeypatch.setattr(
        EVALUATE,
        "_validate_persistent_namespace_contract",
        tracked("namespace", namespace),
    )
    monkeypatch.setattr(
        EVALUATE, "_validate_evaluator_lock", tracked("lock", evaluator_lock)
    )
    monkeypatch.setattr(
        EVALUATE, "_validate_rfc3161_attestation", tracked("rfc3161", rfc3161)
    )
    monkeypatch.setattr(EVALUATE, "_write_all", tracked("ack", write_all))
    EVALUATE._require_evaluator_role_handoff(argv)
    EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)
    assert events == ["guard", "namespace", "lock", "rfc3161", "ack"]


def test_invalid_rfc3161_imprint_fails_before_role_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(
        tmp_path, monkeypatch, invalid_rfc3161_imprint=True
    )
    EVALUATE.os.set_blocking(EVALUATE._TEST_ACK_READ_FD, False)
    try:
        with pytest.raises(PermissionError, match="timestamp record"):
            EVALUATE._require_evaluator_role_handoff(argv)
        with pytest.raises(BlockingIOError):
            EVALUATE.os.read(EVALUATE._TEST_ACK_READ_FD, 4096)
    finally:
        EVALUATE.os.close(EVALUATE._TEST_ACK_WRITE_FD)
        EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)


def test_noncanonical_rfc3161_openssl_fails_before_role_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(EVALUATE, "OPENSSL_PATH", Path("/usr/bin/openssl"))
    EVALUATE.os.set_blocking(EVALUATE._TEST_ACK_READ_FD, False)
    try:
        with pytest.raises(PermissionError, match="context is not exact"):
            EVALUATE._require_evaluator_role_handoff(argv)
        with pytest.raises(BlockingIOError):
            EVALUATE.os.read(EVALUATE._TEST_ACK_READ_FD, 4096)
    finally:
        EVALUATE.os.close(EVALUATE._TEST_ACK_WRITE_FD)
        EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)


def test_lock_freeze_receipt_drift_fails_before_role_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(
        tmp_path, monkeypatch, invalid_lock_freeze_receipt=True
    )
    EVALUATE.os.set_blocking(EVALUATE._TEST_ACK_READ_FD, False)
    try:
        with pytest.raises(PermissionError, match="execution receipt differs"):
            EVALUATE._require_evaluator_role_handoff(argv)
        with pytest.raises(BlockingIOError):
            EVALUATE.os.read(EVALUATE._TEST_ACK_READ_FD, 4096)
    finally:
        EVALUATE.os.close(EVALUATE._TEST_ACK_WRITE_FD)
        EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)


def test_authority_handoff_drift_fails_before_role_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(
        tmp_path,
        monkeypatch,
        handoff_change=("results_authority_mount", {"path": "/forged"}),
    )
    EVALUATE.os.set_blocking(EVALUATE._TEST_ACK_READ_FD, False)
    try:
        with pytest.raises(PermissionError, match="authority bindings differ"):
            EVALUATE._require_evaluator_role_handoff(argv)
        with pytest.raises(BlockingIOError):
            EVALUATE.os.read(EVALUATE._TEST_ACK_READ_FD, 4096)
    finally:
        EVALUATE.os.close(EVALUATE._TEST_ACK_WRITE_FD)
        EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)


def test_evaluator_authority_validation_never_opens_iq_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(tmp_path, monkeypatch)
    source_root = EVALUATE.SEALED_INPUT_SOURCE_ROOT.absolute()
    original_open = EVALUATE.os.open

    def guarded_open(path, *args, **kwargs):
        if isinstance(path, (str, bytes, Path)):
            absolute = Path(path).absolute()
            if absolute.is_relative_to(source_root):
                raise AssertionError("evaluator attempted to open source IQ bytes")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(EVALUATE.os, "open", guarded_open)
    result = EVALUATE._require_evaluator_role_handoff(argv)
    assert result["sealed_input_snapshot"]["input_count"] == 30
    EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)


def test_active_project_authority_never_falls_back_to_original_project_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(EVALUATE, "_ACTIVE_PROJECT_ARTIFACT_MAPPING", {})
    with pytest.raises(PermissionError, match="absent from the sealed authority"):
        EVALUATE.regular_file_identity(EVALUATE.FROZEN_EVALUATOR_PATH)


def test_post_ack_unit_manifest_must_equal_root_attestation() -> None:
    manifest = {
        "schema_version": EVALUATE.UNIT_MANIFEST_SCHEMA_VERSION,
        "unit_manifest_payload_sha256": "a" * 64,
    }
    identity = _identity("/results/run/unit-manifest-amended.json", "b")
    completion = {
        "unit_manifest": identity,
        "unit_manifest_payload_sha256": "a" * 64,
    }
    EVALUATE._validate_post_ack_unit_manifest(
        campaign_completion=completion,
        manifest=manifest,
        manifest_identity=identity,
    )
    with pytest.raises(PermissionError, match="differs from root-attested"):
        EVALUATE._validate_post_ack_unit_manifest(
            campaign_completion=completion,
            manifest=manifest,
            manifest_identity={**identity, "sha256": "c" * 64},
        )


@pytest.mark.parametrize(
    ("change", "extra_environment", "namespace_inode", "message"),
    (
        (("child_pid", 999999), None, 222, "content differs"),
        (("role_script", _identity("/wrong-script", "f")), None, 222, "malformed"),
        (("role_script_bootstrap_sha256", "0" * 64), None, 222, "content differs"),
        (("runtime_guard", _identity("/wrong-guard", "d")), None, 222, "transitive binding"),
        (("evaluator_lock", _identity("/wrong-lock", "e")), None, 222, "transitive binding"),
        (None, None, 333, "transitive binding"),
        (None, ("UNEXPECTED", "1"), 222, "sanitized environment"),
    ),
)
def test_evaluator_role_handoff_tamper_security_and_environment_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: tuple[str, object] | None,
    extra_environment: tuple[str, str] | None,
    namespace_inode: int,
    message: str,
) -> None:
    argv = _role_handoff_fixture(
        tmp_path,
        monkeypatch,
        handoff_change=change,
        extra_environment=extra_environment,
        self_namespace_inode=namespace_inode,
    )
    with pytest.raises(PermissionError, match=message):
        EVALUATE._require_evaluator_role_handoff(argv)


def test_evaluator_role_handoff_nonce_path_and_post_drop_security_drift_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(tmp_path, monkeypatch)
    actual_path = EVALUATE.os.environ["TY_NAMESPACE_ROLE_HANDOFF"]
    EVALUATE.os.environ["TY_NAMESPACE_ROLE_HANDOFF"] = actual_path + ".alias"
    with pytest.raises(PermissionError, match="path or nonce"):
        EVALUATE._require_evaluator_role_handoff(argv)

    argv = _role_handoff_fixture(tmp_path / "second", monkeypatch)
    insecure = {
        "euid": 1000, "egid": 1000, "supplementary_gids": [],
        "cap_inheritable": 0, "cap_permitted": 0, "cap_effective": 1,
        "cap_ambient": 0, "no_new_privileges": 1, "dumpable": 0,
    }
    monkeypatch.setattr(EVALUATE, "_post_exec_security_state", lambda: insecure)
    with pytest.raises(PermissionError, match="retained privilege"):
        EVALUATE._require_evaluator_role_handoff(argv)


def test_evaluator_rejects_pinned_role_script_fd_that_survived_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _role_handoff_fixture(tmp_path, monkeypatch)
    handoff_path = Path(EVALUATE.os.environ["TY_NAMESPACE_ROLE_HANDOFF"])
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    descriptor = EVALUATE.os.open("/dev/null", EVALUATE.os.O_RDONLY)
    try:
        handoff["role_script_fd"] = descriptor
        handoff["exec_argv"][4] = str(descriptor)
        handoff["handoff_payload_sha256"] = EVALUATE.sha256_document(
            {
                key: value
                for key, value in handoff.items()
                if key != "handoff_payload_sha256"
            }
        )
        _write_json(handoff_path, handoff)
        with pytest.raises(PermissionError, match="survived bootstrap"):
            EVALUATE._require_evaluator_role_handoff(argv)
    finally:
        EVALUATE.os.close(descriptor)
        EVALUATE.os.close(EVALUATE._TEST_ACK_WRITE_FD)
        EVALUATE.os.close(EVALUATE._TEST_ACK_READ_FD)

def _publication_identities(parent: Path, root: Path):
    return (
        EVALUATE._live_directory_identity(parent),
        EVALUATE._live_directory_identity(root),
    )


def test_evaluation_publish_pins_guard_and_attested_directory_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "results"
    root = parent / "run"
    root.mkdir(parents=True, mode=0o700)
    parent.chmod(0o700)
    monkeypatch.setattr(EVALUATE, "RESULTS_PARENT_V3", parent)
    parent_identity, root_identity = _publication_identities(parent, root)
    moved = parent / "moved"
    root.rename(moved)
    root.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="output-root FD identity differs"):
        EVALUATE._publish(
            root / EVALUATE.EVALUATION_OUTPUT_NAME,
            {"schema_version": "test"},
            campaign_results_parent=parent_identity,
            output_root_identity=root_identity,
            result_tree_output_root=root_identity,
        )
    assert not (root / EVALUATE.EVALUATION_OUTPUT_NAME).exists()


def test_evaluation_publish_partial_failure_removes_only_own_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "results"
    root = parent / "run"
    root.mkdir(parents=True, mode=0o700)
    parent.chmod(0o700)
    monkeypatch.setattr(EVALUATE, "RESULTS_PARENT_V3", parent)
    parent_identity, root_identity = _publication_identities(parent, root)
    output = root / EVALUATE.EVALUATION_OUTPUT_NAME
    monkeypatch.setattr(
        EVALUATE,
        "_write_all",
        lambda *args: (_ for _ in ()).throw(OSError("partial write")),
    )
    with pytest.raises(OSError, match="partial write"):
        EVALUATE._publish(
            output,
            {"schema_version": "test"},
            campaign_results_parent=parent_identity,
            output_root_identity=root_identity,
            result_tree_output_root=root_identity,
        )
    assert not output.exists()


def test_evaluation_publish_preserves_preexisting_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "results"
    root = parent / "run"
    root.mkdir(parents=True, mode=0o700)
    parent.chmod(0o700)
    monkeypatch.setattr(EVALUATE, "RESULTS_PARENT_V3", parent)
    parent_identity, root_identity = _publication_identities(parent, root)
    output = root / EVALUATE.EVALUATION_OUTPUT_NAME
    output.write_bytes(b"preexisting")
    with pytest.raises(ValueError, match="already exists"):
        EVALUATE._publish(
            output,
            {"schema_version": "test"},
            campaign_results_parent=parent_identity,
            output_root_identity=root_identity,
            result_tree_output_root=root_identity,
        )
    assert output.read_bytes() == b"preexisting"


def test_evaluation_publish_never_unlinks_replacement_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "results"
    root = parent / "run"
    root.mkdir(parents=True, mode=0o700)
    parent.chmod(0o700)
    monkeypatch.setattr(EVALUATE, "RESULTS_PARENT_V3", parent)
    parent_identity, root_identity = _publication_identities(parent, root)
    output = root / EVALUATE.EVALUATION_OUTPUT_NAME

    def replace_then_fail(*_args) -> None:
        output.unlink()
        output.write_bytes(b"replacement")
        raise OSError("write failed after replacement")

    monkeypatch.setattr(EVALUATE, "_write_all", replace_then_fail)
    with pytest.raises(ExceptionGroup, match="publication and cleanup failed"):
        EVALUATE._publish(
            output,
            {"schema_version": "test"},
            campaign_results_parent=parent_identity,
            output_root_identity=root_identity,
            result_tree_output_root=root_identity,
        )
    assert output.read_bytes() == b"replacement"
