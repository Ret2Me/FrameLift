from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "work/blind-phase-confirmatory-v2/recover_activation_mounts_v9.py"
)
SPEC = importlib.util.spec_from_file_location("recover_activation_mounts_v9", SOURCE)
assert SPEC and SPEC.loader
RECOVERY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RECOVERY
SPEC.loader.exec_module(RECOVERY)


def _state(stage: str):
    namespaces = {}
    for offset, (inode, rows) in enumerate(RECOVERY.EXPECTED_ROWS.items()):
        if stage == "initial":
            selected = rows
        elif stage == "candidate":
            selected = rows[:2]
        elif stage == "final":
            selected = rows[:1]
        else:  # pragma: no cover
            raise AssertionError(stage)
        unrelated = (
            f"{900 + offset} {800 + offset} 0:5 / /proc rw,nosuid,nodev,noexec,relatime "
            "- proc proc rw"
        )
        lines = tuple((*selected, unrelated))
        namespaces[inode] = RECOVERY.NamespaceState(
            inode=inode,
            representative_pid=1000 + offset,
            member_pids=(1000 + offset,),
            raw_lines=lines,
            records=RECOVERY.parse_mountinfo("\n".join(lines)),
        )
    return RECOVERY.ProcState(namespaces=namespaces)


def test_mountinfo_parser_preserves_exact_record_and_decodes_paths() -> None:
    line = "7 2 8:1 /a\\040b /x\\040y ro shared:9 master:1 - ext4 /dev/a rw"
    record = RECOVERY.parse_mountinfo(line)[0]
    assert record.raw == line
    assert record.root == "/a b"
    assert record.mount_point == "/x y"
    assert record.optional_fields == ("shared:9", "master:1")


def test_exact_initial_and_candidate_complete_topologies_pass() -> None:
    initial = _state("initial")
    RECOVERY.validate_topology(initial, "cleanup-candidate")
    candidate = _state("candidate")
    RECOVERY.validate_topology(candidate, "cleanup-project")
    assert initial.canonical_sha256() != candidate.canonical_sha256()


@pytest.mark.parametrize("mutation", ["extra_descendant", "wrong_option", "missing_namespace"])
def test_topology_mutations_fail_closed(mutation: str) -> None:
    state = _state("initial")
    namespaces = dict(state.namespaces)
    inode = RECOVERY.EXPECTED_NAMESPACE_INODES[0]
    namespace = namespaces[inode]
    lines = list(namespace.raw_lines)
    if mutation == "extra_descendant":
        lines.append(
            "777 123 252:0 / /home/ubuntu/telemetry-yield/releases/"
            "blind-phase-confirmatory-v2/runtime-r4/child ro - ext4 /dev/x rw"
        )
    elif mutation == "wrong_option":
        lines[1] = lines[1].replace("rw,relatime", "ro,relatime")
    else:
        namespaces.pop(inode)
        with pytest.raises(RECOVERY.RecoveryError):
            RECOVERY.validate_topology(RECOVERY.ProcState(namespaces), "cleanup-candidate")
        return
    namespaces[inode] = RECOVERY.NamespaceState(
        inode=inode,
        representative_pid=namespace.representative_pid,
        member_pids=namespace.member_pids,
        raw_lines=tuple(lines),
        records=RECOVERY.parse_mountinfo("\n".join(lines)),
    )
    with pytest.raises(RECOVERY.RecoveryError):
        RECOVERY.validate_topology(RECOVERY.ProcState(namespaces), "cleanup-candidate")


def test_unrelated_shared_group_elsewhere_does_not_expand_relevant_rows() -> None:
    state = _state("initial")
    namespaces = dict(state.namespaces)
    inode = RECOVERY.EXPECTED_NAMESPACE_INODES[0]
    namespace = namespaces[inode]
    extra = "778 29 0:9 / /media/x rw shared:1 - tmpfs tmpfs rw"
    lines = (*namespace.raw_lines, extra)
    namespaces[inode] = RECOVERY.NamespaceState(
        inode=inode,
        representative_pid=namespace.representative_pid,
        member_pids=namespace.member_pids,
        raw_lines=lines,
        records=RECOVERY.parse_mountinfo("\n".join(lines)),
    )
    RECOVERY.validate_topology(RECOVERY.ProcState(namespaces), "cleanup-candidate")


def test_postcondition_requires_exact_fanout_and_byte_stable_unrelated_rows() -> None:
    initial = _state("initial")
    candidate = _state("candidate")
    RECOVERY.validate_postcondition(initial, candidate, "cleanup-candidate")
    namespaces = dict(candidate.namespaces)
    inode = RECOVERY.EXPECTED_NAMESPACE_INODES[0]
    namespace = namespaces[inode]
    changed = list(namespace.raw_lines)
    changed[-1] = changed[-1].replace("rw", "ro", 1)
    namespaces[inode] = RECOVERY.NamespaceState(
        inode=inode,
        representative_pid=namespace.representative_pid,
        member_pids=namespace.member_pids,
        raw_lines=tuple(changed),
        records=RECOVERY.parse_mountinfo("\n".join(changed)),
    )
    with pytest.raises(RECOVERY.RecoveryError, match="unrelated change"):
        RECOVERY.validate_postcondition(
            initial, RECOVERY.ProcState(namespaces), "cleanup-candidate"
        )


def test_pinned_inventory_reads_every_fd_even_without_live_representative(
    tmp_path: Path,
) -> None:
    initial = _state("initial")
    pins = {inode: index + 10 for index, inode in enumerate(initial.namespaces)}
    seen = []

    def scanner(inode, descriptor, proc_fd):
        seen.append((inode, descriptor))
        return initial.namespaces[inode].raw_lines

    pinned = RECOVERY.capture_pinned_state(
        pins, RECOVERY.ProcState(namespaces={}), proc_root=tmp_path, scanner=scanner
    )
    assert pinned.canonical_sha256() == initial.canonical_sha256()
    assert len(seen) == len(pins)
    assert all(pinned.namespaces[inode].representative_pid == -1 for inode in pins)


