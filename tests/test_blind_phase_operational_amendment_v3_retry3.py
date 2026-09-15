from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/freeze_operational_amendment_v3_retry3.py"
TEMPLATE = ROOT / "work/blind-phase-confirmatory-v2/operational-amendment-review-template-v3-retry3.json"
SPEC = importlib.util.spec_from_file_location("freeze_operational_amendment_v3_retry3", SOURCE)
assert SPEC and SPEC.loader
FREEZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FREEZER)


def _write(path: Path, payload: bytes) -> dict[str, object]:
    path.write_bytes(payload)
    return {
        "path": str(path.absolute()), "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_json(path: Path, value: object) -> dict[str, object]:
    return _write(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def _immutable_json(path: Path, value: object) -> dict[str, object]:
    identity = _write_json(path, value)
    os.chmod(path, 0o444)
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{identity['sha256']}  {path.name}\n", encoding="ascii")
    os.chmod(sidecar, 0o444)
    return identity


def _self_hashed(path: Path, *, schema: str, status: str, field: str) -> tuple[dict[str, object], dict[str, object]]:
    value: dict[str, object] = {
        "schema_version": schema,
        "status": status,
        "detail": path.stem,
    }
    value[field] = FREEZER.sha256_document(value)
    return value, _immutable_json(path, value)


def _identity(path: Path, *, code: bool = False) -> dict[str, object]:
    return FREEZER._identity(
        path, hashlib.sha256(path.read_bytes()).hexdigest(), code=code
    )


def _live_identity(path: Path, identity: dict[str, object]) -> dict[str, object]:
    status = path.lstat()
    return {
        "path": str(path.absolute()), "st_dev": status.st_dev,
        "st_ino": status.st_ino, "uid": status.st_uid, "gid": status.st_gid,
        "mode": status.st_mode & 0o777, "nlink": status.st_nlink,
        "size_bytes": identity["size_bytes"], "sha256": identity["sha256"],
    }


def _make_evidence(
    identities: dict[str, dict[str, object]], chain: dict[str, dict[str, object]],
    chain_documents: dict[str, dict[str, object]], previous: dict[str, object],
    *, created_at: str = "2026-09-06T07:00:00Z",
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": FREEZER.EVIDENCE_SCHEMA,
        "status": FREEZER.EVIDENCE_STATUS,
        "created_at_utc": created_at,
        "protocol": {
            "retry2_amendment": identities["previous-amendment"],
            "retry2_amendment_payload_sha256": FREEZER.EXPECTED_PREVIOUS_AMENDMENT_PAYLOAD_SHA256,
            "retry2_independent_review": identities["previous-review"],
            "retry2_review_payload_sha256": FREEZER.EXPECTED_PREVIOUS_REVIEW_PAYLOAD_SHA256,
            "retry2_rfc3161_timestamp_query": FREEZER._identity_at(
                identities["previous-timestamp-query"],
                Path("/var/lib/telemetry-yield-confirmatory-v3/amendment-v3.tsq"),
            ),
            "retry2_rfc3161_timestamp_response": FREEZER._identity_at(
                identities["previous-timestamp-reply"],
                Path("/var/lib/telemetry-yield-confirmatory-v3/amendment-v3.tsr"),
            ),
            "failed_attempt_runtime_guard_builder": {
                "path": "/var/lib/telemetry-yield-confirmatory-v3/build_runtime_guard_v3.py",
                "size_bytes": 369199,
                "sha256": "eb528941de423c59e675119af1f9f7468ad39977c3119176728ca8d6ae7408c2",
            },
            "corrected_runtime_guard_builder": identities["runtime-guard-builder"],
            "corrected_runtime_guard_builder_tests": identities[
                "runtime-guard-builder-test"
            ],
            "evidence_generator": identities["fourth-failure-evidence-generator"],
            "evidence_generator_tests": identities[
                "fourth-failure-evidence-generator-test"
            ],
        },
        "lifecycle": {
            "start_succeeded": True,
            "activate_succeeded": True,
            "seal_succeeded": True,
            "close_succeeded": True,
            "freeze_guard_succeeded": False,
            "original_rollback_intent_published": True,
            "original_rollback_ready_or_complete_published": False,
            "original_rollback_failed_errno": "EBUSY",
        },
        "readonly_activation": {
            "activation_intent": {"status": "PASS"},
            "seal_window": {"status": "PASS"},
            "seal_transaction_intent": {"status": "PASS"},
            "sealed_runtime": {"status": "PASS"},
            "closed": {"status": "PASS"},
            "closed_mount_topology": {"status": "closed"},
            "sealed_input_snapshot": {
                "schema_version": "blind-phase-confirmatory-sealed-input-snapshot-v3",
                "status": "PASS", "input_count": 30,
                "snapshot_payload_sha256": "a" * 64,
                "mechanically_copied_hashed_and_sealed": True,
            },
            "sealed_project_artifacts": {"status": "PASS"},
        },
        "freeze_attempts": {
            "attempt1": {
                "handoff_artifact": {
                    "original_identity": chain["freeze-handoff-1"],
                    "schema_version": chain_documents["freeze-handoff-1"]["schema_version"],
                    "status": chain_documents["freeze-handoff-1"]["status"],
                    "self_hash_field": "payload_sha256",
                    "payload_sha256": chain_documents["freeze-handoff-1"]["payload_sha256"],
                },
                "pid": 101, "nonce_sha256": "1" * 64,
                "failure": {"error_type": "OSError", "errno": 28},
                "runtime_guard_absent_after_attempt": True,
                "failure_artifact": chain["recovery-incident"],
                "failure_record_pointer": "/user_owned_transcript_receipts/71937",
                "failure_class": "ENOSPC",
                "runtime_guard_created": False,
            },
            "attempt2": {
                "handoff_artifact": {
                    "original_identity": chain["freeze-handoff-2"],
                    "schema_version": chain_documents["freeze-handoff-2"]["schema_version"],
                    "status": chain_documents["freeze-handoff-2"]["status"],
                    "self_hash_field": "payload_sha256",
                    "payload_sha256": chain_documents["freeze-handoff-2"]["payload_sha256"],
                },
                "pid": 102, "nonce_sha256": "2" * 64,
                "failure": {"error_type": "ValueError"},
                "runtime_guard_absent_after_attempt": True,
                "failure_artifact": chain["recovery-incident"],
                "failure_record_pointer": "/user_owned_transcript_receipts/71991",
                "failure_class": "MISSING_CAPACITY_CONTRACT_IN_CANDIDATE_METADATA_CONTEXT",
                "runtime_guard_created": False,
            },
        },
        "temporary_storage_remediation": {
            "paths": [
                {
                    "path": f"/var/tmp/RML24-old-{index}",
                    "measured_allocated_bytes": size,
                    "currently_absent": True,
                }
                for index, size in enumerate((
                    1_589_424_128, 1_589_420_032, 1_589_428_224, 1_589_428_224,
                ))
            ],
            "path_count": 4,
            "measured_allocated_bytes_total": 6_357_700_608,
        },
        "recovery": {
            "rollback_intent": chain["rollback-intent"],
            "failed_original_rollback": chain_documents["recovery-incident"][
                "failed_original_rollback"
            ],
            "stage1_rollback_recovery": {
                "incident": chain["recovery-incident"],
                "rollback_recovery": {"path": "/rollback", "size_bytes": 1, "sha256": "1" * 64},
                "rollback_ready": {"path": "/ready", "size_bytes": 1, "sha256": "2" * 64},
                "rollback_complete": {"path": "/complete", "size_bytes": 1, "sha256": "3" * 64},
                "source_restoration": {"path": "/restoration", "size_bytes": 1, "sha256": "4" * 64},
                "parent_open_receipts": {}, "runner": {"path": "/stage1.py", "size_bytes": 1, "sha256": "5" * 64},
                "runner_tests": {"path": "/test-stage1.py", "size_bytes": 1, "sha256": "6" * 64},
                "independent_review": {"path": "/stage1-review", "size_bytes": 1, "sha256": "7" * 64},
            },
            "retry1_failed_preflight": {
                "incident": chain_documents["recovery-pass"]["recovery_implementation_chain"]["retry1_preflight_failure_incident"],
                "failure_class": "FULL_VERSUS_PROJECTED_CONTROL_EVIDENCE_COMPOSITION_MISMATCH",
                "mutation_reached": False,
            },
            "stage2_abort_and_cleanup": {
                "recovery_pass": chain["recovery-pass"],
                "runner": identities["recovery-runner"],
                "runner_tests": identities["recovery-runner-test"],
                "independent_review": identities["recovery-independent-review"],
                "durable_prefix_length": 9,
                "ordered_receipts": [
                    {"path": f"/receipt-{index}", "size_bytes": 1, "sha256": f"{index:x}" * 64}
                    for index in range(9)
                ],
                "abort_intent": chain_documents["recovery-pass"]["abort_intent"],
                "stopped_contract": chain_documents["recovery-pass"]["stopped_contract"],
                "cleanup_receipt": chain_documents["recovery-pass"]["cleanup_receipt"],
            },
            "control_root_quarantine": {
                "intent": chain["quarantine-intent"],
                "provenance": chain["quarantine-provenance"],
                "runner": identities["quarantine-runner"],
                "runner_tests": identities["quarantine-runner-test"],
                "independent_review": identities["quarantine-independent-review"],
                "source_control_root": "/var/lib/telemetry-yield-confirmatory-v3",
                "quarantine_root": "/var/lib/quarantine",
                "full_relative_manifest_sha256": "8" * 64,
            },
            "corrective_evidence": chain_documents["recovery-pass"]["corrective_evidence"],
            "terminal_state": {
                "stage2_post_recovery_evidence": chain_documents["recovery-pass"]["post_recovery_evidence"],
                "post_quarantine_state": chain_documents["quarantine-provenance"]["post_quarantine_state"],
                "canonical_control_root_absent": True,
                "quarantined_control_root_preserved": True,
            },
            "transaction_directory_disposition": chain_documents["recovery-pass"][
                "transaction_directory_disposition"
            ],
            "live_keeper_or_mount_remaining": False,
        },
        "pre_recovery_live_state": {
            "topology_authority": {"path": "/closed", "size_bytes": 1, "sha256": "c" * 64},
            "keeper": {"pid": 101}, "relevant_mounts": [], "relevant_mount_count": 6,
            "runtime_guard_absent": True,
            "authorized_campaign_or_evaluator_process_count": 0,
            "role_artifact_count": 0, "decoder_artifact_count": 0,
            "outcome_artifact_count": 0,
            "current_post_rollback_failure_state_was_not_used_as_pre_recovery_state": True,
        },
        "scientific_exposure": {
            "iq_mechanically_copied": True,
            "iq_mechanically_hashed": True,
            "iq_mechanically_sealed": True,
            "iq_used_as_decoder_input": False,
            "iq_content_accessed_by_evidence_generator": False,
            "runtime_guard_created": False,
            "campaign_role_started": False,
            "evaluator_role_started": False,
            "decoder_started": False,
            "outcome_read": False,
            "outcome_generated": False,
        },
    }
    value[FREEZER.EVIDENCE_SELF_HASH_FIELD] = FREEZER.sha256_document(value)
    return value


def _base_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[argparse.Namespace, dict[str, object]]:
    expected_paths = dict(FREEZER.EXPECTED_FIXED_PATHS)
    for role in (
        "fourth-failure-evidence-generator",
        "fourth-failure-evidence-generator-test",
        "fourth-failure-evidence-independent-review",
        "fourth-failure-evidence-independent-review-sidecar",
        "fourth-failure-evidence-postpublish-review-generator",
        "fourth-failure-evidence-postpublish-review-generator-test",
        "fourth-failure-evidence-postpublish-independent-review",
        "fourth-failure-evidence-postpublish-independent-review-sidecar",
    ):
        path = tmp_path / f"{role}.py"
        path.write_text(f"# {role}\n")
        expected_paths[role] = path.absolute()
    monkeypatch.setattr(FREEZER, "EXPECTED_FIXED_PATHS", expected_paths)
    fixed_hashes = dict(FREEZER.FIXED_HASHES)
    for role in (
        "fourth-failure-evidence-generator",
        "fourth-failure-evidence-generator-test",
        "fourth-failure-evidence-independent-review",
        "fourth-failure-evidence-independent-review-sidecar",
        "fourth-failure-evidence-postpublish-review-generator",
        "fourth-failure-evidence-postpublish-review-generator-test",
        "fourth-failure-evidence-postpublish-independent-review",
        "fourth-failure-evidence-postpublish-independent-review-sidecar",
    ):
        fixed_hashes.pop(role)
    monkeypatch.setattr(FREEZER, "FIXED_HASHES", fixed_hashes)
    monkeypatch.setattr(FREEZER, "IMMUTABLE_EVIDENCE_UID", os.geteuid())
    monkeypatch.setattr(FREEZER, "IMMUTABLE_EVIDENCE_GID", os.getegid())
    monkeypatch.setattr(FREEZER, "FIXED_CHAIN_SHA256", {})
    monkeypatch.setattr(FREEZER, "FIXED_CHAIN_PAYLOAD_SHA256", {})
    monkeypatch.setattr(FREEZER, "FIXED_CHAIN_CONTRACTS", {})
    monkeypatch.setattr(FREEZER, "FIXED_CHAIN_PATHS", {})
    monkeypatch.setattr(FREEZER, "HISTORICAL_CHAIN_PATHS", {
        role: tmp_path / "historical-control" / f"{role}.json"
        for role in ("freeze-handoff-1", "freeze-handoff-2", "rollback-intent")
    })
    monkeypatch.setattr(FREEZER, "OUTPUT", tmp_path / "amendment.json")
    monkeypatch.setattr(FREEZER, "REVIEW_OUTPUT", tmp_path / "review.json")

    identities: dict[str, dict[str, object]] = {}
    values: dict[str, object] = {}
    for role, path in expected_paths.items():
        identity = _identity(path, code=role in FREEZER.CODE_ROLES)
        identities[role] = identity
        key = role.replace("-", "_")
        values[key] = path
        values[f"expected_{key}_sha256"] = identity["sha256"]

    for role in (
        "recovery-runner", "recovery-runner-test",
        "quarantine-runner", "quarantine-runner-test",
        "terminal-validator", "terminal-validator-test",
    ):
        path = tmp_path / f"{role}.py"
        path.write_text(f"# {role}\n")
        identity = _identity(path, code=True)
        identities[role] = identity
        key = role.replace("-", "_")
        values[key] = path
        values[f"expected_{key}_sha256"] = identity["sha256"]
    recovery_review: dict[str, object] = {
        "schema_version": (
            "blind-phase-confirmatory-fourth-pre-campaign-abort-"
            "retry2-independent-review-v1"
        ),
        "status": "GO",
        "findings": {"p0_open": 0, "p1_open": 0, "p2_open": 0},
    }
    chain_documents: dict[str, dict[str, object]] = {}
    chain_identities: dict[str, dict[str, object]] = {}

    def add_chain(role: str, value: dict[str, object]) -> None:
        value["payload_sha256"] = FREEZER.sha256_document(value)
        identity = _immutable_json(tmp_path / f"{role}.json", value)
        chain_documents[role] = value
        chain_identities[role] = identity
        key = role.replace("-", "_")
        values[key] = Path(str(identity["path"]))
        values[f"expected_{key}_sha256"] = identity["sha256"]
        values[f"{key}_schema"] = value["schema_version"]
        values[f"{key}_status"] = value["status"]
        values[f"{key}_self_hash_field"] = "payload_sha256"

    for role in ("freeze-handoff-1", "freeze-handoff-2", "rollback-intent"):
        add_chain(role, {
            "schema_version": f"test-{role}-v1",
            "status": "PASS" if role == "quarantine-provenance" else "AUTHORIZED",
            "detail": role,
        })
    add_chain("recovery-incident", {
        "schema_version": "test-recovery-incident-v1",
        "status": "COMMITTED_BEFORE_RECOVERY",
        "failed_original_rollback": {
            "rollback_intent": chain_identities["rollback-intent"],
            "failure_class": "EBUSY_SELF_HELD_PARENT_MOUNT_FD",
            "rollback_ready_created": False,
            "rollback_complete_created": False,
        },
    })
    retry1_failure = {
        "path": "/retry1-failure", "size_bytes": 1, "sha256": "9" * 64,
    }
    recovery_review.update({
        "reviewed_bindings": {
            "abort_runner_retry2": identities["recovery-runner"],
            "abort_runner_retry2_test": identities["recovery-runner-test"],
            "retry1_preflight_failure_incident": retry1_failure,
        },
    })
    recovery_review["review_payload_sha256"] = FREEZER.sha256_document(recovery_review)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_STAGE2_REVIEW_PAYLOAD_SHA256",
        recovery_review["review_payload_sha256"],
    )
    recovery_review_identity = _immutable_json(tmp_path / "recovery-review.json", recovery_review)
    identities["recovery-independent-review"] = recovery_review_identity
    values.update({
        "recovery_independent_review": Path(str(recovery_review_identity["path"])),
        "expected_recovery_independent_review_sha256": recovery_review_identity["sha256"],
        "recovery_independent_review_schema": recovery_review["schema_version"],
        "recovery_independent_review_self_hash_field": "review_payload_sha256",
    })
    receipt = {"path": "/receipt", "size_bytes": 1, "sha256": "b" * 64}
    add_chain("recovery-pass", {
        "schema_version": "test-recovery-pass-v1",
        "status": "PASS",
        "incident": chain_identities["recovery-incident"],
        "rollback_intent": FREEZER._chain_evidence_identity(
            "rollback-intent", chain_identities["rollback-intent"]
        ),
        "recovery_implementation_chain": {
            "retry1_preflight_failure_incident": retry1_failure,
        },
        "corrective_evidence": {
            "nonexecuted_corrected_runtime_guard_builder": identities[
                "runtime-guard-builder"
            ],
            "corrected_runtime_guard_builder_tests": identities[
                "runtime-guard-builder-test"
            ],
            "independently_reviewed_by": recovery_review_identity,
        },
        "abort_intent": receipt, "stopped_contract": receipt,
        "cleanup_receipt": receipt,
        "post_recovery_evidence": {"processes": {}},
        "transaction_directory_disposition": {
            label: {"status": "IRREVERSIBLY_DELETED_OPERATIONAL_RUNTIME_DUPLICATES"}
            for label in ("candidate", "component")
        },
    })
    quarantine_review: dict[str, object] = {
        "schema_version": "test-quarantine-review-v1", "status": "GO",
        "reviewed_bindings": {
            "quarantine_runner": identities["quarantine-runner"],
            "quarantine_runner_test": identities["quarantine-runner-test"],
            "stage2_recovery_result": chain_identities["recovery-pass"],
        },
        "findings": {"P0": 0, "P1": 0, "P2": 0},
    }
    quarantine_review["review_payload_sha256"] = FREEZER.sha256_document(quarantine_review)
    quarantine_review_identity = _immutable_json(
        tmp_path / "quarantine-review.json", quarantine_review)
    identities["quarantine-independent-review"] = quarantine_review_identity
    values.update({
        "quarantine_independent_review": Path(str(quarantine_review_identity["path"])),
        "expected_quarantine_independent_review_sha256": quarantine_review_identity["sha256"],
        "quarantine_independent_review_schema": "test-quarantine-review-v1",
        "quarantine_independent_review_self_hash_field": "review_payload_sha256",
    })
    add_chain("quarantine-intent", {
        "schema_version": "test-quarantine-intent-v1", "status": "COMMITTED",
        "stage2": {"result": chain_identities["recovery-pass"]},
    })
    add_chain("quarantine-provenance", {
        "schema_version": "test-quarantine-provenance-v1", "status": "PASS",
        "intent": chain_identities["quarantine-intent"],
        "stage2_recovery_result": chain_identities["recovery-pass"],
        "post_quarantine_state": {"keeper_absent": True, "relevant_mount_count": 0},
    })
    monkeypatch.setattr(FREEZER, "FIXED_CHAIN_PAYLOAD_SHA256", {
        "quarantine-intent": chain_documents["quarantine-intent"]["payload_sha256"],
        "quarantine-provenance": chain_documents["quarantine-provenance"]["payload_sha256"],
    })

    terminal_review: dict[str, object] = {
        "schema_version": (
            "blind-phase-confirmatory-fourth-control-quarantine-"
            "terminal-validator-independent-review-v1"
        ),
        "status": "GO",
        "findings": {"P0": 0, "P1": 0, "P2": 0},
        "reviewed_bindings": {
            "terminal_validator": identities["terminal-validator"],
            "terminal_validator_test": identities["terminal-validator-test"],
            "historical_quarantine_runner": identities["quarantine-runner"],
            "historical_quarantine_runner_test": identities["quarantine-runner-test"],
            "historical_quarantine_independent_review": quarantine_review_identity,
            "historical_quarantine_intent": chain_identities["quarantine-intent"],
            "historical_quarantine_provenance": chain_identities["quarantine-provenance"],
            "corrected_quarantine_runner_contract_repo": identities[
                "corrected-quarantine-contract"
            ],
            "corrected_quarantine_runner_contract_test_repo": identities[
                "corrected-quarantine-contract-test"
            ],
        },
        "terminal_live_contract": FREEZER._expected_terminal_review_contract({
            **identities, **chain_identities,
        }),
    }
    terminal_review["review_payload_sha256"] = FREEZER.sha256_document(terminal_review)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_TERMINAL_REVIEW_PAYLOAD_SHA256",
        terminal_review["review_payload_sha256"],
    )
    terminal_review_identity = _immutable_json(
        tmp_path / "terminal-review.json", terminal_review,
    )
    identities["terminal-validator-independent-review"] = terminal_review_identity
    values.update({
        "terminal_validator_independent_review": Path(
            str(terminal_review_identity["path"])
        ),
        "expected_terminal_validator_independent_review_sha256": (
            terminal_review_identity["sha256"]
        ),
    })

    monkeypatch.setattr(FREEZER, "FIXED_IMPLEMENTATION_PATHS", {
        role: Path(str(identities[role]["path"]))
        for role in FREEZER.RECOVERY_IMPLEMENTATION_ROLES
    })
    monkeypatch.setattr(FREEZER, "FIXED_IMPLEMENTATION_SHA256", {
        role: str(identities[role]["sha256"])
        for role in FREEZER.RECOVERY_IMPLEMENTATION_ROLES
    })

    values.update({
        "review": FREEZER.REVIEW_OUTPUT,
        "expected_review_sha256": "f" * 64,
        "output": FREEZER.OUTPUT,
        "created_at": "2026-09-06T07:01:00Z",
    })
    previous = json.loads(Path(str(identities["previous-amendment"]["path"])).read_bytes())
    evidence_chain_identities = {
        role: dict(FREEZER._chain_evidence_identity(role, identity))
        for role, identity in chain_identities.items()
    }
    evidence = _make_evidence(
        identities, evidence_chain_identities, chain_documents, previous
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_FAILED_ORIGINAL_ROLLBACK",
        evidence["recovery"]["failed_original_rollback"],
    )
    evidence_identity = _immutable_json(tmp_path / "fourth-evidence.json", evidence)
    monkeypatch.setattr(FREEZER, "EXPECTED_EVIDENCE_PATH", Path(str(evidence_identity["path"])))
    monkeypatch.setattr(FREEZER, "EXPECTED_EVIDENCE_SHA256", evidence_identity["sha256"])
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_PAYLOAD_SHA256",
        evidence[FREEZER.EVIDENCE_SELF_HASH_FIELD],
    )
    evidence_sidecar_identity = _identity(
        Path(str(evidence_identity["path"]) + ".sha256")
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SIDECAR_SHA256",
        evidence_sidecar_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SIDECAR_SIZE_BYTES",
        evidence_sidecar_identity["size_bytes"],
    )
    values["fourth_failure_evidence"] = Path(str(evidence_identity["path"]))
    values["expected_fourth_failure_evidence_sha256"] = evidence_identity["sha256"]
    evidence_payload = Path(str(evidence_identity["path"])).read_bytes()
    assert evidence_payload.endswith(b"\n")
    literal_backslash_n = evidence_payload[:-1] + b"\\n"
    superseded_prediction = {
        "path": str(evidence_identity["path"]),
        "size_bytes": len(literal_backslash_n),
        "sha256": hashlib.sha256(literal_backslash_n).hexdigest(),
        "evidence_payload_sha256": evidence[FREEZER.EVIDENCE_SELF_HASH_FIELD],
    }
    evidence_review: dict[str, object] = {
        "schema_version": (
            "blind-phase-confirmatory-fourth-freeze-failure-evidence-"
            "independent-review-v1"
        ),
        "status": "GO",
        "findings": {"P0": 0, "P1": 0, "P2": 0},
        "reviewed_bindings": {
            "evidence_generator": identities["fourth-failure-evidence-generator"],
            "evidence_generator_test": identities[
                "fourth-failure-evidence-generator-test"
            ],
            "exact_stage1_recovery_incident": chain_identities["recovery-incident"],
            "stage2_recovery_result": chain_identities["recovery-pass"],
            "quarantine_provenance": chain_identities["quarantine-provenance"],
            "terminal_validator_independent_review": terminal_review_identity,
        },
        "read_only_dry_run": {
            "status": "DRY_RUN_PASS_NO_PUBLICATION",
            "publication_executed": False,
            "candidate": superseded_prediction,
        },
    }
    evidence_review["review_payload_sha256"] = FREEZER.sha256_document(
        evidence_review
    )
    evidence_review_path = expected_paths[
        "fourth-failure-evidence-independent-review"
    ]
    evidence_review_identity = _immutable_json(evidence_review_path, evidence_review)
    identities["fourth-failure-evidence-independent-review"] = evidence_review_identity
    evidence_review_sidecar_path = Path(str(evidence_review_path) + ".sha256")
    expected_paths["fourth-failure-evidence-independent-review-sidecar"] = (
        evidence_review_sidecar_path
    )
    evidence_review_sidecar_identity = _identity(evidence_review_sidecar_path)
    identities["fourth-failure-evidence-independent-review-sidecar"] = (
        evidence_review_sidecar_identity
    )
    values["fourth_failure_evidence_independent_review_sidecar"] = (
        evidence_review_sidecar_path
    )
    values["expected_fourth_failure_evidence_independent_review_sidecar_sha256"] = (
        evidence_review_sidecar_identity["sha256"]
    )
    values["expected_fourth_failure_evidence_independent_review_sha256"] = (
        evidence_review_identity["sha256"]
    )
    fixed_hashes = dict(FREEZER.FIXED_HASHES)
    fixed_hashes["fourth-failure-evidence-independent-review"] = str(
        evidence_review_identity["sha256"]
    )
    fixed_hashes["fourth-failure-evidence-independent-review-sidecar"] = str(
        evidence_review_sidecar_identity["sha256"]
    )
    monkeypatch.setattr(FREEZER, "FIXED_HASHES", fixed_hashes)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_PATH", Path(str(evidence_identity["path"])),
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SHA256", evidence_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SIZE_BYTES", evidence_identity["size_bytes"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_PAYLOAD_SHA256",
        evidence[FREEZER.EVIDENCE_SELF_HASH_FIELD],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_STAGE2_REVIEW_PAYLOAD_SHA256",
        recovery_review["review_payload_sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_TERMINAL_REVIEW_PAYLOAD_SHA256",
        terminal_review["review_payload_sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "_expected_terminal_review_contract",
        lambda _identities: terminal_review["terminal_live_contract"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_REVIEW_PAYLOAD_SHA256",
        evidence_review["review_payload_sha256"], raising=False,
    )
    monkeypatch.setattr(
        FREEZER, "SUPERSEDED_LITERAL_BACKSLASH_N_PREDICTION",
        superseded_prediction,
    )
    evidence_live = _live_identity(
        Path(str(evidence_identity["path"])), evidence_identity,
    )
    evidence_sidecar_live = _live_identity(
        Path(str(evidence_identity["path"]) + ".sha256"),
        evidence_sidecar_identity,
    )
    prepublish_live = _live_identity(evidence_review_path, evidence_review_identity)
    prepublish_sidecar_live = _live_identity(
        evidence_review_sidecar_path, evidence_review_sidecar_identity,
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_LIVE_IDENTITY", evidence_live,
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SIDECAR_LIVE_IDENTITY",
        evidence_sidecar_live,
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PREPUBLISH_EVIDENCE_REVIEW_LIVE_IDENTITY",
        prepublish_live,
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_PREPUBLISH_EVIDENCE_REVIEW_SIDECAR_LIVE_IDENTITY",
        prepublish_sidecar_live,
    )
    postpublish_review: dict[str, object] = {
        "schema_version": (
            "blind-phase-confirmatory-fourth-freeze-failure-evidence-"
            "postpublish-corrective-independent-review-v1"
        ),
        "status": "GO",
        "attempt_id": FREEZER.ATTEMPT_ID,
        "findings": {"P0": 0, "P1": 0, "P2": 0},
        "scope": {
            "read_only_postpublication_review": True,
            "publication_or_lifecycle_executed": False,
            "iq_or_outcome_opened": False,
        },
        "reviewed_bindings": {
            "prepublish_review": prepublish_live,
            "prepublish_review_sidecar": prepublish_sidecar_live,
            "published_evidence": evidence_live,
            "published_evidence_sidecar": evidence_sidecar_live,
            "review_generator_repo": _live_identity(
                Path(str(identities[
                    "fourth-failure-evidence-postpublish-review-generator"
                ]["path"])),
                identities[
                    "fourth-failure-evidence-postpublish-review-generator"
                ],
            ),
            "review_generator_test_repo": _live_identity(
                Path(str(identities[
                    "fourth-failure-evidence-postpublish-review-generator-test"
                ]["path"])),
                identities[
                    "fourth-failure-evidence-postpublish-review-generator-test"
                ],
            ),
        },
        "corrective_finding": {
            "classification": (
                "STAGING_FILE_IDENTITY_PREDICTION_USED_LITERAL_BACKSLASH_N"
            ),
            "semantic_document_changed": False,
            "publication_invariants_violated": False,
            "prepublish_review_is_terminal_authority": False,
            "superseded_prediction": superseded_prediction,
            "actual_published_evidence": evidence_live,
            "proof": {
                "actual_has_one_final_lf": True,
                "operation": "actual_bytes_without_final_LF + bytes([92,110])",
                "result_sha256": superseded_prediction["sha256"],
                "result_size_bytes": superseded_prediction["size_bytes"],
            },
        },
        "verification": {
            "prepublish_review_file_sidecar_selfhash_and_false_prediction_bound": True,
            "reports_parent_path_and_held_directory_fd_identity_exact": True,
            "published_pair_read_fd_relative_nofollow_and_same_inode_revalidated": True,
            "published_main_and_sidecar_root_root_0444_nlink1": True,
            "published_sidecar_content_binds_actual_main_sha256": True,
            "published_evidence_selfhash_and_metadata_only_semantics_valid": True,
            "semantic_payload_equals_prepublish_prediction_payload": True,
            "one_byte_prediction_error_reproduced_exactly": True,
        },
        "retry3_gate": {
            "status": "GO_ONLY_AFTER_EXACT_REBINDING_OF_ACTUAL_PUBLISHED_PAIR",
            "required_evidence": evidence_live,
            "required_evidence_sidecar": evidence_sidecar_live,
            "required_semantic_payload_sha256": evidence[
                FREEZER.EVIDENCE_SELF_HASH_FIELD
            ],
            "must_reject_superseded_sha256": superseded_prediction["sha256"],
            "must_not_treat_prepublish_review_as_terminal_authority": True,
        },
        "test_evidence": {
            "corrective_review_tests_expected": 8,
            "python_compile_required": True,
            "live_pair_validation_passed": True,
        },
    }
    postpublish_review["review_payload_sha256"] = FREEZER.sha256_document(
        postpublish_review
    )
    postpublish_review_path = expected_paths[
        "fourth-failure-evidence-postpublish-independent-review"
    ]
    postpublish_review_identity = _write_json(
        postpublish_review_path, postpublish_review,
    )
    postpublish_sidecar_path = Path(str(postpublish_review_path) + ".sha256")
    expected_paths[
        "fourth-failure-evidence-postpublish-independent-review-sidecar"
    ] = postpublish_sidecar_path
    postpublish_sidecar_path.write_text(
        f"{postpublish_review_identity['sha256']}  {postpublish_review_path.name}\n",
        encoding="ascii",
    )
    postpublish_sidecar_identity = _identity(postpublish_sidecar_path)
    for role, identity, path in (
        (
            "fourth-failure-evidence-postpublish-independent-review",
            postpublish_review_identity, postpublish_review_path,
        ),
        (
            "fourth-failure-evidence-postpublish-independent-review-sidecar",
            postpublish_sidecar_identity, postpublish_sidecar_path,
        ),
    ):
        identities[role] = identity
        key = role.replace("-", "_")
        values[key] = path
        values[f"expected_{key}_sha256"] = identity["sha256"]
        fixed_hashes[role] = str(identity["sha256"])
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_POSTPUBLISH_REVIEW_PAYLOAD_SHA256",
        postpublish_review["review_payload_sha256"],
    )
    args = argparse.Namespace(**values)
    prepared = FREEZER._prepare(args)
    review: dict[str, object] = {
        "schema_version": FREEZER.REVIEW_SCHEMA,
        "status": "GO",
        "reviewed_bindings": prepared["review_bindings"],
        "checks": FREEZER.expected_review_checks(),
    }
    review["review_payload_sha256"] = FREEZER.sha256_document(review)
    review_identity = _write_json(FREEZER.REVIEW_OUTPUT, review)
    args.expected_review_sha256 = review_identity["sha256"]
    return args, evidence


def _rewrite_evidence(
    args: argparse.Namespace, tmp_path: Path, value: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value[FREEZER.EVIDENCE_SELF_HASH_FIELD] = FREEZER.sha256_document({
        key: item for key, item in value.items()
        if key != FREEZER.EVIDENCE_SELF_HASH_FIELD
    })
    identity = _immutable_json(tmp_path / "changed-evidence.json", value)
    args.fourth_failure_evidence = Path(str(identity["path"]))
    args.expected_fourth_failure_evidence_sha256 = identity["sha256"]
    sidecar_identity = _identity(Path(str(identity["path"]) + ".sha256"))
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SIDECAR_SHA256", sidecar_identity["sha256"],
    )
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_SIDECAR_SIZE_BYTES",
        sidecar_identity["size_bytes"],
    )


def test_build_preserves_science_and_replaces_all_builder_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    previous = json.loads(args.previous_amendment.read_bytes())
    document = FREEZER.build_amendment(args)
    builder = document["amended_implementation"]["runtime_guard_builder"]
    assert FREEZER._science_snapshot(document) == FREEZER._science_snapshot(previous)
    assert document["schedule"]["unit_count"] == 6168
    assert document["schedule"]["units_to_execute"] == 6166
    assert len(document["prior_decoder_exposures"]) == 2
    assert document["sensitivity_exclusion_observation_ids"] == [4491]
    assert FREEZER._active_builder_identities(document) == [builder] * 4
    assert builder["sha256"] == FREEZER.EXPECTED_RUNTIME_GUARD_BUILDER_SHA256


def test_fourth_attempt_disclosure_and_recovery_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    attempt = document["fourth_freeze_failure_and_recovery"]
    assert attempt["freeze_handoff1_failure_class"] == "ENOSPC"
    assert attempt["freeze_handoff2_failure_class"] == "MISSING_CAPACITY_CONTRACT_IN_CANDIDATE_METADATA_CONTEXT"
    assert attempt["iq_mechanically_copied_hashed_and_sealed"] is True
    assert attempt["iq_used_as_decoder_input"] is False
    assert attempt["runtime_guard_created"] is False
    assert attempt["decoder_started"] is False
    assert document["failed_v3_activation_attempts"]["attempt_count"] == 4
    assert document["failed_v3_activation_attempts"]["all_four_attempts_scientific_outcome_count"] == 0


def test_dedicated_recovery_and_post_freeze_evaluator_tools_are_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    implementation = document["amended_implementation"]
    assert implementation["fourth_failure_recovery_runner"]["sha256"] == args.expected_recovery_runner_sha256
    assert implementation["fourth_failure_recovery_runner_tests"]["sha256"] == args.expected_recovery_runner_test_sha256
    assert implementation["post_freeze_evaluator_review_generator"]["sha256"] == FREEZER.EXPECTED_EVALUATOR_REVIEW_GENERATOR_SHA256
    assert implementation["post_freeze_evaluator_review_generator_test"]["sha256"] == FREEZER.EXPECTED_EVALUATOR_REVIEW_GENERATOR_TEST_SHA256
    assert implementation["fourth_failure_terminal_quarantine_validator"] == {
        "path": str(args.terminal_validator),
        "size_bytes": args.terminal_validator.stat().st_size,
        "sha256": args.expected_terminal_validator_sha256,
    }
    assert implementation["corrected_quarantine_terminal_contract"]["sha256"] == (
        FREEZER.FIXED_HASHES["corrected-quarantine-contract"]
    )
    assert implementation["fourth_failure_evidence_independent_review"] == {
        "path": str(args.fourth_failure_evidence_independent_review),
        "size_bytes": args.fourth_failure_evidence_independent_review.stat().st_size,
        "sha256": args.expected_fourth_failure_evidence_independent_review_sha256,
    }
    assert implementation[
        "fourth_failure_evidence_postpublish_corrective_independent_review"
    ]["sha256"] == (
        args.expected_fourth_failure_evidence_postpublish_independent_review_sha256
    )
    assert document["fourth_freeze_failure_and_recovery"][
        "evidence_postpublish_corrective_independent_review"
    ]["sha256"] == (
        args.expected_fourth_failure_evidence_postpublish_independent_review_sha256
    )


def test_compiled_retry3_authorities_are_total_and_distinguish_executed_contract() -> None:
    assert set(FREEZER.FIXED_CHAIN_SHA256) == set(FREEZER.CHAIN_ROLES)
    assert set(FREEZER.FIXED_CHAIN_PAYLOAD_SHA256) == set(FREEZER.CHAIN_ROLES)
    assert set(FREEZER.FIXED_CHAIN_PATHS) == set(FREEZER.CHAIN_ROLES)
    assert set(FREEZER.FIXED_CHAIN_CONTRACTS) == set(FREEZER.CHAIN_ROLES)
    assert set(FREEZER.FIXED_IMPLEMENTATION_PATHS) == set(
        FREEZER.RECOVERY_IMPLEMENTATION_ROLES
    )
    assert set(FREEZER.FIXED_IMPLEMENTATION_SHA256) == set(
        FREEZER.RECOVERY_IMPLEMENTATION_ROLES
    )
    assert FREEZER.FIXED_IMPLEMENTATION_SHA256["quarantine-runner"] != (
        FREEZER.FIXED_HASHES["corrected-quarantine-contract"]
    )
    assert FREEZER.EXPECTED_EVIDENCE_PATH.name == (
        "blind-phase-confirmatory-fourth-freeze-failure-evidence-v1.json"
    )


def test_historical_control_chain_projection_preserves_bytes_not_current_locator() -> None:
    current = {
        "path": str(FREEZER.FIXED_CHAIN_PATHS["rollback-intent"]),
        "size_bytes": 123, "sha256": FREEZER.FIXED_CHAIN_SHA256["rollback-intent"],
    }
    projected = FREEZER._chain_evidence_identity("rollback-intent", current)
    assert projected["path"] == str(FREEZER.HISTORICAL_CHAIN_PATHS["rollback-intent"])
    assert projected["sha256"] == current["sha256"]
    assert projected["size_bytes"] == current["size_bytes"]


@pytest.mark.parametrize(
    ("section", "field", "replacement", "message"),
    [
        ("scientific_exposure", "iq_used_as_decoder_input", True, "scientific exposure"),
        ("lifecycle", "freeze_guard_succeeded", True, "lifecycle/remediation"),
        ("readonly_activation", "input_count", 29, "lifecycle/remediation"),
        ("temporary_storage_remediation", "path_count", 3, "lifecycle/remediation"),
        ("pre_recovery_live_state", "outcome_artifact_count", 1, "lifecycle/remediation"),
    ],
)
def test_evidence_semantic_contradictions_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    section: str, field: str, replacement: object, message: str,
) -> None:
    args, evidence = _base_args(tmp_path, monkeypatch)
    changed = copy.deepcopy(evidence)
    changed[section][field] = replacement
    _rewrite_evidence(args, tmp_path, changed, monkeypatch)
    with pytest.raises(ValueError, match=message):
        FREEZER.build_amendment(args)


