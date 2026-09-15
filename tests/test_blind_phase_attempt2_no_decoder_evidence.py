from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "work/blind-phase-confirmatory-v2/build_attempt2_no_decoder_evidence.py"
SPEC = importlib.util.spec_from_file_location("build_attempt2_no_decoder_evidence", SOURCE)
assert SPEC and SPEC.loader
EVIDENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EVIDENCE
SPEC.loader.exec_module(EVIDENCE)


def _identity(path: str, sha256: str, *, mode: str = "0444", uid: int = 0) -> dict[str, object]:
    return {
        "path": path, "size_bytes": 1, "sha256": sha256,
        "st_dev": 1, "st_ino": int(sha256[:8], 16), "uid": uid, "gid": uid,
        "mode": mode, "nlink": 1,
    }


def _sidecar(identity: dict[str, object]) -> dict[str, object]:
    return _identity(str(identity["path"]) + ".sha256", "f" * 64, uid=int(identity["uid"]))


def _synthetic_chain(monkeypatch):
    previous_id = _identity("/reports/previous.json", EVIDENCE.PREVIOUS_EVIDENCE_SHA256, uid=1000)
    incident_id = _identity("/control/incident.json", EVIDENCE.INCIDENT_SHA256)
    failure_id = _identity("/control/failure.json", EVIDENCE.FAILURE_SHA256)
    remediation_intent_id = _identity("/control/remediation-intent.json", EVIDENCE.REMEDIATION_INTENT_SHA256)
    remediation_pass_id = _identity("/control/remediation-pass.json", EVIDENCE.REMEDIATION_PASS_SHA256)
    recovery_id = _identity("/control/recovery.json", EVIDENCE.RECOVERY_PASS_SHA256)
    executed = _identity("/control/executed.py", EVIDENCE.EXECUTED_RUNNER_SHA256, mode="0555")
    corrected = _identity("/project/corrected.py", EVIDENCE.CORRECTED_RUNNER_SHA256, mode="0664", uid=1000)
    corrected_test = _identity("/project/test_corrected.py", EVIDENCE.CORRECTED_TEST_SHA256, mode="0664", uid=1000)

    pycache_file = _identity("/var/lib/quarantine/code.pyc", "3" * 64, mode="0644")
    pycache_file.update(st_dev=1, st_ino=33)
    pycache_root = {"path": "/var/lib/quarantine", "st_dev": 1, "st_ino": 22, "uid": 0, "gid": 0, "mode": "0755", "nlink": 2}
    contamination = {
        "directory": {key: pycache_root[key] for key in ("st_dev", "st_ino", "uid", "gid", "mode", "nlink")},
        "entry_count": 1,
        "file": {"name": "code.pyc", **{key: pycache_file[key] for key in ("size_bytes", "sha256", "st_dev", "st_ino", "uid", "gid", "mode", "nlink")}},
    }
    pycache = {"root": pycache_root, "files": {"code.pyc": pycache_file}, "directories": {}, "entry_count": 1, "tree_semantic_sha256": EVIDENCE.PYCACHE_TREE_SHA256}

    lifecycle_docs = {}
    control_files = {}
    bound = {}
    lifecycle_specs = {
        "intent": ("persistent-mount-namespace-intent-v3.json", "intent_before_unshare"),
        "registration": ("persistent-mount-namespace-registration-v3.json", "keeper_registered_root_recursively_private"),
        "active": ("persistent-mount-namespace-active-v3.json", "active_root_recursively_private"),
    }
    for index, (label, (name, status)) in enumerate(lifecycle_specs.items(), start=1):
        identity = _identity(f"/var/lib/control/{name}", str(index) * 64)
        sidecar = _sidecar(identity)
        document = {
            "schema_version": "blind-phase-confirmatory-persistent-mount-namespace-v3",
            "status": status, "attempt_id": EVIDENCE.ATTEMPT_ID,
        }
        if label == "intent":
            document.update({
                "campaign_metadata": {}, "control_parent": {},
                "external_inventory_before": {"inventory_sha256": EVIDENCE.EXTERNAL_INVENTORY_SHA256},
                "first_mount_mutation_required": True, "project_root": {},
                "runtime_guard_builder_control_copy": {},
                "runtime_guard_builder_source": {},
            })
        elif label == "registration":
            document.update({
                "intent_payload_sha256": lifecycle_docs["intent"]["namespace_contract_payload_sha256"],
                "keeper": {}, "project_mount_count_before_activation": 0,
                "root_private_attestation": {},
                "runtime_guard_builder_control_copy": {},
            })
        else:
            document.update({
                "campaign_metadata": {},
                "external_inventory_before": {"inventory_sha256": EVIDENCE.EXTERNAL_INVENTORY_SHA256},
                "external_inventory_after": {"inventory_sha256": EVIDENCE.EXTERNAL_INVENTORY_SHA256},
                "external_namespace_mount_delta": 0,
                "intent": EVIDENCE._simple(control_files["persistent-mount-namespace-intent-v3.json"]),
                "registration": EVIDENCE._simple(control_files["persistent-mount-namespace-registration-v3.json"]),
                "keeper": {}, "root_private_attestation": {},
            })
        document["namespace_contract_payload_sha256"] = EVIDENCE._sha256_document(document)
        lifecycle_docs[label] = document
        control_files[name] = identity
        control_files[name + ".sha256"] = sidecar
        bound[str(identity["path"])] = (document, identity, sidecar)
    control = {
        "root": {"path": "/var/lib/control", "st_dev": 1, "st_ino": 2, "uid": 0, "gid": 0, "mode": "0755", "nlink": 3},
        "files": control_files, "directories": {"results-v3": {"path": "/var/lib/control/results-v3", "entry_count": 0}},
        "entry_count": len(control_files) + 1,
        "tree_semantic_sha256": EVIDENCE.CONTROL_TREE_SHA256,
    }

    previous = {
        "evidence_payload_sha256": "e" * 64,
        "checks": {
            "active_decoder_process_count": 0,
            "new_decoder_output_count_since_v2": 0,
            "iq_opened_by_generator": False,
            "candidate_or_component_runtime_opened_by_generator": False,
        }
    }
    exposure = {
        "iq_contents_opened_by_recovery": False,
        "decoder_started_by_recovery": False,
        "seal_started_by_recovery": False,
        "launcher_or_evaluator_started_by_recovery": False,
    }
    incident = {
        "attempt_id": EVIDENCE.ATTEMPT_ID,
        "project_or_authority_activation_intent_absent": True,
        "project_or_authority_activation_mounts_absent": True,
        "start_created_results_tmpfs_present": True,
        "scientific_exposure": exposure,
    }
    failure = {"incident": EVIDENCE._simple(incident_id), "pidfd_signal_committed": True, "scientific_exposure": exposure}
    external_inventory = {"inventory_sha256": EVIDENCE.EXTERNAL_INVENTORY_SHA256}
    remediation_exposure = {
        "iq_contents_opened_by_remediation": False,
        "decoder_started_by_remediation": False,
        "launcher_or_evaluator_started_by_remediation": False,
        "mount_or_namespace_operation_by_remediation": False,
    }
    remediation_intent = {
        "schema_version": "blind-phase-confirmatory-agent-pycache-contamination-v1",
        "status": "COMMITTED_BEFORE_EXACT_DIRECTORY_RENAME",
        "recorded_at_utc": "2026-09-06T00:00:00Z",
        "remediation_runner": EVIDENCE._simple(corrected),
        "bound_recovery_history": {
            "recovery_incident": EVIDENCE._simple(incident_id),
            "recovery_failure": EVIDENCE._simple(failure_id),
            "recovery_runner": EVIDENCE._simple(executed),
        },
        "contamination": contamination,
        "source": "/var/lib/source/__pycache__",
        "quarantine": pycache_root["path"],
        "authorized_action": {
            "operation": "renameat2_RENAME_NOREPLACE",
            "same_filesystem": True,
            "recursive_delete": False,
            "mount_operation": False,
        },
        "keeper_generation_absent": True,
        "keeper_mount_namespace_absent": True,
        "external_namespace_inventory": external_inventory,
        "scientific_exposure": remediation_exposure,
        "incident_payload_sha256": "4" * 64,
    }
    remediation_pass = {
        "schema_version": "blind-phase-confirmatory-agent-pycache-remediation-v1",
        "status": "PASS",
        "incident": EVIDENCE._simple(remediation_intent_id),
        "remediation_runner": EVIDENCE._simple(corrected),
        "source_absent": True,
        "quarantine": contamination,
        "external_namespace_inventory": external_inventory,
        "external_namespace_inventory_before": external_inventory,
        "external_namespace_inventory_after": external_inventory,
        "external_namespace_zero_delta": True,
        "scientific_exposure": remediation_exposure,
        "remediation_payload_sha256": "5" * 64,
    }
    recovery = {
        "schema_version": "blind-phase-confirmatory-start-only-recovery-v2",
        "status": "PASS", "completed_at_utc": "2026-09-06T00:00:00Z",
        "attempt_id": EVIDENCE.ATTEMPT_ID,
        "incident": EVIDENCE._simple(incident_id),
        "failure_resume_authorization": EVIDENCE._simple(failure_id),
        "resume_transition": "existing_incident_keeper_already_absent",
        "keeper_termination": {
            "method": "absence_observed_after_committed_incident",
            "signal": None,
            "pidfd_ready": False,
            "exact_generation_absent": True,
            "namespace_holder_count_after": 0,
        },
        "namespace_teardown": {
            "mount_operations_performed_by_recovery": False,
            "kernel_lifetime_teardown_expected_mount_count": 1,
            "kernel_lifetime_teardown_mount_role": "start_created_results_tmpfs",
            "private_namespace_absent": True,
            "project_or_authority_activation_mount_count_before_recovery": 0,
        },
        "external_namespace_zero_delta": {
            "before_inventory_sha256": EVIDENCE.EXTERNAL_INVENTORY_SHA256,
            "after_inventory_sha256": EVIDENCE.EXTERNAL_INVENTORY_SHA256,
            "byte_identical": True,
        },
        "no_decoder_evidence": {
            "prior": EVIDENCE._simple(previous_id),
            "project_or_authority_activation_intent_absent": True,
            "start_created_results_tmpfs_was_present": True,
            "active_decoder_or_role_processes_before": 0,
            "active_decoder_or_role_processes_after": 0,
            "pre_private_results_entry_count": 0,
            "iq_contents_opened": False,
        },
        "scientific_exposure": exposure,
        "recovery_payload_sha256": "6" * 64,
    }
    bound.update({
        str(previous_id["path"]): (previous, previous_id, _sidecar(previous_id)),
        str(incident_id["path"]): (incident, incident_id, _sidecar(incident_id)),
        str(failure_id["path"]): (failure, failure_id, _sidecar(failure_id)),
        str(remediation_intent_id["path"]): (remediation_intent, remediation_intent_id, _sidecar(remediation_intent_id)),
        str(remediation_pass_id["path"]): (remediation_pass, remediation_pass_id, _sidecar(remediation_pass_id)),
        str(recovery_id["path"]): (recovery, recovery_id, _sidecar(recovery_id)),
    })
    monkeypatch.setattr(EVIDENCE, "_bound_json", lambda identity, *args: bound[str(identity["path"])])
    raw = {item["sha256"]: item for item in (executed, corrected, corrected_test)}
    monkeypatch.setattr(EVIDENCE, "_raw_identity", lambda identity, digest: raw[digest])
    monkeypatch.setattr(EVIDENCE, "_validate_tree", lambda inventory, digest: pycache if digest == EVIDENCE.PYCACHE_TREE_SHA256 else control)

    dossier = {
        "schema_version": EVIDENCE.DOSSIER_SCHEMA,
        "status": EVIDENCE.DOSSIER_STATUS,
        "created_at_utc": "2026-09-06T00:00:00Z",
        "generator": _identity("/project/generator.py", "a" * 64),
        "attempt_id": EVIDENCE.ATTEMPT_ID,
        "frozen_protocol": {},
        "lifecycle_attempt": {
            "artifacts": control_files, "documents": lifecycle_docs,
            "external_inventory_sha256": EVIDENCE.EXTERNAL_INVENTORY_SHA256,
            "external_inventory_unchanged": True,
        },
        "failed_activation": {
            "incident": incident_id, "failure": failure_id,
            "executed_historical_runner": executed,
            "post_incident_corrected_runner_source_not_executed": corrected,
            "post_incident_corrected_runner_tests": corrected_test,
            "activation_intent_absent_from_quarantined_control_tree": True,
            "project_or_authority_activation_mounts_absent": True,
            "start_created_results_tmpfs_only": True,
        },
        "audit_contamination_remediation": {
            "intent": remediation_intent_id, "pass": remediation_pass_id,
            "quarantined_pycache": {"tree_semantic_sha256": EVIDENCE.PYCACHE_TREE_SHA256},
        },
        "recovery": {"pass": recovery_id},
        "quarantined_canonical_control_tree": {"tree_semantic_sha256": EVIDENCE.CONTROL_TREE_SHA256},
        "current_implementation": {},
        "no_decoder_evidence": {
            "artifact": previous_id, "evidence_payload_sha256": "e" * 64,
            "immutable_recovery_receipt": recovery_id,
            "supporting_live_process_fixed_point": {},
            "role_started": False, "decoder_started": False,
            "outcome_generated": False, "quarantined_results_entry_count": 0,
        },
        "iq_access_disclosure": {
            "iq_source_records_were_inventory_metadata_only": True,
            "failed_activation_opened_copied_or_hashed_iq_bytes": False,
            "remediation_opened_copied_or_hashed_iq_bytes": False,
            "recovery_opened_copied_or_hashed_iq_bytes": False,
            "dossier_generator_accepts_iq_paths": False,
            "dossier_generator_opened_copied_or_hashed_iq_bytes": False,
        },
        "scope": {
            "publication_provenance_only": True,
            "runtime_guard_modified": False,
            "remediation_modified": False,
            "retry_freezer_modified": False,
            "scientific_protocol_or_results_changed": False,
        },
        EVIDENCE.DOSSIER_HASH_FIELD: EVIDENCE.DOSSIER_PAYLOAD_SHA256,
    }
    return dossier