def test_new_unknown_namespace_fails_before_pinned_scan(monkeypatch, tmp_path: Path) -> None:
    initial = _state("initial")
    unknown = dict(initial.namespaces)
    extra_line = "999 1 0:5 / / rw - tmpfs tmpfs rw"
    unknown[999999] = RECOVERY.NamespaceState(
        inode=999999,
        representative_pid=9,
        member_pids=(9,),
        raw_lines=(extra_line,),
        records=RECOVERY.parse_mountinfo(extra_line),
    )
    monkeypatch.setattr(
        RECOVERY, "capture_proc_state", lambda proc_root: RECOVERY.ProcState(unknown)
    )
    with pytest.raises(RECOVERY.RecoveryError, match="new or unknown"):
        RECOVERY._capture_pinned_and_live(
            {inode: index for index, inode in enumerate(initial.namespaces)}, tmp_path
        )


def test_umount_binary_requires_compiled_exact_identity(monkeypatch) -> None:
    metadata = RECOVERY.UMOUNT.lstat()
    assert metadata.st_uid == 0
    monkeypatch.setattr(
        RECOVERY,
        "_regular_identity",
        lambda path: dict(RECOVERY.EXPECTED_UMOUNT_IDENTITY, sha256="0" * 64),
    )
    with pytest.raises(RECOVERY.RecoveryError, match="frozen exact identity"):
        RECOVERY._validate_umount_binary()


def _fake_process(proc: Path, *, inode: int, target: str) -> None:
    process = proc / "100"
    (process / "ns").mkdir(parents=True)
    (process / "fd").mkdir()
    (process / "fdinfo").mkdir()
    tail = ["S", *("0" for _ in range(18)), "12345"]
    (process / "stat").write_text(
        "100 (reference scanner) " + " ".join(tail) + "\n", encoding="ascii"
    )
    os.symlink(f"mnt:[{inode}]", process / "ns/mnt")
    os.symlink(target, process / "cwd")
    os.symlink("/", process / "root")
    os.symlink("/usr/bin/test", process / "exe")
    (process / "maps").write_text(
        f"1-2 r--p 0 00:00 0 {target}/mapped.so\n", encoding="utf-8"
    )
    os.symlink(f"{target}/open", process / "fd/7")
    (process / "fdinfo/7").write_text("pos:\t0\nmnt_id:\t123\n", encoding="utf-8")


def test_reference_scan_covers_cwd_maps_and_fds(tmp_path: Path) -> None:
    inode = RECOVERY.EXPECTED_NAMESPACE_INODES[0]
    _fake_process(tmp_path, inode=inode, target=RECOVERY.CANDIDATE)
    initial = _state("initial")
    findings = RECOVERY.scan_process_references(RECOVERY.CANDIDATE, initial, tmp_path)
    assert {finding["kind"] for finding in findings} == {"cwd", "maps", "fd"}


def test_anonymous_mount_namespace_fd_holder_is_rejected(tmp_path: Path) -> None:
    process = tmp_path / "100"
    (process / "fd").mkdir(parents=True)
    os.symlink("mnt:[999]", process / "fd/4")
    findings = RECOVERY.scan_anonymous_namespace_handles(
        tmp_path, RECOVERY.ProcState(namespaces={})
    )
    assert findings == ("fd:100:4:mnt:[999]",)


def _install_run_mocks(
    monkeypatch, tmp_path: Path, captures, refs=None, returncodes=None, journal=None,
    command_streams=None,
):
    journal = {} if journal is None else journal
    identity = {
        "path": str(RECOVERY.CONTROL_COPY),
        "size_bytes": 123,
        "sha256": "a" * 64,
        "st_dev": 1,
        "st_ino": 2,
        "uid": 0,
        "gid": 0,
        "mode": "0555",
        "nlink": 1,
    }
    monkeypatch.setattr(RECOVERY.os, "geteuid", lambda: 0)
    monkeypatch.setattr(RECOVERY, "_assert_host_namespace", lambda proc_root: None)
    monkeypatch.setattr(RECOVERY, "_validate_control_parent", lambda: None)
    monkeypatch.setattr(RECOVERY, "_validate_umount_binary", lambda: None)
    monkeypatch.setattr(
        RECOVERY,
        "_validate_v3_refusal_incident",
        lambda: dict(RECOVERY.EXPECTED_V3_REFUSAL_INCIDENT_IDENTITY),
    )
    monkeypatch.setattr(
        RECOVERY,
        "_validate_v5_no_go_review",
        lambda: dict(RECOVERY.EXPECTED_V5_NO_GO_REVIEW_IDENTITY),
    )
    monkeypatch.setattr(
        RECOVERY,
        "_validate_v6_no_go_review",
        lambda: dict(RECOVERY.EXPECTED_V6_NO_GO_REVIEW_IDENTITY),
    )
    monkeypatch.setattr(
        RECOVERY,
        "_validate_v7_no_go_review",
        lambda: dict(RECOVERY.EXPECTED_V7_NO_GO_REVIEW_IDENTITY),
    )
    monkeypatch.setattr(
        RECOVERY,
        "_validate_v8_no_go_review",
        lambda: dict(RECOVERY.EXPECTED_V8_NO_GO_REVIEW_IDENTITY),
    )
    monkeypatch.setattr(
        RECOVERY,
        "_regular_identity",
        lambda path: (
            dict(RECOVERY.EXPECTED_UMOUNT_IDENTITY)
            if Path(path) == RECOVERY.UMOUNT
            else {
                "path": str(RECOVERY.LEGACY_V3_CONTROL_COPY),
                "size_bytes": RECOVERY.LEGACY_V3_SIZE,
                "sha256": RECOVERY.LEGACY_V3_SHA256,
                "st_dev": 1,
                "st_ino": 3,
                "uid": 0,
                "gid": 0,
                "mode": "0555",
                "nlink": 1,
            }
            if Path(path) == RECOVERY.LEGACY_V3_CONTROL_COPY
            else dict(identity, path=str(path))
        ),
    )
    monkeypatch.setattr(RECOVERY, "_targets_identity", lambda: {"stable": True})
    monkeypatch.setattr(RECOVERY, "capture_proc_state", lambda proc_root: captures[0])
    monkeypatch.setattr(RECOVERY, "scan_anonymous_namespace_handles", lambda *args: ())
    pins = {inode: index + 10 for index, inode in enumerate(captures[0].namespaces)}
    monkeypatch.setattr(RECOVERY, "open_namespace_pins", lambda *args: pins)
    monkeypatch.setattr(RECOVERY, "_close_pins", lambda pins: None)
    queue = list(captures[1:])

    def capture_pinned(pins_arg, proc_root):
        state = queue.pop(0)
        if isinstance(state, BaseException):
            raise state
        return state, state

    monkeypatch.setattr(RECOVERY, "_capture_pinned_and_live", capture_pinned)
    ref_queue = list(refs or [])

    def references(*args):
        return tuple(ref_queue.pop(0)) if ref_queue else ()

    monkeypatch.setattr(RECOVERY, "scan_process_references", references)

    def present(path):
        return str(path) in journal

    def publish(path, document):
        value = RECOVERY._self_hashed(document)
        encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        item_identity = {
            "path": str(path),
            "size_bytes": len(encoded),
            "sha256": __import__("hashlib").sha256(encoded).hexdigest(),
        }
        existing = journal.get(str(path))
        if existing and existing[0] != value:
            raise RECOVERY.RecoveryError("conflict")
        journal[str(path)] = (value, item_identity)
        return value, item_identity

    monkeypatch.setattr(RECOVERY, "_journal_present", present)
    monkeypatch.setattr(RECOVERY, "_publish_journal", publish)
    def publish_claim(path, document):
        existed = present(path)
        value, item_identity = publish(path, document)
        return value, item_identity, not existed

    monkeypatch.setattr(RECOVERY, "_publish_project_attempt_claim", publish_claim)
    monkeypatch.setattr(RECOVERY, "_load_journal", lambda path: journal[str(path)])
    command_calls = []
    codes = list(returncodes or [0, 0])
    streams = list(command_streams or [])

    def runner(argv, **kwargs):
        command_calls.append((argv, kwargs))
        stdout, stderr = streams.pop(0) if streams else (b"", b"")
        return subprocess.CompletedProcess(argv, codes.pop(0), stdout, stderr)

    return journal, command_calls, runner