def test_swapped_or_unrelated_recovery_crosslink_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, evidence = _base_args(tmp_path, monkeypatch)
    changed = copy.deepcopy(evidence)
    changed["recovery"]["stage2_abort_and_cleanup"]["recovery_pass"] = (
        changed["recovery"]["stage1_rollback_recovery"]["incident"]
    )
    _rewrite_evidence(args, tmp_path, changed, monkeypatch)
    with pytest.raises(ValueError, match="recovery crosslink"):
        FREEZER.build_amendment(args)


def test_semantically_valid_alternate_evidence_refuses_compiled_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, evidence = _base_args(tmp_path, monkeypatch)
    changed = copy.deepcopy(evidence)
    changed["created_at_utc"] = "2026-09-06T07:00:01Z"
    _rewrite_evidence(args, tmp_path, changed, monkeypatch)
    with pytest.raises(ValueError, match="compiled exact publication"):
        FREEZER.build_amendment(args)


def test_terminal_review_live_contract_drift_refuses_after_exact_file_rebinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    changed = json.loads(args.terminal_validator_independent_review.read_bytes())
    changed["terminal_live_contract"]["provenance_committed"] = False
    changed["review_payload_sha256"] = FREEZER.sha256_document({
        key: value for key, value in changed.items()
        if key != "review_payload_sha256"
    })
    changed_identity = _immutable_json(tmp_path / "terminal-review-drift.json", changed)
    args.terminal_validator_independent_review = Path(str(changed_identity["path"]))
    args.expected_terminal_validator_independent_review_sha256 = changed_identity["sha256"]
    paths = dict(FREEZER.FIXED_IMPLEMENTATION_PATHS)
    hashes = dict(FREEZER.FIXED_IMPLEMENTATION_SHA256)
    paths["terminal-validator-independent-review"] = Path(str(changed_identity["path"]))
    hashes["terminal-validator-independent-review"] = str(changed_identity["sha256"])
    monkeypatch.setattr(FREEZER, "FIXED_IMPLEMENTATION_PATHS", paths)
    monkeypatch.setattr(FREEZER, "FIXED_IMPLEMENTATION_SHA256", hashes)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_TERMINAL_REVIEW_PAYLOAD_SHA256",
        changed["review_payload_sha256"],
    )
    with pytest.raises(ValueError, match="terminal quarantine validation review"):
        FREEZER.build_amendment(args)


