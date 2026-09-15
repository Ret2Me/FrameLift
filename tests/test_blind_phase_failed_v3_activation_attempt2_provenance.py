from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "work/blind-phase-confirmatory-v2/build_failed_v3_activation_attempt2_provenance.py"
SPEC = importlib.util.spec_from_file_location("build_failed_v3_activation_attempt2_provenance", SOURCE)
assert SPEC and SPEC.loader
DOSSIER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DOSSIER
SPEC.loader.exec_module(DOSSIER)


def _identity(path: str, marker: str) -> dict[str, object]:
    return {"path": path, "size_bytes": 1, "sha256": marker * 64, "st_dev": 1, "st_ino": ord(marker), "uid": 0, "gid": 0, "mode": "0444", "nlink": 1}


def _synthetic_fixed() -> dict[str, object]:
    amendment_id = _identity("/reports/amendment.json", "a")
    review_id = _identity("/reports/review.json", "b")
    evidence_id = _identity("/reports/evidence.json", "c")
    incident_id = _identity(str(DOSSIER.INCIDENT), "d")
    failure_id = _identity(str(DOSSIER.FAILURE), "e")
    runner = _identity(str(DOSSIER.RECOVERY_RUNNER), "f")
    return {
        "amendment": {"amendment_payload_sha256": "1" * 64}, "amendment_id": amendment_id,
        "review": {"review_payload_sha256": "2" * 64}, "review_id": review_id,
        "evidence": {"evidence_payload_sha256": "3" * 64}, "evidence_id": evidence_id,
        "incident": {"incident_payload_sha256": "4" * 64}, "incident_id": incident_id,
        "failure": {"recovery_payload_sha256": "5" * 64}, "failure_id": failure_id,
        "recovery_runner": runner,
        "current_builder": _identity(str(DOSSIER.CURRENT_BUILDER), "6"),
        "current_builder_test": _identity(str(DOSSIER.CURRENT_BUILDER_TEST), "7"),
        "corrected_recovery_runner_source": _identity(str(DOSSIER.CORRECTED_RECOVERY_RUNNER_SOURCE), "4"),
        "corrected_recovery_runner_test": _identity(str(DOSSIER.CORRECTED_RECOVERY_RUNNER_TEST), "5"),
    }


def _runtime() -> DOSSIER.RuntimeInputs:
    def spec(name: str, marker: str) -> DOSSIER.RuntimeJSON:
        return DOSSIER.RuntimeJSON(Path(f"/{name}.json"), marker * 64, (marker.upper()) * 64, f"schema-{name}", "PASS", f"{name}_payload_sha256")
    return DOSSIER.RuntimeInputs(
        remediation_intent=spec("intent", "8"),
        remediation_pass=spec("remediation", "9"),
        recovery_pass=DOSSIER.RuntimeJSON(
            Path("/recovery.json"), "a" * 64, "A" * 64,
            DOSSIER.RECOVERY_PASS_SCHEMA, DOSSIER.RECOVERY_PASS_STATUS,
            DOSSIER.RECOVERY_PASS_HASH_FIELD,
        ),
        pycache_quarantine=Path("/quarantine/pycache"),
        expected_pycache_tree_sha256="b" * 64,
        quarantined_control_root=Path("/quarantine/control"),
        expected_control_tree_sha256="c" * 64,
    )