def test_validate_chain_rebinds_every_semantic_stage(monkeypatch) -> None:
    dossier = _synthetic_chain(monkeypatch)
    dossier_id = _identity(str(EVIDENCE.DOSSIER_PATH), EVIDENCE.DOSSIER_SHA256, uid=1000)
    result = EVIDENCE._validate_chain(dossier, dossier_id)
    assert result["previous_evidence"]["sha256"] == EVIDENCE.PREVIOUS_EVIDENCE_SHA256
    assert result["incident"]["sha256"] == EVIDENCE.INCIDENT_SHA256
    assert result["quarantined_pycache"]["tree_semantic_sha256"] == EVIDENCE.PYCACHE_TREE_SHA256
    assert result["quarantined_control_tree"]["tree_semantic_sha256"] == EVIDENCE.CONTROL_TREE_SHA256
    assert result["executed_historical_runner"]["sha256"] != result["corrected_runner_source_not_executed"]["sha256"]


def test_validate_chain_rejects_outcome_extra_and_wrong_history(monkeypatch) -> None:
    dossier = _synthetic_chain(monkeypatch)
    dossier_id = _identity(str(EVIDENCE.DOSSIER_PATH), EVIDENCE.DOSSIER_SHA256, uid=1000)
    dossier["no_decoder_evidence"]["outcomes"] = ["forged"]
    with pytest.raises(EVIDENCE.EvidenceError, match="no-decoder claims"):
        EVIDENCE._validate_chain(dossier, dossier_id)
    dossier = _synthetic_chain(monkeypatch)
    # The exact immutable intent is supplied by the bound-json mock; replace its
    # semantic history through the shared returned document.
    original = EVIDENCE._bound_json

    def wrong_history(identity, *args):
        document, live, sidecar = original(identity, *args)
        if live["sha256"] == EVIDENCE.REMEDIATION_INTENT_SHA256:
            document = dict(document, bound_recovery_history={})
        return document, live, sidecar

    monkeypatch.setattr(EVIDENCE, "_bound_json", wrong_history)
    with pytest.raises(EVIDENCE.EvidenceError, match="remediation semantic"):
        EVIDENCE._validate_chain(dossier, dossier_id)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda document: dict(document, scientific_exposure={}), "recovery semantic"),
        (lambda document: dict(document, no_decoder_evidence={}), "recovery semantic"),
        (
            lambda document: dict(
                document,
                no_decoder_evidence={**document["no_decoder_evidence"], "outcomes": []},
            ),
            "recovery semantic",
        ),
        (
            lambda document: dict(
                document,
                namespace_teardown={**document["namespace_teardown"], "decoder_started": False},
            ),
            "recovery semantic",
        ),
    ),
)
def test_recovery_rejects_empty_or_extended_scientific_claims(monkeypatch, mutation, message) -> None:
    dossier = _synthetic_chain(monkeypatch)
    dossier_id = _identity(str(EVIDENCE.DOSSIER_PATH), EVIDENCE.DOSSIER_SHA256, uid=1000)
    original = EVIDENCE._bound_json

    def forged(identity, *args):
        document, live, sidecar = original(identity, *args)
        if live["sha256"] == EVIDENCE.RECOVERY_PASS_SHA256:
            document = mutation(document)
        return document, live, sidecar

    monkeypatch.setattr(EVIDENCE, "_bound_json", forged)
    with pytest.raises(EVIDENCE.EvidenceError, match=message):
        EVIDENCE._validate_chain(dossier, dossier_id)