def _put_journal(journal, path: Path, document):
    value = RECOVERY._self_hashed(document)
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    identity = {
        "path": str(path),
        "size_bytes": len(encoded),
        "sha256": __import__("hashlib").sha256(encoded).hexdigest(),
    }
    journal[str(path)] = (value, identity)
    return value, identity


def _seed_exact_v3_candidate_complete(journal, *, max_wait_seconds: int = 5):
    initial = _state("initial")
    legacy = {
        "path": str(RECOVERY.LEGACY_V3_CONTROL_COPY),
        "size_bytes": RECOVERY.LEGACY_V3_SIZE,
        "sha256": RECOVERY.LEGACY_V3_SHA256,
        "st_dev": 1,
        "st_ino": 3,
        "uid": 0,
        "gid": 0,
        "mode": "0555",
        "nlink": 1,
    }
    intent, intent_identity = _put_journal(journal, RECOVERY.INTENT_PATH, {
        "schema_version": "blind-phase-confirmatory-mount-recovery-intent-v3",
        "status": "committed_before_first_unmount",
        "runtime_recovery_runner": legacy,
        "umount_binary": dict(RECOVERY.EXPECTED_UMOUNT_IDENTITY),
        "host_namespace_inode": RECOVERY.HOST_NAMESPACE_INODE,
        "max_wait_seconds": max_wait_seconds,
        "target_identities": {"stable": True},
        "initial_mount_namespaces": initial.canonical_value(),
        "initial_mount_namespaces_sha256": initial.canonical_sha256(),
        "expected_counts": {
            "affected_namespaces": 10,
            "project_mounts": 10,
            "candidate_mounts": 20,
            "candidate_descendant_mounts": 0,
        },
        "policy": {
            "plain_umount_only": True,
            "force": False,
            "lazy": False,
            "recursive": False,
            "nsenter": False,
            "propagation_change": False,
            "remount": False,
            "iq_content_reads": False,
        },
    })
    intent_reference = RECOVERY._journal_reference(RECOVERY.INTENT_PATH, intent_identity)
    candidate = _state("candidate")
    candidate_document = {
        **RECOVERY._phase_base(
            schema="blind-phase-confirmatory-mount-recovery-candidate-phase-v3",
            status="PASS",
            intent_reference=intent_reference,
        ),
        "transition": "executed",
        "command": {
            "argv": [str(RECOVERY.UMOUNT), "--no-canonicalize", RECOVERY.CANDIDATE],
            "exit_code": 0,
            "stdout_size_bytes": 0,
            "stdout_sha256": __import__("hashlib").sha256(b"").hexdigest(),
            "stderr_size_bytes": 0,
            "stderr_sha256": __import__("hashlib").sha256(b"").hexdigest(),
        },
        "before_mount_namespaces_sha256": initial.canonical_sha256(),
        "after_mount_namespaces": candidate.canonical_value(),
        "after_mount_namespaces_sha256": candidate.canonical_sha256(),
        "expected_candidate_replicas_removed": 20,
        "unrelated_mountinfo_records_byte_stable": True,
    }
    candidate_value, candidate_identity = _put_journal(
        journal, RECOVERY.CANDIDATE_PHASE_PATH, candidate_document
    )
    return intent, intent_identity, candidate_value, candidate_identity


def _seed_exact_v3_ready(journal, *, max_wait_seconds: int = 5):
    intent, intent_identity, candidate, candidate_identity = (
        _seed_exact_v3_candidate_complete(
            journal, max_wait_seconds=max_wait_seconds
        )
    )
    intent_reference = RECOVERY._journal_reference(
        RECOVERY.INTENT_PATH, intent_identity
    )
    candidate_reference = RECOVERY._journal_reference(
        RECOVERY.CANDIDATE_PHASE_PATH, candidate_identity
    )
    state = _state("candidate")
    ready = {
        **RECOVERY._phase_base(
            schema="blind-phase-confirmatory-mount-recovery-project-ready-v3",
            status="PASS",
            intent_reference=intent_reference,
        ),
        "candidate_phase": candidate_reference,
        "mount_namespaces": state.canonical_value(),
        "mount_namespaces_sha256": state.canonical_sha256(),
        "project_reference_count": 0,
        "poll_count": 1,
        "target_identities": {"stable": True},
    }
    ready_value, ready_identity = _put_journal(
        journal, RECOVERY.PROJECT_READY_PATH, ready
    )
    return (
        intent,
        intent_identity,
        candidate,
        candidate_identity,
        ready_value,
        ready_identity,
    )


