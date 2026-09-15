from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "work/blind-phase-confirmatory-v2/freeze_amended_evaluator_lock_v2.py"


def _module():
    name = "blind_phase_amended_evaluator_lock_v2_test_module"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FREEZE = _module()
FREEZER_IMPORTED_WITHOUT_EVALUATOR = FREEZE.EVALUATOR is None
FREEZE.EVALUATOR = FREEZE._load_module(
    "blind_phase_amended_evaluator_for_lock_freezer_test_only",
    FREEZE.EVALUATOR_PATH,
    FREEZE.EXPECTED_EVALUATOR_SHA256,
)


def _identity(path: str, marker: str) -> dict[str, object]:
    return {"path": path, "size_bytes": 1, "sha256": marker * 64}


def _review_checks() -> dict[str, object]:
    return {
        "iq_or_campaign_outcomes_opened_by_evaluator_work": False,
        "scientific_unit_count": 6168,
        "full_cohort_observation_count": 30,
        "mandatory_unexposed_sensitivity_observation_count": 29,
        "sensitivity_exclusion_observation_ids": [4491],
        "operational_amendment_v3_schema_and_exact_identity_bound": True,
        "runtime_guard_v3_schema_and_exact_identity_bound": True,
        "campaign_launcher_v3_exact_identity_bound": True,
        "frozen_scoring_engine_reused_without_endpoint_changes": True,
        "normalizer_v2_semantic_projection_fix_bound": True,
        "exact_result_identity_path_order_and_self_hash_validation": True,
        "all_json_artifacts_parsed_from_same_verified_fd_bytes": True,
        "duplicate_and_incomplete_coverage_fail_closed": True,
        "evaluation_output_no_clobber": True,
        "evaluation_output_canonical_dirfd_o_excl_nofollow_same_fd_attested": True,
        "amended_evaluator_verified_from_bytes_before_import": True,
        "lock_freezer_imports_no_project_code_before_root_guard_and_sealed_mapping": True,
        "lock_freezer_loads_evaluator_only_from_guard_bound_sealed_bytes": True,
        "frozen_evaluator_verified_from_bytes_before_import": True,
        "frozen_launcher_verified_from_bytes_before_import": True,
        "metadata_guard_and_evaluator_lock_validated_before_dependency_activation": True,
        "active_persistent_namespace_contract_validated_before_dependency_activation": True,
        "active_persistent_namespace_revalidated_after_outcome_evaluation": True,
        "keeper_contract_pid_starttime_executable_credentials_and_namespace_inode_bound": True,
        "current_process_namespace_inode_matches_active_keeper": True,
        "live_namespace_root_has_no_shared_master_propagate_from_or_unbindable_tag": True,
        "live_readonly_project_mount_exact_with_zero_nested_mounts": True,
        "evaluator_lock_required_by_exec_campaign_handoff": True,
        "separate_role_bound_exec_evaluator_handoff_required": True,
        "handoffs_use_bounded_explicit_argv_without_shell": True,
        "immutable_role_completion_receipts_bind_handoff_and_exit_or_signal": True,
        "distinct_pre_exec_failure_receipts_required": True,
        "positive_post_exec_ack_required_before_outcome_reads": True,
        "positive_ack_follows_full_control_and_live_namespace_validation": True,
        "campaign_completion_and_root_output_attestation_validated_before_outcomes": True,
        "campaign_completion_binds_exact_handoff_context_namespace_output_and_unit_manifest": True,
        "offline_rfc3161_attestation_validated_before_ack_and_outcomes": True,
        "rfc3161_attestation_binds_amendment_guard_lock_and_both_timestamps": True,
        "rfc3161_binds_root_metadata_lock_freeze_attestation_and_receipt": True,
        "role_handoffs_and_completions_bind_exact_rfc3161_attestation": True,
        "post_ack_manifest_and_output_root_equal_root_attestation": True,
        "private_results_export_required_before_persistent_namespace_stop": True,
        "results_export_binds_exact_completions_attestations_outputs_and_source_mount": True,
        "results_export_full_tree_equality_no_clobber_and_immutable_manifest": True,
        "namespace_stop_requires_exact_export_and_dead_keeper_recovery_is_intent_gated": True,
        "role_script_rechecked_after_uid_gid_drop": True,
        "pinned_role_script_fd_verified_and_closed_before_compile_exec": True,
        "parent_pipe_and_child_cleanup_total_on_all_exceptions": True,
        "evaluator_consumer_revalidates_handoff_and_post_drop_security_state": True,
        "evaluator_consumer_requires_sanitized_env_then_clears_role_authorization": True,
        "evaluator_consumer_clears_all_role_and_ack_environment": True,
        "direct_or_wrong_role_evaluator_invocation_fails_before_outcome_reads": True,
        "invalid_lock_fails_before_any_frozen_dependency_import": True,
        "exact_sealed_candidate_runtime_closure_bound": True,
        "sys_executable_is_exact_plan_bound_sealed_candidate_python": True,
        "unsafe_project_and_external_site_search_paths_removed_before_import": True,
        "all_loaded_telemetry_numpy_scipy_modules_inside_sealed_runtime": True,
        "evaluator_lock_root_owned_control_parent_enforced": True,
        "runtime_guard_is_exact_root_root_0444_control_parent_child": True,
        "evaluator_lock_is_exact_root_root_0444_control_parent_child": True,
        "control_parent_separate_from_uid1000_campaign_results": True,
        "sealed_input_snapshot_and_mapping_bound_without_evaluator_iq_access": True,
        "sealed_project_artifact_snapshot_and_mapping_bound_without_original_byte_access": True,
        "runtime_and_results_authority_mounts_live_revalidated_pre_and_post_ack": True,
        "private_tmpfs_results_authority_is_canonical_and_not_host_forgeable": True,
        "evaluated_namespace_is_exact_uid1000_mode0700_child_of_guarded_results_parent": True,
        "evaluation_publication_pins_guarded_parent_and_attested_output_root_identities": True,
        "evaluation_and_lock_failed_publications_cleanup_only_created_inode": True,
        "publication_cleanup_fsyncs_parent_and_preserves_all_errors": True,
        "independent_sealed_tmpfs_full_execute_security_suite_bound": True,
        "independent_private_authorities_security_suite_bound": True,
        "independent_small_artifact_authority_security_suite_bound": True,
        "independent_results_export_security_suite_bound": True,
        "capacity_contract_binds_exact_2gib_results_and_1_5gib_prework_reserve": True,
        "system_memory_cpu_fd_pid_and_persistent_export_capacity_preflight_bound": True,
        "capacity_contract_propagates_guard_handoff_completion_and_output_attestation": True,
        "zero_executed_unbound_local_python": True,
        "no_decoder_since_v2_incident_evidence_bound": True,
        "complete_component_runtime_closure_bound_before_remaining_6166_runs": True,
        "exact_6168_schedule_is_6166_new_decoder_runs_plus_two_historical_normalization_only_units": True,
        "historical_two_decoder_units_are_never_rerun": True,
        "component_runtime_closure_generator_and_self_hash_verified": True,
        "prior_two_exposures_full_component_closure_not_cryptographically_established": True,
        "full_30_cohort_mixed_runtime_history_non_pristine": True,
        "mandatory_sensitivity_29_excludes_all_of_observation_4491": True,
        "p0_findings": 0,
        "p1_findings": 0,
        "p2_findings": 0,
    }


