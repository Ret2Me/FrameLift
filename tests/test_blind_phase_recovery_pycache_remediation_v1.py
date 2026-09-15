from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "work/blind-phase-confirmatory-v2/remediate_recovery_pycache_v1.py"
SPEC = importlib.util.spec_from_file_location("remediate_recovery_pycache_v1", SOURCE)
assert SPEC and SPEC.loader
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R
SPEC.loader.exec_module(R)

RECOVERY_SOURCE = (
    Path("/var/lib/telemetry-yield-confirmatory-v3-recovery-attempt-67adc3bf")
    / "recover_partial_activation_attempt2_v3.py"
)


def _dir_identity(path: Path) -> dict[str, object]:
    status = path.lstat()
    return {
        "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": format(stat.S_IMODE(status.st_mode), "04o"),
        "nlink": status.st_nlink,
    }


def _file_identity(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    status = path.lstat()
    return {
        "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        "st_dev": status.st_dev, "st_ino": status.st_ino,
        "uid": status.st_uid, "gid": status.st_gid,
        "mode": format(stat.S_IMODE(status.st_mode), "04o"),
        "nlink": status.st_nlink,
    }


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source_parent = tmp_path / "recovery"
    control = tmp_path / "remediation"
    source_parent.mkdir()
    control.mkdir()
    source = source_parent / "__pycache__"
    source.mkdir()
    child = source / R.CONTAMINATION_NAME
    child.write_bytes(b"exact-pyc")
    runner_path = control / "runner.py"
    runner_path.write_text("runner")
    quarantine = control / "quarantine"
    incident = control / "incident.json"
    passed = control / "pass.json"
    failure = control / "failure.json"
    monkeypatch.setattr(R, "SOURCE_PARENT", source_parent)
    monkeypatch.setattr(R, "SOURCE", source)
    monkeypatch.setattr(R, "REMEDIATION_CONTROL", control)
    monkeypatch.setattr(R, "CONTROL_COPY", runner_path)
    monkeypatch.setattr(R, "QUARANTINE", quarantine)
    monkeypatch.setattr(R, "INCIDENT", incident)
    monkeypatch.setattr(R, "PASS", passed)
    monkeypatch.setattr(R, "FAILURE", failure)
    monkeypatch.setattr(R, "SOURCE_NAMES_BEFORE", ("__pycache__",))
    monkeypatch.setattr(R, "SOURCE_NAMES_AFTER", ())
    before_parent = _dir_identity(source_parent)
    monkeypatch.setattr(R, "SOURCE_PARENT_IDENTITY", before_parent)
    monkeypatch.setattr(
        R, "SOURCE_PARENT_AFTER_IDENTITY", {**before_parent, "nlink": 2}
    )
    monkeypatch.setattr(R, "SOURCE_DIRECTORY_IDENTITY", _dir_identity(source))
    monkeypatch.setattr(R, "CONTAMINATION_IDENTITY", _file_identity(child))
    runner = {"path": str(runner_path), "size_bytes": 6, "sha256": "a" * 64}
    external = {"inventory_sha256": R.EXPECTED_EXTERNAL_INVENTORY_SHA256}
    history = {
        "recovery_incident": {"path": "incident", "size_bytes": 1, "sha256": "1" * 64},
        "recovery_failure": {"path": "failure", "size_bytes": 1, "sha256": "2" * 64},
        "recovery_runner": {"path": "runner", "size_bytes": 1, "sha256": "3" * 64},
        "external_inventory": external,
    }
    monkeypatch.setattr(R, "_validate_control_parent", lambda: None)
    monkeypatch.setattr(R, "_validate_runner", lambda _sha: runner)
    monkeypatch.setattr(R, "_validate_bound_history", lambda: history)
    monkeypatch.setattr(R, "_validate_prestate", lambda _proc, _history: external)
    monkeypatch.setattr(R, "_capture_external", lambda _proc: external)
    monkeypatch.setattr(R, "_fsync_directory", lambda _path: None)
    documents: dict[Path, tuple[dict[str, object], dict[str, object]]] = {}
    events: list[str] = []

    def publish(path: Path, document: dict[str, object], field: str):
        events.append(f"publish:{path.name}")
        final = R._self_hashed(document, field)
        identity = {"path": str(path), "size_bytes": 1, "sha256": path.name[0] * 64}
        documents[path] = (final, identity)
        path.touch()
        return identity

    def load(path: Path, _field: str):
        return documents[path]

    def rename(source_path: Path, destination_path: Path):
        events.append("rename")
        os.rename(source_path, destination_path)

    monkeypatch.setattr(R, "_publish", publish)
    monkeypatch.setattr(R, "_load_receipt", load)
    monkeypatch.setattr(R, "_rename_noreplace", rename)
    return source, quarantine, incident, passed, failure, runner, history, external, documents, events


def test_frozen_inputs_and_destination_are_exact_and_outside_recovery_control() -> None:
    assert R.BOUND_FILES[R.RECOVERY_INCIDENT]["sha256"] == "a0cc55b53847b6c4b24115701800e93a733ff1843e6b43a49064395a12a74ebd"
    assert R.BOUND_FILES[R.RECOVERY_FAILURE]["sha256"] == "e66603c8645c934d4d93122308357b32ccb4b64db03992417df1373ec8f363f5"
    assert R.BOUND_FILES[R.RECOVERY_RUNNER]["sha256"] == "b1a93cd7eee10dc6c1dfc16a2e648943517f4f80f9ad1473f21f518892d4c169"
    assert R.CONTAMINATION_IDENTITY["sha256"] == "32ba3e841497f432cdb33281876b6a507bc56710ba67c49d12dc1ed48590ccf9"
    assert R.EXPECTED_EXTERNAL_INVENTORY_SHA256 == "c9189df4cd87dedf48a81aba866147c885b4ba75212f7e71680c9523ab5b013a"
    assert R.QUARANTINE.parent == R.REMEDIATION_CONTROL
    assert not R.QUARANTINE.is_relative_to(R.SOURCE_PARENT)
    assert R.REMEDIATION_CONTROL == Path(
        "/var/lib/telemetry-yield-confirmatory-v3-pycache-remediation-v2-32ba3e84"
    )


def test_success_commits_incident_before_single_rename_and_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, quarantine, _incident, _passed, _failure, *_rest, events = _fixture(
        tmp_path, monkeypatch
    )
    result = R.remediate("a" * 64, proc_root=tmp_path)
    assert result["status"] == "PASS"
    assert events == ["publish:incident.json", "rename", "publish:pass.json"]
    assert not source.exists()
    assert quarantine.is_dir()


def test_crash_after_rename_resumes_without_second_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, quarantine, incident, _passed, _failure, runner, history, external, documents, events = _fixture(
        tmp_path, monkeypatch
    )
    contamination = R._directory_inventory(source)
    expected = R._incident_document(runner, history, external, contamination)
    incident.touch()
    documents[incident] = (
        R._self_hashed(expected, "incident_payload_sha256"),
        {"path": str(incident), "size_bytes": 1, "sha256": "i" * 64},
    )
    os.rename(source, quarantine)
    result = R.remediate("a" * 64, proc_root=tmp_path)
    assert result["status"] == "PASS"
    assert "rename" not in events
    assert events == ["publish:pass.json"]


def test_post_rename_external_drift_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    *_prefix, failure, _runner, _history, external, _documents, events = _fixture(
        tmp_path, monkeypatch
    )
    calls = 0

    def capture(_proc: Path):
        nonlocal calls
        calls += 1
        return external if calls == 1 else {"inventory_sha256": "f" * 64}

    monkeypatch.setattr(R, "_capture_external", capture)
    monkeypatch.setattr(R, "_validate_prestate", lambda proc, history: capture(proc))
    with pytest.raises(R.RemediationError, match="changed during remediation"):
        R.remediate("a" * 64, proc_root=tmp_path)
    assert failure.exists()
    assert events == ["publish:incident.json", "rename", "publish:failure.json"]


def test_source_and_destination_or_changed_pyc_refuse_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, quarantine, *_rest, events = _fixture(tmp_path, monkeypatch)
    quarantine.mkdir()
    with pytest.raises(R.RemediationError, match="topology"):
        R.remediate("a" * 64, proc_root=tmp_path)
    assert events == []
    quarantine.rmdir()
    (source / R.CONTAMINATION_NAME).write_bytes(b"drift")
    with pytest.raises(R.RemediationError, match="identity differs"):
        R.remediate("a" * 64, proc_root=tmp_path)
    assert events == []


def test_post_incident_rename_failure_publishes_lockout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    *_prefix, events = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        R, "_rename_noreplace",
        lambda *_a: (_ for _ in ()).throw(OSError("rename failed")),
    )
    with pytest.raises(OSError, match="rename failed"):
        R.remediate("a" * 64, proc_root=tmp_path)
    assert events == ["publish:incident.json", "publish:failure.json"]


