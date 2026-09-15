from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat

import pytest


SOURCE = Path(__file__).parents[1] / "work/blind-phase-confirmatory-v2/build_fourth_freeze_failure_evidence.py"
SPEC = importlib.util.spec_from_file_location("fourth_freeze_evidence", SOURCE)
assert SPEC is not None and SPEC.loader is not None
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def identity(path: str, sha: str = "a" * 64, size: int = 1) -> dict[str, object]:
    return {"path": path, "size_bytes": size, "sha256": sha}


def hashed(schema: str, status: str, field: str, **extra: object) -> dict[str, object]:
    value: dict[str, object] = {"schema_version": schema, "status": status, **extra}
    value[field] = M._sha256_document(value)
    return value


def args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(
        recovery_incident=tmp_path / "incident.json",
        expected_recovery_incident_sha256="1" * 64,
        expected_recovery_incident_payload_sha256="2" * 64,
        quarantine_provenance=tmp_path / "quarantine.json",
        expected_quarantine_provenance_sha256="3" * 64,
        expected_quarantine_provenance_payload_sha256="4" * 64,
        quarantine_intent=tmp_path / "quarantine-intent.json",
        expected_quarantine_intent_sha256="0" * 64,
        expected_quarantine_intent_payload_sha256="f" * 64,
        quarantine_runner=tmp_path / "quarantine-runner.py",
        expected_quarantine_runner_sha256="e" * 64,
        quarantine_runner_tests=tmp_path / "test-quarantine-runner.py",
        expected_quarantine_runner_tests_sha256="d" * 64,
        quarantine_independent_review=tmp_path / "quarantine-review.json",
        expected_quarantine_independent_review_sha256="c" * 64,
        expected_quarantine_independent_review_payload_sha256="b" * 64,
        terminal_quarantine_validator=tmp_path / "terminal-validator.py",
        expected_terminal_quarantine_validator_sha256="1" * 64,
        terminal_quarantine_validator_tests=tmp_path / "test-terminal-validator.py",
        expected_terminal_quarantine_validator_tests_sha256="2" * 64,
        terminal_quarantine_validator_independent_review=tmp_path / "terminal-review.json",
        expected_terminal_quarantine_validator_independent_review_sha256="3" * 64,
        expected_terminal_quarantine_validator_independent_review_payload_sha256="4" * 64,
        expected_terminal_quarantine_validator_controller_st_dev=64512,
        expected_terminal_quarantine_validator_controller_st_ino=1594219,
        recovery_pass=tmp_path / "recovery.json",
        expected_recovery_pass_sha256="5" * 64,
        expected_recovery_pass_payload_sha256="6" * 64,
        recovery_independent_review=tmp_path / "review.json",
        expected_recovery_independent_review_sha256="7" * 64,
        expected_recovery_independent_review_payload_sha256="8" * 64,
        recovery_independent_review_schema="review-v1",
        recovery_independent_review_self_hash_field="review_payload_sha256",
        recovery_runner=tmp_path / "runner.py",
        expected_recovery_runner_sha256="9" * 64,
        recovery_runner_tests=tmp_path / "test_runner.py",
        expected_recovery_runner_tests_sha256="a" * 64,
        corrected_builder_source=tmp_path / "builder.py",
        expected_corrected_builder_source_sha256="b" * 64,
        corrected_builder_tests=tmp_path / "test_builder.py",
        expected_corrected_builder_tests_sha256="c" * 64,
        control_artifact_root=tmp_path / "control",
        expected_generator_sha256="d" * 64,
        expected_generator_test_sha256="e" * 64,
        created_at_utc="2026-09-06T07:00:00Z",
        publish=False,
    )