def test_postpublish_corrective_review_semantic_drift_refuses_after_rebinding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    role = "fourth-failure-evidence-postpublish-independent-review"
    sidecar_role = role + "-sidecar"
    key = role.replace("-", "_")
    sidecar_key = sidecar_role.replace("-", "_")
    changed = json.loads(Path(getattr(args, key)).read_bytes())
    changed["verification"][
        "published_sidecar_content_binds_actual_main_sha256"
    ] = False
    changed["review_payload_sha256"] = FREEZER.sha256_document({
        name: value for name, value in changed.items()
        if name != "review_payload_sha256"
    })
    changed_path = tmp_path / "bad-postpublish-review.json"
    changed_identity = _write_json(changed_path, changed)
    sidecar_path = Path(str(changed_path) + ".sha256")
    sidecar_path.write_text(
        f"{changed_identity['sha256']}  {changed_path.name}\n", encoding="ascii",
    )
    sidecar_identity = _identity(sidecar_path)
    paths = dict(FREEZER.EXPECTED_FIXED_PATHS)
    hashes = dict(FREEZER.FIXED_HASHES)
    paths[role] = changed_path
    paths[sidecar_role] = sidecar_path
    hashes[role] = str(changed_identity["sha256"])
    hashes[sidecar_role] = str(sidecar_identity["sha256"])
    monkeypatch.setattr(FREEZER, "EXPECTED_FIXED_PATHS", paths)
    monkeypatch.setattr(FREEZER, "FIXED_HASHES", hashes)
    monkeypatch.setattr(
        FREEZER, "EXPECTED_EVIDENCE_POSTPUBLISH_REVIEW_PAYLOAD_SHA256",
        changed["review_payload_sha256"],
    )
    setattr(args, key, changed_path)
    setattr(args, f"expected_{key}_sha256", changed_identity["sha256"])
    setattr(args, sidecar_key, sidecar_path)
    setattr(args, f"expected_{sidecar_key}_sha256", sidecar_identity["sha256"])
    with pytest.raises(ValueError, match="postpublish corrective evidence review"):
        FREEZER.build_amendment(args)