@pytest.mark.parametrize("target", ("remediation", "lifecycle-attempt", "lifecycle-extra"))
def test_semantic_chain_rejects_remediation_and_lifecycle_forgery(monkeypatch, target: str) -> None:
    dossier = _synthetic_chain(monkeypatch)
    dossier_id = _identity(str(EVIDENCE.DOSSIER_PATH), EVIDENCE.DOSSIER_SHA256, uid=1000)
    original = EVIDENCE._bound_json

    def forged(identity, *args):
        document, live, sidecar = original(identity, *args)
        if target == "remediation" and live["sha256"] == EVIDENCE.REMEDIATION_INTENT_SHA256:
            document = dict(document, outcomes=[])
        elif target == "lifecycle-attempt" and document.get("status") == "active_root_recursively_private":
            document = dict(document, attempt_id="0" * 64)
        elif target == "lifecycle-extra" and document.get("status") == "intent_before_unshare":
            document = dict(document, role_started=False)
        return document, live, sidecar

    monkeypatch.setattr(EVIDENCE, "_bound_json", forged)
    with pytest.raises(EVIDENCE.EvidenceError, match="remediation semantic|lifecycle"):
        EVIDENCE._validate_chain(dossier, dossier_id)


def test_build_evidence_is_self_hashed_and_has_zero_scientific_exposure(monkeypatch) -> None:
    dossier = {EVIDENCE.DOSSIER_HASH_FIELD: EVIDENCE.DOSSIER_PAYLOAD_SHA256}
    dossier_id = _identity(str(EVIDENCE.DOSSIER_PATH), EVIDENCE.DOSSIER_SHA256, uid=1000)
    generator = _identity(str(SOURCE), "0" * 64, mode="0664", uid=1000)
    monkeypatch.setattr(EVIDENCE, "_read_regular", lambda path, maximum=EVIDENCE.MAX_JSON_BYTES: (b"x", generator))
    monkeypatch.setattr(EVIDENCE, "_load_dossier", lambda spec: (dossier, dossier_id, _sidecar(dossier_id)))
    monkeypatch.setattr(EVIDENCE, "_validate_chain", lambda document, identity: {"dossier": identity})
    monkeypatch.setattr(EVIDENCE, "_process_gate", lambda: {"active_decoder_or_role_process_count": 0, "matches": []})
    spec = EVIDENCE.DossierSpec(EVIDENCE.DOSSIER_PATH, EVIDENCE.DOSSIER_SHA256, EVIDENCE.DOSSIER_PAYLOAD_SHA256, EVIDENCE.DOSSIER_SCHEMA, EVIDENCE.DOSSIER_STATUS, EVIDENCE.DOSSIER_HASH_FIELD)
    document = EVIDENCE.build_evidence("0" * 64, spec, "2026-09-06T12:34:56Z")
    unhashed = dict(document)
    stored = unhashed.pop(EVIDENCE.HASH_FIELD)
    assert stored == EVIDENCE._sha256_document(unhashed)
    assert not any(document["scientific_exposure"].values())
    assert document["checks"]["quarantined_results_unit_count"] == 0
    assert document["iq_access_disclosure"]["IQ_content_opened_by_generator"] is False