def recovery_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    canonical = tmp_path / "canonical-absent"
    control_artifact_root = tmp_path / "control"
    control_artifact_root.mkdir()
    monkeypatch.setattr(M, "CONTROL", canonical)
    lifecycle_artifacts = {
        name: {
            "original_identity": identity(str(canonical / name), spec[0]),
            "original_sidecar_identity": identity(
                str(canonical / f"{name}.sha256"), str(index % 10) * 64),
        }
        for index, (name, spec) in enumerate(M.CONTROL_JSON.items())
    }
    pre = {
        "rollback_intent": lifecycle_artifacts[
            "rollback-sealed-runtimes-intent-v3.json"]["original_identity"],
        "freeze_attempts": [
            {"handoff_artifact": lifecycle_artifacts[
                "freeze-guard-handoff-v3-02991a55db4b3a6e8383e3647edac0c6503df569cfea6414b1fc71adafbe17b4.json"]},
            {"handoff_artifact": lifecycle_artifacts[
                "freeze-guard-handoff-v3-a400667469aaa7526797f08114f59b19410f71624f02cc610198e1598d463484.json"]},
        ],
        "lifecycle_artifacts": lifecycle_artifacts,
        "keeper_generation": {"pid": M.KEEPER_PID, "mount_namespace_inode": M.KEEPER_NAMESPACE_INODE},
    }
    incident_id = identity(str(tmp_path / "incident.json"), "1" * 64)
    monkeypatch.setattr(M, "EXACT_RECOVERY_INCIDENT", {
        "path": incident_id["path"], "sha256": incident_id["sha256"],
        "payload_sha256": "2" * 64,
    })
    quarantine_intent_id = identity(str(tmp_path / "quarantine-intent.json"), "0" * 64)
    quarantine_id = identity(str(tmp_path / "quarantine.json"), "3" * 64)
    recovery_id = identity(str(tmp_path / "recovery.json"), "5" * 64)
    review_id = identity(str(tmp_path / "review.json"), "7" * 64)
    runner_id = identity(str(tmp_path / "runner.py"), "9" * 64)
    runner_test_id = identity(str(tmp_path / "test_runner.py"), "a" * 64)
    builder_id = identity(str(tmp_path / "builder.py"), "b" * 64)
    builder_test_id = identity(str(tmp_path / "test_builder.py"), "c" * 64)
    quarantine_runner_id = identity(str(tmp_path / "quarantine-runner.py"), "e" * 64)
    quarantine_runner_test_id = identity(str(tmp_path / "test-quarantine-runner.py"), "d" * 64)
    quarantine_review_id = identity(str(tmp_path / "quarantine-review.json"), "c" * 64)
    terminal_validator_id = identity(str(tmp_path / "terminal-validator.py"), "1" * 64)
    terminal_validator_test_id = identity(str(tmp_path / "test-terminal-validator.py"), "2" * 64)
    terminal_validator_review_id = identity(str(tmp_path / "terminal-review.json"), "3" * 64)
    corrected_quarantine_runner_id = identity(
        str(M.CORRECTED_QUARANTINE_RUNNER), M.CORRECTED_QUARANTINE_RUNNER_SHA256)
    corrected_quarantine_runner_test_id = identity(
        str(M.CORRECTED_QUARANTINE_RUNNER_TEST),
        M.CORRECTED_QUARANTINE_RUNNER_TEST_SHA256)
    temporary = {"paths": [], "path_count": 4}
    member = {
        "pid": M.KEEPER_PID, "ppid": 1, "state": "S",
        "starttime_ticks": M.KEEPER_STARTTIME_TICKS,
        "uid": [0, 0, 0, 0], "gid": [0, 0, 0, 0],
    }
    incident_pre = {
        "keeper": {
            "pid": M.KEEPER_PID, "mount_namespace_inode": M.KEEPER_NAMESPACE_INODE,
            "starttime_ticks": M.KEEPER_STARTTIME_TICKS, "euid": 0, "egid": 0,
        },
        "expected_project_mount_id": 166,
        "expected_remaining_lifetime_mount_ids_after_rollback": [165, 167, 168, 171, 172],
        "expected_runtime_parent_mount_ids": [169, 170],
        "mountinfo": {"row_count": 42, "sha256": M.FAILED_ROLLBACK_MOUNTINFO_SHA256,
                      "size_bytes": 4762},
        "private_results_entry_count": 0,
        "rollback_ready_absent": True, "rollback_complete_absent": True,
        "runtime_guard_role_decoder_evaluator_export_absent": True,
        "keeper_holder_fixed_point": {
            "first": {"namespace_fd_holders": [], "namespace_members": [member]},
            "second": {"namespace_fd_holders": [], "namespace_members": [member]},
            "first_suspicious_processes": [], "second_suspicious_processes": [],
        },
        "no_role_state": {"forbidden_names": [], "suspicious_processes": []},
    }
    bound = {
        name: {
            **artifact["original_identity"], "uid": 0, "gid": 0,
            "mode": 0o444, "nlink": 1,
            "sidecar": artifact["original_sidecar_identity"],
        }
        for name, artifact in lifecycle_artifacts.items()
    }
    receipts = {}
    for label, physical in (
        ("freeze_attempt1", 71941), ("temporary_du", 71966),
        ("temporary_cleanup", 71975), ("freeze_attempt2", 71995),
        ("failed_original_rollback", 72157),
    ):
        value = M.TRANSCRIPT_RECEIPTS[label]
        receipt = {
            "transcript_path": str(M.TRANSCRIPT), "ordinal": value["ordinal"],
            "physical_line_number": physical, "line_sha256": value["line_sha256"],
            "exit_code": value["exit_code"], "output_sha256": value["stdout_sha256"],
            "source_is_user_owned_append_only_transcript_not_root_immutable": True,
        }
        if "stdout_size_bytes" in value:
            receipt["output_size_bytes"] = value["stdout_size_bytes"]
        receipts[str(value["ordinal"])] = receipt
    incident = {
        "attempt_id": M.ATTEMPT_ID,
        "immutable_pre_recovery_state": incident_pre,
        "bound_control_artifacts": bound,
        "user_owned_transcript_receipts": receipts,
        "historical_runtime_guard_builder": {
            **identity(str(M.BUILDER_CONTROL), M.PROTOCOL_FILES[
                "runtime_guard_builder_control_copy"][1],
                M.PROTOCOL_FILES["runtime_guard_builder_control_copy"][2]),
            "uid": 0, "gid": 0, "mode": 0o555, "nlink": 1,
        },
        "patched_nonexecuted_runtime_guard_builder": builder_id,
        "scientific_exposure": {
            "decoder_evaluator_or_campaign_role_executed": False,
            "iq_copied_and_sealed_before_failure": True,
            "iq_used_as_decoder_input": False,
        },
    }
    ready_id = identity(str(canonical / "rollback-sealed-runtimes-ready-to-unmount-v3.json"), "4" * 64)
    complete_id = identity(str(canonical / "rollback-sealed-runtimes-complete-v3.json"), "5" * 64)
    rollback_recovery_id = identity(str(tmp_path / "rollback-recovery.json"), "6" * 64)
    source_restoration_id = identity(str(tmp_path / "source-restoration.json"), "7" * 64)
    stage1_review_id = identity(str(tmp_path / "stage1-review.json"), "8" * 64)
    retry1_failure_id = identity(str(tmp_path / "retry1-failure.json"), "9" * 64)
    parent_open_ids = {
        label: identity(str(tmp_path / f"parent-open-{label}.json"), str(index) * 64)
        for index, label in enumerate(("candidate", "component"), start=1)
    }
    rollback_runner_id = identity(str(tmp_path / "rollback-runner.py"), "2" * 64)
    rollback_test_id = identity(str(tmp_path / "rollback-test.py"), "3" * 64)
    historical_id = identity(str(tmp_path / "historical.py"), "4" * 64)
    implementation = {
        "abort_runner": runner_id, "abort_test": runner_test_id,
        "stage2_independent_review": review_id,
        "source_restoration": source_restoration_id,
        "parent_open_receipts": parent_open_ids,
        "stage1_independent_review": stage1_review_id,
        "rollback_runner": rollback_runner_id, "rollback_test": rollback_test_id,
        "patched_builder": builder_id, "historical_quarantine": historical_id,
        "retry1_preflight_failure_incident": retry1_failure_id,
    }
    incident["recovery_runner"] = rollback_runner_id
    ordered_ids = [identity(str(tmp_path / f"stage2-{index}.json"), f"{index:x}" * 64) for index in range(8)]
    ordered_ids.append(recovery_id)
    post = {
        "control": {"files": {}}, "external": {"inventory_sha256": "a" * 64},
        "processes": {"namespace_member_pids": [], "namespace_fd_handles": [], "suspicious_processes": []},
        "results": {"host_backing": {"entry_count": 0}},
        "source_restoration_state": {"patched_builder": builder_id, "historical_quarantine": historical_id},
    }
    transactions = {
        label: {
            "status": "IRREVERSIBLY_DELETED_OPERATIONAL_RUNTIME_DUPLICATES",
            "cleanup_delete_receipt": identity(str(tmp_path / f"delete-{label}.json")),
        }
        for label in ("candidate", "component")
    }
    recovery = {
        "attempt_id": M.ATTEMPT_ID,
        "incident": incident_id,
        "rollback_intent": pre["rollback_intent"],
        "rollback_ready": ready_id, "rollback_complete": complete_id,
        "rollback_recovery": rollback_recovery_id,
        "recovery_implementation_chain": implementation,
        "failure_resume_authorization": None,
        "post_recovery_evidence": post,
        "keeper_namespace_and_mounts_absent": True,
        "external_namespace_zero_delta": True,
        "runtime_guard_role_decoder_evaluator_export_absent": True,
        "abort_intent": ordered_ids[0], "stopped_contract": ordered_ids[1],
        "cleanup_receipt": ordered_ids[7],
        "scientific_exposure": {
            "iq_copied_and_sealed_before_failure": True,
            "iq_opened_by_abort": False, "iq_used_as_decoder_input": False,
            "decoder_evaluator_or_campaign_role_executed": False,
        },
        "transaction_directory_disposition": transactions,
    }
    retry1_failure = {
        "schema_version": "blind-phase-confirmatory-fourth-pre-campaign-abort-retry1-preflight-failure-incident-v1",
        "status": "COMMITTED_FOR_RETRY2_AFTER_READ_ONLY_PREFLIGHT_FAILURE",
        "attempt_id": M.ATTEMPT_ID,
        "failure_cause": {
            "classification": "FULL_VERSUS_PROJECTED_CONTROL_EVIDENCE_COMPOSITION_MISMATCH",
            "detail": "_validate_chain removed the documents member from control_inventory while _pre_signal_evidence compared that projection to a fresh full inventory containing documents",
            "live_control_drift": False,
            "mutation_reached": False,
            "required_correction": "use one explicit validated control-evidence projection at both composition boundaries",
        },
        "scientific_exposure": {
            "decoder_evaluator_or_campaign_role_executed": False,
            "iq_opened_by_preflight": False, "results_export_executed": False,
        },
    }
    ready = {"status": "roots_swapped_parents_restored_ready_to_unmount_project"}
    complete = {"status": "rollback_complete_project_unmounted"}
    rollback_recovery = {
        "status": "PASS", "incident": incident_id,
        "canonical_rollback_ready": ready_id, "canonical_rollback_complete": complete_id,
        "source_restoration": source_restoration_id,
        "redirected_parent_open_receipts": parent_open_ids,
    }
    source_restoration = {
        "status": "PASS", "restored_patched_source": builder_id,
        "quarantined_projection": historical_id,
    }
    stage1_review = {"status": "GO"}
    stage2_snapshot = {
        "parent": {
            "path": str(tmp_path / "stage2-parent"), "st_dev": 1,
            "st_ino": 2, "uid": 0, "gid": 0, "mode": 0o755,
        },
        "durable_prefix_length": 9, "ordered_receipts": ordered_ids,
        "result": recovery_id, "external_inventory_sha256": "a" * 64,
    }
    quarantine_tools = {
        "runner": quarantine_runner_id, "runner_test": quarantine_runner_test_id,
        "independent_review": quarantine_review_id,
    }
    before = {"phase": "before", "manifest": {"manifest_sha256": "b" * 64}}
    quarantine_intent = {
        "attempt_id": M.ATTEMPT_ID, "tools": quarantine_tools,
        "stage2": stage2_snapshot, "pre_rename_snapshot": before,
        "controller_parent": {"path": str(tmp_path / "quarantine-controller")},
        "zero_runtime_state": {
            "external_inventory_sha256": "a" * 64,
            "keeper_absent": True,
            "relevant_mount_count": 0,
            "role_decoder_evaluator_processes": {
                "namespace_member_pids": [], "namespace_fd_handles": [],
                "suspicious_processes": [],
            },
        },
    }
    quarantine = {
        "attempt_id": M.ATTEMPT_ID, "intent": quarantine_intent_id,
        "stage2_recovery_result": recovery_id, "tools": quarantine_tools,
        "source_control_root": str(canonical),
        "quarantine_root": str(control_artifact_root),
        "all_source_entries_accounted_for": True,
        "canonical_control_root_absent_after_recovery": True,
        "post_quarantine_state": quarantine_intent["zero_runtime_state"],
        "source_results_and_iq_preservation": dict(M.QUARANTINE_PRESERVATION),
    }
    review_document = {
        "reviewed_bindings": {
            "abort_runner_retry2": runner_id, "abort_runner_retry2_test": runner_test_id,
            "retry1_preflight_failure_incident": retry1_failure_id,
            "stage1_independent_review": stage1_review_id,
            "stage1_rollback_recovery": rollback_recovery_id,
            "canonical_rollback_ready": ready_id, "canonical_rollback_complete": complete_id,
        },
        "findings": {"p0_open": 0, "p1_open": 0, "p2_open": 0},
    }
    quarantine_review = {
        "reviewed_bindings": {
            "quarantine_runner": quarantine_runner_id,
            "quarantine_runner_test": quarantine_runner_test_id,
            "stage2_recovery_result": recovery_id,
        },
        "findings": {"P0": 0, "P1": 0, "P2": 0},
    }
    terminal_result = {
        "schema_version": (
            "blind-phase-confirmatory-fourth-control-root-quarantine-terminal-preflight-v1"
        ),
        "status": M.TERMINAL_VALIDATOR_STATUS,
        "attempt_id": M.ATTEMPT_ID,
        "durable_prefix_length": 2,
        "source_state": "DESTINATION_QUARANTINED",
        "intent": quarantine_intent_id,
        "provenance": quarantine_id,
        "provenance_committed": True,
        "validated_partial_provenance": False,
        "canonical_control_root_absent": True,
        "destination": {
            "path": str(control_artifact_root), "st_dev": 64512,
            "st_ino": 1180459, "uid": 0, "gid": 0, "mode": 0o755,
            "nlink": 6,
        },
        "stage2_result": recovery_id,
        "zero_runtime_state": quarantine["post_quarantine_state"],
        "historical_tools": quarantine_tools,
        "validator_tools": {
            "terminal_validator": terminal_validator_id,
            "terminal_validator_test": terminal_validator_test_id,
            "independent_review": terminal_validator_review_id,
        },
        "snapshot_sha256": M.POST_QUARANTINE_SNAPSHOT_SHA256,
        "historical_controller_inventory_sha256": (
            M.HISTORICAL_QUARANTINE_CONTROLLER_INVENTORY_SHA256),
        "var_lib_sibling_state": {
            "canonical_absent": True,
            "parent": {
                "path": "/var/lib", "st_dev": 64512, "st_ino": 1179656,
                "uid": 0, "gid": 0, "mode": 0o755, "nlink": 67,
                "entry_count": 67, "size_bytes": 4096,
                "mtime_ns": 123, "ctime_ns": 456,
            },
            "relevant_siblings": ["destination", "historical", "validator"],
        },
        "lifecycle_action_executed": False,
        "publication_executed": False,
        "rename_executed": False,
    }
    terminal_review = {
        "reviewed_bindings": {
            "terminal_validator": terminal_validator_id,
            "terminal_validator_test": terminal_validator_test_id,
            "historical_quarantine_runner": quarantine_runner_id,
            "historical_quarantine_runner_test": quarantine_runner_test_id,
            "historical_quarantine_independent_review": quarantine_review_id,
            "historical_quarantine_intent": quarantine_intent_id,
            "historical_quarantine_provenance": quarantine_id,
            "corrected_quarantine_runner_contract_repo": corrected_quarantine_runner_id,
            "corrected_quarantine_runner_contract_test_repo": (
                corrected_quarantine_runner_test_id),
        },
        "terminal_live_contract": {
            "pre_rename_snapshot_sha256": M.PRE_QUARANTINE_SNAPSHOT_SHA256,
            "post_rename_snapshot_sha256": M.POST_QUARANTINE_SNAPSHOT_SHA256,
            "provenance_payload_sha256": "4" * 64,
            "stage2_result": recovery_id,
            "stage2_durable_prefix_length": 9,
            "terminal_validator_prefix": 2,
            "provenance_committed": True,
            "validated_partial_provenance": False,
            "external_inventory_sha256": "a" * 64,
            "canonical_source_absent": str(canonical),
            "destination": terminal_result["destination"],
        },
        "findings": {"P0": 0, "P1": 0, "P2": 0},
    }
    by_path = {
        str(tmp_path / "incident.json"): (incident, incident_id),
        str(tmp_path / "quarantine-intent.json"): (quarantine_intent, quarantine_intent_id),
        str(tmp_path / "quarantine.json"): (quarantine, quarantine_id),
        str(tmp_path / "recovery.json"): (recovery, recovery_id),
        str(tmp_path / "review.json"): (review_document, review_id),
        str(tmp_path / "quarantine-review.json"): (quarantine_review, quarantine_review_id),
        str(tmp_path / "terminal-review.json"): (terminal_review, terminal_validator_review_id),
    }
    monkeypatch.setattr(M, "_validate_external_json", lambda path, *_a, **_k: by_path[str(path)])
    regulars = {
        str(tmp_path / "runner.py"): runner_id,
        str(tmp_path / "test_runner.py"): runner_test_id,
        str(tmp_path / "builder.py"): builder_id,
        str(tmp_path / "test_builder.py"): builder_test_id,
        str(tmp_path / "quarantine-runner.py"): quarantine_runner_id,
        str(tmp_path / "test-quarantine-runner.py"): quarantine_runner_test_id,
        str(tmp_path / "terminal-validator.py"): terminal_validator_id,
        str(tmp_path / "test-terminal-validator.py"): terminal_validator_test_id,
        str(M.CORRECTED_QUARANTINE_RUNNER): corrected_quarantine_runner_id,
        str(M.CORRECTED_QUARANTINE_RUNNER_TEST): corrected_quarantine_runner_test_id,
    }
    monkeypatch.setattr(M, "_validate_external_regular", lambda path, *_a, **_k: regulars[str(path)])
    referenced = {
        ready_id["path"]: ready, complete_id["path"]: complete,
        rollback_recovery_id["path"]: rollback_recovery,
        source_restoration_id["path"]: source_restoration,
        stage1_review_id["path"]: stage1_review,
        retry1_failure_id["path"]: retry1_failure,
        **{value["path"]: {"status": "PASS"} for value in parent_open_ids.values()},
    }
    monkeypatch.setattr(
        M, "_load_referenced_selfhashed",
        lambda reference, *_a, **_k: (referenced[reference["path"]], reference),
    )
    monkeypatch.setattr(M, "_validate_referenced_regular", lambda reference, **_k: reference)
    expected_bound_chain = {
        "control_inventory": post["control"], "incident": incident_id,
        "rollback_recovery": rollback_recovery_id,
        "canonical_rollback_ready": ready_id,
        "canonical_rollback_complete": complete_id,
        "source_restoration": source_restoration_id,
        "parent_open_receipts": parent_open_ids,
        "stage1_independent_review": stage1_review_id,
        "stage2_independent_review": review_id,
        "rollback_runner": rollback_runner_id, "rollback_test": rollback_test_id,
        "abort_test": runner_test_id, "patched_builder": builder_id,
        "historical_quarantine": historical_id,
        "retry1_preflight_failure_incident": retry1_failure_id,
        "recovery_parent": stage2_snapshot["parent"],
    }
    class Stage2:
        def _load_stage2_prefix(self, **_kwargs):
            return ([{"bound_chain": expected_bound_chain}]
                    + [{} for _ in range(7)] + [recovery], ordered_ids)
        def _validate_progress_documents(self, documents, identities, *_args):
            if documents[-1].get("abort_intent") != identities[0]:
                raise RuntimeError("stage2 abort binding differs")
        def _validate_failure_binding(self, *_args):
            return None
    after = {"phase": "after", "manifest": {"manifest_sha256": "b" * 64}}
    quarantine_module = type("Quarantine", (), {
        "QUARANTINE_ROOT": control_artifact_root,
        "_validate_snapshot_at_root": staticmethod(lambda *_args: after),
        "_zero_runtime_state": staticmethod(lambda *_args: quarantine["post_quarantine_state"]),
        "_validate_stage2_snapshot": staticmethod(lambda *_args: None),
        "_provenance_semantics": staticmethod(lambda *_args, **_kwargs: None),
        "_validate_controller_inventory": staticmethod(lambda *_args, **_kwargs: None),
        "_sha256_document": staticmethod(
            lambda document: (
                M.PRE_QUARANTINE_SNAPSHOT_SHA256
                if document.get("phase") == "before"
                else M.POST_QUARANTINE_SNAPSHOT_SHA256
            )
        ),
    })()
    terminal_module = type("Terminal", (), {
        "validate": staticmethod(lambda _expected: terminal_result),
    })()
    monkeypatch.setattr(
        M, "_load_exact_module",
        lambda path, *_args: (
            (
                Stage2() if path == tmp_path / "runner.py"
                else terminal_module if path == tmp_path / "terminal-validator.py"
                else quarantine_module
            ),
            regulars[str(path)],
        ),
    )
    monkeypatch.setattr(M, "_load_json", lambda path, *_a: ({}, identity(str(path))))
    monkeypatch.setattr(M, "_temporary_storage_disclosure", lambda: temporary)
    monkeypatch.setattr(
        M, "_validate_terminal_execution_receipt",
        lambda _args, _terminal: dict(M.TERMINAL_VALIDATOR_EXECUTION_RECEIPT),
    )
    return pre, incident, quarantine, recovery, {
        "intent": quarantine_intent,
        "terminal": terminal_result,
        "terminal_review": terminal_review,
        "stage2_review": review_document,
    }