def test_compiled_published_evidence_sidecar_identity_is_mandatory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    monkeypatch.setattr(FREEZER, "EXPECTED_EVIDENCE_SIDECAR_SHA256", "f" * 64)
    with pytest.raises(ValueError, match="sidecar differs from exact publication"):
        FREEZER.build_amendment(args)


def test_compiled_implementation_path_drift_refuses_before_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    alternate = tmp_path / "terminal-validator-copy.py"
    alternate.write_bytes(args.terminal_validator.read_bytes())
    args.terminal_validator = alternate
    args.expected_terminal_validator_sha256 = hashlib.sha256(
        alternate.read_bytes()
    ).hexdigest()
    with pytest.raises(ValueError, match="compiled exact implementation"):
        FREEZER.build_amendment(args)


def test_chain_json_self_hash_is_mandatory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    path = args.recovery_pass
    value = json.loads(path.read_bytes())
    value["payload_sha256"] = "a" * 64
    changed = _write_json(tmp_path / "bad-recovery-pass.json", value)
    args.recovery_pass = Path(str(changed["path"]))
    args.expected_recovery_pass_sha256 = changed["sha256"]
    with pytest.raises(ValueError, match="self-hash mismatch"):
        FREEZER.build_amendment(args)


def test_non_clean_recovery_review_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    value = json.loads(args.recovery_independent_review.read_bytes())
    value["findings"]["P2"] = 1
    value["review_payload_sha256"] = FREEZER.sha256_document({
        key: item for key, item in value.items() if key != "review_payload_sha256"
    })
    changed = _write_json(tmp_path / "bad-recovery-review.json", value)
    args.recovery_independent_review = Path(str(changed["path"]))
    args.expected_recovery_independent_review_sha256 = changed["sha256"]
    with pytest.raises(ValueError, match="compiled exact implementation"):
        FREEZER.build_amendment(args)