@pytest.mark.skip(reason="historical v4 candidate-transition scenario is forbidden in v5")
def historical_v4_recover_all_holds_one_pin_set_and_runs_only_two_plain_umounts(
    monkeypatch, tmp_path: Path
) -> None:
    initial, candidate, final = _state("initial"), _state("candidate"), _state("final")
    captures = [initial, initial, candidate, candidate, candidate, final]
    journal, calls, runner = _install_run_mocks(monkeypatch, tmp_path, captures)
    result = RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=runner,
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
    )
    assert [call[0] for call in calls] == [
        ["/usr/bin/umount", "--no-canonicalize", RECOVERY.CANDIDATE],
        ["/usr/bin/umount", "--no-canonicalize", RECOVERY.PROJECT],
    ]
    assert all(call[1]["cwd"] == "/" for call in calls)
    assert result["status"] == "PASS"
    assert result["namespace_inventory"]["all_live_mount_namespaces_clear"] is True
    assert set(journal) == {
        str(RECOVERY.INTENT_PATH),
        str(RECOVERY.CANDIDATE_PHASE_PATH),
        str(RECOVERY.PROJECT_READY_PATH),
        str(RECOVERY.PROJECT_PHASE_PATH),
    }


@pytest.mark.skip(reason="historical v4 candidate-transition scenario is forbidden in v5")
def historical_v4_candidate_reference_blocks_before_any_command(monkeypatch, tmp_path: Path) -> None:
    initial = _state("initial")
    captures = [initial, initial]
    _, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, captures, refs=[[{"kind": "fd"}]]
    )
    with pytest.raises(RECOVERY.RecoveryError, match="references candidate"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=0,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []


@pytest.mark.skip(reason="historical v4 candidate-transition scenario is forbidden in v5")
def historical_v4_nonzero_candidate_unmount_stops_without_project_attempt(
    monkeypatch, tmp_path: Path
) -> None:
    initial = _state("initial")
    captures = [initial, initial]
    _, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, captures, returncodes=[32]
    )
    with pytest.raises(RECOVERY.RecoveryError, match="no further action"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=0,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert len(calls) == 1


@pytest.mark.skip(reason="historical v4 candidate-transition scenario is forbidden in v5")
def historical_v4_wait_is_bounded_and_never_attempts_project_with_live_reference(
    monkeypatch, tmp_path: Path
) -> None:
    initial, candidate = _state("initial"), _state("candidate")
    captures = [initial, initial, candidate, candidate]
    _, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        captures,
        refs=[(), ({"kind": "cwd"},)],
    )
    ticks = iter((0.0, 0.0))
    with pytest.raises(RECOVERY.RecoveryError, match="wait expired"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=0,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: next(ticks),
        )
    assert len(calls) == 1


@pytest.mark.skip(reason="historical v4 candidate-transition scenario is forbidden in v5")
def historical_v4_existing_ready_journal_survives_reappearing_reference_and_retry(
    monkeypatch, tmp_path: Path
) -> None:
    initial, candidate, final = _state("initial"), _state("candidate"), _state("final")
    captures = [initial, initial, candidate, candidate, candidate]
    journal, first_calls, first_runner = _install_run_mocks(
        monkeypatch, tmp_path, captures, returncodes=[0, 32]
    )
    with pytest.raises(RECOVERY.RecoveryError, match="no further action"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=first_runner,
            monotonic=lambda: 0.0,
        )
    assert len(first_calls) == 2
    original_ready = journal[str(RECOVERY.PROJECT_READY_PATH)][0]

    captures = [candidate, candidate, candidate, candidate, candidate, final]
    journal, second_calls, second_runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        captures,
        refs=[({"kind": "fd"},), ()],
        returncodes=[0],
        journal=journal,
    )
    ticks = iter((0.0, 0.0, 0.1))
    result = RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=second_runner,
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )
    assert len(second_calls) == 1
    assert second_calls[0][0][-1] == RECOVERY.PROJECT
    assert journal[str(RECOVERY.PROJECT_READY_PATH)][0] == original_ready
    assert result["status"] == "PASS"


@pytest.mark.skip(reason="historical v4 candidate-transition scenario is forbidden in v5")
def historical_v4_crash_after_project_umount_before_phase_is_inferred_without_command(
    monkeypatch, tmp_path: Path
) -> None:
    initial, candidate, final = _state("initial"), _state("candidate"), _state("final")
    captures = [initial, initial, candidate, candidate, candidate]
    journal, _, first_runner = _install_run_mocks(
        monkeypatch, tmp_path, captures, returncodes=[0, 32]
    )
    with pytest.raises(RECOVERY.RecoveryError):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=first_runner,
            monotonic=lambda: 0.0,
        )
    # Simulate that the plain project umount actually completed after its
    # ready journal, but the process died before publishing project-complete.
    captures = [final, final, final]
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, captures, journal=journal
    )
    result = RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=runner,
    )
    assert calls == []
    assert result["transition"] == "recovered"
    assert result["command"]["status"] == "exact_success_inferred_after_unjournaled_crash"


@pytest.mark.parametrize(
    ("journal_name", "field", "value"),
    [
        ("candidate", "status", "NOPE"),
        ("candidate", "scientific_exposure", {}),
        ("ready", "project_reference_count", 1),
        ("ready", "status", "NOPE"),
    ],
)
@pytest.mark.skip(reason="historical v4 candidate-transition scenario is forbidden in v5")
def historical_v4_existing_phase_journal_semantics_are_strict(
    monkeypatch, tmp_path: Path, journal_name: str, field: str, value
) -> None:
    initial, candidate = _state("initial"), _state("candidate")
    captures = [initial, initial, candidate, candidate, candidate]
    journal, _, runner = _install_run_mocks(
        monkeypatch, tmp_path, captures, returncodes=[0, 32]
    )
    with pytest.raises(RECOVERY.RecoveryError):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    path = (
        RECOVERY.CANDIDATE_PHASE_PATH
        if journal_name == "candidate"
        else RECOVERY.PROJECT_READY_PATH
    )
    document, identity = journal[str(path)]
    tampered = dict(document)
    tampered[field] = value
    tampered = RECOVERY._self_hashed(tampered)
    journal[str(path)] = (tampered, identity)
    captures = [candidate, candidate, candidate]
    _install_run_mocks(monkeypatch, tmp_path, captures, journal=journal)
    with pytest.raises(RECOVERY.RecoveryError):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
        )