def _install_build_mocks(monkeypatch):
    fixed = _synthetic_fixed()
    runtime = _runtime()
    generator = {"path": str(SOURCE), "size_bytes": 1, "sha256": "0" * 64, "st_dev": 1, "st_ino": 1, "uid": 1000, "gid": 1000, "mode": "0664", "nlink": 1}
    monkeypatch.setattr(DOSSIER, "_read_regular", lambda path, maximum=DOSSIER.MAX_FILE_BYTES: (b"x", generator))
    monkeypatch.setattr(DOSSIER, "_fixed_inputs", lambda: fixed)
    inventory = {"inventory_sha256": DOSSIER.EXTERNAL_INVENTORY_SHA256}
    pycache_root = {"path": str(runtime.pycache_quarantine), "st_dev": 1, "st_ino": 2, "uid": 0, "gid": 0, "mode": "0755", "nlink": 2}
    pycache_file = {"path": str(runtime.pycache_quarantine / DOSSIER.PY_CACHE_FILE), "size_bytes": 3, "sha256": "d" * 64, "st_dev": 1, "st_ino": 3, "uid": 0, "gid": 0, "mode": "0644", "nlink": 1}
    contamination = {
        "directory": {key: pycache_root[key] for key in ("st_dev", "st_ino", "uid", "gid", "mode", "nlink")},
        "file": {"name": DOSSIER.PY_CACHE_FILE, **{key: pycache_file[key] for key in ("size_bytes", "sha256", "st_dev", "st_ino", "uid", "gid", "mode", "nlink")}},
        "entry_count": 1,
    }
    intent = {
        "schema_version": runtime.remediation_intent.expected_schema,
        "status": runtime.remediation_intent.expected_status,
        "recorded_at_utc": "2026-09-06T00:00:00Z",
        "remediation_runner": _identity("/var/lib/remediation-runner.py", "f"),
        "bound_recovery_history": {
            "recovery_incident": DOSSIER._simple(fixed["incident_id"]),
            "recovery_failure": DOSSIER._simple(fixed["failure_id"]),
            "recovery_runner": DOSSIER._simple(fixed["recovery_runner"]),
        },
        "contamination": contamination,
        "source": str(DOSSIER.RECOVERY_CONTROL / "__pycache__"),
        "quarantine": str(runtime.pycache_quarantine),
        "authorized_action": {
            "operation": "renameat2_RENAME_NOREPLACE",
            "same_filesystem": True,
            "recursive_delete": False,
            "mount_operation": False,
        },
        "keeper_generation_absent": True,
        "keeper_mount_namespace_absent": True,
        "external_namespace_inventory": inventory,
        "scientific_exposure": dict(DOSSIER.REMEDIATION_SCIENTIFIC_EXPOSURE),
        runtime.remediation_intent.self_hash_field: runtime.remediation_intent.expected_payload_sha256,
    }
    intent_id = _identity(str(runtime.remediation_intent.path), "8")
    remediation = {
        "schema_version": runtime.remediation_pass.expected_schema,
        "status": runtime.remediation_pass.expected_status,
        "incident": DOSSIER._simple(intent_id),
        "remediation_runner": intent["remediation_runner"],
        "source_absent": True,
        "quarantine": contamination,
        "external_namespace_inventory": inventory,
        "external_namespace_inventory_before": inventory,
        "external_namespace_inventory_after": inventory,
        "external_namespace_zero_delta": True,
        "scientific_exposure": dict(DOSSIER.REMEDIATION_SCIENTIFIC_EXPOSURE),
        runtime.remediation_pass.self_hash_field: runtime.remediation_pass.expected_payload_sha256,
    }
    remediation_id = _identity(str(runtime.remediation_pass.path), "9")
    recovery = {
        "schema_version": runtime.recovery_pass.expected_schema,
        "status": runtime.recovery_pass.expected_status,
        "attempt_id": DOSSIER.ATTEMPT_ID,
        "completed_at_utc": "2026-09-06T00:00:00Z",
        "incident": DOSSIER._simple(fixed["incident_id"]),
        "failure_resume_authorization": DOSSIER._simple(fixed["failure_id"]),
        "resume_transition": "existing_incident_keeper_already_absent",
        "keeper_termination": {
            "method": "absence_observed_after_committed_incident",
            "signal": None, "pidfd_ready": False,
            "exact_generation_absent": True, "namespace_holder_count_after": 0,
        },
        "namespace_teardown": {
            "mount_operations_performed_by_recovery": False,
            "kernel_lifetime_teardown_expected_mount_count": 1,
            "kernel_lifetime_teardown_mount_role": "start_created_results_tmpfs",
            "project_or_authority_activation_mount_count_before_recovery": 0,
            "private_namespace_absent": True,
        },
        "external_namespace_zero_delta": {"before_inventory_sha256": DOSSIER.EXTERNAL_INVENTORY_SHA256, "after_inventory_sha256": DOSSIER.EXTERNAL_INVENTORY_SHA256, "byte_identical": True},
        "no_decoder_evidence": {
            "prior": DOSSIER._simple(fixed["evidence_id"]),
            "project_or_authority_activation_intent_absent": True,
            "start_created_results_tmpfs_was_present": True,
            "pre_private_results_entry_count": 0,
            "active_decoder_or_role_processes_before": 0,
            "active_decoder_or_role_processes_after": 0,
            "iq_contents_opened": False,
        },
        "scientific_exposure": dict(DOSSIER.RECOVERY_SCIENTIFIC_EXPOSURE),
        runtime.recovery_pass.self_hash_field: runtime.recovery_pass.expected_payload_sha256,
    }
    recovery_id = _identity(str(runtime.recovery_pass.path), "a")
    values = {
        runtime.remediation_intent.path: (intent, intent_id, _identity(str(runtime.remediation_intent.path) + ".sha256", "1")),
        runtime.remediation_pass.path: (remediation, remediation_id, _identity(str(runtime.remediation_pass.path) + ".sha256", "2")),
        runtime.recovery_pass.path: (recovery, recovery_id, _identity(str(runtime.recovery_pass.path) + ".sha256", "3")),
    }
    monkeypatch.setattr(DOSSIER, "_load_runtime_json", lambda spec: values[spec.path])
    monkeypatch.setattr(DOSSIER, "_pycache_tree", lambda path, digest: {"root": pycache_root, "files": {DOSSIER.PY_CACHE_FILE: pycache_file}, "entry_count": 1, "tree_semantic_sha256": digest})
    control_files = {
        "amendment-v3.tsq": _identity("/control/amendment-v3.tsq", "4"),
        "amendment-v3.tsr": _identity("/control/amendment-v3.tsr", "5"),
        "build_runtime_guard_v3.py": _identity("/control/build_runtime_guard_v3.py", "6"),
    }
    monkeypatch.setattr(DOSSIER, "_control_tree", lambda path, digest: ({"root": {"path": str(path)}, "tree_semantic_sha256": digest}, control_files))
    monkeypatch.setattr(DOSSIER, "_validate_lifecycle", lambda files: {"intent": {}, "registration": {}, "active": {}})
    monkeypatch.setattr(DOSSIER, "_process_gate", lambda: {"active_decoder_or_role_process_count": 0, "matches": []})
    return runtime, fixed, values


