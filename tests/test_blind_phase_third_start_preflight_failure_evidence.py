from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest


SOURCE = Path(__file__).parents[1] / "work/blind-phase-confirmatory-v2/build_third_start_preflight_failure_evidence.py"
SPEC = importlib.util.spec_from_file_location("third_start_preflight_evidence", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def canonical_sha(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def conflict_documents():
    current = {"path": MODULE.CONFLICT_PATH, "size_bytes": 369199, "sha256": MODULE.BUILDER_SHA256}
    stale = {"path": MODULE.CONFLICT_PATH, "size_bytes": 357637, "sha256": MODULE.STALE_BUILDER_SHA256}
    return {
        "plan": {}, "acquisition": {}, "source": {},
        "amendment": {
            "amended_implementation": {
                "runtime_guard_builder": current,
                "runtime_guard_builder_v3": current,
                "transitive_code_dependencies": {"runtime_guard_builder_v3": stale},
            },
            "transitive_code_dependency_contract": {"dependencies": {"runtime_guard_builder_v3": stale}},
            "planned_artifact_mirror_contract": {
                "ignored": [current, {**current, "sha256": "0" * 64}],
            },
        },
    }


def test_exact_conflict_reconstruction_and_mirror_exclusion():
    result = MODULE._validate_conflict(conflict_documents())
    selectors = list(MODULE.CURRENT_SELECTORS + MODULE.STALE_SELECTORS)
    assert result == {
        "conflict_path": {"path": MODULE.CONFLICT_PATH, "selector_count": 4, "selectors": selectors},
        "conflicting_fields": selectors,
        "current_builder_identity": {"path": MODULE.CONFLICT_PATH, "size_bytes": 369199, "sha256": MODULE.BUILDER_SHA256},
        "stale_builder_identity": {"path": MODULE.CONFLICT_PATH, "size_bytes": 357637, "sha256": MODULE.STALE_BUILDER_SHA256},
    }


@pytest.mark.parametrize("mutation", ["remove", "third", "stale_selector"])
def test_conflict_set_is_fail_closed(mutation):
    documents = conflict_documents()
    if mutation == "remove":
        documents["amendment"]["transitive_code_dependency_contract"]["dependencies"]["runtime_guard_builder_v3"] = documents["amendment"]["amended_implementation"]["runtime_guard_builder"]
    elif mutation == "third":
        documents["amendment"]["extra"] = {"path": "/tmp/other", "size_bytes": 1, "sha256": "1" * 64}
        documents["amendment"]["more"] = {"path": "/tmp/other", "size_bytes": 2, "sha256": "2" * 64}
    else:
        documents["amendment"]["transitive_code_dependency_contract"]["dependencies"] = {"renamed": documents["amendment"]["transitive_code_dependency_contract"]["dependencies"]["runtime_guard_builder_v3"]}
    with pytest.raises(MODULE.EvidenceError):
        MODULE._validate_conflict(documents)


def test_exact_builder_ast_proves_pre_intent_metadata_only_path():
    payload = SOURCE.with_name("build_runtime_guard_v3.py").read_bytes()
    proof = MODULE._validate_control_flow(payload)
    assert proof["status"] == "PASS"
    assert proof["failure_precedes_activation_intent"] is True
    assert proof["failure_precedes_unshare"] is True
    assert proof["failure_precedes_iq_access"] is True
    assert proof["frozen_iq_identity_collector_metadata_only"] is True


def test_ast_rejects_iq_collector_byte_open():
    source = SOURCE.with_name("build_runtime_guard_v3.py").read_text()
    marker = ") -> list[dict[str, object]]:\n    acquisition = context.get(\"acquisition\")"
    source = source.replace(marker, ") -> list[dict[str, object]]:\n    open('/forbidden')\n    acquisition = context.get(\"acquisition\")", 1)
    with pytest.raises(MODULE.EvidenceError, match="byte-read"):
        MODULE._validate_control_flow(source.encode())


def test_journal_exact_projection(monkeypatch):
    records = []
    messages = [
        "  ubuntu : PWD=/home/ubuntu/telemetry-yield ; USER=root ; COMMAND=" + MODULE.EXPECTED_COMMAND[:700],
        "  ubuntu : (command continued) " + MODULE.EXPECTED_COMMAND[MODULE.EXPECTED_COMMAND.index("--expected-amendment-sha256"):],
        "pam_unix(sudo:session): session opened for user root(uid=0) by ubuntu(uid=1000)",
        "pam_unix(sudo:session): session closed for user root",
    ]
    timestamps = [MODULE.JOURNAL_FIRST_REALTIME_US, MODULE.JOURNAL_FIRST_REALTIME_US + 1, MODULE.JOURNAL_FIRST_REALTIME_US + 2, MODULE.JOURNAL_LAST_REALTIME_US]
    for seq, message, timestamp in zip(range(36251, 36255), messages, timestamps):
        records.append({"__SEQNUM": str(seq), "__REALTIME_TIMESTAMP": str(timestamp), "_PID": str(MODULE.JOURNAL_PID), "_BOOT_ID": MODULE.JOURNAL_BOOT_ID, "_SYSTEMD_INVOCATION_ID": MODULE.JOURNAL_INVOCATION_ID, "_CMDLINE": MODULE.EXPECTED_SUDO_CMDLINE, "MESSAGE": message})
    journal_stdout = b"".join(json.dumps(record).encode() + b"\n" for record in records)
    monkeypatch.setattr(MODULE, "_read_regular", lambda path, maximum=0: (b"", {"path": str(path), "size_bytes": 1, "sha256": MODULE.JOURNALCTL_SHA256, "uid": 0, "gid": 0}))
    monkeypatch.setattr(MODULE, "_run_bounded", lambda argv, maximum: subprocess.CompletedProcess(argv, 0, journal_stdout, b""))
    result = MODULE._journal_evidence()
    assert result["record_count"] == 4
    assert result["session_opened"] is True
    assert result["stderr_or_exit_status_captured_by_journal"] is False


def test_journal_wrong_cmdline_rejected(monkeypatch):
    monkeypatch.setattr(MODULE, "_read_regular", lambda path, maximum=0: (b"", {"sha256": MODULE.JOURNALCTL_SHA256, "uid": 0, "gid": 0}))
    bad = {"__SEQNUM": "1"}
    monkeypatch.setattr(MODULE, "_run_bounded", lambda argv, maximum: subprocess.CompletedProcess(argv, 0, json.dumps(bad).encode() + b"\n", b""))
    with pytest.raises(MODULE.EvidenceError):
        MODULE._journal_evidence()


def test_subprocess_output_bound_is_enforced_while_running():
    with pytest.raises(MODULE.EvidenceError, match="output exceeded"):
        MODULE._run_bounded(
            ["/usr/bin/python3.12", "-I", "-S", "-c", "import os; os.write(1, b'x' * 200000)"],
            1024,
        )


def test_live_state_fixed_namespace_and_rejects_canonical_mount(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    pid = proc / "7"
    (pid / "ns").mkdir(parents=True)
    (pid / "fd").mkdir()
    os.symlink("mnt:[123]", pid / "ns/mnt")
    (pid / "cmdline").write_bytes(b"systemd\0")
    mountinfo = b"1 0 8:1 / / rw - ext4 /dev/x rw\n"
    (pid / "mountinfo").write_bytes(mountinfo)
    monkeypatch.setattr(MODULE, "EXPECTED_EXTERNAL_NAMESPACE_INODES", (123,))
    full = {"expected_namespace_inodes": [123], "namespaces": [{"namespace_inode": 123, "mountinfo_sha256": hashlib.sha256(mountinfo).hexdigest(), "mountinfo_size_bytes": len(mountinfo), "mountinfo": mountinfo.decode().splitlines()}], "ignored_empty_namespaces": [], "anonymous_or_unexpected_namespace_fd_handles": []}
    monkeypatch.setattr(MODULE, "EXTERNAL_NAMESPACE_BASELINE_SHA256", canonical_sha(full))
    result = MODULE._capture_live_state(proc)
    assert result["canonical_control_mount_count"] == 0
    (pid / "mountinfo").write_text(f"1 0 8:1 / {MODULE.CONTROL} rw - ext4 /dev/x rw\n")
    with pytest.raises(MODULE.EvidenceError, match="canonical control mount"):
        MODULE._capture_live_state(proc)


def test_live_state_rejects_suspicious_process(tmp_path, monkeypatch):
    proc = tmp_path / "proc"
    proc.mkdir()
    pid = proc / "7"
    (pid / "ns").mkdir(parents=True)
    (pid / "fd").mkdir()
    os.symlink("mnt:[123]", pid / "ns/mnt")
    (pid / "cmdline").write_bytes(b"exec-evaluator\0")
    (pid / "mountinfo").write_bytes(b"x\n")
    monkeypatch.setattr(MODULE, "EXPECTED_EXTERNAL_NAMESPACE_INODES", (123,))
    with pytest.raises(MODULE.EvidenceError, match="process exists"):
        MODULE._capture_live_state(proc)


def test_seed_requires_exact_four_root_files(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir(mode=0o755)
    monkeypatch.setattr(MODULE, "CONTROL", control)
    with pytest.raises(MODULE.EvidenceError, match="metadata differs|entry set differs"):
        MODULE._validate_seed()


def test_build_document_exact_top_shape_and_selfhash(monkeypatch):
    identity = {"path": str(SOURCE), "size_bytes": 1, "sha256": "a" * 64}
    test_identity = {"path": str(MODULE.GENERATOR_TEST), "size_bytes": 1, "sha256": "b" * 64}
    def read(path, maximum=0):
        return (b"", identity if Path(path) == SOURCE else test_identity)
    monkeypatch.setattr(MODULE, "_read_regular", read)
    state = {"inventory_sha256": MODULE.EXTERNAL_NAMESPACE_BASELINE_SHA256}
    seed = {"path": str(MODULE.CONTROL), "directory_identity": {}, "expected_entry_count": 4, "exact_entries": [{"path": str(MODULE.TSQ), "size_bytes": 69, "sha256": MODULE.TSQ_SHA256}, {"path": str(MODULE.TSR), "size_bytes": 1, "sha256": MODULE.TSR_SHA256}, {"path": str(MODULE.CA_CERTIFICATES), "size_bytes": 1, "sha256": MODULE.CA_SHA256}, {"path": str(MODULE.BUILDER_CONTROL), "size_bytes": 1, "sha256": MODULE.BUILDER_SHA256}]}
    protocol = {"amendment": {"path": str(MODULE.AMENDMENT), "size_bytes": 1, "sha256": MODULE.AMENDMENT_SHA256}}
    docs = {"builder": {"source": b""}}
    monkeypatch.setattr(MODULE, "_capture_live_state", lambda: state)
    monkeypatch.setattr(MODULE, "_validate_seed", lambda: dict(seed))
    monkeypatch.setattr(MODULE, "_validate_protocol", lambda: (protocol, docs))
    monkeypatch.setattr(MODULE, "_validate_conflict", lambda value: {"conflict_path": {}, "conflicting_fields": [], "current_builder_identity": {}, "stale_builder_identity": {}})
    monkeypatch.setattr(MODULE, "_validate_control_flow", lambda value: {"status": "PASS"})
    monkeypatch.setattr(MODULE, "_journal_evidence", lambda: {"pid": MODULE.JOURNAL_PID})
    monkeypatch.setattr(MODULE, "_verify_rfc3161", lambda amendment, value: {})
    document = MODULE.build_evidence("a" * 64, "b" * 64, "2026-09-06T06:00:00Z")
    assert set(document) == {"schema_version", "status", "created_at_utc", "generator", "generator_tests", "previous_protocol", "preflight_failure", "root_control_seed", "scientific_exposure", MODULE.HASH_FIELD}
    unhashed = dict(document)
    assert unhashed.pop(MODULE.HASH_FIELD) == canonical_sha(unhashed)
    assert all(value is False for value in document["scientific_exposure"].values())


def test_preflight_only_never_publishes(monkeypatch, capsys):
    monkeypatch.setattr(MODULE, "build_evidence", lambda *args: {"ok": True})
    monkeypatch.setattr(MODULE, "publish", lambda *args: pytest.fail("publish called"))
    assert MODULE.main(["--expected-generator-sha256", "a" * 64, "--expected-generator-tests-sha256", "b" * 64, "--created-at-utc", "2026-09-06T06:00:00Z", "--preflight-only"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_publication_is_no_clobber_and_root_style_mode(tmp_path, monkeypatch):
    reports = tmp_path / "reports"
    reports.mkdir()
    output = reports / MODULE.OUTPUT.name
    monkeypatch.setattr(MODULE, "REPORTS", reports)
    monkeypatch.setattr(MODULE, "OUTPUT", output)
    document = {"schema_version": MODULE.SCHEMA, MODULE.HASH_FIELD: "a" * 64}
    MODULE.publish(document, output)
    assert stat.S_IMODE(output.stat().st_mode) == 0o444
    assert output.stat().st_nlink == 1
    sidecar = output.with_name(output.name + ".sha256")
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o444
    assert sidecar.read_text().split()[0] == hashlib.sha256(output.read_bytes()).hexdigest()
    with pytest.raises(MODULE.EvidenceError, match="already exists"):
        MODULE.publish(document, output)


def test_parser_has_no_iq_path_and_created_at_is_explicit():
    destinations = {action.dest for action in MODULE._parser()._actions}
    assert not any("iq" in value.lower() for value in destinations)
    assert {"expected_generator_sha256", "expected_generator_tests_sha256", "created_at_utc", "preflight_only"}.issubset(destinations)
    with pytest.raises(MODULE.EvidenceError):
        MODULE._validate_created_at("now")