def test_created_at_is_explicit_canonical_and_deterministic_across_retry(monkeypatch) -> None:
    dossier = {EVIDENCE.DOSSIER_HASH_FIELD: EVIDENCE.DOSSIER_PAYLOAD_SHA256}
    dossier_id = _identity(str(EVIDENCE.DOSSIER_PATH), EVIDENCE.DOSSIER_SHA256, uid=1000)
    generator = _identity(str(SOURCE), "0" * 64, mode="0664", uid=1000)
    monkeypatch.setattr(EVIDENCE, "_read_regular", lambda path, maximum=EVIDENCE.MAX_JSON_BYTES: (b"x", generator))
    monkeypatch.setattr(EVIDENCE, "_load_dossier", lambda spec: (dossier, dossier_id, _sidecar(dossier_id)))
    monkeypatch.setattr(EVIDENCE, "_validate_chain", lambda document, identity: {"dossier": identity})
    monkeypatch.setattr(EVIDENCE, "_process_gate", lambda: {"active_decoder_or_role_process_count": 0, "matches": []})
    spec = EVIDENCE.DossierSpec(EVIDENCE.DOSSIER_PATH, EVIDENCE.DOSSIER_SHA256, EVIDENCE.DOSSIER_PAYLOAD_SHA256, EVIDENCE.DOSSIER_SCHEMA, EVIDENCE.DOSSIER_STATUS, EVIDENCE.DOSSIER_HASH_FIELD)
    first = EVIDENCE.build_evidence("0" * 64, spec, "2026-09-06T12:34:56Z")
    second = EVIDENCE.build_evidence("0" * 64, spec, "2026-09-06T12:34:56Z")
    assert first == second
    for invalid in ("2026-09-06T12:34:56.1Z", "2026-09-06 12:34:56Z", "not-a-time"):
        with pytest.raises(EVIDENCE.EvidenceError, match="created-at"):
            EVIDENCE.build_evidence("0" * 64, spec, invalid)