def test_recovery_chain_accepts_exact_bindings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a = args(tmp_path)
    pre, *_ = recovery_fixture(monkeypatch, tmp_path)
    result = M._validate_recovery_chain(a, pre)
    assert result["live_keeper_or_mount_remaining"] is False
    assert result["control_root_quarantine"]["quarantine_root"] == str(a.control_artifact_root)
    assert result["stage2_abort_and_cleanup"]["durable_prefix_length"] == 9
    assert result["retry1_failed_preflight"]["mutation_reached"] is False
    assert result["failed_original_rollback"]["failure_class"] == "EBUSY_SELF_HELD_PARENT_MOUNT_FD"
    terminal = result["control_root_quarantine"]["terminal_validation"]["result"]
    assert terminal["var_lib_sibling_state"]["parent"] == {
        "path": "/var/lib", "st_dev": 64512, "st_ino": 1179656,
        "uid": 0, "gid": 0, "mode": 0o755,
    }


@pytest.mark.parametrize("field", ["path", "sha256", "payload_sha256"])
def test_recovery_chain_requires_compiled_exact_incident_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str,
) -> None:
    a = args(tmp_path)
    pre, *_ = recovery_fixture(monkeypatch, tmp_path)
    if field == "path":
        a.recovery_incident = tmp_path / "different-incident.json"
    elif field == "sha256":
        a.expected_recovery_incident_sha256 = "f" * 64
    else:
        a.expected_recovery_incident_payload_sha256 = "e" * 64
    with pytest.raises(M.EvidenceError, match="compiled exact file/payload"):
        M._validate_recovery_chain(a, pre)


