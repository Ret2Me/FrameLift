from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "work/blind-phase-confirmatory-v2/recover_partial_activation_attempt2_v3.py"
SPEC = importlib.util.spec_from_file_location("recover_partial_activation_attempt2_v3", SOURCE)
assert SPEC and SPEC.loader
RECOVERY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RECOVERY
SPEC.loader.exec_module(RECOVERY)


def test_frozen_attempt2_constants_bind_exact_audited_state_without_live_reads() -> None:
    assert RECOVERY.ATTEMPT_ID == "67adc3bf4cc49c7f60c1c0691186631686d0b1716c3e65e1d29a51e97f6c73f4"
    assert RECOVERY.KEEPER["pid"] == 1787143
    assert RECOVERY.KEEPER["starttime_ticks"] == 36760858
    assert RECOVERY.KEEPER["mount_namespace_inode"] == 4026532311
    assert RECOVERY.EXPECTED_FILES[RECOVERY.AMENDMENT_PATH]["sha256"] == "1d7b8624bf62b957118a02ebafec6851e98f02541d88d22afa0170784926c8dd"
    assert RECOVERY.EXPECTED_FILES[RECOVERY.NO_DECODER_EVIDENCE_PATH]["sha256"] == "e51e0ec6e95760fc775f1c909dfbe8f87d92ffd14575064315aad616c0990abd"
    assert RECOVERY.EXPECTED_FILES[RECOVERY.BUILDER_CONTROL_COPY]["sha256"] == "2f13929fc235d3d4a428e3d71df8cc179a164f8459c135f503bb7f28411c1ef9"
    assert RECOVERY.EXPECTED_KEEPER_MOUNTINFO_SHA256 == "cc47d7c1585d8e93a04558d26cb812419d4cfddd4a451baf823460f156e7f3f3"
    assert len(RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS) == 35
    assert len(RECOVERY.EXPECTED_MOUNT_ROWS) == 1
    assert RECOVERY.ACTIVATION_FAILURE_CONTEXT[
        "project_or_authority_activation_intent_published"
    ] is False
    assert RECOVERY.ACTIVATION_FAILURE_CONTEXT[
        "project_or_authority_activation_mounts_performed"
    ] is False
    assert RECOVERY.ACTIVATION_FAILURE_CONTEXT[
        "start_created_results_tmpfs_present"
    ] is True
    assert RECOVERY.ACTIVATION_FAILURE_CONTEXT[
        "start_created_results_tmpfs_mount_count"
    ] == 1
    assert RECOVERY.ACTIVATION_FAILURE_CONTEXT["iq_content_opened"] is False


def test_exact_one_mount_topology_passes_and_extra_or_changed_mount_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)
    assert RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER) == RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS

    extra = "168 114 0:50 / /var/lib/telemetry-yield-confirmatory-v3/sealed-project-artifacts-v3 ro - tmpfs tmpfs ro"
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: (*RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS, extra))
    with pytest.raises(RECOVERY.RecoveryError, match="full 35-row"):
        RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER)

    changed = list(RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)
    changed[-1] = changed[-1].replace("rw,nosuid", "ro,nosuid")
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: tuple(changed))
    with pytest.raises(RECOVERY.RecoveryError, match="full 35-row"):
        RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER)


def test_shared_root_fails_topology_gate(monkeypatch, tmp_path: Path) -> None:
    changed = list(RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)
    changed[0] = changed[0].replace("rw,relatime -", "rw,relatime shared:9 -")
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: tuple(changed))
    with pytest.raises(RECOVERY.RecoveryError, match="full 35-row"):
        RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER)


def test_control_inventory_requires_exact_pre_activation_names(monkeypatch, tmp_path: Path) -> None:
    for name in RECOVERY.EXPECTED_CONTROL_NAMES:
        target = tmp_path / name
        target.mkdir() if name == "results-v3" else target.write_text("x")
    monkeypatch.setattr(RECOVERY, "CONTROL_PARENT", tmp_path)
    assert RECOVERY._check_control_absence() == list(RECOVERY.EXPECTED_CONTROL_NAMES)
    (tmp_path / "namespace-role-completion-v3.json").write_text("x")
    with pytest.raises(RECOVERY.RecoveryError, match="exact pre-activation state"):
        RECOVERY._check_control_absence()