def test_dossier_cli_contract_is_exact_and_not_caller_selectable() -> None:
    wrong = EVIDENCE.DossierSpec(EVIDENCE.DOSSIER_PATH, "0" * 64, EVIDENCE.DOSSIER_PAYLOAD_SHA256, EVIDENCE.DOSSIER_SCHEMA, EVIDENCE.DOSSIER_STATUS, EVIDENCE.DOSSIER_HASH_FIELD)
    with pytest.raises(EVIDENCE.EvidenceError, match="CLI binding"):
        EVIDENCE._load_dossier(wrong)


def test_process_gate_detects_role_and_fails_closed_on_permission(monkeypatch, tmp_path: Path) -> None:
    process = tmp_path / "123"
    process.mkdir()
    (process / "cmdline").write_bytes(b"python\0exec-evaluator\0")
    with pytest.raises(EVIDENCE.EvidenceError, match="process exists"):
        EVIDENCE._process_gate(tmp_path)
    original = os.open

    def denied(path, flags, *args, **kwargs):
        if Path(path) == process / "cmdline":
            raise PermissionError("denied")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(EVIDENCE.os, "open", denied)
    with pytest.raises(EVIDENCE.EvidenceError, match="fail-closed"):
        EVIDENCE._process_gate(tmp_path)