def test_build_dossier_binds_all_runtime_chains_and_metadata_only_disclosure(monkeypatch) -> None:
    runtime, fixed, _ = _install_build_mocks(monkeypatch)
    document = DOSSIER.build_dossier("0" * 64, runtime)
    unhashed = dict(document)
    stored = unhashed.pop(DOSSIER.HASH_FIELD)
    assert stored == DOSSIER._sha256_document(unhashed)
    assert document["attempt_id"] == DOSSIER.ATTEMPT_ID
    assert document["failed_activation"]["incident"] == fixed["incident_id"]
    assert document["failed_activation"]["activation_intent_absent_from_quarantined_control_tree"] is True
    assert document["recovery"]["private_namespace_absent"] is True
    assert document["lifecycle_attempt"]["external_inventory_unchanged"] is True
    assert document["no_decoder_evidence"]["outcome_generated"] is False
    assert document["iq_access_disclosure"] == {
        "iq_source_records_were_inventory_metadata_only": True,
        "failed_activation_opened_copied_or_hashed_iq_bytes": False,
        "remediation_opened_copied_or_hashed_iq_bytes": False,
        "recovery_opened_copied_or_hashed_iq_bytes": False,
        "dossier_generator_accepts_iq_paths": False,
        "dossier_generator_opened_copied_or_hashed_iq_bytes": False,
    }


