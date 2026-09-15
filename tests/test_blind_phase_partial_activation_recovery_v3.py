from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "work/blind-phase-confirmatory-v2/recover_partial_activation_v3.py"
SPEC = importlib.util.spec_from_file_location("recover_partial_activation_v3", SOURCE)
assert SPEC and SPEC.loader
RECOVERY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RECOVERY
SPEC.loader.exec_module(RECOVERY)


def test_frozen_artifact_chain_or_completed_recovery_validates_read_only() -> None:
    if RECOVERY.INTENT_PATH.exists():
        chain = RECOVERY._validate_artifact_chain()
        assert chain["active"]["sha256"] == "13295c2a6fb96a5b7f524ddb346439049004ac909f7cd1f5bec6c99a38dc5c5f"
        assert chain["amendment"]["sha256"] == "6929a1eeb492e02a7bb6630b4ec4a46b82702d1fbead18a286f183258fe2a252"
        assert chain["prior_no_decoder_evidence"]["sha256"] == "e51e0ec6e95760fc775f1c909dfbe8f87d92ffd14575064315aad616c0990abd"
    else:
        recovery, _identity = RECOVERY._load_recovery_report(
            RECOVERY.CONTROL_RESULT_OUTPUT, "recovery_payload_sha256"
        )
        assert recovery["status"] == "PASS"
        assert recovery["keeper_termination"]["exact_generation_absent"] is True
        assert recovery["external_namespace_zero_delta"]["byte_identical"] is True


def test_exact_three_mount_topology_passes_and_extra_or_changed_mount_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)
    assert RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER) == RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS

    extra = "168 114 0:50 / /var/lib/telemetry-yield-confirmatory-v3/sealed-project-artifacts-v3 ro - tmpfs tmpfs ro"
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: (*RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS, extra))
    with pytest.raises(RECOVERY.RecoveryError, match="full 37-row"):
        RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER)

    changed = list(RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)
    changed[-3] = changed[-3].replace("rw,nosuid", "ro,nosuid")
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: tuple(changed))
    with pytest.raises(RECOVERY.RecoveryError, match="full 37-row"):
        RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER)


def test_shared_root_fails_topology_gate(monkeypatch, tmp_path: Path) -> None:
    changed = list(RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)
    changed[0] = changed[0].replace("rw,relatime -", "rw,relatime shared:9 -")
    monkeypatch.setattr(RECOVERY, "_mountinfo", lambda path: tuple(changed))
    with pytest.raises(RECOVERY.RecoveryError, match="full 37-row"):
        RECOVERY._validate_keeper_topology(tmp_path, RECOVERY.KEEPER)


def test_forbidden_role_or_freeze_artifact_fails_closed(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "persistent-mount-namespace-active-v3.json").write_text("x")
    (tmp_path / "namespace-role-completion-v3.json").write_text("x")
    monkeypatch.setattr(RECOVERY, "CONTROL_PARENT", tmp_path)
    with pytest.raises(RECOVERY.RecoveryError, match="role/seal/freeze/export"):
        RECOVERY._check_control_absence()


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
        "activation_intent": {"path": "activation", "size_bytes": 1, "sha256": "4" * 64},
        "builder_control_copy": {"path": "builder", "size_bytes": 1, "sha256": "5" * 64},
        "amendment": {"path": "amendment", "size_bytes": 1, "sha256": "6" * 64},
        "prior_no_decoder_evidence": {"path": "evidence", "size_bytes": 1, "sha256": "7" * 64},
        "external_baseline": baseline,
    }
    monkeypatch.setattr(RECOVERY, "_validate_control_parent", lambda: events.append("control"))
    monkeypatch.setattr(RECOVERY, "_validate_runner", lambda sha: {"path": "runner", "size_bytes": 1, "sha256": sha})
    monkeypatch.setattr(RECOVERY, "_validate_failure_lockout", lambda token: None)
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
        lambda path, field: ({"status": "FAIL_CLOSED_AFTER_COMMITTED_INCIDENT"}, identity),
    )
    with pytest.raises(RECOVERY.RecoveryError, match="manual"):
        RECOVERY._validate_failure_lockout(None)
    assert RECOVERY._validate_failure_lockout("f" * 64) == identity


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


def test_existing_incident_resume_compares_full_37_row_prestate(monkeypatch) -> None:
    expected = {"schema_version": "x", "recorded_at_utc": "new", "exact_private_namespace_mountinfo": {"rows": list(RECOVERY.EXPECTED_KEEPER_MOUNTINFO_ROWS)}}
    actual = {**expected, "recorded_at_utc": "old", "incident_payload_sha256": "8" * 64}
    identity = {"path": str(RECOVERY.CONTROL_INCIDENT_OUTPUT), "size_bytes": 2, "sha256": "9" * 64}
    monkeypatch.setattr(RECOVERY.CONTROL_INCIDENT_OUTPUT.__class__, "exists", lambda self: self == RECOVERY.CONTROL_INCIDENT_OUTPUT)
    monkeypatch.setattr(RECOVERY, "_load_recovery_report", lambda path, field: (actual, identity))
    returned, returned_identity, created = RECOVERY._publish_or_resume_incident(expected)
    assert returned == actual
    assert returned_identity == identity
    assert created is False
    mutated = json.loads(json.dumps(actual))
    mutated["exact_private_namespace_mountinfo"]["rows"][-1] += " changed"
    monkeypatch.setattr(RECOVERY, "_load_recovery_report", lambda path, field: (mutated, identity))
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