@pytest.mark.parametrize("mutation", [
    "rollback", "ready", "message", "exposure", "runner", "quarantine",
    "transaction", "stage2_abort", "preservation", "terminal_status",
    "terminal_prefix", "terminal_review", "terminal_provenance",
    "stage2_review_findings", "terminal_inventory",
])
def test_recovery_chain_rejects_adversarial_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    a = args(tmp_path)
    pre, incident, _quarantine, recovery, context = recovery_fixture(monkeypatch, tmp_path)
    if mutation == "rollback":
        incident["bound_control_artifacts"][
            "rollback-sealed-runtimes-intent-v3.json"]["sha256"] = "0" * 64
    elif mutation == "ready":
        incident["immutable_pre_recovery_state"]["rollback_ready_absent"] = False
    elif mutation == "message":
        incident["user_owned_transcript_receipts"]["72153"]["line_sha256"] = "0" * 64
    elif mutation == "exposure":
        recovery["scientific_exposure"]["decoder_started"] = True
    elif mutation == "quarantine":
        _quarantine["canonical_control_root_absent_after_recovery"] = False
    elif mutation == "transaction":
        recovery["transaction_directory_disposition"].pop("component")
    elif mutation == "stage2_abort":
        recovery["abort_intent"] = identity("/wrong")
    elif mutation == "preservation":
        _quarantine["source_results_and_iq_preservation"]["iq_source_metadata_unchanged"] = False
    elif mutation == "terminal_status":
        context["terminal"]["status"] = "PASS"
    elif mutation == "terminal_prefix":
        context["terminal"]["durable_prefix_length"] = 1
    elif mutation == "terminal_review":
        context["terminal_review"]["reviewed_bindings"]["terminal_validator"] = identity("/wrong")
    elif mutation == "terminal_provenance":
        context["terminal"]["provenance"] = identity("/wrong")
    elif mutation == "stage2_review_findings":
        context["stage2_review"]["findings"] = {"P0": 0, "P1": 0, "P2": 0}
    elif mutation == "terminal_inventory":
        context["terminal"]["historical_controller_inventory_sha256"] = "0" * 64
    else:
        recovery["recovery_implementation_chain"]["abort_runner"] = identity("/wrong")
    with pytest.raises(M.EvidenceError):
        M._validate_recovery_chain(a, pre)