def test_missing_remediation_cross_link_fails_closed(monkeypatch) -> None:
    runtime, _fixed, values = _install_build_mocks(monkeypatch)
    document, identity, sidecar = values[runtime.remediation_pass.path]
    changed = dict(document)
    changed["incident"] = _identity("/unrelated.json", "0")
    values[runtime.remediation_pass.path] = (changed, identity, sidecar)
    with pytest.raises(DOSSIER.ProvenanceError, match="semantic cross-links"):
        DOSSIER.build_dossier("0" * 64, runtime)


def test_unrelated_nested_references_do_not_satisfy_semantic_links(monkeypatch) -> None:
    runtime, fixed, values = _install_build_mocks(monkeypatch)
    document, identity, sidecar = values[runtime.remediation_intent.path]
    changed = dict(document)
    changed.pop("bound_recovery_history")
    changed["unrelated_padding"] = {
        "incident": DOSSIER._simple(fixed["incident_id"]),
        "failure": DOSSIER._simple(fixed["failure_id"]),
        "runner": DOSSIER._simple(fixed["recovery_runner"]),
    }
    values[runtime.remediation_intent.path] = (changed, identity, sidecar)
    with pytest.raises(DOSSIER.ProvenanceError, match="semantic cross-links"):
        DOSSIER.build_dossier("0" * 64, runtime)


def test_remediation_exact_top_level_rejects_contradictory_outcome_extra(monkeypatch) -> None:
    runtime, _fixed, values = _install_build_mocks(monkeypatch)
    document, identity, sidecar = values[runtime.remediation_pass.path]
    changed = dict(document, outcomes={"generated": True}, role_started=True)
    values[runtime.remediation_pass.path] = (changed, identity, sidecar)
    with pytest.raises(DOSSIER.ProvenanceError, match="semantic cross-links"):
        DOSSIER.build_dossier("0" * 64, runtime)


def test_remediation_scientific_exposure_is_required_in_intent_and_pass(monkeypatch) -> None:
    runtime, _fixed, values = _install_build_mocks(monkeypatch)
    document, identity, sidecar = values[runtime.remediation_intent.path]
    changed = dict(document)
    changed["scientific_exposure"] = dict(DOSSIER.REMEDIATION_SCIENTIFIC_EXPOSURE)
    changed["scientific_exposure"]["iq_contents_opened_by_remediation"] = True
    values[runtime.remediation_intent.path] = (changed, identity, sidecar)
    with pytest.raises(DOSSIER.ProvenanceError, match="semantic cross-links"):
        DOSSIER.build_dossier("0" * 64, runtime)


def test_recovery_extra_field_or_nonfalse_exposure_fails_closed(monkeypatch) -> None:
    runtime, _fixed, values = _install_build_mocks(monkeypatch)
    document, identity, sidecar = values[runtime.recovery_pass.path]
    with_extra = dict(document, outcomes={"forged": True})
    values[runtime.recovery_pass.path] = (with_extra, identity, sidecar)
    with pytest.raises(DOSSIER.ProvenanceError, match="unexpected fields or exposure"):
        DOSSIER.build_dossier("0" * 64, runtime)

    exposed = dict(document)
    exposed["scientific_exposure"] = {"iq": False, "decoder": True}
    values[runtime.recovery_pass.path] = (exposed, identity, sidecar)
    with pytest.raises(DOSSIER.ProvenanceError, match="unexpected fields or exposure"):
        DOSSIER.build_dossier("0" * 64, runtime)