def _write_review(path: Path, **fields: object) -> str:
    document: dict[str, object] = {
        "schema_version": FREEZE.REVIEW_SCHEMA_VERSION,
        "status": "PASS",
        **fields,
    }
    document["review_payload_sha256"] = FREEZE.EVALUATOR.sha256_document(document)
    path.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_freezer_defers_v3_evaluator_import_until_guard_bootstrap() -> None:
    assert FREEZER_IMPORTED_WITHOUT_EVALUATOR is True
    assert FREEZE.EVALUATOR_PATH.name == "evaluate_campaign_amended_v3.py"
    assert FREEZE.REVIEW_SCHEMA_VERSION.endswith("review-v3")
    identity = FREEZE.EVALUATOR.regular_file_identity(FREEZE.EVALUATOR_PATH)
    assert identity["sha256"] == FREEZE.EXPECTED_EVALUATOR_SHA256
    assert FREEZE.EVALUATOR.LOCK_SCHEMA_VERSION.endswith("lock-v2")


def test_import_never_executes_adjacent_project_evaluator(tmp_path: Path) -> None:
    freezer_path = tmp_path / SCRIPT.name
    evaluator_path = tmp_path / FREEZE.EVALUATOR_PATH.name
    marker = tmp_path / "executed"
    freezer_path.write_bytes(SCRIPT.read_bytes())
    evaluator_path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    name = "blind_phase_lock_freezer_untrusted_adjacent_test"
    spec = importlib.util.spec_from_file_location(name, freezer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert module.EVALUATOR is None
    assert not marker.exists()


def test_sealed_loader_ignores_original_evaluator_replacement(tmp_path: Path) -> None:
    original = tmp_path / "logical-evaluator.py"
    sealed = tmp_path / "sealed-evaluator.py"
    marker = tmp_path / "executed"
    payload = b"BOUND_VALUE = 7\n"
    digest = hashlib.sha256(payload).hexdigest()
    original.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    sealed.write_bytes(payload)
    module = FREEZE._load_module(
        "blind_phase_sealed_evaluator_replacement_test",
        original,
        digest,
        physical_path=sealed,
    )
    original.write_text("raise RuntimeError('post-guard replacement')\n", encoding="utf-8")
    assert module.BOUND_VALUE == 7
    assert module.__file__ == str(original.absolute())
    assert not marker.exists()


def test_guard_bootstrap_ignores_original_replacement_before_and_after_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    freezer = _module()
    original = tmp_path / "logical-evaluator.py"
    sealed = tmp_path / "sealed-evaluator.py"
    before_marker = tmp_path / "before-executed"
    after_marker = tmp_path / "after-executed"
    payload = b"BOUND_VALUE = 11\n"
    digest = hashlib.sha256(payload).hexdigest()
    sealed.write_bytes(payload)
    original.write_text("BOUND_VALUE = 0\n", encoding="utf-8")
    source_identity = {
        "path": str(original.absolute()),
        "size_bytes": len(payload),
        "sha256": digest,
    }
    sealed_identity = {
        "path": str(sealed.absolute()),
        "size_bytes": len(payload),
        "sha256": digest,
    }
    guard = {
        "sealed_project_artifacts": {
            "artifacts": [{"source": source_identity, "sealed": sealed_identity}]
        }
    }

    def root_guard(*_args):
        original.write_text(
            f"from pathlib import Path\nPath({str(before_marker)!r}).write_text('bad')\n",
            encoding="utf-8",
        )
        return guard, _identity("/guard", "a")

    def sealed_path(_guard):
        original.write_text(
            f"from pathlib import Path\nPath({str(after_marker)!r}).write_text('bad')\n",
            encoding="utf-8",
        )
        return sealed

    monkeypatch.setattr(freezer, "EVALUATOR_PATH", original)
    monkeypatch.setattr(freezer, "EXPECTED_EVALUATOR_SHA256", digest)
    monkeypatch.setattr(freezer, "_root_control_guard", root_guard)
    monkeypatch.setattr(freezer, "_sealed_evaluator_physical_path", sealed_path)
    parsed, identity = freezer._bootstrap_evaluator_from_guard(
        Path("/ignored-guard"), "a" * 64
    )
    assert parsed is guard
    assert identity == _identity("/guard", "a")
    assert freezer.EVALUATOR.BOUND_VALUE == 11
    assert not before_marker.exists()
    assert not after_marker.exists()


def test_review_binds_namespace_implementation_and_clean_findings(tmp_path: Path) -> None:
    evaluator = _identity("/evaluator", "1")
    evaluator_test = _identity("/evaluator-test", "2")
    freezer = _identity("/freezer", "3")
    freezer_test = _identity("/freezer-test", "4")
    amendment = _identity("/amendment-v3", "c")
    guard = _identity("/guard-v3", "d")
    amended_launcher = _identity("/campaign-launcher-v3", "e")
    normalizer = _identity("/normalizer-v2", "f")
    evidence = _identity("/no-decoder-evidence", "1")
    dependencies = {
        "amended_evaluator": evaluator,
        "frozen_scoring_evaluator": _identity("/scoring", "5"),
        "frozen_schedule_launcher": _identity("/launcher", "6"),
        "sealed_candidate_runtime": {
            "path": "/runtime", "recursive_entry_count": 1,
            "recursive_manifest_sha256": "7" * 64,
        },
    }
    closure = _identity("/closure", "8")
    closure_generator = _identity("/closure-generator", "9")
    control = {"path": "/control", "uid": 0, "gid": 0, "mode": 0o755}
    results = {"path": "/results", "uid": 1000, "gid": 1000, "mode": 0o700}
    namespace = _identity("/control/persistent-mount-namespace-active-v3.json", "a")
    live = {
        "mount_namespace_st_dev": 4,
        "mount_namespace_inode": 5,
        "keeper_pid": 6,
        "keeper_starttime_ticks": 7,
        "root_mount_id": 8,
        "root_recursively_private": True,
        "readonly_project_mount_id": 9,
        "readonly_project_has_zero_nested_mounts": True,
        "live_mountinfo_sha256": "b" * 64,
    }
    review_path = tmp_path / "review.json"
    digest = _write_review(
        review_path,
        reviewed_amended_evaluator=evaluator,
        reviewed_evaluator_tests=evaluator_test,
        reviewed_lock_freezer=freezer,
        reviewed_lock_freezer_tests=freezer_test,
        reviewed_operational_amendment_v3=amendment,
        reviewed_runtime_guard_v3=guard,
        reviewed_campaign_launcher_v3=amended_launcher,
        reviewed_normalizer_v2=normalizer,
        reviewed_no_decoder_since_v2_evidence=evidence,
        reviewed_executed_local_python_dependencies=dependencies,
        reviewed_component_runtime_closure=closure,
        reviewed_component_runtime_closure_generator=closure_generator,
        reviewed_control_parent=control,
        reviewed_campaign_results_parent=results,
        reviewed_persistent_mount_namespace=namespace,
        reviewed_persistent_mount_namespace_live_identity=live,
        checks=_review_checks(),
    )
    review, identity = FREEZE._review(
        review_path,
        expected_sha256=digest,
        evaluator_identity=evaluator,
        evaluator_test_identity=evaluator_test,
        freezer_identity=freezer,
        freezer_test_identity=freezer_test,
        amendment_identity=amendment,
        runtime_guard_identity=guard,
        amended_launcher_identity=amended_launcher,
        amended_normalizer_identity=normalizer,
        no_decoder_evidence_identity=evidence,
        executed_dependencies=dependencies,
        component_closure_identity=closure,
        component_closure_generator_identity=closure_generator,
        control_parent=control,
        campaign_results_parent=results,
        persistent_namespace_identity=namespace,
        persistent_namespace_live_identity=live,
    )
    assert review["status"] == "PASS"
    assert identity["sha256"] == digest
    broken = copy.deepcopy(review)
    broken["checks"]["p2_findings"] = 1
    broken["review_payload_sha256"] = FREEZE.EVALUATOR.sha256_document(
        {key: value for key, value in broken.items() if key != "review_payload_sha256"}
    )
    broken_digest = _write_review(
        tmp_path / "ignored.json",
        **{key: value for key, value in broken.items() if key not in {"schema_version", "status", "review_payload_sha256"}},
    )
    with pytest.raises(ValueError, match="not the exact clean PASS"):
        FREEZE._review(
            tmp_path / "ignored.json",
            expected_sha256=broken_digest,
            evaluator_identity=evaluator,
            evaluator_test_identity=evaluator_test,
            freezer_identity=freezer,
            freezer_test_identity=freezer_test,
            amendment_identity=amendment,
            runtime_guard_identity=guard,
            amended_launcher_identity=amended_launcher,
            amended_normalizer_identity=normalizer,
            no_decoder_evidence_identity=evidence,
            executed_dependencies=dependencies,
            component_closure_identity=closure,
            component_closure_generator_identity=closure_generator,
            control_parent=control,
            campaign_results_parent=results,
            persistent_namespace_identity=namespace,
            persistent_namespace_live_identity=live,
        )


def _lock(identities: dict[str, dict[str, object]]) -> dict[str, object]:
    executed = {
        key: identities[key]
        for key in (
            "amended_evaluator", "frozen_scoring_evaluator",
            "frozen_schedule_launcher", "sealed_candidate_runtime",
        )
    }
    document: dict[str, object] = {
        "schema_version": FREEZE.EVALUATOR.LOCK_SCHEMA_VERSION,
        "status": "frozen_before_campaign_outcome_evaluation",
        **identities,
        "executed_local_python_dependencies": executed,
        "analysis_contract": {
            "scientific_unit_count": 6168,
            "full_cohort_observation_count": 30,
            "mandatory_sensitivity_observation_count": 29,
            "sensitivity_exclusion_observation_ids": [4491],
            "primary_endpoint": "component_a_plus_candidate_trusted_native_union_increment_over_component_a",
            "secondary_endpoints_unchanged_from_frozen_evaluator": True,
            "frozen_bootstrap_resamples": 10_000,
            "frozen_bootstrap_seed": 20_260_903,
            "blinded_arm_labels": "validate_if_present",
            "normalizer_v2_semantic_projection_excludes": [
                "process_receipt_sha256", "raw_result_payload_sha256"
            ],
            "scheduled_unit_count": 6168,
            "new_decoder_execution_unit_count": 6166,
            "historical_normalization_only_unit_count": 2,
            "historical_decoder_rerun_permitted": False,
            "full_cohort_runtime_history": "mixed_and_non_pristine",
            "persistent_v3_mount_namespace_required": True,
        },
        "runtime_handoff_contract": dict(FREEZE.EVALUATOR.RUNTIME_HANDOFF_CONTRACT),
    }
    document["evaluator_lock_payload_sha256"] = FREEZE.EVALUATOR.sha256_document(document)
    return document


def test_evaluator_lock_requires_exact_role_bound_runtime_handoff() -> None:
    names = (
        "execution_plan", "acquisition_manifest", "source_manifest", "amendment",
        "runtime_guard", "no_decoder_evidence", "no_decoder_evidence_generator",
        "component_runtime_closure", "component_runtime_closure_generator",
        "amended_launcher", "amended_normalizer", "frozen_scoring_evaluator",
        "frozen_schedule_launcher", "amended_evaluator", "sealed_candidate_runtime",
        "control_parent", "campaign_results_parent", "persistent_mount_namespace",
        "persistent_mount_namespace_live_identity",
        "sealed_input_snapshot", "sealed_project_artifacts",
        "runtime_authority_mounts",
        "results_authority_mount",
    )
    identities = {name: {"name": name} for name in names}
    lock = _lock(identities)
    assert FREEZE.EVALUATOR._validate_evaluator_lock(lock, identities=identities) == lock
    broken = copy.deepcopy(lock)
    broken["runtime_handoff_contract"]["shell_permitted"] = True
    broken["evaluator_lock_payload_sha256"] = FREEZE.EVALUATOR.sha256_document(
        {key: value for key, value in broken.items() if key != "evaluator_lock_payload_sha256"}
    )
    with pytest.raises(ValueError, match="handoff contract"):
        FREEZE.EVALUATOR._validate_evaluator_lock(broken, identities=identities)


def test_freezer_metadata_boundary_has_no_outcome_iq_or_unit_manifest_inputs() -> None:
    parameters = inspect.signature(FREEZE.build_lock).parameters
    assert not any("iq" in name for name in parameters)
    assert not any("result" in name for name in parameters)
    assert not any("unit_manifest" in name for name in parameters)
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'dict(EVALUATOR.RUNTIME_HANDOFF_CONTRACT)' in source
    assert '"persistent_mount_namespace": persistent_namespace_identity' in source


def test_lock_publish_is_direct_root_owned_no_clobber(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fstat = FREEZE.os.fstat
    real_stat = FREEZE.os.stat

    def as_root(value):
        mode = value.st_mode
        if stat.S_ISDIR(mode):
            mode = stat.S_IFDIR | 0o755
        return SimpleNamespace(
            st_dev=value.st_dev, st_ino=value.st_ino, st_mode=mode,
            st_uid=0, st_gid=0, st_nlink=value.st_nlink, st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(FREEZE.os, "fstat", lambda fd: as_root(real_fstat(fd)))
    monkeypatch.setattr(
        FREEZE.os, "stat", lambda *args, **kwargs: as_root(real_stat(*args, **kwargs))
    )
    output = tmp_path / "lock.json"
    parent = as_root(real_stat(tmp_path))
    parent_identity = {
        "path": str(tmp_path), "st_dev": parent.st_dev, "st_ino": parent.st_ino,
        "uid": 0, "gid": 0, "mode": 0o755,
    }
    FREEZE._publish(
        output, {"schema_version": "test"}, control_parent=parent_identity
    )
    before = output.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        FREEZE._publish(
            output, {"schema_version": "different"}, control_parent=parent_identity
        )
    assert output.read_bytes() == before


def test_lock_publish_failure_cleans_only_created_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fstat = FREEZE.os.fstat
    real_stat = FREEZE.os.stat

    def as_root(value):
        mode = stat.S_IFDIR | 0o755 if stat.S_ISDIR(value.st_mode) else value.st_mode
        return SimpleNamespace(
            st_dev=value.st_dev, st_ino=value.st_ino, st_mode=mode,
            st_uid=0, st_gid=0, st_nlink=value.st_nlink, st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(FREEZE.os, "fstat", lambda fd: as_root(real_fstat(fd)))
    monkeypatch.setattr(
        FREEZE.os, "stat", lambda *args, **kwargs: as_root(real_stat(*args, **kwargs))
    )
    output = tmp_path / "partial-lock.json"
    parent = as_root(real_stat(tmp_path))
    parent_identity = {
        "path": str(tmp_path), "st_dev": parent.st_dev, "st_ino": parent.st_ino,
        "uid": 0, "gid": 0, "mode": 0o755,
    }
    monkeypatch.setattr(
        FREEZE.EVALUATOR,
        "_write_all",
        lambda *args: (_ for _ in ()).throw(OSError("partial lock write")),
    )
    with pytest.raises(OSError, match="partial lock write"):
        FREEZE._publish(
            output, {"schema_version": "test"}, control_parent=parent_identity
        )
    assert not output.exists()


def test_lock_publish_rejects_control_parent_inode_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fstat = FREEZE.os.fstat
    real_stat = FREEZE.os.stat

    def as_root(value):
        mode = stat.S_IFDIR | 0o755 if stat.S_ISDIR(value.st_mode) else value.st_mode
        return SimpleNamespace(
            st_dev=value.st_dev, st_ino=value.st_ino, st_mode=mode,
            st_uid=0, st_gid=0, st_nlink=value.st_nlink, st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(FREEZE.os, "fstat", lambda fd: as_root(real_fstat(fd)))
    monkeypatch.setattr(
        FREEZE.os, "stat", lambda *args, **kwargs: as_root(real_stat(*args, **kwargs))
    )
    output = tmp_path / "lock.json"
    actual = as_root(real_stat(tmp_path))
    stale_parent = {
        "path": str(tmp_path), "st_dev": actual.st_dev,
        "st_ino": actual.st_ino + 1, "uid": 0, "gid": 0, "mode": 0o755,
    }
    with pytest.raises(ValueError, match="parent FD identity differs"):
        FREEZE._publish(
            output, {"schema_version": "test"}, control_parent=stale_parent
        )
    assert not output.exists()


def test_lock_publish_failure_preserves_replacement_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fstat = FREEZE.os.fstat
    real_stat = FREEZE.os.stat

    def as_root(value):
        mode = stat.S_IFDIR | 0o755 if stat.S_ISDIR(value.st_mode) else value.st_mode
        return SimpleNamespace(
            st_dev=value.st_dev, st_ino=value.st_ino, st_mode=mode,
            st_uid=0, st_gid=0, st_nlink=value.st_nlink, st_size=value.st_size,
            st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns,
        )

    monkeypatch.setattr(FREEZE.os, "fstat", lambda fd: as_root(real_fstat(fd)))
    monkeypatch.setattr(
        FREEZE.os, "stat", lambda *args, **kwargs: as_root(real_stat(*args, **kwargs))
    )
    output = tmp_path / "replacement-lock.json"
    parent = as_root(real_stat(tmp_path))
    parent_identity = {
        "path": str(tmp_path), "st_dev": parent.st_dev, "st_ino": parent.st_ino,
        "uid": 0, "gid": 0, "mode": 0o755,
    }

    def replace_then_fail(*_args) -> None:
        output.unlink()
        output.write_bytes(b"replacement")
        raise OSError("lock write failed after replacement")

    monkeypatch.setattr(FREEZE.EVALUATOR, "_write_all", replace_then_fail)
    with pytest.raises(ExceptionGroup, match="publication and cleanup failed"):
        FREEZE._publish(
            output, {"schema_version": "test"}, control_parent=parent_identity
        )
    assert output.read_bytes() == b"replacement"


def test_review_template_is_explicitly_unusable_until_final_v3_identities_exist() -> None:
    template_path = (
        ROOT
        / "work/blind-phase-confirmatory-v2/amended-evaluator-review-template-v3.json"
    )
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert template["schema_version"] == FREEZE.REVIEW_SCHEMA_VERSION
    assert template["status"] == "REVIEW_REQUIRED"
    assert template["checks"].keys() == _review_checks().keys()
    assert template["reviewed_amended_evaluator"] == (
        FREEZE.EVALUATOR.regular_file_identity(FREEZE.EVALUATOR_PATH)
    )
    assert template["reviewed_lock_freezer"] == (
        FREEZE.EVALUATOR.regular_file_identity(SCRIPT)
    )
    assert template["reviewed_evaluator_tests"] == (
        FREEZE.EVALUATOR.regular_file_identity(
            ROOT / "tests/test_blind_phase_campaign_evaluation_amended_v3.py"
        )
    )
    assert template["reviewed_lock_freezer_tests"] == (
        FREEZE.EVALUATOR.regular_file_identity(Path(__file__))
    )
    assert template["reviewed_operational_amendment_v3"]["sha256"] == "TBD_FINAL"
    assert template["reviewed_runtime_guard_v3"]["sha256"] == "TBD_FINAL"
    assert template["reviewed_campaign_launcher_v3"]["sha256"] == "TBD_FINAL"
    assert template["review_payload_sha256"] == "TBD_AFTER_INDEPENDENT_REVIEW"