def test_build_discloses_mechanical_iq_but_no_science(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = args(tmp_path)
    pre, *_ = recovery_fixture(monkeypatch, tmp_path)
    pre.update({
        "lifecycle_artifacts": {
            "persistent-mount-namespace-intent-v3.json": {"a": 1},
            "persistent-mount-namespace-registration-v3.json": {"b": 2},
            "persistent-mount-namespace-active-v3.json": {"c": 3},
            "readonly-project-activation-intent-v3.json": {"d": 4},
            "readonly-project-seal-window-v3.json": {"e": 5},
            "seal-transaction-intent-v3.json": {"f": 6},
            "sealed-runtime-transaction-v3.json": {"g": 7},
                "readonly-project-closed-v3.json": {
                    "h": 8, "original_identity": identity("/control/closed.json")},
        },
        "keeper_generation": {"pid": M.KEEPER_PID},
        "closed_mount_topology": {"mount_id": 166},
        "sealed_input_snapshot": {"input_count": 30},
        "sealed_project_artifacts": {"artifact_count": 1},
    })
    protocol = {
        "amendment": identity("/amendment"), "review": identity("/review"),
        "timestamp_query": identity("/query"), "timestamp_response": identity("/response"),
        "runtime_guard_builder_control_copy": identity("/builder-control"),
    }
    recovery = {
        "stage1_rollback_recovery": {"incident": identity("/incident")},
        "corrective_evidence": {
            "nonexecuted_corrected_runtime_guard_builder": identity("/builder"),
            "corrected_runtime_guard_builder_tests": identity("/test-builder"),
        },
    }
    temporary = {"path_count": 4}
    monkeypatch.setattr(M, "_protocol", lambda _root: protocol)
    monkeypatch.setattr(M, "_immutable_pre_recovery_state", lambda _p, _root: pre)
    monkeypatch.setattr(M, "_validate_recovery_chain", lambda _a, _p: recovery)
    monkeypatch.setattr(M, "_temporary_storage_disclosure", lambda: temporary)
    def fake_read(path: Path, *_a, **_k):
        digest = a.expected_generator_sha256 if path == Path(M.__file__) else a.expected_generator_test_sha256
        return b"x", {**identity(str(path), digest), "st_dev": 1, "st_ino": 2,
                      "uid": 1000, "gid": 1000, "mode": 0o644, "nlink": 1}
    monkeypatch.setattr(M, "_read_regular", fake_read)
    doc = M.build(a)
    assert doc["scientific_exposure"]["iq_mechanically_copied"] is True
    assert doc["scientific_exposure"]["iq_used_as_decoder_input"] is False
    assert doc["scientific_exposure"]["iq_content_accessed_by_evidence_generator"] is False
    assert doc["scientific_exposure"]["decoder_started"] is False
    assert doc[M.HASH_FIELD] == M._sha256_document({k: v for k, v in doc.items() if k != M.HASH_FIELD})


def test_explicit_timestamp_is_strict_and_deterministic() -> None:
    assert M._created_at("2026-09-06T07:00:00Z") == "2026-09-06T07:00:00Z"
    for bad in ("2026-09-06T07:00Z", "2026-09-06 07:00:00Z", "now"):
        with pytest.raises(M.EvidenceError):
            M._created_at(bad)


def test_identity_shape_rejects_bool_size() -> None:
    assert M._identity_shape(identity("/x"))
    assert not M._identity_shape({"path": "/x", "size_bytes": True, "sha256": "a" * 64})


def test_incident_candidate_is_stdout_only_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = args(tmp_path)
    pre, *_ = recovery_fixture(monkeypatch, tmp_path)
    temporary = {"path_count": 4}
    monkeypatch.setattr(M, "_protocol", lambda _root: {"x": 1})
    monkeypatch.setattr(M, "_immutable_pre_recovery_state", lambda _p, _root: pre)
    monkeypatch.setattr(M, "_temporary_storage_disclosure", lambda: temporary)
    def fake_read(path: Path, *_a, **_k):
        digest = a.expected_generator_sha256 if path == Path(M.__file__) else a.expected_generator_test_sha256
        return b"x", {**identity(str(path), digest), "st_dev": 1, "st_ino": 2,
                      "uid": 1000, "gid": 1000, "mode": 0o644, "nlink": 1}
    monkeypatch.setattr(M, "_read_regular", fake_read)
    doc = M.build_incident_candidate(a)
    assert doc["schema_version"] == M.INCIDENT_SCHEMA
    assert doc["status"] == M.INCIDENT_STATUS
    assert doc["recovery_started_before_incident_publication"] is True
    assert doc["pre_recovery_topology_authority"] == pre["lifecycle_artifacts"]["readonly-project-closed-v3.json"]["original_identity"]
    assert doc[M.INCIDENT_HASH_FIELD] == M._sha256_document({k: v for k, v in doc.items() if k != M.INCIDENT_HASH_FIELD})


def test_selfhash_rejects_mutation() -> None:
    doc = hashed("x", "PASS", "payload_sha256", value=1)
    M._validate_selfhash(doc, "payload_sha256", doc["payload_sha256"], "x")
    doc["value"] = 2
    with pytest.raises(M.EvidenceError):
        M._validate_selfhash(doc, "payload_sha256", doc["payload_sha256"], "x")


def test_regular_reader_rejects_symlink_and_is_bounded(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_bytes(b"abcd")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(M.EvidenceError):
        M._read_regular(link)
    with pytest.raises(M.EvidenceError):
        M._read_regular(real, 3)


def test_terminal_execution_receipt_is_exact_and_drains_unrelated_oversized_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    a = args(tmp_path)
    terminal = {"status": M.TERMINAL_VALIDATOR_STATUS, "durable_prefix_length": 2}
    output = json.dumps(terminal, sort_keys=True) + "\n"
    record = {
        "ordinal": 1,
        "payload": {"item": {
            "type": "CommandExecution",
            "command": ["/bin/bash", "-lc", M._terminal_validator_command(a)],
            "cwd": "file:///home/ubuntu/telemetry-yield",
            "status": "completed", "exit_code": 0, "stderr": "",
            "stdout": output, "aggregated_output": output,
        }},
    }
    raw = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"x" * (256 * 1024) + b"\n" + raw)
    receipt = {
        "source_path": str(transcript), "line_number": 2, "ordinal": 1,
        "raw_line_size_bytes": len(raw), "raw_line_sha256": hashlib.sha256(raw).hexdigest(),
        "exit_code": 0, "stdout_size_bytes": len(output.encode()),
        "stdout_sha256": hashlib.sha256(output.encode()).hexdigest(),
        "stderr_size_bytes": 0,
    }
    monkeypatch.setattr(M, "TERMINAL_VALIDATOR_TRANSCRIPT", transcript)
    monkeypatch.setattr(M, "TERMINAL_VALIDATOR_EXECUTION_RECEIPT", receipt)
    assert M._validate_terminal_execution_receipt(a, terminal) == receipt


def test_terminal_execution_receipt_rejects_oversized_target_line(tmp_path: Path) -> None:
    transcript = tmp_path / "oversized.jsonl"
    transcript.write_bytes(b"x" * (64 * 1024 + 1) + b"\n")
    with pytest.raises(M.EvidenceError, match="exceeds bound"):
        M._read_bounded_transcript_line(transcript, 1)


def _publication_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> tuple[Path, Path]:
    reports = tmp_path / "reports"
    reports.mkdir(mode=0o775)
    reports.chmod(0o775)
    output = reports / "evidence.json"
    owner_uid = os.geteuid()
    owner_gid = os.getegid()
    status = reports.stat()
    monkeypatch.setattr(M, "REPORTS", reports)
    monkeypatch.setattr(M, "OUTPUT", output)
    monkeypatch.setattr(M, "EXPECTED_REPORTS_PARENT", {
        "path": str(reports), "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": owner_uid, "gid": owner_gid, "mode": 0o775,
        "nlink": status.st_nlink,
    })
    monkeypatch.setattr(M, "PUBLISHED_UID", owner_uid)
    monkeypatch.setattr(M, "PUBLISHED_GID", owner_gid)
    monkeypatch.setattr(M.os, "geteuid", lambda: 0)
    return reports, output


def test_publisher_recovers_exact_sidecar_first_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _reports, output = _publication_fixture(monkeypatch, tmp_path)
    doc = {"schema_version": "x", "status": "PASS", "payload_sha256": "a" * 64}
    payload = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
    file_sha = hashlib.sha256(payload).hexdigest()
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.write_text(f"{file_sha}  {output.name}\n")
    sidecar.chmod(0o444)
    M.publish(doc)
    assert output.read_bytes() == payload
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert output.stat().st_nlink == 1
    before = (output.stat().st_ino, sidecar.stat().st_ino)
    M.publish(doc)
    assert (output.stat().st_ino, sidecar.stat().st_ino) == before


def test_publisher_exact_pair_resumes_after_post_main_link_failure_without_relink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _reports, output = _publication_fixture(monkeypatch, tmp_path)
    doc = {"schema_version": "x", "status": "PASS", "payload_sha256": "a" * 64}
    real_validate = M._validate_published_relative
    injected = False

    def fail_once(directory_fd, name, payload, expected_inode=None):
        nonlocal injected
        result = real_validate(directory_fd, name, payload, expected_inode)
        if name == output.name and not injected:
            injected = True
            raise RuntimeError("fault after durable main link")
        return result

    monkeypatch.setattr(M, "_validate_published_relative", fail_once)
    with pytest.raises(RuntimeError, match="fault after durable main link"):
        M.publish(doc)
    sidecar = output.with_name(output.name + ".sha256")
    committed_inodes = (output.stat().st_ino, sidecar.stat().st_ino)
    monkeypatch.setattr(M, "_validate_published_relative", real_validate)

    def no_relink(*_args, **_kwargs):
        raise AssertionError("exact committed pair must not be relinked")

    monkeypatch.setattr(M, "_publish_one", no_relink)
    M.publish(doc)
    assert (output.stat().st_ino, sidecar.stat().st_ino) == committed_inodes


def test_publisher_rejects_main_only_or_mismatched_committed_pair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _reports, output = _publication_fixture(monkeypatch, tmp_path)
    output.write_bytes(b"foreign")
    output.chmod(0o444)
    with pytest.raises(M.EvidenceError, match="main-only"):
        M.publish({"schema_version": "x", "status": "PASS"})
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.write_text("0" * 64 + f"  {output.name}\n")
    sidecar.chmod(0o444)
    with pytest.raises(M.EvidenceError, match="published file identity/bytes differ"):
        M.publish({"schema_version": "x", "status": "PASS"})


def test_publisher_rejects_reports_parent_swap_before_main_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    reports, output = _publication_fixture(monkeypatch, tmp_path)
    displaced = tmp_path / "displaced-reports"
    real_publish_one = M._publish_one

    def publish_then_swap(directory_fd: int, name: str, payload: bytes):
        result = real_publish_one(directory_fd, name, payload)
        if name.endswith(".sha256"):
            reports.rename(displaced)
            reports.mkdir(mode=0o775)
            reports.chmod(0o775)
        return result

    monkeypatch.setattr(M, "_publish_one", publish_then_swap)
    with pytest.raises(M.EvidenceError, match="parent identity/path differs"):
        M.publish({"schema_version": "x", "status": "PASS"})
    assert not output.exists()
    assert (displaced / f"{output.name}.sha256").is_file()


def test_publisher_rejects_output_collision_fd_relative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _reports, output = _publication_fixture(monkeypatch, tmp_path)
    target = tmp_path / "target"
    target.write_bytes(b"foreign")
    output.symlink_to(target)
    with pytest.raises(M.EvidenceError, match="main-only"):
        M.publish({"schema_version": "x", "status": "PASS"})


def test_publisher_rejects_post_link_canonical_child_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    reports, output = _publication_fixture(monkeypatch, tmp_path)
    real_publish_one = M._publish_one

    def publish_then_replace(directory_fd: int, name: str, payload: bytes):
        result = real_publish_one(directory_fd, name, payload)
        if name == output.name:
            displaced = reports / f"{name}.displaced"
            (reports / name).rename(displaced)
            (reports / name).write_bytes(payload)
            (reports / name).chmod(0o444)
        return result

    monkeypatch.setattr(M, "_publish_one", publish_then_replace)
    with pytest.raises(M.EvidenceError, match="published file identity/bytes differ"):
        M.publish({"schema_version": "x", "status": "PASS"})


def test_no_iq_cli_or_dangerous_mutator() -> None:
    source = SOURCE.read_text()
    parser_text = source[source.index("def _parser"):]
    assert "--iq" not in parser_text
    tree = ast.parse(source)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
            elif isinstance(node.func, ast.Name):
                calls.append(node.func.id)
    for forbidden in ("rmtree", "unlink", "remove", "setns", "mount", "umount"):
        assert forbidden not in calls