def test_activation_intent_presence_fails_before_bound_artifact_reads(monkeypatch, tmp_path: Path) -> None:
    activation = tmp_path / "readonly-project-activation-intent-v3.json"
    activation.write_text("foreign")
    monkeypatch.setattr(RECOVERY, "ACTIVATION_INTENT_PATH", activation)
    monkeypatch.setattr(
        RECOVERY, "_load_bound_json",
        lambda path: (_ for _ in ()).throw(AssertionError("artifact read after absence failure")),
    )
    with pytest.raises(RECOVERY.RecoveryError, match="activation intent exists"):
        RECOVERY._validate_artifact_chain()


def test_recovery_control_inventory_is_append_only_and_incident_first(monkeypatch, tmp_path: Path) -> None:
    control = tmp_path / RECOVERY.CONTROL_COPY.name
    control.write_text("runner")
    monkeypatch.setattr(RECOVERY, "RECOVERY_CONTROL_PARENT", tmp_path)
    monkeypatch.setattr(RECOVERY, "CONTROL_COPY", control)
    monkeypatch.setattr(RECOVERY, "CONTROL_INCIDENT_OUTPUT", tmp_path / "incident.json")
    monkeypatch.setattr(RECOVERY, "CONTROL_FAILURE_OUTPUT", tmp_path / "failure.json")
    monkeypatch.setattr(RECOVERY, "CONTROL_RESULT_OUTPUT", tmp_path / "result.json")
    assert RECOVERY._validate_recovery_control_inventory() == (control.name,)
    (tmp_path / "result.json").write_text("{}")
    with pytest.raises(RECOVERY.RecoveryError, match="without the incident"):
        RECOVERY._validate_recovery_control_inventory()
    (tmp_path / "foreign").write_text("x")
    with pytest.raises(RECOVERY.RecoveryError, match="unexpected recovery-control"):
        RECOVERY._validate_recovery_control_inventory()


def test_nonempty_private_results_fail_before_signal(monkeypatch, tmp_path: Path) -> None:
    view = tmp_path / str(RECOVERY.KEEPER["pid"]) / "root" / "var/lib/telemetry-yield-confirmatory-v3/results-v3"
    view.mkdir(parents=True)
    (view / "unit-manifest.json").write_text("{}")
    with pytest.raises(RECOVERY.RecoveryError, match="not empty"):
        RECOVERY._check_results_empty(tmp_path, RECOVERY.KEEPER)


def test_keeper_exclusivity_rejects_second_member_and_namespace_fd(monkeypatch, tmp_path: Path) -> None:
    p1, p2 = tmp_path / "100", tmp_path / "101"
    for item in (p1, p2):
        (item / "fd").mkdir(parents=True)
    monkeypatch.setattr(RECOVERY, "_numeric_children", lambda path: (p1, p2))
    monkeypatch.setattr(RECOVERY, "_pid_starttime", lambda path: 7)
    monkeypatch.setattr(RECOVERY, "_namespace_identity", lambda path: (4, RECOVERY.KEEPER["mount_namespace_inode"]))
    monkeypatch.setattr(RECOVERY, "_read_proc_bytes", lambda path, maximum: b"")
    monkeypatch.setattr(RECOVERY.os, "readlink", lambda path: f"mnt:[{RECOVERY.KEEPER['mount_namespace_inode']}]")
    keeper = {**RECOVERY.KEEPER, "pid": 100}
    with pytest.raises(RECOVERY.RecoveryError, match="exclusivity"):
        RECOVERY._scan_keeper_holders(tmp_path, keeper)