def test_v5_project_only_runner_can_invoke_only_project_umount(
    monkeypatch, tmp_path: Path
) -> None:
    candidate, final = _state("candidate"), _state("final")
    captures = [candidate, candidate, candidate, candidate, final]
    journal, calls, runner = _install_run_mocks(monkeypatch, tmp_path, captures)
    _seed_exact_v3_candidate_complete(journal)
    result = RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=runner,
        sleep=lambda _: None,
        monotonic=lambda: 0.0,
    )
    assert result["status"] == "PASS"
    assert [call[0] for call in calls] == [
        [str(RECOVERY.UMOUNT), "--no-canonicalize", RECOVERY.PROJECT]
    ]
    assert all(RECOVERY.CANDIDATE not in call[0] for call in calls)
    assert str(RECOVERY.V9_RESUME_PATH) in journal


def test_v5_missing_v3_candidate_journal_fails_before_command(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [candidate, candidate]
    )
    with pytest.raises(RECOVERY.RecoveryError, match="project-only"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert set(journal) == {str(RECOVERY.V9_FAILURE_PATH)}
    assert calls == []
    journal, retry_calls, retry_runner = _install_run_mocks(
        monkeypatch, tmp_path, [candidate], journal=journal
    )
    with pytest.raises(RECOVERY.RecoveryError, match="failure lockout already exists"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=retry_runner,
        )
    assert retry_calls == []


def test_v5_initial_topology_cannot_repeat_candidate_umount(
    monkeypatch, tmp_path: Path
) -> None:
    initial = _state("initial")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [initial, initial]
    )
    _seed_exact_v3_candidate_complete(journal)
    with pytest.raises(RECOVERY.RecoveryError, match="project-pending topology"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []


def test_v5_static_source_has_no_candidate_umount_call() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "_run_plain_umount(CANDIDATE" not in source


def test_v6_existing_ready_and_candidate_refuses_automatic_retry(
    monkeypatch, tmp_path: Path
) -> None:
    candidate, final = _state("candidate"), _state("final")
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, final],
    )
    _seed_exact_v3_ready(journal)
    with pytest.raises(RECOVERY.RecoveryError, match="automatic retry is forbidden"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert calls == []


def test_v6_nonzero_first_attempt_cannot_be_retried(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, first_calls, first_runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, candidate],
        returncodes=[32],
    )
    _seed_exact_v3_candidate_complete(journal)
    with pytest.raises(RECOVERY.RecoveryError, match="no further action"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=first_runner,
            monotonic=lambda: 0.0,
        )
    assert len(first_calls) == 1
    assert first_calls[0][0][-1] == RECOVERY.PROJECT
    assert str(RECOVERY.PROJECT_READY_PATH) in journal

    journal, retry_calls, retry_runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate],
        journal=journal,
    )
    with pytest.raises(RECOVERY.RecoveryError, match="failure lockout already exists"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=retry_runner,
            monotonic=lambda: 0.0,
        )
    assert retry_calls == []


def test_v7_nonzero_binary_streams_survive_losslessly_in_durable_lockout(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    stdout = b"\x00out\xff\n"
    stderr = b"\x80err\x00\xfe"
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, candidate],
        returncodes=[17],
        command_streams=[(stdout, stderr)],
    )
    _seed_exact_v3_candidate_complete(journal)
    with pytest.raises(RECOVERY.RecoveryCommandFailure, match="exit code 17"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert len(calls) == 1
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]
    receipt = failure["command_failure"]["receipt"]
    assert __import__("base64").b64decode(receipt["stdout_base64"]) == stdout
    assert __import__("base64").b64decode(receipt["stderr_base64"]) == stderr
    assert receipt["stdout_size_bytes"] == len(stdout)
    assert receipt["stderr_size_bytes"] == len(stderr)
    assert receipt["stdout_sha256"] == __import__("hashlib").sha256(stdout).hexdigest()
    assert receipt["stderr_sha256"] == __import__("hashlib").sha256(stderr).hexdigest()
    assert failure["project_attempt_claim"]["validated_self_hashed"] is True
    assert failure["command_failure"]["before_mount_namespaces_sha256"] == candidate.canonical_sha256()
    assert failure["command_failure"]["after_mount_namespaces_sha256"] == candidate.canonical_sha256()


def _partial_project_fanout_state():
    final = _state("final")
    candidate = _state("candidate")
    namespaces = dict(final.namespaces)
    inode = RECOVERY.EXPECTED_NAMESPACE_INODES[0]
    namespaces[inode] = candidate.namespaces[inode]
    return RECOVERY.ProcState(namespaces)


def _assert_v9_second_invocation_locked(
    monkeypatch, tmp_path: Path, journal, state
) -> None:
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [state], journal=journal
    )
    with pytest.raises(RECOVERY.RecoveryError, match="failure lockout already exists"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []


def test_v9_exit0_partial_fanout_preserves_receipt_and_locks_retry(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    partial = _partial_project_fanout_state()
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, partial],
        returncodes=[0],
        command_streams=[(b"partial-out", b"partial-err")],
    )
    _seed_exact_v3_candidate_complete(journal)
    with pytest.raises(RECOVERY.RecoveryCommandFailure, match="postcondition_validation"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert len(calls) == 1
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]["command_failure"]
    assert failure["receipt"]["exit_code"] == 0
    assert failure["post_command_error"]["stage"] == "postcondition_validation"
    assert failure["before_mount_namespaces_sha256"] == candidate.canonical_sha256()
    assert failure["after_mount_namespaces_sha256"] == partial.canonical_sha256()
    _assert_v9_second_invocation_locked(monkeypatch, tmp_path, journal, partial)