def test_otmpfile_publication_is_no_clobber_and_self_hashed(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / EVIDENCE.OUTPUT.name
    monkeypatch.setattr(EVIDENCE, "REPORTS", tmp_path)
    monkeypatch.setattr(EVIDENCE, "OUTPUT", output)
    document = {"schema_version": EVIDENCE.SCHEMA, "status": EVIDENCE.STATUS}
    document[EVIDENCE.HASH_FIELD] = EVIDENCE._sha256_document(document)
    try:
        EVIDENCE.publish(document, output)
    except OSError as error:
        if error.errno in {getattr(os, "EOPNOTSUPP", 95), 95}:
            pytest.skip("test filesystem lacks O_TMPFILE")
        raise
    assert json.loads(output.read_bytes()) == document
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.with_name(output.name + ".sha256").read_text() == f"{digest}  {output.name}\n"
    assert (output.stat().st_mode & 0o777) == 0o444
    assert (output.with_name(output.name + ".sha256").stat().st_mode & 0o777) == 0o444
    with pytest.raises(EVIDENCE.EvidenceError, match="already exists"):
        EVIDENCE.publish(document, output)


def test_sidecar_first_crash_state_resumes_without_overwrite(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / EVIDENCE.OUTPUT.name
    monkeypatch.setattr(EVIDENCE, "REPORTS", tmp_path)
    monkeypatch.setattr(EVIDENCE, "OUTPUT", output)
    document = {"schema_version": EVIDENCE.SCHEMA, "status": EVIDENCE.STATUS}
    document[EVIDENCE.HASH_FIELD] = EVIDENCE._sha256_document(document)
    original = EVIDENCE._publish_tmpfile
    calls = 0

    def fail_json(directory_fd: int, name: str, payload: bytes):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected JSON publication failure")
        return original(directory_fd, name, payload)

    monkeypatch.setattr(EVIDENCE, "_publish_tmpfile", fail_json)
    with pytest.raises(OSError, match="injected"):
        EVIDENCE.publish(document, output)
    assert not output.exists()
    assert output.with_name(output.name + ".sha256").exists()
    monkeypatch.setattr(EVIDENCE, "_publish_tmpfile", original)
    EVIDENCE.publish(document, output)
    assert output.exists()


def test_foreign_sidecar_only_state_fails_closed(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / EVIDENCE.OUTPUT.name
    output.with_name(output.name + ".sha256").write_text("foreign\n")
    monkeypatch.setattr(EVIDENCE, "REPORTS", tmp_path)
    monkeypatch.setattr(EVIDENCE, "OUTPUT", output)
    document = {"schema_version": EVIDENCE.SCHEMA, "status": EVIDENCE.STATUS}
    document[EVIDENCE.HASH_FIELD] = EVIDENCE._sha256_document(document)
    with pytest.raises(EVIDENCE.EvidenceError, match="foreign or inconsistent"):
        EVIDENCE.publish(document, output)
    assert not output.exists()


def test_cli_has_no_iq_path_and_requires_full_dossier_identity() -> None:
    options = {value for action in EVIDENCE._parser()._actions for value in action.option_strings}
    for required in (
        "--created-at-utc", "--dossier", "--expected-dossier-sha256",
        "--expected-dossier-payload-sha256", "--expected-dossier-schema",
        "--expected-dossier-status", "--dossier-self-hash-field",
    ):
        assert required in options
    assert not any("iq" in value.lower() for value in options)
    source = SOURCE.read_text(encoding="utf-8")
    assert "/iq-data" not in source and "s3://" not in source