def _install_recover_mocks(monkeypatch, events: list[str], tmp_path: Path, *, external_after=None):
    monkeypatch.setattr(RECOVERY, "RECOVERY_CONTROL_PARENT", tmp_path)
    monkeypatch.setattr(RECOVERY, "CONTROL_INCIDENT_OUTPUT", tmp_path / "incident.json")
    monkeypatch.setattr(RECOVERY, "CONTROL_RESULT_OUTPUT", tmp_path / "result.json")
    monkeypatch.setattr(RECOVERY, "CONTROL_FAILURE_OUTPUT", tmp_path / "failure.json")
    baseline = {"inventory_sha256": "b" * 64, "namespaces": []}
    chain = {
        "intent": {"path": "intent", "size_bytes": 1, "sha256": "1" * 64},
        "registration": {"path": "registration", "size_bytes": 1, "sha256": "2" * 64},
        "active": {"path": "active", "size_bytes": 1, "sha256": "3" * 64},
        "project_or_authority_activation_intent_absent": True,
        "start_created_results_tmpfs_present": True,
        "lifecycle_sidecars": {},
        "builder_control_copy": {"path": "builder", "size_bytes": 1, "sha256": "5" * 64},
        "amendment": {"path": "amendment", "size_bytes": 1, "sha256": "6" * 64},
        "independent_review": {"path": "review", "size_bytes": 1, "sha256": "4" * 64},
        "prior_no_decoder_evidence": {"path": "evidence", "size_bytes": 1, "sha256": "7" * 64},
        "failure_context": dict(RECOVERY.ACTIVATION_FAILURE_CONTEXT),
        "external_baseline": baseline,
    }
    monkeypatch.setattr(RECOVERY, "_validate_control_parent", lambda: events.append("control"))
    monkeypatch.setattr(RECOVERY, "_validate_runner", lambda sha: {"path": "runner", "size_bytes": 1, "sha256": sha})
    monkeypatch.setattr(RECOVERY, "_validate_recovery_control_inventory", lambda: (RECOVERY.CONTROL_COPY.name,))
    monkeypatch.setattr(
        RECOVERY, "_validate_failure_lockout", lambda token, **kwargs: None
    )
    monkeypatch.setattr(RECOVERY, "_validate_artifact_chain", lambda: chain)
    monkeypatch.setattr(RECOVERY, "_check_results_empty", lambda proc, keeper: events.append("results-empty"))
    monkeypatch.setattr(RECOVERY, "_check_control_absence", lambda: ["active"])
    monkeypatch.setattr(RECOVERY, "_validate_keeper_generation", lambda proc, keeper: events.append("keeper-valid"))
    process_before = {"namespace_member_pids": [RECOVERY.KEEPER["pid"]], "namespace_fd_handles": [], "suspicious_processes": []}
    process_after = {"namespace_member_pids": [], "namespace_fd_handles": [], "suspicious_processes": []}
    monkeypatch.setattr(RECOVERY, "_scan_keeper_holders", lambda proc, keeper, require_keeper=True: process_before if require_keeper else process_after)
    monkeypatch.setattr(RECOVERY, "_validate_keeper_topology", lambda proc, keeper: RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)
    monkeypatch.setattr(RECOVERY, "_keeper_generation_state", lambda proc, keeper: "present" if "wait-ready" not in events else "absent")
    monkeypatch.setattr(RECOVERY, "_wait_keeper_generation_absent", lambda proc, keeper, timeout: events.append("generation-absent"))
    captures = iter((baseline, baseline, external_after or baseline))
    monkeypatch.setattr(RECOVERY, "_capture_external_fixed_point", lambda proc, inode: next(captures))
    monkeypatch.setattr(RECOVERY, "_pidfd_open", lambda pid: events.append("pidfd-open") or 91)
    monkeypatch.setattr(RECOVERY, "_pidfd_send", lambda fd: events.append("sigterm"))
    monkeypatch.setattr(RECOVERY, "_wait_pidfd", lambda fd, timeout: events.append("wait-ready"))
    monkeypatch.setattr(RECOVERY, "_keeper_generation_absent", lambda proc, keeper: True)
    monkeypatch.setattr(RECOVERY.os, "close", lambda fd: events.append("pidfd-close") if fd == 91 else None)

    publications = []
    def publish(path, document, field):
        publications.append((path.name, document["status"], field))
        events.append("publish-incident" if path == RECOVERY.CONTROL_INCIDENT_OUTPUT else "publish-result")
        return {"path": str(path), "size_bytes": 1, "sha256": "9" * 64}
    monkeypatch.setattr(RECOVERY, "_publish", publish)
    monkeypatch.setattr(
        RECOVERY,
        "_publish_or_resume_incident",
        lambda document: (
            {**document, "incident_payload_sha256": "8" * 64},
            {"path": str(RECOVERY.CONTROL_INCIDENT_OUTPUT), "size_bytes": 1, "sha256": "9" * 64},
            events.append("publish-incident") is None,
        ),
    )
    return publications