def test_v9_exit0_post_capture_exception_preserves_receipt_and_locks_retry(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [
            candidate,
            candidate,
            candidate,
            candidate,
            RECOVERY.RecoveryError("post-capture exploded"),
        ],
        returncodes=[0],
        command_streams=[(b"ok-out", b"ok-err")],
    )
    _seed_exact_v3_candidate_complete(journal)
    with pytest.raises(RECOVERY.RecoveryCommandFailure, match="post_command_capture"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert len(calls) == 1
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]["command_failure"]
    assert failure["receipt"]["exit_code"] == 0
    assert failure["post_command_error"] == {
        "stage": "post_command_capture",
        "type": "RecoveryError",
        "message": "post-capture exploded",
    }
    assert failure["after_mount_namespaces"] is None
    _assert_v9_second_invocation_locked(monkeypatch, tmp_path, journal, candidate)


def test_v9_exit0_target_identity_drift_preserves_receipt_and_locks_retry(
    monkeypatch, tmp_path: Path
) -> None:
    candidate, final = _state("candidate"), _state("final")
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, final],
        returncodes=[0],
    )
    _seed_exact_v3_candidate_complete(journal)
    identities = iter(({"stable": True}, {"stable": True}, {"drift": True}))
    monkeypatch.setattr(RECOVERY, "_targets_identity", lambda: next(identities))
    with pytest.raises(RECOVERY.RecoveryCommandFailure, match="target_identity_recheck"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert len(calls) == 1
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]["command_failure"]
    assert failure["receipt"]["exit_code"] == 0
    assert failure["post_command_error"]["stage"] == "target_identity_recheck"
    _assert_v9_second_invocation_locked(monkeypatch, tmp_path, journal, final)


def test_v9_exit0_completion_publication_error_preserves_receipt_and_locks_retry(
    monkeypatch, tmp_path: Path
) -> None:
    candidate, final = _state("candidate"), _state("final")
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, final],
        returncodes=[0],
    )
    _seed_exact_v3_candidate_complete(journal)
    original_publish = RECOVERY._publish_journal

    def fail_completion(path, document):
        if path == RECOVERY.PROJECT_PHASE_PATH:
            raise OSError("completion publication exploded")
        return original_publish(path, document)

    monkeypatch.setattr(RECOVERY, "_publish_journal", fail_completion)
    with pytest.raises(RECOVERY.RecoveryCommandFailure, match="completion_publication"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert len(calls) == 1
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]["command_failure"]
    assert failure["receipt"]["exit_code"] == 0
    assert failure["post_command_error"] == {
        "stage": "completion_publication",
        "type": "OSError",
        "message": "completion publication exploded",
    }
    _assert_v9_second_invocation_locked(monkeypatch, tmp_path, journal, final)


def test_v9_exit0_complete_then_pin_close_failure_retains_receipt_and_completion(
    monkeypatch, tmp_path: Path
) -> None:
    candidate, final = _state("candidate"), _state("final")
    stdout, stderr = b"complete-before-close\x00", b"close-will-fail\xff"
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, final],
        returncodes=[0],
        command_streams=[(stdout, stderr)],
    )
    _seed_exact_v3_candidate_complete(journal)

    def close_failure(pins):
        raise OSError("namespace pin close exploded after completion")

    monkeypatch.setattr(RECOVERY, "_close_pins", close_failure)
    with pytest.raises(RECOVERY.RecoveryCommandFailure, match="namespace_pin_close"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert len(calls) == 1
    assert str(RECOVERY.PROJECT_PHASE_PATH) in journal
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]["command_failure"]
    assert failure["post_command_error"] == {
        "stage": "namespace_pin_close",
        "type": "OSError",
        "message": "namespace pin close exploded after completion",
    }
    assert failure["supplemental_errors"] == []
    assert failure["completion_evidence"] == {
        "path": str(RECOVERY.PROJECT_PHASE_PATH),
        "size_bytes": journal[str(RECOVERY.PROJECT_PHASE_PATH)][1]["size_bytes"],
        "sha256": journal[str(RECOVERY.PROJECT_PHASE_PATH)][1]["sha256"],
    }
    assert __import__("base64").b64decode(failure["receipt"]["stdout_base64"]) == stdout
    assert __import__("base64").b64decode(failure["receipt"]["stderr_base64"]) == stderr
    _assert_v9_second_invocation_locked(monkeypatch, tmp_path, journal, final)


def test_v9_postcapture_primary_plus_pin_close_failure_preserves_both_errors(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [
            candidate,
            candidate,
            candidate,
            candidate,
            RECOVERY.RecoveryError("post-capture primary exploded"),
        ],
        returncodes=[0],
        command_streams=[(b"issued-out", b"issued-err")],
    )
    _seed_exact_v3_candidate_complete(journal)

    def close_failure(pins):
        raise OSError("namespace pin close secondary exploded")

    monkeypatch.setattr(RECOVERY, "_close_pins", close_failure)
    with pytest.raises(RECOVERY.RecoveryCommandFailure, match="post_command_capture"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert len(calls) == 1
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]["command_failure"]
    assert failure["post_command_error"] == {
        "stage": "post_command_capture",
        "type": "RecoveryError",
        "message": "post-capture primary exploded",
    }
    assert failure["supplemental_errors"] == [
        {
            "stage": "namespace_pin_close",
            "type": "OSError",
            "message": "namespace pin close secondary exploded",
        }
    ]
    assert failure["after_mount_namespaces"] is None
    assert failure["receipt"]["exit_code"] == 0
    _assert_v9_second_invocation_locked(monkeypatch, tmp_path, journal, candidate)


def test_v9_precommand_primary_plus_pin_close_failure_preserves_both_without_receipt(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [candidate, candidate]
    )

    def close_failure(pins):
        raise OSError("pre-command pin close exploded")

    monkeypatch.setattr(RECOVERY, "_close_pins", close_failure)
    with pytest.raises(RECOVERY.RecoveryCleanupFailure, match="primary failure"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []
    failure = journal[str(RECOVERY.V9_FAILURE_PATH)][0]
    assert failure["command_failure"] is None
    assert failure["cleanup_failure"] == {
        "primary_error": {
            "type": "RecoveryError",
            "message": (
                "v9 is project-only and requires exact durable v3 intent "
                "and candidate-complete journals"
            ),
        },
        "cleanup_error": {
            "type": "OSError",
            "message": "pre-command pin close exploded",
        },
    }
    _assert_v9_second_invocation_locked(monkeypatch, tmp_path, journal, candidate)


def test_v9_normal_success_closes_pins_once_without_failure_lockout(
    monkeypatch, tmp_path: Path
) -> None:
    candidate, final = _state("candidate"), _state("final")
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, final],
        returncodes=[0],
    )
    _seed_exact_v3_candidate_complete(journal)
    closes = []
    monkeypatch.setattr(RECOVERY, "_close_pins", lambda pins: closes.append(dict(pins)))
    result = RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=runner,
        monotonic=lambda: 0.0,
    )
    assert result["status"] == "PASS"
    assert len(calls) == 1
    assert len(closes) == 1
    assert str(RECOVERY.V9_FAILURE_PATH) not in journal