def test_recovery_contract_is_not_caller_selectable(monkeypatch) -> None:
    runtime, _fixed, _values = _install_build_mocks(monkeypatch)
    changed = DOSSIER.RuntimeInputs(
        remediation_intent=runtime.remediation_intent,
        remediation_pass=runtime.remediation_pass,
        recovery_pass=DOSSIER.RuntimeJSON(
            runtime.recovery_pass.path,
            runtime.recovery_pass.expected_file_sha256,
            runtime.recovery_pass.expected_payload_sha256,
            "attacker-selected-schema",
            runtime.recovery_pass.expected_status,
            runtime.recovery_pass.self_hash_field,
        ),
        pycache_quarantine=runtime.pycache_quarantine,
        expected_pycache_tree_sha256=runtime.expected_pycache_tree_sha256,
        quarantined_control_root=runtime.quarantined_control_root,
        expected_control_tree_sha256=runtime.expected_control_tree_sha256,
    )
    with pytest.raises(DOSSIER.ProvenanceError, match="recovery PASS contract"):
        DOSSIER.build_dossier("0" * 64, changed)


def test_runtime_json_requires_exact_file_payload_schema_status_sidecar(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    base = {"schema_version": "runtime-v1", "status": "PASS"}
    base["runtime_payload_sha256"] = DOSSIER._sha256_document(base)
    payload = (json.dumps(base, sort_keys=True) + "\n").encode()
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (tmp_path / "runtime.json.sha256").write_text(f"{digest}  runtime.json\n")
    original = DOSSIER._read_regular
    monkeypatch.setattr(DOSSIER, "VAR_LIB_ROOT", tmp_path)
    monkeypatch.setattr(
        DOSSIER, "_directory_identity",
        lambda target, expected_mode=None: {
            "path": str(target), "st_dev": 1, "st_ino": 2, "uid": 0,
            "gid": 0, "mode": "0755", "nlink": 2,
        },
    )
    def root_identity(target: Path, maximum=DOSSIER.MAX_FILE_BYTES):
        content, identity = original(target, maximum)
        identity.update(uid=0, gid=0, mode="0444", nlink=1)
        return content, identity
    monkeypatch.setattr(DOSSIER, "_read_regular", root_identity)
    spec = DOSSIER.RuntimeJSON(path, digest, base["runtime_payload_sha256"], "runtime-v1", "PASS", "runtime_payload_sha256")
    document, identity, sidecar = DOSSIER._load_runtime_json(spec)
    assert document == base
    assert identity["sha256"] == digest
    assert sidecar["path"].endswith(".sha256")
    with pytest.raises(DOSSIER.ProvenanceError, match="self-hash"):
        DOSSIER._load_runtime_json(DOSSIER.RuntimeJSON(path, digest, "f" * 64, "runtime-v1", "PASS", "runtime_payload_sha256"))
    with pytest.raises(DOSSIER.ProvenanceError, match="root-control identity"):
        DOSSIER._load_runtime_json(DOSSIER.RuntimeJSON(path, "f" * 64, base["runtime_payload_sha256"], "runtime-v1", "PASS", "runtime_payload_sha256"))
    with pytest.raises(DOSSIER.ProvenanceError, match="schema/status"):
        DOSSIER._load_runtime_json(DOSSIER.RuntimeJSON(path, digest, base["runtime_payload_sha256"], "wrong-schema", "PASS", "runtime_payload_sha256"))
    with pytest.raises(DOSSIER.ProvenanceError, match="schema/status"):
        DOSSIER._load_runtime_json(DOSSIER.RuntimeJSON(path, digest, base["runtime_payload_sha256"], "runtime-v1", "FAIL", "runtime_payload_sha256"))
    (tmp_path / "runtime.json.sha256").write_text("0" * 64 + "  runtime.json\n")
    with pytest.raises(DOSSIER.ProvenanceError, match="sidecar"):
        DOSSIER._load_runtime_json(spec)
    with pytest.raises(DOSSIER.ProvenanceError, match="self-hash field"):
        DOSSIER._validate_self_hash(
            {"schema_version": "runtime-v1", "status": "a" * 64},
            "status", "a" * 64, "reserved collision",
        )


def test_control_tree_requires_exact_set_and_empty_results(monkeypatch, tmp_path: Path) -> None:
    results = tmp_path / "results-v3"
    results.mkdir()
    contents = {"one": b"one", "two": b"two"}
    expected = {}
    for name, payload in contents.items():
        target = tmp_path / name
        target.write_bytes(payload)
        live = DOSSIER._read_regular(target)[1]
        expected[name] = {
            key: live[key]
            for key in ("size_bytes", "sha256", "uid", "gid", "mode", "nlink")
        }
    monkeypatch.setattr(DOSSIER, "EXPECTED_CONTROL_FILES", expected)
    monkeypatch.setattr(DOSSIER, "CONTROL_TOP_LEVEL", set(expected) | {"results-v3"})
    monkeypatch.setattr(DOSSIER, "VAR_LIB_ROOT", tmp_path.parent)
    monkeypatch.setattr(DOSSIER, "_directory_identity", lambda path, expected_mode=None: {"path": str(path), "st_dev": 1, "st_ino": 2, "uid": 0, "gid": 0, "mode": "0755", "nlink": 2})
    provisional = {"root": DOSSIER._directory_identity(tmp_path), "files": {}, "directories": {"results-v3": {**DOSSIER._directory_identity(results), "entry_count": 0}}, "entry_count": 3}
    for name in sorted(expected):
        provisional["files"][name] = DOSSIER._read_regular(tmp_path / name)[1]
    semantic = DOSSIER._sha256_document(provisional)
    inventory, _ = DOSSIER._control_tree(tmp_path, semantic)
    assert inventory["tree_semantic_sha256"] == semantic
    (results / "outcome.json").write_text("{}")
    with pytest.raises(DOSSIER.ProvenanceError, match="not empty"):
        DOSSIER._control_tree(tmp_path, semantic)


def test_control_tree_rejects_directory_identity_drift(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "results-v3").mkdir()
    monkeypatch.setattr(DOSSIER, "EXPECTED_CONTROL_FILES", {})
    monkeypatch.setattr(DOSSIER, "CONTROL_TOP_LEVEL", {"results-v3"})
    monkeypatch.setattr(DOSSIER, "VAR_LIB_ROOT", tmp_path.parent)
    root_calls = 0

    def directory_identity(path: Path, expected_mode=None):
        nonlocal root_calls
        is_root = Path(path) == tmp_path
        if is_root:
            root_calls += 1
        return {
            "path": str(path), "st_dev": 1, "st_ino": 2 if is_root else 3,
            "uid": 0, "gid": 0, "mode": "0755", "nlink": 2,
            "mtime_ns": root_calls if is_root else 0, "ctime_ns": 0,
        }

    monkeypatch.setattr(DOSSIER, "_directory_identity", directory_identity)
    with pytest.raises(DOSSIER.ProvenanceError, match="changed during inventory"):
        DOSSIER._control_tree(tmp_path, "0" * 64)


def test_pycache_tree_binds_preserved_inodes_and_exact_only_file(monkeypatch, tmp_path: Path) -> None:
    child = tmp_path / DOSSIER.PY_CACHE_FILE
    child.write_bytes(b"pyc")
    child_identity = DOSSIER._read_regular(child)[1]
    expected = {key: child_identity[key] for key in ("size_bytes", "sha256", "st_dev", "st_ino", "uid", "gid", "mode", "nlink")}
    monkeypatch.setattr(DOSSIER, "PY_CACHE_EXPECTED", expected)
    monkeypatch.setattr(DOSSIER, "VAR_LIB_ROOT", tmp_path.parent)
    monkeypatch.setattr(DOSSIER, "_directory_identity", lambda path, expected_mode=None: {"path": str(path), "st_dev": 64512, "st_ino": 1182239, "uid": 0, "gid": 0, "mode": "0755", "nlink": 2})
    provisional = {"root": DOSSIER._directory_identity(tmp_path), "files": {DOSSIER.PY_CACHE_FILE: child_identity}, "entry_count": 1}
    semantic = DOSSIER._sha256_document(provisional)
    inventory = DOSSIER._pycache_tree(tmp_path, semantic)
    assert inventory["tree_semantic_sha256"] == semantic
    (tmp_path / "foreign").write_text("x")
    with pytest.raises(DOSSIER.ProvenanceError, match="entries drift"):
        DOSSIER._pycache_tree(tmp_path, semantic)


def test_process_gate_detects_role_without_opening_any_science_file(tmp_path: Path) -> None:
    process = tmp_path / "123"
    process.mkdir()
    (process / "cmdline").write_bytes(b"python\0exec-evaluator\0")
    with pytest.raises(DOSSIER.ProvenanceError, match="process exists"):
        DOSSIER._process_gate(tmp_path)


def test_process_gate_fails_closed_on_unreadable_cmdline(monkeypatch, tmp_path: Path) -> None:
    process = tmp_path / "123"
    process.mkdir()
    original = os.open

    def denied(path, flags, *args, **kwargs):
        if Path(path) == process / "cmdline":
            raise PermissionError("denied")
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(DOSSIER.os, "open", denied)
    with pytest.raises(DOSSIER.ProvenanceError, match="fail-closed"):
        DOSSIER._process_gate(tmp_path)


def test_atomic_publish_is_no_clobber_selfhash_and_sidecar(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / DOSSIER.OUTPUT.name
    monkeypatch.setattr(DOSSIER, "REPORTS", tmp_path)
    monkeypatch.setattr(DOSSIER, "OUTPUT", output)
    document = {"schema_version": DOSSIER.SCHEMA, "status": "PASS_METADATA_ONLY_SECOND_FAILED_ACTIVATION_RECOVERED"}
    document[DOSSIER.HASH_FIELD] = DOSSIER._sha256_document(document)
    DOSSIER.publish(document, output)
    assert json.loads(output.read_bytes()) == document
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    assert output.with_name(output.name + ".sha256").read_text() == f"{digest}  {output.name}\n"
    assert not any("staging" in item.name for item in tmp_path.iterdir())
    with pytest.raises(DOSSIER.ProvenanceError, match="already exists"):
        DOSSIER.publish(document, output)


def test_failed_atomic_publication_cleans_only_staging_inode(monkeypatch, tmp_path: Path) -> None:
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    monkeypatch.setattr(DOSSIER, "_rename_noreplace", lambda *args: (_ for _ in ()).throw(PermissionError("stop")))
    try:
        with pytest.raises(PermissionError):
            DOSSIER._publish_file(fd, "result.json", b"payload")
    finally:
        os.close(fd)
    assert tuple(tmp_path.iterdir()) == ()


def test_directory_fsync_failure_after_rename_removes_only_owned_target(monkeypatch, tmp_path: Path) -> None:
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    original = os.fsync

    def fail_directory(fd: int) -> None:
        if fd == directory_fd:
            raise OSError("injected directory fsync failure")
        original(fd)

    monkeypatch.setattr(DOSSIER.os, "fsync", fail_directory)
    try:
        with pytest.raises(BaseException):
            DOSSIER._publish_file(directory_fd, "result.json", b"payload")
    finally:
        os.close(directory_fd)
    assert tuple(tmp_path.iterdir()) == ()


def test_json_failure_rolls_back_just_published_sidecar(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / DOSSIER.OUTPUT.name
    monkeypatch.setattr(DOSSIER, "REPORTS", tmp_path)
    monkeypatch.setattr(DOSSIER, "OUTPUT", output)
    original = DOSSIER._rename_noreplace

    def fail_json(directory_fd: int, source: str, target: str) -> None:
        if target == output.name:
            raise PermissionError("injected JSON commit failure")
        original(directory_fd, source, target)

    monkeypatch.setattr(DOSSIER, "_rename_noreplace", fail_json)
    document = {"schema_version": DOSSIER.SCHEMA, "status": "PASS"}
    document[DOSSIER.HASH_FIELD] = DOSSIER._sha256_document(document)
    with pytest.raises(PermissionError, match="injected"):
        DOSSIER.publish(document, output)
    assert tuple(tmp_path.iterdir()) == ()


def test_sidecar_collision_refuses_before_creating_output(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / DOSSIER.OUTPUT.name
    sidecar = output.with_name(output.name + ".sha256")
    sidecar.write_text("preexisting\n")
    original = sidecar.read_bytes()
    monkeypatch.setattr(DOSSIER, "REPORTS", tmp_path)
    monkeypatch.setattr(DOSSIER, "OUTPUT", output)
    document = {"schema_version": DOSSIER.SCHEMA, "status": "PASS"}
    document[DOSSIER.HASH_FIELD] = DOSSIER._sha256_document(document)
    with pytest.raises(DOSSIER.ProvenanceError, match="sidecar already exists"):
        DOSSIER.publish(document, output)
    assert not output.exists()
    assert sidecar.read_bytes() == original


def test_cli_requires_every_runtime_identity_field() -> None:
    with pytest.raises(SystemExit):
        DOSSIER._parser().parse_args(["--expected-generator-sha256", "a" * 64])
    actions = {option for action in DOSSIER._parser()._actions for option in action.option_strings}
    for prefix in ("remediation-intent", "remediation-pass", "recovery-pass"):
        assert f"--{prefix}" in actions
        assert f"--expected-{prefix}-sha256" in actions
        assert f"--expected-{prefix}-payload-sha256" in actions
        assert f"--expected-{prefix}-schema" in actions
        assert f"--expected-{prefix}-status" in actions
        assert f"--{prefix}-self-hash-field" in actions


def test_frozen_bindings_and_source_have_no_iq_path_interface() -> None:
    assert DOSSIER.EXPECTED_FIXED[DOSSIER.AMENDMENT]["sha256"] == "1d7b8624bf62b957118a02ebafec6851e98f02541d88d22afa0170784926c8dd"
    assert DOSSIER.EXPECTED_FIXED[DOSSIER.REVIEW]["sha256"] == "349e6992120fb10ff7bc1fdac5e82560c2af0d2c3e96b783ae8fe06a7687efa8"
    assert DOSSIER.EXPECTED_CONTROL_FILES["amendment-v3.tsq"]["sha256"].startswith("0af504")
    assert DOSSIER.EXPECTED_CONTROL_FILES["amendment-v3.tsr"]["sha256"].startswith("399d838")
    assert DOSSIER.EXPECTED_FIXED[DOSSIER.CURRENT_BUILDER]["sha256"].startswith("eb528")
    assert DOSSIER.EXPECTED_FIXED[DOSSIER.CURRENT_BUILDER_TEST]["sha256"].startswith("5b24")
    assert DOSSIER.EXPECTED_FIXED[DOSSIER.CORRECTED_RECOVERY_RUNNER_SOURCE]["sha256"].startswith("a471")
    assert DOSSIER.EXPECTED_FIXED[DOSSIER.CORRECTED_RECOVERY_RUNNER_TEST]["sha256"].startswith("feb02")
    assert DOSSIER.EXTERNAL_INVENTORY_SHA256.startswith("c918")
    option_strings = {option for action in DOSSIER._parser()._actions for option in action.option_strings}
    assert not any("iq" in option.lower() for option in option_strings)
    source = SOURCE.read_text(encoding="utf-8")
    assert "/iq-data" not in source
    assert "s3://" not in source