def test_preflight_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, quarantine, *_rest, events = _fixture(tmp_path, monkeypatch)
    result = R.remediate("a" * 64, proc_root=tmp_path, preflight_only=True)
    assert result["status"] == "PASS_READ_ONLY_NO_PUBLICATION_NO_RENAME"
    assert source.is_dir() and not quarantine.exists() and events == []


def test_preflight_after_rename_requires_valid_incident_and_never_repairs_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, quarantine, incident, *_rest, documents, events = _fixture(
        tmp_path, monkeypatch
    )
    os.rename(source, quarantine)
    with pytest.raises(R.RemediationError, match="without a valid committed incident"):
        R.remediate("a" * 64, proc_root=tmp_path, preflight_only=True)
    assert events == []

    # The unit fixture's receipt loader is a pure in-memory stub; verify that
    # production passes the explicit read-only mode for this phase.
    incident.touch()
    runner = {"path": str(tmp_path / "remediation/runner.py"), "size_bytes": 6, "sha256": "a" * 64}
    history = R._validate_bound_history()
    external = R._validate_prestate(tmp_path, history)
    contamination = R._directory_inventory(quarantine)
    documents[incident] = (
        R._self_hashed(
            R._incident_document(runner, history, external, contamination),
            "incident_payload_sha256",
        ),
        {"path": str(incident), "size_bytes": 1, "sha256": "i" * 64},
    )
    observed: list[bool] = []
    original = R._load_receipt

    def load(path: Path, field: str, *, allow_sidecar_completion: bool = True):
        observed.append(allow_sidecar_completion)
        return original(path, field)

    monkeypatch.setattr(R, "_load_receipt", load)
    result = R.remediate("a" * 64, proc_root=tmp_path, preflight_only=True)
    assert result["phase"] == "after" and observed == [False]