def test_review_binding_mismatch_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    review = json.loads(args.review.read_bytes())
    review["checks"]["runtime_guard_validation_is_not_weakened"] = False
    review["review_payload_sha256"] = FREEZER.sha256_document({
        key: item for key, item in review.items() if key != "review_payload_sha256"
    })
    changed = _write_json(tmp_path / "bad-review.json", review)
    monkeypatch.setattr(FREEZER, "REVIEW_OUTPUT", Path(str(changed["path"])))
    args.review = Path(str(changed["path"]))
    args.expected_review_sha256 = changed["sha256"]
    with pytest.raises(ValueError, match="exact clean GO"):
        FREEZER.build_amendment(args)


def test_timestamp_must_follow_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    args.created_at = "2026-09-06T06:59:59Z"
    with pytest.raises(ValueError, match="does not follow"):
        FREEZER.build_amendment(args)


def test_builder_test_replaces_prior_identity_in_test_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    path = str(FREEZER.EXPECTED_FIXED_PATHS["runtime-guard-builder-test"])
    matching = [
        item for item in document["amended_implementation"]["tests"]
        if isinstance(item, dict) and item.get("path") == path
    ]
    assert matching == [document["amended_implementation"]["runtime_guard_builder_test"]]


def test_active_document_does_not_reembed_historical_builder_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    builder_path = str(FREEZER.EXPECTED_FIXED_PATHS["runtime-guard-builder"])
    found: list[dict[str, object]] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            if set(value) == {"path", "size_bytes", "sha256"}:
                if value.get("path") == builder_path:
                    found.append(value)
                return
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(document)
    assert found
    assert {item["sha256"] for item in found} == {
        FREEZER.EXPECTED_RUNTIME_GUARD_BUILDER_SHA256
    }