def test_v7_failure_marker_publication_collision_still_locks_out(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [candidate, candidate]
    )
    original_publish = RECOVERY._publish_journal

    def collide(path, document):
        if path == RECOVERY.V9_FAILURE_PATH:
            competing = dict(document)
            competing["error"] = {
                "type": "ConcurrentRefusal",
                "message": "independent fail-closed refusal",
            }
            original_publish(path, competing)
            raise RECOVERY.RecoveryError("failure marker publication collision")
        return original_publish(path, document)

    monkeypatch.setattr(RECOVERY, "_publish_journal", collide)
    with pytest.raises(RECOVERY.RecoveryError, match="publication collision"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []
    assert str(RECOVERY.V9_FAILURE_PATH) in journal


def test_v6_ready_publication_collision_loser_never_calls_umount(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [candidate, candidate, candidate, candidate]
    )
    _seed_exact_v3_candidate_complete(journal)

    def collision(path, document):
        value, identity = _put_journal(journal, path, document)
        return value, identity, False

    monkeypatch.setattr(RECOVERY, "_publish_project_attempt_claim", collision)
    with pytest.raises(RECOVERY.RecoveryError, match="automatic retry is forbidden"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: 0.0,
        )
    assert calls == []


def test_v5_crash_after_project_syscall_is_inferred_without_command(
    monkeypatch, tmp_path: Path
) -> None:
    final = _state("final")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [final, final, final]
    )
    _seed_exact_v3_ready(journal)
    result = RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=runner,
    )
    assert calls == []
    assert result["transition"] == "recovered"
    assert result["command"]["status"] == "exact_success_inferred_after_unjournaled_crash"


def test_v5_existing_project_complete_and_final_is_idempotent_no_command(
    monkeypatch, tmp_path: Path
) -> None:
    candidate, final = _state("candidate"), _state("final")
    journal, first_calls, first_runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate, candidate, final],
    )
    _seed_exact_v3_candidate_complete(journal)
    RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=first_runner,
        monotonic=lambda: 0.0,
    )
    assert len(first_calls) == 1
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [final, final], journal=journal
    )
    result = RECOVERY.run_recover_all(
        expected_script_sha256="a" * 64,
        max_wait_seconds=5,
        proc_root=tmp_path,
        command_runner=runner,
    )
    assert result["status"] == "PASS"
    assert calls == []


def test_v5_final_topology_without_ready_is_rejected_no_command(
    monkeypatch, tmp_path: Path
) -> None:
    final = _state("final")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [final, final]
    )
    _seed_exact_v3_candidate_complete(journal)
    with pytest.raises(RECOVERY.RecoveryError, match="without durable project-ready"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []


def test_v5_partial_topology_is_rejected_no_command(monkeypatch, tmp_path: Path) -> None:
    candidate = _state("candidate")
    namespaces = dict(candidate.namespaces)
    inode = RECOVERY.EXPECTED_NAMESPACE_INODES[0]
    old = namespaces[inode]
    lines = tuple(line for line in old.raw_lines if RECOVERY.PROJECT not in line)
    namespaces[inode] = RECOVERY.NamespaceState(
        inode=inode,
        representative_pid=old.representative_pid,
        member_pids=old.member_pids,
        raw_lines=lines,
        records=RECOVERY.parse_mountinfo("\n".join(lines)),
    )
    partial = RECOVERY.ProcState(namespaces)
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [partial, partial]
    )
    _seed_exact_v3_candidate_complete(journal)
    with pytest.raises(RECOVERY.RecoveryError, match="project-pending topology"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []


def test_v5_reappearing_reference_after_ready_blocks_project_call(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch,
        tmp_path,
        [candidate, candidate, candidate],
        refs=[({"kind": "fd"},)],
    )
    _seed_exact_v3_ready(journal, max_wait_seconds=0)
    ticks = iter((0.0, 0.0))
    with pytest.raises(RECOVERY.RecoveryError, match="wait expired"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=0,
            proc_root=tmp_path,
            command_runner=runner,
            monotonic=lambda: next(ticks),
        )
    assert calls == []


@pytest.mark.parametrize(
    ("journal_path", "field", "value"),
    [
        (RECOVERY.INTENT_PATH, "status", "tampered"),
        (RECOVERY.CANDIDATE_PHASE_PATH, "status", "tampered"),
        (RECOVERY.CANDIDATE_PHASE_PATH, "scientific_exposure", {}),
    ],
)
def test_v5_tampered_v3_intent_or_candidate_binding_fails_no_command(
    monkeypatch, tmp_path: Path, journal_path: Path, field: str, value
) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [candidate, candidate]
    )
    _seed_exact_v3_candidate_complete(journal)
    document, identity = journal[str(journal_path)]
    tampered = dict(document)
    tampered[field] = value
    journal[str(journal_path)] = (RECOVERY._self_hashed(tampered), identity)
    with pytest.raises(RECOVERY.RecoveryError):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []


def test_v5_refusal_incident_drift_fails_before_command(monkeypatch, tmp_path: Path) -> None:
    candidate = _state("candidate")
    journal, calls, runner = _install_run_mocks(
        monkeypatch, tmp_path, [candidate, candidate]
    )
    _seed_exact_v3_candidate_complete(journal)

    def drift():
        raise RECOVERY.RecoveryError("v3 refusal incident exact identity drift")

    monkeypatch.setattr(RECOVERY, "_validate_v3_refusal_incident", drift)
    with pytest.raises(RECOVERY.RecoveryError, match="refusal incident"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=5,
            proc_root=tmp_path,
            command_runner=runner,
        )
    assert calls == []