def test_existing_failure_locks_out_before_history_or_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source, _quarantine, _incident, _passed, failure, *_rest, events = _fixture(
        tmp_path, monkeypatch
    )
    failure.touch()
    monkeypatch.setattr(R, "_load_receipt", lambda *_a: ({"status": "FAIL"}, {}))
    monkeypatch.setattr(
        R, "_validate_bound_history", lambda: pytest.fail("history read after lock")
    )
    with pytest.raises(R.RemediationError, match="failure lock"):
        R.remediate("a" * 64, proc_root=tmp_path)
    assert events == []


def test_renameat2_collision_is_no_clobber(monkeypatch: pytest.MonkeyPatch) -> None:
    class Call:
        argtypes = None
        restype = None

        def __call__(self, *_args):
            ctypes = __import__("ctypes")
            ctypes.set_errno(__import__("errno").EEXIST)
            return -1

    class Lib:
        renameat2 = Call()

    monkeypatch.setattr(R.ctypes, "CDLL", lambda *_a, **_k: Lib())
    with pytest.raises(R.RemediationError, match="no-clobber"):
        R._rename_noreplace(Path("/source"), Path("/destination"))


def test_receipt_json_is_validated_before_missing_sidecar_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "receipt.json"
    path.write_text("broken")
    publications: list[Path] = []
    original_identity = R._identity

    def identity(target: Path, maximum: int):
        if target == path:
            return b"broken", {
                "path": str(path), "size_bytes": 6, "sha256": "a" * 64,
                "st_dev": 1, "st_ino": 2, "uid": 0, "gid": 0,
                "mode": "0444", "nlink": 1,
            }
        return original_identity(target, maximum)

    monkeypatch.setattr(R, "_identity", identity)
    monkeypatch.setattr(
        R, "_publish_one",
        lambda target, _payload: publications.append(target) or {},
    )
    with pytest.raises(R.RemediationError, match="JSON is invalid"):
        R._load_receipt(path, "payload_sha256")
    assert publications == []


def test_existing_pass_revalidates_every_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _source, _quarantine, _incident, passed, _failure, *_middle, documents, events = _fixture(
        tmp_path, monkeypatch
    )
    first = R.remediate("a" * 64, proc_root=tmp_path)
    assert first["status"] == "PASS"
    events.clear()
    second = R.remediate("a" * 64, proc_root=tmp_path)
    assert second["status"] == "PASS" and events == []
    document, identity = documents[passed]
    tampered = dict(document)
    tampered["remediation_runner"] = {"path": "wrong"}
    tampered = R._self_hashed(tampered, "remediation_payload_sha256")
    documents[passed] = (tampered, identity)
    with pytest.raises(R.RemediationError, match="existing PASS"):
        R.remediate("a" * 64, proc_root=tmp_path)


def test_pass_binds_equal_before_and_after_external_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    *_prefix, external, _documents, _events = _fixture(tmp_path, monkeypatch)
    result = R.remediate("a" * 64, proc_root=tmp_path)
    assert result["external_namespace_inventory_before"] == external
    assert result["external_namespace_inventory_after"] == external
    assert result["external_namespace_zero_delta"] is True