def test_review_template_matches_exact_contract() -> None:
    template = json.loads(TEMPLATE.read_bytes())
    assert template["schema_version"] == FREEZER.REVIEW_SCHEMA
    assert template["status"] == "REVIEW_REQUIRED"
    assert template["reviewed_bindings"] is None
    assert set(template["checks"]) == set(FREEZER.expected_review_checks())
    assert all(value is None for value in template["checks"].values())


def test_cli_has_no_iq_or_lifecycle_execution_arguments() -> None:
    destinations = {action.dest for action in FREEZER.parser()._actions}
    assert not any("iq" in value for value in destinations)
    assert not any(value in destinations for value in (
        "seal", "mount", "decoder", "launcher", "evaluator", "activate", "rollback",
    ))


def test_source_is_metadata_only_and_base_is_exact_byte_loaded() -> None:
    source = SOURCE.read_text()
    imports = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "subprocess" not in imports
    assert "importlib" not in imports
    assert "BASE_FREEZER_SHA256" in source
    assert "compile(payload" in source
    assert "--iq" not in source


@pytest.mark.skipif(os.geteuid() != 0, reason="O_TMPFILE publication requires root")
def test_no_clobber_publication_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    first = FREEZER.publish_no_clobber(args.output, document)
    second = FREEZER.publish_no_clobber(args.output, document)
    assert first == second
    assert args.output.stat().st_nlink == 1
    assert (args.output.stat().st_mode & 0o777) == 0o444


@pytest.mark.skipif(os.geteuid() != 0, reason="O_TMPFILE publication requires root")
def test_no_clobber_rejects_different_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    args, _evidence = _base_args(tmp_path, monkeypatch)
    document = FREEZER.build_amendment(args)
    FREEZER.publish_no_clobber(args.output, document)
    changed = copy.deepcopy(document)
    changed["created_at"] = "2026-09-06T07:02:00Z"
    with pytest.raises(ValueError, match="published artifact"):
        FREEZER.publish_no_clobber(args.output, changed)