def test_wrong_control_hash_fails_before_inventory_or_command(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(RECOVERY.os, "geteuid", lambda: 0)
    monkeypatch.setattr(RECOVERY, "_assert_host_namespace", lambda proc_root: None)
    monkeypatch.setattr(RECOVERY, "_validate_control_parent", lambda: None)
    monkeypatch.setattr(RECOVERY, "_validate_umount_binary", lambda: None)
    monkeypatch.setattr(RECOVERY, "_targets_identity", lambda: {})
    monkeypatch.setattr(
        RECOVERY,
        "_regular_identity",
        lambda path: {"path": str(path), "sha256": "b" * 64},
    )
    with pytest.raises(RECOVERY.RecoveryError, match="expected SHA"):
        RECOVERY.run_recover_all(
            expected_script_sha256="a" * 64,
            max_wait_seconds=0,
            proc_root=tmp_path,
        )


def test_target_symlinked_ancestor_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(RECOVERY.RecoveryError, match="symlinked or noncanonical"):
        RECOVERY._directory_ancestry_identity(alias)


def test_cli_exposes_only_atomic_recover_all() -> None:
    parser = RECOVERY._parser()
    args = parser.parse_args(
        ["--expected-script-sha256", "a" * 64, "--max-wait-seconds", "60", "recover-all"]
    )
    assert args.operation == "recover-all"
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["--expected-script-sha256", "a" * 64, "--max-wait-seconds", "60", "cleanup-project"]
        )


def _fake_proc_pid(proc_root: Path, pid: int, inode: int, starttime: int) -> Path:
    pid_root = proc_root / str(pid)
    (pid_root / "ns").mkdir(parents=True)
    (pid_root / "ns/mnt").symlink_to(f"mnt:[{inode}]")
    # Text after the closing comm parenthesis starts at field 3; index 19 is
    # Linux /proc/PID/stat field 22 (starttime).
    tail = ["S", *("0" for _ in range(18)), str(starttime)]
    (pid_root / "stat").write_text(
        f"{pid} (worker with spaces) " + " ".join(tail) + "\n",
        encoding="ascii",
    )
    (pid_root / "mountinfo").write_text(
        "1 0 0:1 / / rw - tmpfs tmpfs rw\n", encoding="utf-8"
    )
    return pid_root


def test_capture_skips_pid_whose_namespace_link_vanishes_while_pid_dir_remains(
    tmp_path: Path,
) -> None:
    stable = _fake_proc_pid(tmp_path, 101, 9001, 111)
    vanished = _fake_proc_pid(tmp_path, 102, 9002, 222)
    (vanished / "ns/mnt").unlink()
    state = RECOVERY.capture_proc_state(tmp_path)
    assert set(state.namespaces) == {9001}
    assert state.namespaces[9001].representative_pid == 101
    assert stable.exists() and vanished.exists()


def test_unstable_reused_pid_generation_is_skipped(monkeypatch, tmp_path: Path) -> None:
    pid_root = tmp_path / "103"
    pid_root.mkdir()
    starts = iter(range(1, RECOVERY.PROC_IDENTITY_ATTEMPTS * 2 + 1))
    monkeypatch.setattr(RECOVERY, "_proc_starttime", lambda path: next(starts))
    monkeypatch.setattr(RECOVERY, "_namespace_inode", lambda path: 9003)
    assert RECOVERY._stable_pid_namespace(pid_root) is None


def test_persistent_proc_permission_error_fails_closed(monkeypatch, tmp_path: Path) -> None:
    pid_root = tmp_path / "104"
    pid_root.mkdir()
    monkeypatch.setattr(RECOVERY, "_proc_starttime", lambda path: 444)

    def denied(path):
        raise RECOVERY.RecoveryError("denied") from PermissionError(13, "denied")

    monkeypatch.setattr(RECOVERY, "_namespace_inode", denied)
    with pytest.raises(RECOVERY.RecoveryError, match="denied"):
        RECOVERY._stable_pid_namespace(pid_root)


def test_stable_pin_acquisition_retries_lost_representative(monkeypatch, tmp_path: Path) -> None:
    first, second = _state("candidate"), _state("candidate")
    captures = iter((first, second))
    monkeypatch.setattr(RECOVERY, "capture_proc_state", lambda root: next(captures))
    monkeypatch.setattr(RECOVERY, "scan_anonymous_namespace_handles", lambda root, state: ())
    attempts = iter((RECOVERY.ProcSnapshotRace("gone"), {1: 10}))

    def pin(state, root):
        value = next(attempts)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(RECOVERY, "open_namespace_pins", pin)
    monkeypatch.setattr(
        RECOVERY,
        "_capture_pinned_and_live",
        lambda pins, root: (second, second),
    )
    monkeypatch.setattr(RECOVERY, "_close_pins", lambda pins: None)
    captured, pins, pinned, live = RECOVERY._capture_and_pin_stable(tmp_path)
    assert captured == second
    assert pins == {1: 10}
    assert pinned == second == live


def test_stable_pin_acquisition_preserves_scan_primary_when_close_also_fails(
    monkeypatch, tmp_path: Path
) -> None:
    candidate = _state("candidate")
    monkeypatch.setattr(RECOVERY, "capture_proc_state", lambda root: candidate)
    monkeypatch.setattr(
        RECOVERY, "scan_anonymous_namespace_handles", lambda root, state: ()
    )
    monkeypatch.setattr(RECOVERY, "open_namespace_pins", lambda state, root: {1: 10})

    def scan_failure(pins, root):
        raise RECOVERY.RecoveryError("pinned scan primary exploded")

    def close_failure(pins):
        raise OSError("stable-acquisition close exploded")

    monkeypatch.setattr(RECOVERY, "_capture_pinned_and_live", scan_failure)
    monkeypatch.setattr(RECOVERY, "_close_pins", close_failure)
    with pytest.raises(RECOVERY.RecoveryCleanupFailure) as raised:
        RECOVERY._capture_and_pin_stable(tmp_path)
    assert type(raised.value.primary) is RECOVERY.RecoveryError
    assert str(raised.value.primary) == "pinned scan primary exploded"
    assert type(raised.value.cleanup) is OSError
    assert str(raised.value.cleanup) == "stable-acquisition close exploded"