def test_recover_commits_incident_before_exact_pidfd_signal_and_pass_after_zero_delta(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    publications = _install_recover_mocks(monkeypatch, events, tmp_path)
    result = RECOVERY.recover("a" * 64, proc_root=tmp_path)
    assert result["status"] == "PASS"
    assert events.index("publish-incident") < events.index("sigterm") < events.index("wait-ready") < events.index("publish-result")
    assert events.count("pidfd-close") == 1
    assert publications == [(RECOVERY.CONTROL_RESULT_OUTPUT.name, "PASS", "recovery_payload_sha256")]


def test_preflight_is_read_only_and_rechecks_exact_state(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    publications = _install_recover_mocks(monkeypatch, events, tmp_path)
    result = RECOVERY.recover("a" * 64, proc_root=tmp_path, preflight_only=True)
    assert result["status"] == "PASS_READ_ONLY_NO_PUBLICATION_NO_SIGNAL"
    assert result["exact_private_namespace_mountinfo_row_count"] == 35
    assert result["expected_relevant_mount_count"] == 1
    assert publications == []
    assert "publish-incident" not in events
    assert "sigterm" not in events


def test_incident_distinguishes_results_tmpfs_from_absent_activation_mounts() -> None:
    chain = {
        "prior_no_decoder_evidence": {
            "path": "evidence", "size_bytes": 1, "sha256": "e" * 64
        },
        "failure_context": dict(RECOVERY.ACTIVATION_FAILURE_CONTEXT),
        "external_baseline": {"inventory_sha256": "b" * 64},
    }
    document = RECOVERY._incident_document(
        runner={"path": "runner", "size_bytes": 1, "sha256": "a" * 64},
        chain=chain,
        topology=RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS,
        process_gate={
            "namespace_member_pids": [RECOVERY.KEEPER["pid"]],
            "namespace_fd_handles": [],
            "suspicious_processes": [],
        },
        control_names=RECOVERY.EXPECTED_CONTROL_NAMES,
        external={"inventory_sha256": "b" * 64},
    )
    assert document["project_or_authority_activation_intent_absent"] is True
    assert document["project_or_authority_activation_mounts_absent"] is True
    assert document["start_created_results_tmpfs_present"] is True
    assert document["exact_private_namespace_mountinfo"][
        "relevant_mount_role"
    ] == "start_created_results_tmpfs_only"
    assert document["exact_private_namespace_mountinfo"][
        "expected_relevant_mount_count"
    ] == 1
    assert document["authorized_action"]["mount_operations_by_recovery"] is False


def test_artifact_drift_after_incident_blocks_signal_and_publishes_lockout(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    publications = _install_recover_mocks(monkeypatch, events, tmp_path)
    exact = RECOVERY._validate_artifact_chain()
    changed = dict(exact)
    changed["project_or_authority_activation_intent_absent"] = False
    values = iter((exact, changed))
    monkeypatch.setattr(RECOVERY, "_validate_artifact_chain", lambda: next(values))
    with pytest.raises(RECOVERY.RecoveryError, match="artifact chain drifted"):
        RECOVERY.recover("a" * 64, proc_root=tmp_path)
    assert "sigterm" not in events
    assert publications[-1][1] == "FAIL_CLOSED_AFTER_COMMITTED_INCIDENT"


def test_external_delta_after_pidfd_termination_fails_and_never_publishes_pass(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    publications = _install_recover_mocks(monkeypatch, events, tmp_path, external_after={"inventory_sha256": "c" * 64, "namespaces": []})
    with pytest.raises(RECOVERY.RecoveryError, match="changed during keeper termination"):
        RECOVERY.recover("a" * 64, proc_root=tmp_path)
    assert all(status != "PASS" for _, status, _ in publications)


def test_signal_failure_publishes_fail_closed_receipt_not_pass(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    publications = _install_recover_mocks(monkeypatch, events, tmp_path)
    monkeypatch.setattr(RECOVERY, "_pidfd_send", lambda fd: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(PermissionError):
        RECOVERY.recover("a" * 64, proc_root=tmp_path)
    assert publications[-1][1] == "FAIL_CLOSED_AFTER_COMMITTED_INCIDENT"
    assert all(status != "PASS" for _, status, _ in publications)


def test_pidfd_close_failure_cannot_mask_primary_and_is_journaled(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    publications = _install_recover_mocks(monkeypatch, events, tmp_path)
    monkeypatch.setattr(RECOVERY, "_pidfd_send", lambda fd: (_ for _ in ()).throw(PermissionError("primary")))
    monkeypatch.setattr(RECOVERY.os, "close", lambda fd: (_ for _ in ()).throw(OSError("close")) if fd == 91 else None)
    with pytest.raises(RECOVERY.RecoveryCleanupError) as caught:
        RECOVERY.recover("a" * 64, proc_root=tmp_path)
    assert isinstance(caught.value.primary, PermissionError)
    assert isinstance(caught.value.cleanup, OSError)
    assert publications[-1][0] == RECOVERY.CONTROL_FAILURE_OUTPUT.name


def test_failure_lockout_requires_exact_manual_resume_hash(monkeypatch) -> None:
    identity = {"path": str(RECOVERY.CONTROL_FAILURE_OUTPUT), "size_bytes": 2, "sha256": "f" * 64}
    monkeypatch.setattr(RECOVERY.CONTROL_FAILURE_OUTPUT.__class__, "exists", lambda self: self == RECOVERY.CONTROL_FAILURE_OUTPUT)
    monkeypatch.setattr(
        RECOVERY, "_load_recovery_report",
        lambda path, field, **kwargs: ({"status": "FAIL_CLOSED_AFTER_COMMITTED_INCIDENT"}, identity),
    )
    with pytest.raises(RECOVERY.RecoveryError, match="manual"):
        RECOVERY._validate_failure_lockout(None)
    assert RECOVERY._validate_failure_lockout("f" * 64) == identity


def test_preflight_never_completes_missing_failure_sidecar(monkeypatch, tmp_path: Path) -> None:
    original_validate_failure = RECOVERY._validate_failure_lockout
    events: list[str] = []
    _install_recover_mocks(monkeypatch, events, tmp_path)
    monkeypatch.setattr(RECOVERY, "_validate_failure_lockout", original_validate_failure)
    failure = RECOVERY.CONTROL_FAILURE_OUTPUT
    document = RECOVERY._self_hashed(
        {
            "schema_version": "blind-phase-confirmatory-start-only-recovery-v2",
            "status": "FAIL_CLOSED_AFTER_COMMITTED_INCIDENT",
        },
        "recovery_payload_sha256",
    )
    payload = (json.dumps(document, sort_keys=True) + "\n").encode()
    failure.write_bytes(payload)
    identity = {
        "path": str(failure),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "st_dev": 1,
        "st_ino": 2,
        "uid": 0,
        "gid": 0,
        "mode": "0444",
        "nlink": 1,
    }
    monkeypatch.setattr(RECOVERY, "_read_regular", lambda path, maximum: (payload, identity))
    publications: list[Path] = []
    monkeypatch.setattr(
        RECOVERY, "_publish_one",
        lambda path, content: publications.append(path),
    )
    with pytest.raises(RECOVERY.RecoveryError, match="read-only validation"):
        RECOVERY.recover(
            "a" * 64,
            proc_root=tmp_path,
            preflight_only=True,
            resume_after_failure_sha256=identity["sha256"],
        )
    assert publications == []
    assert "publish-incident" not in events
    assert "sigterm" not in events


def test_resume_with_committed_incident_and_absent_keeper_does_not_signal(monkeypatch, tmp_path: Path) -> None:
    events: list[str] = []
    publications = _install_recover_mocks(monkeypatch, events, tmp_path)
    monkeypatch.setattr(RECOVERY, "_keeper_generation_state", lambda proc, keeper: "absent")
    monkeypatch.setattr(RECOVERY.CONTROL_INCIDENT_OUTPUT.__class__, "exists", lambda self: self == RECOVERY.CONTROL_INCIDENT_OUTPUT)
    monkeypatch.setattr(
        RECOVERY, "_publish_or_resume_incident",
        lambda document: (
            {**document, "incident_payload_sha256": "8" * 64},
            {"path": str(RECOVERY.CONTROL_INCIDENT_OUTPUT), "size_bytes": 1, "sha256": "9" * 64},
            False,
        ),
    )
    result = RECOVERY.recover("a" * 64, proc_root=tmp_path)
    assert result["resume_transition"] == "existing_incident_keeper_already_absent"
    assert "sigterm" not in events
    assert publications[-1][1] == "PASS"


def test_existing_incident_resume_compares_full_35_row_prestate(monkeypatch) -> None:
    expected = {"schema_version": "x", "recorded_at_utc": "new", "exact_private_namespace_mountinfo": {"rows": list(RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)}}
    actual = {**expected, "recorded_at_utc": "old", "incident_payload_sha256": "8" * 64}
    identity = {"path": str(RECOVERY.CONTROL_INCIDENT_OUTPUT), "size_bytes": 2, "sha256": "9" * 64}
    monkeypatch.setattr(RECOVERY.CONTROL_INCIDENT_OUTPUT.__class__, "exists", lambda self: self == RECOVERY.CONTROL_INCIDENT_OUTPUT)
    monkeypatch.setattr(RECOVERY, "_load_recovery_report", lambda path, field, **kwargs: (actual, identity))
    returned, returned_identity, created = RECOVERY._publish_or_resume_incident(expected)
    assert returned == actual
    assert returned_identity == identity
    assert created is False
    mutated = json.loads(json.dumps(actual))
    mutated["exact_private_namespace_mountinfo"]["rows"][-1] += " changed"
    monkeypatch.setattr(RECOVERY, "_load_recovery_report", lambda path, field, **kwargs: (mutated, identity))
    with pytest.raises(RECOVERY.RecoveryError, match="exact runner/state"):
        RECOVERY._publish_or_resume_incident(expected)


def test_wait_for_exact_generation_absence_tolerates_transient_zombie(monkeypatch, tmp_path: Path) -> None:
    states = iter(("present", "present", "absent"))
    monkeypatch.setattr(RECOVERY, "_keeper_generation_state", lambda proc, keeper: next(states))
    clock = iter((0.0, 0.1, 0.2))
    RECOVERY._wait_keeper_generation_absent(
        tmp_path, RECOVERY.KEEPER, 1.0,
        monotonic=lambda: next(clock), sleep=lambda seconds: None,
    )


def test_pid_reuse_or_namespace_reuse_fails_closed(monkeypatch, tmp_path: Path) -> None:
    pid_path = tmp_path / str(RECOVERY.KEEPER["pid"])
    monkeypatch.setattr(
        RECOVERY, "_pid_starttime",
        lambda path: int(RECOVERY.KEEPER["starttime_ticks"]) + 1,
    )
    with pytest.raises(RECOVERY.RecoveryError, match="PID was reused"):
        RECOVERY._keeper_generation_state(tmp_path, RECOVERY.KEEPER)
    monkeypatch.setattr(
        RECOVERY, "_pid_starttime",
        lambda path: int(RECOVERY.KEEPER["starttime_ticks"]),
    )
    monkeypatch.setattr(RECOVERY, "_namespace_identity", lambda path: (4, 99))
    with pytest.raises(RECOVERY.RecoveryError, match="different namespace"):
        RECOVERY._keeper_generation_state(tmp_path, RECOVERY.KEEPER)


def test_stat_can_outlive_mount_namespace_during_keeper_exit(tmp_path: Path) -> None:
    pid_path = tmp_path / str(RECOVERY.KEEPER["pid"])
    pid_path.mkdir()
    fields = ["S", *(["0"] * 18), str(RECOVERY.KEEPER["starttime_ticks"])]
    (pid_path / "stat").write_text(
        f"{RECOVERY.KEEPER['pid']} (keeper) {' '.join(fields)}\n",
        encoding="ascii",
    )
    # Reproduce the observed Linux teardown state exactly: /proc/PID/stat is
    # still readable for this generation while /proc/PID/ns/mnt is ENOENT.
    assert not (pid_path / "ns/mnt").exists()
    assert RECOVERY._keeper_generation_state(tmp_path, RECOVERY.KEEPER) == "absent"


def test_wait_for_generation_absence_times_out_without_sigkill(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(RECOVERY, "_keeper_generation_state", lambda proc, keeper: "present")
    clock = iter((0.0, 0.5, 1.1))
    with pytest.raises(RECOVERY.RecoveryError, match="remains"):
        RECOVERY._wait_keeper_generation_absent(
            tmp_path, RECOVERY.KEEPER, 1.0,
            monotonic=lambda: next(clock), sleep=lambda seconds: None,
        )


def test_no_clobber_publication_rejects_json_or_sidecar_collision(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(RECOVERY, "CONTROL_INCIDENT_OUTPUT", tmp_path / "incident.json")
    monkeypatch.setattr(RECOVERY, "CONTROL_RESULT_OUTPUT", tmp_path / "result.json")
    monkeypatch.setattr(RECOVERY, "CONTROL_FAILURE_OUTPUT", tmp_path / "failure.json")
    RECOVERY.CONTROL_INCIDENT_OUTPUT.write_text("foreign")
    with pytest.raises(RECOVERY.RecoveryError, match="no-clobber"):
        RECOVERY._publish(RECOVERY.CONTROL_INCIDENT_OUTPUT, {"schema_version": "x"}, "hash")
    RECOVERY.CONTROL_INCIDENT_OUTPUT.unlink()
    RECOVERY.CONTROL_INCIDENT_OUTPUT.with_name(RECOVERY.CONTROL_INCIDENT_OUTPUT.name + ".sha256").write_text("foreign")
    with pytest.raises(RECOVERY.RecoveryError, match="no-clobber"):
        RECOVERY._publish(RECOVERY.CONTROL_INCIDENT_OUTPUT, {"schema_version": "x"}, "hash")


def test_otmpfile_publication_is_complete_and_exclusive(tmp_path: Path) -> None:
    if os.geteuid() != 0:
        pytest.skip("linkat(AT_EMPTY_PATH) publication requires the production root context")
    target = tmp_path / "receipt.json"
    identity = RECOVERY._publish_one(target, b'{"complete":true}\n')
    assert target.read_bytes() == b'{"complete":true}\n'
    assert identity["mode"] == "0444"
    assert identity["nlink"] == 1
    with pytest.raises(RECOVERY.RecoveryError, match="no-clobber"):
        RECOVERY._publish_one(target, b"different")


def test_self_hash_projection_excludes_only_its_own_field() -> None:
    document = RECOVERY._self_hashed({"schema_version": "x", "status": "PASS", "nested": {"sha256": "a"}}, "payload_sha256")
    unhashed = dict(document)
    stored = unhashed.pop("payload_sha256")
    assert stored == RECOVERY._sha256_document(unhashed)
    mutated = dict(unhashed)
    mutated["status"] = "FAIL"
    assert stored != RECOVERY._sha256_document(mutated)


def test_cli_requires_exact_script_hash() -> None:
    with pytest.raises(SystemExit):
        RECOVERY._parser().parse_args([])
    args = RECOVERY._parser().parse_args(["--expected-script-sha256", "a" * 64])
    assert args.expected_script_sha256 == "a" * 64
    preflight = RECOVERY._parser().parse_args(
        ["--expected-script-sha256", "a" * 64, "--preflight-only"]
    )
    assert preflight.preflight_only is True


def test_production_ast_allows_only_exact_pidfd_sigterm_and_no_mount_or_iq_access() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    attributes = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in calls
        if isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "os.setns" not in attributes
    assert "os.system" not in attributes
    assert "os.execve" not in attributes
    assert "subprocess.run" not in attributes
    assert "subprocess.Popen" not in attributes
    assert "signal.pidfd_send_signal" in attributes
    assert "signal.SIGKILL" not in attributes
    assert "EXPECTED_MOUNT_ROWS" in source
    assert "iq_contents_opened_by_recovery\": False" in source


def test_attempt2_outputs_and_control_parent_are_new_and_disjoint_from_prior_runner() -> None:
    assert RECOVERY.CONTROL_COPY.name == "recover_partial_activation_attempt2_v3.py"
    assert RECOVERY.RECOVERY_CONTROL_PARENT.name == "telemetry-yield-confirmatory-v3-recovery-attempt-67adc3bf"
    assert RECOVERY.CONTROL_INCIDENT_OUTPUT.name == "start-only-failure-incident-v2.json"
    assert RECOVERY.CONTROL_RESULT_OUTPUT.name == "start-only-failure-recovery-v2.json"
    assert RECOVERY.CONTROL_FAILURE_OUTPUT.name == "start-only-failure-lockout-v2.json"
    assert "recover_partial_activation_v3.py" not in str(RECOVERY.CONTROL_COPY)