def test_post_remediation_inventory_is_accepted_by_b1_recovery_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location("attempt2_recovery_b1_inventory", RECOVERY_SOURCE)
    assert spec and spec.loader
    recovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recovery)
    control = tmp_path / "recovery-control"
    control.mkdir()
    runner = control / "recover_partial_activation_attempt2_v3.py"
    runner.write_text("runner")
    incident = control / "start-only-failure-incident-v2.json"
    failure = control / "start-only-failure-lockout-v2.json"
    for path in (incident, failure):
        path.write_text("{}")
        path.with_name(path.name + ".sha256").write_text("sidecar")
    monkeypatch.setattr(recovery, "RECOVERY_CONTROL_PARENT", control)
    monkeypatch.setattr(recovery, "CONTROL_COPY", runner)
    monkeypatch.setattr(recovery, "CONTROL_INCIDENT_OUTPUT", incident)
    monkeypatch.setattr(recovery, "CONTROL_FAILURE_OUTPUT", failure)
    monkeypatch.setattr(
        recovery, "CONTROL_RESULT_OUTPUT", control / "start-only-failure-recovery-v2.json"
    )
    assert recovery._validate_recovery_control_inventory() == tuple(
        sorted(path.name for path in control.iterdir())
    )


def test_cli_and_ast_are_bounded_to_receipts_and_one_directory_rename() -> None:
    args = R._parser().parse_args(["--expected-script-sha256", "a" * 64])
    assert args.expected_script_sha256 == "a" * 64
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    attributes = {
        f"{node.func.value.id}.{node.func.attr}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }
    assert "os.rename" not in attributes
    assert "os.unlink" not in attributes
    assert "os.remove" not in attributes
    assert "shutil.rmtree" not in attributes
    assert "subprocess.run" not in attributes
    assert "os.setns" not in attributes
    assert "renameat2" in SOURCE.read_text(encoding="utf-8")
    assert 'b"-I", b"-S", b"-B"' in SOURCE.read_text(encoding="utf-8")


def test_cli_success_systemexit_is_not_relabelled_as_refusal() -> None:
    completed = subprocess.run(
        ["/usr/bin/python3.12", "-I", "-S", "-B", str(SOURCE), "--help"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 0
    assert b"REFUSED" not in completed.stderr


def test_cli_operational_exception_is_refused_with_exit_two() -> None:
    completed = subprocess.run(
        [
            "/usr/bin/python3.12", "-I", "-S", "-B", str(SOURCE),
            "--expected-script-sha256", "not-a-sha", "--preflight-only",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert completed.returncode == 2
    assert b"REFUSED: " in completed.stderr


def test_external_scan_rejects_pinned_failed_namespace_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = tmp_path / "proc"
    pid = proc / "100"
    (pid / "ns").mkdir(parents=True)
    (pid / "fd").mkdir()
    # Index 19 after the comm/state prefix is Linux stat field 22/starttime.
    (pid / "stat").write_bytes(
        b"100 (test) S " + b" ".join([b"0"] * 19 + [b"7"] + [b"0"] * 4)
    )
    (pid / "ns/mnt").symlink_to("mnt:[10]")
    (pid / "mountinfo").write_text(
        "1 0 1:1 / / rw - ext4 /dev/root rw\n", encoding="ascii"
    )
    (pid / "cmdline").write_bytes(b"service\0")
    (pid / "maps").write_bytes(b"")
    (pid / "fd/9").symlink_to("mnt:[999]")
    monkeypatch.setattr(R, "EXPECTED_NAMESPACE_INODES", (10,))
    monkeypatch.setattr(R, "IGNORED_NAMESPACE_INODE", 11)
    monkeypatch.setattr(R, "KEEPER_NAMESPACE_INODE", 999)
    with pytest.raises(R.RemediationError, match="pinned fd"):
        R._capture_external_once(proc)


def test_otmpfile_receipt_and_sidecar_are_root_immutable(tmp_path: Path) -> None:
    if os.geteuid() != 0:
        pytest.skip("root metadata assertion requires production uid")
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(R, "REMEDIATION_CONTROL", tmp_path)
        monkey.setattr(R, "INCIDENT", tmp_path / "incident.json")
        monkey.setattr(R, "PASS", tmp_path / "pass.json")
        monkey.setattr(R, "FAILURE", tmp_path / "failure.json")
        identity = R._publish(
            R.INCIDENT, {"schema_version": "x", "status": "PASS"}, "payload_sha256"
        )
        assert identity["mode"] == "0444" and identity["nlink"] == 1
        assert (tmp_path / "incident.json.sha256").read_text().endswith("  incident.json\n")
        with pytest.raises(R.RemediationError, match="no-clobber"):
            R._publish(R.INCIDENT, {}, "payload_sha256")
    finally:
        monkey.undo()
